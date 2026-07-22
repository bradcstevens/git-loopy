"""Public Python command adapter for the Continuation scenario fixture."""

from __future__ import annotations

import base64
import copy
import io
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from git_loopy import cli, continuation
from git_loopy.gh import (
    ContinuationArtifact,
    ContinuationCarrier,
    ContinuationComment,
)


CONFORMANCE_DIR = Path(__file__).parents[2] / "conformance"
SCRIPTED_GITHUB = Path(__file__).with_name("scripted_github.py")
FIXTURE = json.loads(
    (CONFORMANCE_DIR / "continuation-scenarios.json").read_text(encoding="utf-8")
)


def _install_scripted_github(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    script: list[dict[str, Any]],
) -> tuple[Path, Path, Path]:
    script_path = tmp_path / "github-script.json"
    script_path.write_text(
        json.dumps(script, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    state_path = tmp_path / "github-script-state"
    log_path = tmp_path / "github-calls"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        "#!/bin/sh\nexec "
        f"{shlex.quote(sys.executable)} {shlex.quote(str(SCRIPTED_GITHUB))} \"$@\"\n",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    monkeypatch.setenv("GIT_LOOPY_SCRIPTED_GITHUB_LOG", str(log_path))
    monkeypatch.setenv("GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT", str(script_path))
    monkeypatch.setenv("GIT_LOOPY_SCRIPTED_GITHUB_STATE", str(state_path))
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    return state_path, log_path, fake_gh


def _consumed_steps(state_path: Path) -> int:
    return int(state_path.read_text(encoding="utf-8")) if state_path.exists() else 0


def test_python_scripted_github_transport(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    probe = FIXTURE["github_transport_probe"]
    state_path, log_path, fake_gh = _install_scripted_github(
        monkeypatch,
        tmp_path,
        probe["github_script"],
    )

    for invocation in probe["invocations"]:
        stdin = invocation.get("stdin")
        if "stdin_json" in invocation:
            stdin = json.dumps(invocation["stdin_json"], separators=(",", ":"))
        completed = subprocess.run(
            [str(fake_gh), *invocation["arguments"]],
            input=stdin or "",
            text=True,
            capture_output=True,
            check=False,
            env=os.environ.copy(),
        )
        expected = invocation["expected"]
        assert completed.returncode == expected["exit_code"]
        if "stdout_json" in expected:
            assert json.loads(completed.stdout) == expected["stdout_json"]
        else:
            assert completed.stdout == expected["stdout"]
        assert expected["stderr_contains"].lower() in completed.stderr.lower()

    assert _consumed_steps(state_path) == len(probe["github_script"])
    assert log_path.read_text(encoding="utf-8").splitlines() == probe[
        "expected_github_calls"
    ]


@pytest.mark.parametrize(
    "scenario",
    [
        scenario
        for scenario in FIXTURE["scenarios"]
        if "python" in scenario.get("distributions", ["python"])
    ],
    ids=lambda scenario: scenario["id"],
)
def test_python_native_continuation_scenario(
    scenario: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    arguments = list(scenario["arguments"])
    request = scenario["request"]
    stdin = ""
    if request is not None:
        content = request.get("raw")
        if content is None and "raw_segments" in request:
            content = "".join(
                segment["text"] * int(segment.get("repeat", 1))
                for segment in request["raw_segments"]
            )
        if content is None and "json" in request:
            content = json.dumps(request["json"], separators=(",", ":"))
        if request["source"] == "file":
            input_path = tmp_path / "request.json"
            if "base64" in request:
                input_path.write_bytes(base64.b64decode(request["base64"]))
            else:
                input_path.write_text(content, encoding="utf-8")
            arguments = [
                str(input_path) if value == "$INPUT_FILE" else value
                for value in arguments
            ]
        else:
            stdin = content

    state_path, github_log, _fake_gh = _install_scripted_github(
        monkeypatch,
        tmp_path,
        scenario["github_script"],
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    try:
        exit_code = cli.main(arguments)
    except SystemExit as exc:
        if exc.code is None:
            exit_code = 0
        elif isinstance(exc.code, int):
            exit_code = exc.code
        else:
            exit_code = 1

    captured = capsys.readouterr()
    expected = scenario["expected"]
    assert exit_code == expected["exit_code"], captured.err
    if arguments[:2] == ["continuation", "publish"] and exit_code != 2:
        assert "stdout_exact" in expected
        assert "stderr_exact" in expected
    if "stdout_exact" in expected:
        assert captured.out == expected["stdout_exact"]
    elif expected["stdout"] is None:
        assert captured.out == ""
    else:
        assert json.loads(captured.out) == expected["stdout"]
        assert len(captured.out.splitlines()) == 1
    if "stderr_exact" in expected:
        assert captured.err == expected["stderr_exact"]
    elif expected["stderr_contains"] is None:
        assert captured.err == ""
    else:
        assert expected["stderr_contains"].lower() in captured.err.lower()
    github_calls = (
        github_log.read_text(encoding="utf-8").splitlines()
        if github_log.exists()
        else []
    )
    assert github_calls == expected["github_calls"]
    assert _consumed_steps(state_path) == len(scenario["github_script"])


@pytest.mark.parametrize(
    "workflow",
    [
        workflow
        for workflow in FIXTURE["workflows"]
        if "python" in workflow["distributions"]
    ],
    ids=lambda workflow: workflow["id"],
)
def test_python_native_continuation_workflow(
    workflow: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_path, github_log, _fake_gh = _install_scripted_github(
        monkeypatch,
        tmp_path,
        workflow["github_script"],
    )

    for command in workflow["commands"]:
        request = command["request"]
        stdin = json.dumps(request["json"], separators=(",", ":"))
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        try:
            exit_code = cli.main(list(command["arguments"]))
        except SystemExit as exc:
            exit_code = exc.code if isinstance(exc.code, int) else 1

        captured = capsys.readouterr()
        expected = command["expected"]
        assert exit_code == expected["exit_code"], captured.err
        if command["arguments"][:2] == ["continuation", "publish"]:
            assert "stdout_exact" in expected
            assert "stderr_exact" in expected
        if "stdout_exact" in expected:
            assert captured.out == expected["stdout_exact"]
        else:
            assert json.loads(captured.out) == expected["stdout"]
            assert len(captured.out.splitlines()) == 1
        if "stderr_exact" in expected:
            assert captured.err == expected["stderr_exact"]
        elif expected["stderr_contains"] is None:
            assert captured.err == ""
        else:
            assert expected["stderr_contains"].lower() in captured.err.lower()

    assert _consumed_steps(state_path) == len(workflow["github_script"])
    assert github_log.read_text(encoding="utf-8").splitlines() == workflow[
        "expected_github_calls"
    ]


class _RecordingGitHub:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.body = ""

    def read_issue_comment(
        self,
        repository: str,
        comment_id: int,
    ) -> ContinuationComment:
        self.calls.append(f"read-comment:{comment_id}")
        if comment_id == 7001:
            return ContinuationComment(
                id=7001,
                url="https://github.com/octo/example/issues/237#issuecomment-7001",
                body="Durable transition evidence.",
                author="planner",
            )
        return ContinuationComment(
            id=comment_id,
            url=f"https://github.com/octo/example/issues/237#issuecomment-{comment_id}",
            body=self.body,
            author="planner",
        )

    def ensure_issue_label(
        self,
        repository: str,
        number: int,
        label: str,
    ) -> None:
        self.calls.append(f"label:{number}:{label}")

    def append_issue_comment(
        self,
        repository: str,
        number: int,
        body: str,
    ) -> ContinuationComment:
        self.calls.append(f"append:{number}")
        self.body = body
        return ContinuationComment(
            id=9001,
            url="https://github.com/octo/example/issues/237#issuecomment-9001",
            body="",
            author="planner",
        )

    def list_continuation_carriers(
        self,
        repository: str,
        label: str,
    ) -> list[ContinuationCarrier]:
        self.calls.append("list-carriers")
        if not self.body:
            return []
        return [
            ContinuationCarrier(
                number=237,
                state="OPEN",
                url="https://github.com/octo/example/issues/237",
                comments=(
                    ContinuationComment(
                        id=9001,
                        url=(
                            "https://github.com/octo/example/issues/237"
                            "#issuecomment-9001"
                        ),
                        body=self.body,
                        author="planner",
                    ),
                ),
            )
        ]

    def read_issue(
        self,
        repository: str,
        number: int,
    ) -> ContinuationArtifact:
        self.calls.append(f"read-issue:{number}")
        return ContinuationArtifact(
            number=number,
            state="OPEN",
            url=f"https://github.com/octo/example/issues/{number}",
        )


def _issue(number: int) -> dict[str, Any]:
    return {
        "kind": "issue",
        "repository": "octo/example",
        "number": number,
    }


def _action(
    *,
    kind: str = "Publish spec",
    classification: str = "AFK-safe",
) -> dict[str, Any]:
    return {
        "key": "action",
        "summary": "Publish the specification",
        "kind": kind,
        "occurrence": "v1",
        "instruction": {"mode": "skill", "value": "/to-spec 237"},
        "target": _issue(239),
        "basis": [_issue(237)],
        "prerequisites": [],
        "interaction": {
            "classification": classification,
            "evidence": {
                "kind": "transition-owner-attestation",
                "owner": "wayfinder",
            },
        },
        "completion_condition": {
            "kind": "issue-closed",
            "target": _issue(239),
        },
    }


def _request(
    *,
    publication: str = "shared",
    disposition: str = "continue",
) -> dict[str, Any]:
    completion: dict[str, Any] = {
        "continuation_contract_version": "1.0",
        "record_format": 1,
        "publication": publication,
        "disposition": disposition,
        "workstream": {
            "destination": {
                "kind": "issue-closed",
                "target": _issue(237),
            }
        },
        "transition": {"owner": "wayfinder", "evidence": []},
        "producer": {"login": "planner", "role": "planning"},
    }
    if publication == "shared":
        completion["workstream"]["anchor"] = _issue(237)
        completion["transition"]["evidence"] = [
            {
                "kind": "issue-comment",
                "repository": "octo/example",
                "issue": 237,
                "comment_id": 7001,
            }
        ]
        completion["carrier"] = _issue(237)
    if disposition == "continue":
        completion["actions"] = [_action()]
    elif disposition == "terminal":
        completion["outcome"] = {
            "kind": "complete",
            "destination_satisfied": True,
            "effective_at": "2026-07-22T18:00:00Z",
            "evidence": [_issue(237)],
            "summary": "The Workstream Destination is satisfied.",
        }
    else:
        completion["no_guidance"] = {
            "reason": (
                "no-successor-created"
                if publication == "shared"
                else "ephemeral-only"
            ),
            "summary": "No shared successor can be published.",
            "references": [_issue(237)],
        }
    return {
        "repository": "octo/example",
        "trusted_producers": ["planner"] if publication == "shared" else [],
        "completion": completion,
    }


def _publish_output(
    request: dict[str, Any],
    github: _RecordingGitHub,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, str, str]:
    monkeypatch.setattr(continuation, "_make_github_client", lambda: github)
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps(request, ensure_ascii=False, separators=(",", ":"))),
    )
    exit_code = cli.main(["continuation", "publish"])
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


def _publish_result(
    request: dict[str, Any],
    github: _RecordingGitHub,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, dict[str, Any], str]:
    exit_code, stdout, stderr = _publish_output(
        request,
        github,
        monkeypatch,
        capsys,
    )
    return exit_code, json.loads(stdout), stderr


@pytest.mark.parametrize("disposition", ["terminal", "no-guidance"])
def test_python_publish_commits_each_shared_completion_disposition(
    disposition: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = _RecordingGitHub()

    exit_code, result, stderr = _publish_result(
        _request(disposition=disposition),
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    assert result["receipt"]["status"] == "committed"
    assert result["receipt"]["semantic_fingerprints"] == {}
    assert stderr == ""
    assert github.calls == [
        "read-comment:7001",
        "label:237:git-loopy-continuation",
        "append:237",
        "read-comment:9001",
    ]


def test_python_publish_keeps_ephemeral_advice_visibly_unpublished(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = _RecordingGitHub()

    exit_code, result, stderr = _publish_result(
        _request(publication="ephemeral", disposition="no-guidance"),
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    assert result == {
        "ok": True,
        "operation": "publish",
        "receipt": {
            "status": "unpublished",
            "publication": "ephemeral",
            "disposition": "no-guidance",
            "semantic_fingerprints": {},
        },
    }
    assert stderr == ""
    assert github.calls == []


def _json_pointer_tokens(path: str) -> list[str]:
    assert path.startswith("/")
    return [
        token.replace("~1", "/").replace("~0", "~")
        for token in path[1:].split("/")
    ]


def _apply_fixture_patch(
    value: dict[str, Any],
    operations: list[dict[str, Any]],
) -> None:
    for operation in operations:
        tokens = _json_pointer_tokens(operation["path"])
        parent: Any = value
        for token in tokens[:-1]:
            parent = parent[int(token)] if isinstance(parent, list) else parent[token]
        token = tokens[-1]
        op = operation["op"]
        if isinstance(parent, list):
            index = int(token)
            if op == "remove":
                parent.pop(index)
            elif op == "add":
                parent.insert(index, copy.deepcopy(operation["value"]))
            else:
                assert op == "replace"
                parent[index] = copy.deepcopy(operation["value"])
        elif op == "remove":
            del parent[token]
        else:
            assert op in {"add", "replace"}
            parent[token] = copy.deepcopy(operation["value"])


@pytest.mark.parametrize(
    "case",
    FIXTURE["completion_records"]["semantic_rejections"],
    ids=lambda case: case["id"],
)
def test_python_publish_rejects_invalid_completion_atomically(
    case: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    records = FIXTURE["completion_records"]
    request = copy.deepcopy(records["publish_request_templates"][case["template"]])
    _apply_fixture_patch(request, case["patch"])
    github = _RecordingGitHub()

    exit_code, stdout, stderr = _publish_output(
        request,
        github,
        monkeypatch,
        capsys,
    )

    expected = case["expected"]
    assert exit_code == expected["exit_code"]
    assert stdout == expected["stdout_exact"]
    assert stderr == expected["stderr_exact"]
    assert github.calls == expected["github_calls"]


def test_python_reconcile_reports_unsupported_valid_target_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = _request()
    action = request["completion"]["actions"][0]
    commit = {
        "kind": "commit",
        "repository": "octo/example",
        "sha": "a" * 40,
    }
    action["target"] = commit
    action["completion_condition"] = {
        "kind": "commit-exists",
        "target": commit,
    }
    github = _RecordingGitHub()
    publish_exit, _publish, _stderr = _publish_result(
        request,
        github,
        monkeypatch,
        capsys,
    )
    assert publish_exit == 0

    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            '{"repository":"octo/example","trusted_producers":["planner"]}'
        ),
    )
    reconcile_exit = cli.main(["continuation", "reconcile"])
    captured = capsys.readouterr()
    result = json.loads(captured.out)

    assert reconcile_exit == 0
    assert result["result"]["status"] == "waiting"
    assert result["result"]["actions"] == []
    assert len(result["result"]["diagnostics"]) == 1
    diagnostic = result["result"]["diagnostics"][0]
    assert diagnostic["code"] == "unsupported_reconciliation_semantics"
    assert diagnostic["action_key"] == "action"
    assert len(diagnostic["revision_id"]) == 64
    assert captured.err == ""


@pytest.mark.parametrize(
    "outcome_kind",
    FIXTURE["completion_records"]["outcome_kinds"],
)
def test_python_publish_accepts_every_pinned_terminal_outcome(
    outcome_kind: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = _request(disposition="terminal")
    outcome = request["completion"]["outcome"]
    outcome["kind"] = outcome_kind
    outcome["destination_satisfied"] = outcome_kind == "complete"
    if outcome_kind == "superseded":
        outcome["successor"] = _issue(240)

    exit_code, result, stderr = _publish_result(
        request,
        _RecordingGitHub(),
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    assert result["receipt"]["status"] == "committed"
    assert stderr == ""


@pytest.mark.parametrize(
    "case",
    FIXTURE["completion_records"]["canonical_json_rejections"],
    ids=lambda case: case["id"],
)
def test_python_publish_enforces_portable_canonical_json_profile(
    case: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile = FIXTURE["completion_records"]["canonical_json"]
    request = _request(publication="ephemeral")
    probe = case["probe"]
    if probe == "float":
        request["extra"] = 1.5
    elif probe == "integer":
        request["extra"] = profile["integer_max"] + 1
    elif probe == "nfc":
        request["completion"]["producer"]["login"] = "e\u0301"
    elif probe == "depth":
        nested: dict[str, Any] = {}
        request["extra"] = nested
        for _ in range(profile["maximum_depth"] + 1):
            child: dict[str, Any] = {}
            nested["child"] = child
            nested = child
    elif probe == "array":
        request["extra"] = [0] * (profile["maximum_array_length"] + 1)
    elif probe == "string":
        request["extra"] = "x" * (profile["maximum_string_bytes"] + 1)
    else:
        entry_count = (
            profile["maximum_record_bytes"] // profile["maximum_string_bytes"]
        ) + 1
        request["completion"]["advisory_extensions"] = {
            f"note_{index}": "x" * profile["maximum_string_bytes"]
            for index in range(entry_count)
        }
    github = _RecordingGitHub()

    exit_code, stdout, stderr = _publish_output(
        request,
        github,
        monkeypatch,
        capsys,
    )

    expected = case["expected"]
    assert exit_code == expected["exit_code"]
    assert stdout == expected["stdout_exact"]
    assert stderr == expected["stderr_exact"]
    assert github.calls == expected["github_calls"]


@pytest.mark.parametrize(
    ("kind", "schema"),
    FIXTURE["completion_records"]["action_kind_schemas"].items(),
)
def test_python_publish_accepts_every_pinned_action_kind(
    kind: str,
    schema: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = _request(publication="ephemeral")
    action = request["completion"]["actions"][0]
    action["kind"] = kind
    action["interaction"] = copy.deepcopy(
        FIXTURE["completion_records"]["interaction_examples"][
            schema["example_interaction"]
        ]
    )
    github = _RecordingGitHub()

    exit_code, result, stderr = _publish_result(
        request,
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    assert result["receipt"]["status"] == "unpublished"
    assert set(result["receipt"]["semantic_fingerprints"]) == {"action"}
    assert stderr == ""
    assert github.calls == []


@pytest.mark.parametrize(
    ("kind", "schema"),
    FIXTURE["completion_records"]["condition_schemas"].items(),
)
def test_python_publish_accepts_every_pinned_condition_kind(
    kind: str,
    schema: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = _request(publication="ephemeral")
    action = request["completion"]["actions"][0]
    for supporting_key in schema["supporting_action_keys"]:
        predecessor = copy.deepcopy(action)
        predecessor["key"] = supporting_key
        request["completion"]["actions"].insert(0, predecessor)
    action["prerequisites"] = [copy.deepcopy(schema["example"])]
    github = _RecordingGitHub()

    exit_code, result, stderr = _publish_result(
        request,
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    assert result["receipt"]["status"] == "unpublished"
    assert stderr == ""
    assert github.calls == []


def test_python_publish_fingerprints_only_action_semantics(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original = _request(publication="ephemeral")
    presentation = copy.deepcopy(original)
    presentation_action = presentation["completion"]["actions"][0]
    presentation_action["summary"] = "Reworded for a human"
    presentation_action["basis"] = [_issue(236), _issue(237)]
    presentation_action["context_references"] = [_issue(235)]
    presentation_action["effects"] = []
    presentation_action["requirements"] = []
    presentation_action["triggers"] = []
    presentation_action["instruction"]["advisory_extensions"] = {
        "display_hint": "compact"
    }
    semantic_change = copy.deepcopy(original)
    changed_action = semantic_change["completion"]["actions"][0]
    changed_action["effects"] = [
        {"kind": "tracker-write", "scope": "octo/example#239"}
    ]
    changed_action["requirements"] = [{"kind": "skill", "name": "to-spec"}]
    changed_action["triggers"] = [
        {
            "kind": "human-decision",
            "condition": {"kind": "issue-open", "target": _issue(240)},
        }
    ]

    fingerprints = []
    for request in (original, presentation, semantic_change):
        exit_code, result, _stderr = _publish_result(
            request,
            _RecordingGitHub(),
            monkeypatch,
            capsys,
        )
        assert exit_code == 0
        fingerprints.append(
            result["receipt"]["semantic_fingerprints"]["action"]
        )

    assert fingerprints[0] == fingerprints[1]
    assert fingerprints[2] != fingerprints[0]
