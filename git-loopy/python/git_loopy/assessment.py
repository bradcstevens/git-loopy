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
from .assessment_github import GitHubReads, Transport, Unreadable
from .release_version import (
    parse_release_line, parse_release_version, promote_closed_milestone,
)


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
        "- Not yet assessed by this service version; see Could not read.",
        "", "## Line content",
        "- Not yet assessed by this service version; see Could not read.",
        "", "## Context (does not change the verdict)",
    ])
    lines.extend(f"- {_inert(c)}" for c in r["context"])
    if not r["context"]:
        lines.append("- None.")
    target = r["releaseTarget"] or (
        "no target (VERSION is stable)"
        if r["version"] and "-" not in r["version"] and "+" not in r["version"]
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
        ("Release target", target), ("Baseline tag", "not yet assessed"),
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
    """Load a policy snapshot and construct the single-tool tracer service."""
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
    schema = {
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

    async def list_tools(context, params):
        return types.ListToolsResult(tools=[types.Tool(
            name="assess", description="Read one Release line; advice only.",
            input_schema=schema,
            annotations=types.ToolAnnotations(
                read_only_hint=True, destructive_hint=False, open_world_hint=True,
            ),
        )])

    async def assess(arguments):
        if not isinstance(arguments, dict) or (
            set(arguments) - {"repository", "milestone"}
        ):
            raise ValueError("assess accepts only repository and optional milestone")
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
        policy = policies[repository]
        github = GitHubReads(repository, transport)
        result = {
            "repo": repository, "milestone": milestone, "milestoneState": None,
            "sha": None, "asOf": now(), "version": None, "releaseTarget": None,
            "baseline": None, "policy": policy.revision, "service": service_version,
            "identity": policy.identity, "resultFormat": 1,
            "countedWorkflows": list(policy.workflows),
            "checks": [], "holds": [], "incompletes": [], "content": [], "context": [],
            "verdict": "incomplete", "sources": {},
        }
        line = None
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
                    result["context"].append(
                        f"Prerelease stage is {line.stage}.{line.counter} — context only; "
                        "Promotion promotes from any stage."
                    )
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
        for read, suffix in [
            ("Counted checks", f"/commit/{result['sha'] or 'unresolved'}/checks"),
            ("Open and unabsorbed work", "/issues"),
            ("Baseline and line content", "/tags"),
        ]:
            result["incompletes"].append({
                "read": read, "reason": "not yet assessed by this service version",
                "source": github.web + suffix,
            })
        return result

    async def call_tool(context, params):
        try:
            if params.name != "assess":
                raise ValueError("unknown tool")
            result = await assess(params.arguments)
        except ValueError as exc:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=html.escape(str(exc)))],
                is_error=True,
            )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=render_packet(result))],
            structured_content=result,
        )

    return Server(
        "git-loopy-assessment", version=service_version,
        on_list_tools=list_tools, on_call_tool=call_tool,
        get_tool_input_schema=lambda name: schema if name == "assess" else None,
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
