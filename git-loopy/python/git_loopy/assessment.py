"""Advisory Release assessment served only over local MCP stdio."""

from __future__ import annotations

import asyncio
import html
import os
import re
import string
import sys
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from importlib.metadata import version as installed_version
from typing import Any

from .assessment_config import (
    MILESTONE_PATTERN, REPOSITORY_PATTERN, AssessmentConfigError, load_policy,
)
from .assessment_github import SHA_PATTERN, GitHubReads, Transport, Unreadable
from .release_version import (
    is_release_target_label, parse_release_line, parse_release_version, promote_closed_milestone,
    release_target_label,
)
from .wrapper import extract_checkpoint_refs, extract_close_refs


ADVICE = (
    "Advice only. Whether to close the milestone is your decision. "
    "Closing it triggers Promotion, and Rehearsal and Release smoke still prove "
    "the snapshot afterwards. This assessment changes nothing."
)
MEANINGS = {
    "reviewable": "Evidence is complete and none argues for holding. "
                  "Sufficient for your review — not authorization.",
    "hold_advised": "Evidence is complete and at least one finding argues for holding.",
    "incomplete": "Some required evidence could not be read. "
                  "No conclusion either way — this does not mean probably fine.",
}


def _work_record(record: Any, read: str, source: str, api: str) -> dict:
    if not isinstance(record, dict) or (
        not isinstance(record.get("number"), int) or isinstance(record["number"], bool)
        or record["number"] < 1 or record.get("state") not in ("open", "closed")
        or not isinstance(record.get("title"), str)
        or record.get("url") != f"https://api.github.com{api}/issues/{record['number']}"
        or not isinstance(record.get("labels"), list)
        or not all(isinstance(label, dict) and isinstance(label.get("name"), str)
                   for label in record["labels"])
        or "milestone" not in record or (
            record["milestone"] is not None and (
                not isinstance(record["milestone"], dict)
                or not isinstance(record["milestone"].get("number"), int)
                or isinstance(record["milestone"]["number"], bool)
                or record["milestone"]["number"] < 1
                or not isinstance(record["milestone"].get("title"), str)
            )
        )
    ):
        raise Unreadable(read, "unrecognized evidence shape", source)
    return record


def _inert(value: object) -> str:
    """Encode repository-sourced syntax as inert inline HTML data."""
    # Numeric entities cannot introduce Markdown syntax, line breaks or HTML.
    return "".join(
        c if c in string.ascii_letters + string.digits + " "
        else f"&#{ord(c)};" if c.isprintable() else " "
        for c in str(value)
    )


def render_packet(result: dict[str, Any]) -> str:
    r = result
    # These headline fields are strict tool inputs or validated SHA, not free text.
    lines = [
        f"## Assessment verdict: {r['verdict']} — {r['milestone'] or 'unresolved'} "
        f"at {r['sha'] or 'unresolved'}",
        f"{MEANINGS[r['verdict']]} As of {r['asOf']}.",
        "", f"> {ADVICE}", "", "## Why — hold findings",
    ]
    for hold in r["holds"]:
        lines.extend([
            f"- {_inert(hold['text'])}",
            f"  - [source]({hold['source']}); observed: {_inert(hold['observed'])}",
        ])
        if hold["context"]:
            lines.append(f"  - Context: {_inert(hold['context'])}")
    if not r["holds"]:
        lines.append("- None.")
    lines.extend(["", "## Could not read"])
    for item in r["incompletes"]:
        lines.append(
            f"- {_inert(item['read'])}: {_inert(item['reason'])} "
            f"— [source]({item['source']})"
        )
    if not r["incompletes"]:
        lines.append("- Nothing — every required read completed.")
    lines.extend(["", "## What would change this"])
    if r["verdict"] == "incomplete":
        lines.append("- Re-run the assessment once the unreadable items above can be read.")
    if r["holds"]:
        lines.append(
            "- Each hold finding stands until its source record changes; "
            "resolving it happens outside this assessment."
        )
    if r["verdict"] == "reviewable":
        lines.append(
            "- Review the line content below, then decide yourself whether "
            "to close the milestone in GitHub."
        )
    lines.extend([
        "- Any new trunk commit makes this assessment stale — re-run before deciding.",
        "", "## Counted checks",
    ])
    lines.extend(
        f"- {_inert(c['workflow'])}: {_inert(c['conclusion'])} "
        f"— [{'run' if '/actions/runs/' in c['url'] else 'source'}]({c['url']})"
        for c in r["checks"]
    )
    lines.extend(["", "## Line content"])
    for item in r["content"]:
        lines.append(
            f"- [#{item['n']}](https://github.com/{r['repo']}/issues/{item['n']}): "
            f"{_inert(item['title'])} — "
            f"{'closed, not linked' if item['cls'] == 'closed_not_linked' else item['cls']}"
        )
        lines.extend(
            f"  - {_inert(via['text'])} — [source]({via['source']})"
            for via in item["via"]
        )
    if not r["content"]:
        lines.append("- No classified issues. See Could not read for any missing evidence.")
    lines.extend(["", "## Context (does not change the verdict)"])
    lines.extend(f"- {_inert(c)}" for c in r["context"])
    if not r["context"]:
        lines.append("- None.")
    target = r["releaseTarget"] or (
        "no target (VERSION is stable)"
        if r["releaseTargetState"] == "no_target"
        else "unreadable"
    )
    lines.extend(["", "## Provenance"])
    for label, value in [
        ("Repository", r["repo"]), ("Milestone", r["milestone"] or "unresolved"),
        ("Milestone state", r["milestoneState"] or "unreadable"),
        ("Milestone source", r["sources"].get("milestone", "unresolved")),
        ("Pinned SHA", r["sha"] or "unreadable"), ("As of", r["asOf"]),
        ("Trunk source", r["sources"].get("trunk", "unreadable")),
        ("VERSION at SHA", r["version"] or "unreadable"),
        ("VERSION source", r["sources"].get("version", "unreadable")),
        ("Release target", target), ("Baseline tag", r["baseline"] or "unreadable"),
        ("Baseline source", r["sources"].get("baseline", "unreadable")),
        ("Assessment policy revision", r["policy"]),
        ("Counted workflows", ", ".join(r["countedWorkflows"])),
        ("Service version", r["service"]), ("Result-format version", r["resultFormat"]),
        ("Read identity", r["identity"]),
    ]:
        rendered = f"[source]({value})" if label.endswith("source") and (
            isinstance(value, str) and value.startswith("https://github.com/")
        ) else _inert(value)
        lines.append(f"- {label}: {rendered}")
    return "\n".join(lines) + "\n"


def create_server(
    *, env: Mapping[str, str] | None = None, transport: Transport | None = None,
    clock: Callable[[], str] | None = None,
):
    """Load a policy snapshot and construct the two read-only tools."""
    from mcp import types
    from mcp.server import Server

    policies = load_policy(os.environ if env is None else env)
    if transport is None:
        raise AssessmentConfigError(
            "no GitHub App credential transport is available in this service version; "
            "App authentication arrives with #760; ambient credentials are never used"
        )
    service_version = installed_version("git-loopy")
    now = clock or (lambda: datetime.now(timezone.utc).isoformat())
    schema: dict[str, Any] = {
        "type": "object", "additionalProperties": False, "required": ["repository"],
        "properties": {
            "repository": {
                "type": "string", "minLength": 3, "maxLength": 140,
                "pattern": "^" + REPOSITORY_PATTERN + "$",
            },
            "milestone": {
                "type": "string", "minLength": 6, "maxLength": 64,
                "pattern": "^" + MILESTONE_PATTERN + "$",
            },
        },
    }
    sha_pattern = r"[0-9a-fA-F]{40}"
    freshness_schema: dict[str, Any] = {
        "type": "object", "additionalProperties": False,
        "required": ["repository", "milestone", "sha"],
        "properties": {
            **schema["properties"],
            "sha": {
                "type": "string", "minLength": 40, "maxLength": 40,
                "pattern": "^" + sha_pattern + "$",
            },
        },
    }
    schemas = {"assess": schema, "check_freshness": freshness_schema}

    async def list_tools(context, params):
        return types.ListToolsResult(tools=[types.Tool(
            name=name, description=description, input_schema=schemas[name],
            annotations=types.ToolAnnotations(
                read_only_hint=True, destructive_hint=False, open_world_hint=True,
            ),
        ) for name, description in [
            ("assess", "Read one Release line; advice only."),
            ("check_freshness", "Confirm the pinned head and open milestone title; "
             "stale assessments must be re-run, never refreshed in place."),
        ]])

    def validate_inputs(arguments, name):
        contract = schemas[name]
        if not isinstance(arguments, dict) or (
            set(arguments) - set(contract["properties"])
            or set(contract["required"]) - set(arguments)
        ):
            raise ValueError(
                "assess accepts only repository and optional milestone"
                if name == "assess" else
                "check_freshness requires only repository, milestone and sha"
            )
        repository = arguments.get("repository")
        milestone = arguments.get("milestone")
        if (
            not isinstance(repository, str) or len(repository) > 140
            or re.fullmatch(REPOSITORY_PATTERN, repository) is None
        ):
            raise ValueError("repository must be a bounded owner/name")
        if "milestone" in arguments and (
            not isinstance(milestone, str) or len(milestone) > 64
            or re.fullmatch(MILESTONE_PATTERN, milestone) is None
        ):
            raise ValueError("milestone must be a bounded vX.Y.Z")
        if repository not in policies:
            raise ValueError("repository is not allowlisted")
        return repository, milestone

    async def assess(arguments):
        repository, milestone = validate_inputs(arguments, "assess")
        policy = policies[repository]
        github = GitHubReads(repository, transport)
        result: dict[str, Any] = {
            "repo": repository, "milestone": milestone, "milestoneState": None,
            "sha": None, "asOf": now(), "version": None, "releaseTarget": None,
            "releaseTargetState": "unreadable",
            "baseline": None, "policy": policy.revision, "service": service_version,
            "identity": policy.identity, "resultFormat": 1,
            "countedWorkflows": list(policy.workflows),
            "checks": [], "holds": [], "incompletes": [], "content": [], "context": [],
            "verdict": "incomplete", "sources": {},
        }
        line = None
        selected = None
        try:
            result["sha"] = await github.pin_head()
            result["sources"]["trunk"] = f"{github.web}/commit/{result['sha']}"
        except Unreadable as exc:
            result["incompletes"].append(exc.item)
        if result["sha"]:
            source = f"{github.web}/blob/{result['sha']}/VERSION"
            result["sources"]["version"] = source
            try:
                result["version"] = parse_release_version(
                    await github.version_text(result["sha"]),
                )
                line = parse_release_line(result["version"])
                if line.counter:
                    result["releaseTarget"] = line.target
                    result["releaseTargetState"] = "derived"
                    result["context"].append(
                        f"Prerelease stage is {line.stage}.{line.counter} — context only; "
                        "Promotion promotes from any stage."
                    )
                else:
                    result["releaseTargetState"] = "no_target"
            except Unreadable as exc:
                result["incompletes"].append(exc.item)
            except ValueError:
                result["incompletes"].append({
                    "read": "VERSION at pinned SHA",
                    "reason": "unrecognized evidence shape", "source": source,
                })
        try:
            catalog = await github.milestones()
            candidates = [m for m in catalog if (
                len(m["title"]) <= 64
                and re.fullmatch(MILESTONE_PATTERN, m["title"])
                and (m["title"] == milestone if milestone else m["state"] == "open")
            )]
            if len(candidates) != 1 or candidates[0]["state"] != "open":
                found = ", ".join(
                    f"{m['title']} ({m['state']})" for m in catalog
                    if re.fullmatch(MILESTONE_PATTERN, m["title"])
                ) or "none"
                raise ValueError(f"cannot resolve one open Release milestone; found: {found}")
            selected = candidates[0]
            result["milestone"], result["milestoneState"] = selected["title"], "open"
            result["sources"]["milestone"] = (
                f"{github.web}/milestone/{selected['number']}"
            )
        except Unreadable as exc:
            result["incompletes"].append(exc.item)
        if line is not None and result["version"] and result["milestoneState"] == "open" and (
            promote_closed_milestone(
                result["version"], result["milestone"], "closed",
            ) is None
        ):
            result["holds"].append({
                "text": f"Closing milestone {result['milestone']} would promote nothing: "
                        f"VERSION is {result['version']}.",
                "source": result["sources"]["version"],
                "observed": f"VERSION={result['version']}; milestone={result['milestone']}",
                "context": "",
            })
        checks_source = f"{github.web}/commit/{result['sha'] or 'unresolved'}/checks"
        result["checks"] = [
            {"workflow": w, "conclusion": "unreadable", "url": checks_source}
            for w in policy.workflows
        ]
        if not result["sha"]:
            result["incompletes"].append({
                "read": "Counted checks", "reason": "pinned SHA unavailable",
                "source": checks_source,
            })
        else:
            try:
                runs = await github.workflow_runs(result["sha"])
                result["checks"] = []
                result["context"].extend(
                    f"Unlisted workflow {run['name']}: "
                    f"{run['conclusion'] or run['status']} "
                    f"({github.web}/actions/runs/{run['id']})."
                    for run in runs if run["name"] not in policy.workflows
                )
                for workflow in policy.workflows:
                    matching = [run for run in runs if run["name"] == workflow]
                    if not matching:
                        result["checks"].append({
                            "workflow": workflow, "conclusion": "missing",
                            "url": checks_source,
                        })
                        result["incompletes"].append({
                            "read": f"Counted workflow {workflow}",
                            "reason": "missing: no run at pinned SHA",
                            "source": checks_source,
                        })
                    disagree = len({run["conclusion"] for run in matching}) > 1
                    if disagree:
                        result["incompletes"].append({
                            "read": f"Counted workflow {workflow}",
                            "reason": "unrecognized evidence shape",
                            "source": checks_source,
                        })
                    for run in matching:
                        conclusion = run["conclusion"] or run["status"]
                        url = f"{github.web}/actions/runs/{run['id']}"
                        result["checks"].append({
                            "workflow": workflow, "conclusion": conclusion, "url": url,
                        })
                        if disagree:
                            continue
                        if run["status"] in ("queued", "in_progress") and (
                            run["conclusion"] is None
                        ):
                            result["incompletes"].append({
                                "read": f"Counted workflow {workflow}",
                                "reason": run["status"], "source": url,
                            })
                        elif run["status"] == "completed" and conclusion in (
                            "failure", "cancelled", "timed_out",
                        ):
                            result["holds"].append({
                                "text": f"Counted workflow {workflow} concluded "
                                        f"{conclusion}.",
                                "source": url, "observed": conclusion, "context": "",
                            })
                        elif not (
                            run["status"] == "completed" and conclusion == "success"
                        ):
                            result["incompletes"].append({
                                "read": f"Counted workflow {workflow}",
                                "reason": "unrecognized evidence shape", "source": url,
                            })
            except Unreadable as exc:
                result["incompletes"].append(exc.item)
        label = (
            release_target_label(line.target) if line is not None and line.counter else
            result["milestone"] if line is not None else None
        )
        closed_issues = []
        if label is None:
            result["incompletes"].append({
                "read": "Open and unabsorbed work", "reason": "line label unavailable",
                "source": github.web + "/issues",
            })
        else:
            open_issues = set()
            try:
                async for raw in github.work_records("issues"):
                    try:
                        issue = _work_record(
                            raw, "Issue catalog", github.web + "/issues", github.api,
                        )
                    except Unreadable as exc:
                        result["incompletes"].append(exc.item)
                        continue
                    if "pull_request" in issue:
                        if not isinstance(issue["pull_request"], dict) or (
                            issue["pull_request"].get("url") != (
                                f"https://api.github.com{github.api}/pulls/{issue['number']}"
                            )
                        ):
                            result["incompletes"].append({
                                "read": "Issue catalog", "reason": "unrecognized evidence shape",
                                "source": github.web + "/issues",
                            })
                        continue
                    number = issue["number"]
                    source = f"{github.web}/issues/{number}"
                    labels = [item["name"] for item in issue["labels"]]
                    targets = []
                    for claim in dict.fromkeys(labels):
                        if not is_release_target_label(claim):
                            continue
                        try:
                            release_target_label(claim[1:])
                            targets.append(claim)
                        except ValueError:
                            result["incompletes"].append({
                                "read": f"Release label of issue #{number}",
                                "reason": "unrecognized evidence shape", "source": source,
                            })
                    if len(targets) > 1:
                        result["incompletes"].append({
                            "read": f"Release label of issue #{number}",
                            "reason": "unrecognized evidence shape", "source": source,
                        })
                    if issue["state"] == "closed" and line is not None:
                        if label in labels:
                            if issue.get("state_reason") not in ("completed", "not_planned"):
                                result["incompletes"].append({
                                    "read": f"Closure of issue #{number}",
                                    "reason": "unrecognized evidence shape", "source": source,
                                })
                            else:
                                closed_issues.append(issue)
                        for target in targets:
                            if tuple(map(int, target[1:].split("."))) > tuple(
                                map(int, line.target.split(".")),
                            ):
                                result["holds"].append({
                                    "text": f"Closed issue #{number}: {issue['title']} carries "
                                            f"unabsorbed label {target}; VERSION is "
                                            f"{result['version']} (target {line.target}).",
                                    "source": source,
                                    "observed": f"state=closed; label={target}; "
                                                f"VERSION={result['version']}",
                                    "context": "",
                                })
                    if issue["state"] == "open" and label in labels:
                        open_issues.add(number)
                        hold = {
                            "text": f"Open issue #{number}: {issue['title']} carries "
                                    f"the line's label {label}.",
                            "source": source, "observed": f"state=open; label={label}",
                            "context": "",
                        }
                        result["holds"].append(hold)
                        contexts = []
                        read = f"Native blockers of issue #{number}"
                        try:
                            async for raw_blocker in github.work_records("blockers", number):
                                try:
                                    blocker_url = (
                                        raw_blocker.get("url", "")
                                        if isinstance(raw_blocker, dict) else ""
                                    )
                                    match = re.fullmatch(
                                        r"https://api\.github\.com/repos/("
                                        + REPOSITORY_PATTERN + r")/issues/([1-9][0-9]*)",
                                        blocker_url if isinstance(blocker_url, str) else "",
                                    )
                                    if match is None:
                                        raise Unreadable(
                                            read, "unrecognized evidence shape", source,
                                        )
                                    blocker = _work_record(
                                        raw_blocker,
                                        f"Native blocker {match.group(1)}"
                                        f"#{match.group(2)} of issue #{number}",
                                        source,
                                        f"/repos/{match.group(1)}",
                                    )
                                    if blocker["state"] == "open":
                                        contexts.append(
                                            f"Open blocker {match.group(1)}"
                                            f"#{blocker['number']}: {blocker['title']} "
                                            f"(https://github.com/{match.group(1)}"
                                            f"/issues/{blocker['number']})."
                                        )
                                except Unreadable as exc:
                                    result["incompletes"].append(exc.item)
                        except Unreadable as exc:
                            result["incompletes"].append(exc.item)
                        hold["context"] = " ".join(contexts)
                    elif label not in labels and selected and issue["milestone"] and (
                        issue["milestone"]["number"] == selected["number"]
                    ):
                        result["context"].append(
                            f"Issue #{number}: {issue['title']} belongs to milestone "
                            f"{selected['title']} — context only ({source})."
                        )
            except Unreadable as exc:
                result["incompletes"].append(exc.item)
            try:
                async for pull in github.work_records("pulls"):
                    if not isinstance(pull, dict) or (
                        not isinstance(pull.get("number"), int)
                        or isinstance(pull["number"], bool) or pull["number"] < 1
                        or pull.get("state") != "open"
                        or not isinstance(pull.get("title"), str)
                        or "body" not in pull or (
                            pull["body"] is not None and not isinstance(pull["body"], str)
                        )
                        or pull.get("url") != (
                            f"https://api.github.com{github.api}/pulls/{pull['number']}"
                        )
                    ):
                        result["incompletes"].append({
                            "read": "Open pull requests", "reason": "unrecognized evidence shape",
                            "source": github.web + "/pulls",
                        })
                        continue
                    closing = [
                        number for number in extract_close_refs(
                            pull["title"] + "\n" + (pull["body"] or ""),
                        ) if number in open_issues
                    ]
                    if closing:
                        refs = ", ".join(f"#{number}" for number in closing)
                        result["holds"].append({
                            "text": f"Open pull request #{pull['number']}: {pull['title']} "
                                    f"closes open line issues {refs}.",
                            "source": f"{github.web}/pull/{pull['number']}",
                            "observed": f"state=open; closing references={refs}",
                            "context": "",
                        })
            except Unreadable as exc:
                result["incompletes"].append(exc.item)
        links: dict[int, list[dict[str, str]]] = {}
        content_complete = False
        if not result["sha"]:
            result["incompletes"].append({
                "read": "Baseline tag", "reason": "pinned SHA unavailable",
                "source": github.web + "/tags",
            })
        else:
            try:
                tags = []
                async for tag in github.work_records("tags"):
                    if not isinstance(tag, dict) or not isinstance(tag.get("name"), str):
                        raise Unreadable(
                            "Baseline tag", "unrecognized evidence shape", github.web + "/tags",
                        )
                    if re.fullmatch(MILESTONE_PATTERN, tag["name"]):
                        if not isinstance(tag.get("commit"), dict) or (
                            not isinstance(tag["commit"].get("sha"), str)
                            or re.fullmatch(SHA_PATTERN, tag["commit"]["sha"]) is None
                        ):
                            raise Unreadable(
                                "Baseline tag", "unrecognized evidence shape",
                                github.web + "/tags",
                            )
                        tags.append(tag)
                tags.sort(
                    key=lambda tag: tuple(map(int, tag["name"][1:].split("."))),
                    reverse=True,
                )
                for tag in tags:
                    reachable, _ = await github.comparison(
                        tag["commit"]["sha"], result["sha"], read="Baseline tag",
                        complete=False,
                    )
                    if reachable:
                        result["baseline"] = tag["name"]
                        result["sources"]["baseline"] = (
                            f"{github.web}/commit/{tag['commit']['sha']}"
                        )
                        _, commits = await github.comparison(
                            tag["commit"]["sha"], result["sha"], read="Line commit range",
                        )
                        for commit in commits:
                            message = commit["commit"]["message"]
                            for refs, kind in (
                                (extract_close_refs(message), "closing keyword"),
                                (extract_checkpoint_refs(message), "Checkpoint trailer"),
                            ):
                                for number in refs:
                                    links.setdefault(number, []).append({
                                        "text": f"Commit {commit['sha']} carries a {kind}.",
                                        "source": f"{github.web}/commit/{commit['sha']}",
                                    })
                        content_complete = True
                        break
                else:
                    raise Unreadable(
                        "Baseline tag", "no reachable stable tag", github.web + "/tags",
                    )
            except Unreadable as exc:
                result["incompletes"].append(exc.item)
            try:
                async for pull in github.work_records("closed_pulls"):
                    if not isinstance(pull, dict) or (
                        not isinstance(pull.get("number"), int)
                        or isinstance(pull["number"], bool) or pull["number"] < 1
                        or pull.get("state") != "closed"
                        or not isinstance(pull.get("title"), str)
                        or "body" not in pull or (
                            pull["body"] is not None and not isinstance(pull["body"], str)
                        )
                        or pull.get("url") != (
                            f"https://api.github.com{github.api}/pulls/{pull['number']}"
                        )
                        or "merged_at" not in pull
                        or (pull["merged_at"] is not None and (
                            not isinstance(pull["merged_at"], str) or not pull["merged_at"]
                            or not isinstance(pull.get("merge_commit_sha"), str)
                            or re.fullmatch(SHA_PATTERN, pull["merge_commit_sha"]) is None
                        ))
                    ):
                        raise Unreadable(
                            "Closed pull requests", "unrecognized evidence shape",
                            github.web + "/pulls",
                        )
                    closing = set(extract_close_refs(
                        pull["title"] + "\n" + (pull["body"] or ""),
                    )) & {issue["number"] for issue in closed_issues
                          if issue["state_reason"] == "completed"}
                    if pull["merged_at"] is None or not closing:
                        continue
                    reachable, _ = await github.comparison(
                        pull["merge_commit_sha"], result["sha"],
                        read=f"Merge commit of pull request #{pull['number']}",
                        complete=False,
                    )
                    if reachable:
                        for number in sorted(closing):
                            links.setdefault(number, []).extend([
                                {"text": f"Merged closing pull request #{pull['number']}.",
                                 "source": f"{github.web}/pull/{pull['number']}"},
                                {"text": "Merge commit is reachable from the pinned SHA.",
                                 "source": f"{github.web}/commit/{pull['merge_commit_sha']}"},
                            ])
            except Unreadable as exc:
                result["incompletes"].append(exc.item)
                content_complete = False
        for issue in sorted(closed_issues, key=lambda issue: issue["number"]):
            number = issue["number"]
            source = f"{github.web}/issues/{number}"
            if issue["state_reason"] == "not_planned":
                cls, via = "excluded", [{"text": "Closed as not planned.", "source": source}]
            elif number in links:
                cls, via = "merged", links[number]
            elif content_complete:
                cls, via = "closed_not_linked", [{
                    "text": "Closed as completed; no closing link at the pinned SHA.",
                    "source": source,
                }]
            else:
                continue
            result["content"].append({
                "n": number, "title": issue["title"], "cls": cls, "via": via,
            })
        result["verdict"] = (
            "incomplete" if result["incompletes"] else
            "hold_advised" if result["holds"] else "reviewable"
        )
        return result

    async def check_freshness(arguments):
        """Read head and title live; no expiry, cache or tracker-drift inference."""
        repository, milestone = validate_inputs(arguments, "check_freshness")
        sha = arguments["sha"]
        if not isinstance(sha, str) or re.fullmatch(sha_pattern, sha) is None:
            raise ValueError("sha must be 40 hexadecimal characters")
        sha = sha.lower()
        github = GitHubReads(repository, transport)
        result = {
            "repo": repository, "milestone": milestone, "sha": sha,
            "head": None, "asOf": now(), "status": "stale",
            "reasons": [], "resultFormat": 1,
        }
        try:
            result["head"] = await github.pin_head()
            if result["head"] != sha:
                result["reasons"].append({
                    "read": "Trunk head",
                    "reason": f"trunk head is now {result['head']}",
                    "source": f"{github.web}/commit/{result['head']}",
                })
        except Unreadable as exc:
            result["reasons"].append(exc.item)
        try:
            catalog = await github.milestones()
            matching = [m for m in catalog if m["title"] == milestone]
            for item in matching:
                if item["state"] == "closed":
                    result["reasons"].append({
                        "read": "Milestone catalog",
                        "reason": f"milestone {milestone} is closed",
                        "source": f"{github.web}/milestone/{item['number']}",
                    })
            if not any(m["state"] == "open" for m in matching):
                result["reasons"].append({
                    "read": "Milestone catalog",
                    "reason": f"no open milestone has title {milestone}",
                    "source": f"{github.web}/milestones",
                })
        except Unreadable as exc:
            result["reasons"].append(exc.item)
        if not result["reasons"]:
            result["status"] = "fresh"
        return result

    async def call_tool(context, params):
        try:
            if params.name == "assess":
                result = await assess(params.arguments)
                text = render_packet(result)
            elif params.name == "check_freshness":
                result = await check_freshness(params.arguments)
                text = f"Assessment freshness: {result['status']}.\n"
                text += "".join(
                    f"- {_inert(item['read'])}: {_inert(item['reason'])} "
                    f"— [source]({item['source']})\n"
                    for item in result["reasons"]
                )
            else:
                raise ValueError("unknown tool")
        except ValueError as exc:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=html.escape(str(exc)))],
                is_error=True,
            )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)],
            structured_content=result,
        )

    return Server(
        "git-loopy-assessment", version=service_version,
        on_list_tools=list_tools, on_call_tool=call_tool,
        get_tool_input_schema=schemas.get,
    )


def main() -> int:
    """Never start without the dedicated App transport; never borrow gh auth."""
    try:
        server = create_server()
    except (AssessmentConfigError, ImportError) as exc:
        print(f"git-loopy-assessment: {exc}", file=sys.stderr)
        return 1

    async def serve():
        from mcp.server.stdio import stdio_server

        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(serve())
    return 0
