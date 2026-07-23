"""Public Python command adapter for the Continuation scenario fixture."""

from __future__ import annotations

import base64
import copy
import hashlib
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
    ContinuationBranch,
    ContinuationCarrier,
    ContinuationComment,
    ContinuationCommit,
    ContinuationLabeledArtifact,
    ContinuationReview,
    ContinuationSubIssues,
    GhError,
    SubprocessContinuationGitHubClient,
)


CONFORMANCE_DIR = Path(__file__).parents[2] / "conformance"
SCRIPTED_GITHUB = Path(__file__).with_name("scripted_github.py")
FIXTURE = json.loads(
    (CONFORMANCE_DIR / "continuation-scenarios.json").read_text(encoding="utf-8")
)


def test_python_revision_protocol_vocabulary_matches_shared_fixture() -> None:
    protocol = FIXTURE["revision_protocol"]

    assert continuation.CAPABILITY_MANIFEST["operations"]["repair-index"] is True
    assert protocol["observation_token"] == "sha256"
    assert protocol["human_write_permissions"] == ["ADMIN", "MAINTAIN", "WRITE"]
    assert protocol["receipt_statuses"] == [
        "committed",
        "conflict",
        "idempotent",
        "unpublished",
    ]
    assert protocol["reattestation_modes"] == ["copy", "replace", "retire"]
    assert protocol["index_label"] == "git-loopy-continuation"


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
        f'{shlex.quote(sys.executable)} {shlex.quote(str(SCRIPTED_GITHUB))} "$@"\n',
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


def _materialize_raw_segments(segments: list[dict[str, Any]]) -> str:
    return "".join(
        segment["text"] * int(segment.get("repeat", 1)) for segment in segments
    )


def _assert_native_output(
    expected: dict[str, Any],
    *,
    stdout: str,
    stderr: str,
    exact: bool = False,
) -> None:
    if exact:
        assert "stdout_exact" in expected
        assert "stderr_exact" in expected
    if "stdout_exact" in expected:
        assert stdout == expected["stdout_exact"]
    elif expected["stdout"] is None:
        assert stdout == ""
    else:
        assert json.loads(stdout) == expected["stdout"]
        assert len(stdout.splitlines()) == 1
    if "stderr_exact" in expected:
        assert stderr == expected["stderr_exact"]
    elif expected["stderr_contains"] is None:
        assert stderr == ""
    else:
        assert expected["stderr_contains"].lower() in stderr.lower()


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
    assert (
        log_path.read_text(encoding="utf-8").splitlines()
        == probe["expected_github_calls"]
    )


def test_python_all_carrier_scan_hydrates_comment_authority_and_edit_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    list_command = "api repos/octo/example/issues?state=all&per_page=100&page=1"
    comments_command = (
        "api repos/octo/example/issues/237/comments?per_page=100&page=1"
    )
    state_path, log_path, _fake_gh = _install_scripted_github(
        monkeypatch,
        tmp_path,
        [
            {
                "command": list_command,
                "exit_code": 0,
                "stdout_json": [
                    {
                        "number": 237,
                        "state": "open",
                        "html_url": "https://github.com/octo/example/issues/237",
                        "labels": [{"name": "git-loopy-continuation"}],
                        "comments": 2,
                    }
                ],
            },
            {
                "command": comments_command,
                "exit_code": 0,
                "stdout_json": [
                    {
                        "id": 9000,
                        "html_url": (
                            "https://github.com/octo/example/issues/237"
                            "#issuecomment-9000"
                        ),
                        "body": "Ordinary issue discussion.",
                        "user": {"login": "maintainer", "type": "User"},
                        "created_at": "2026-07-22T19:59:00Z",
                    },
                    {
                        "id": 9001,
                        "html_url": (
                            "https://github.com/octo/example/issues/237"
                            "#issuecomment-9001"
                        ),
                        "body": (
                            "<!-- git-loopy-continuation:1 -->\nauthoritative body"
                        ),
                        "user": {"login": "planner-bot", "type": "Bot"},
                        "created_at": "2026-07-22T20:00:00Z",
                        "updated_at": "2026-07-22T20:01:00Z",
                    },
                ],
            },
        ],
    )

    carriers = SubprocessContinuationGitHubClient().list_all_continuation_carriers(
        "octo/example"
    )

    assert carriers[0].state == "OPEN"
    assert carriers[0].labels == ("git-loopy-continuation",)
    assert carriers[0].comments == (
        ContinuationComment(
            id=9000,
            url="https://github.com/octo/example/issues/237#issuecomment-9000",
            body="Ordinary issue discussion.",
            author="maintainer",
            created_at="2026-07-22T19:59:00Z",
        ),
        ContinuationComment(
            id=9001,
            url="https://github.com/octo/example/issues/237#issuecomment-9001",
            body=("<!-- git-loopy-continuation:1 -->\nauthoritative body"),
            author="planner-bot",
            author_type="Bot",
            created_at="2026-07-22T20:00:00Z",
            updated_at="2026-07-22T20:01:00Z",
        ),
    )
    assert _consumed_steps(state_path) == 2
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        list_command,
        comments_command,
    ]


def test_python_all_carrier_scan_traverses_every_page_and_skips_pull_requests(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Discovery never stops at one page and never mistakes a PR for a carrier."""
    page_size = SubprocessContinuationGitHubClient._CARRIER_PAGE_SIZE
    first_page = [
        {
            "number": index,
            "state": "open",
            "html_url": f"https://github.com/octo/example/issues/{index}",
            "labels": [],
            "comments": 0,
        }
        for index in range(1, page_size + 1)
    ] + [
        {
            "number": page_size + 1,
            "state": "closed",
            "html_url": f"https://github.com/octo/example/pull/{page_size + 1}",
            "labels": [],
            "comments": 0,
            "pull_request": {"url": "https://api.github.com/..."},
        }
    ]
    second_page = [
        {
            "number": page_size + 2,
            "state": "open",
            "html_url": f"https://github.com/octo/example/issues/{page_size + 2}",
            "labels": [],
            "comments": 0,
        }
    ]
    _state_path, log_path, _fake_gh = _install_scripted_github(
        monkeypatch,
        tmp_path,
        [
            {
                "command": (
                    f"api repos/octo/example/issues?state=all&per_page={page_size}"
                    "&page=1"
                ),
                "exit_code": 0,
                "stdout_json": first_page,
            },
            {
                "command": (
                    f"api repos/octo/example/issues?state=all&per_page={page_size}"
                    "&page=2"
                ),
                "exit_code": 0,
                "stdout_json": second_page,
            },
        ],
    )

    carriers = SubprocessContinuationGitHubClient().list_all_continuation_carriers(
        "octo/example"
    )

    assert [carrier.number for carrier in carriers] == list(
        range(1, page_size + 1)
    ) + [page_size + 2]
    assert log_path.read_text(encoding="utf-8").splitlines() == [
        f"api repos/octo/example/issues?state=all&per_page={page_size}&page=1",
        f"api repos/octo/example/issues?state=all&per_page={page_size}&page=2",
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
            content = _materialize_raw_segments(request["raw_segments"])
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
    _assert_native_output(
        expected,
        stdout=captured.out,
        stderr=captured.err,
        exact=arguments[:2] == ["continuation", "publish"],
    )
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
        _assert_native_output(
            expected,
            stdout=captured.out,
            stderr=captured.err,
            exact=command["arguments"][:2] == ["continuation", "publish"],
        )

    assert _consumed_steps(state_path) == len(workflow["github_script"])
    assert (
        github_log.read_text(encoding="utf-8").splitlines()
        == workflow["expected_github_calls"]
    )


class _RecordingGitHub:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.body = ""
        self.labels: set[str] = set()
        self.comment_author = "planner"
        self.comment_author_type = "User"
        self.permission = "WRITE"
        self.comments: list[ContinuationComment] = []
        self.next_comment_id = 9001
        self.actor_login = "planner"
        self.actor_type = "User"
        self.fail_append = False
        self.issues: dict[int, str] = {}
        self.pull_requests: dict[int, str] = {}
        self.issue_labels: dict[int, tuple[str, ...]] = {}
        self.sub_issues: dict[int, tuple[int, int]] = {}
        self.commits: dict[str, bool] = {}
        self.branches: dict[str, str | None] = {}
        self.reviews: dict[tuple[int, int], str | None] = {}
        self.sequences: dict[str, list[Any]] = {}
        self.carriers_override: list[ContinuationCarrier] | None = None

    def _resolve(self, key: str, default: Any) -> Any:
        """Return the configured value for ``key``, consuming a scripted
        sequence one entry at a time (repeating the final entry once
        exhausted) so tests can script flaky or eventually-stable reads."""
        sequence = self.sequences.get(key)
        if not sequence:
            value = default
        elif len(sequence) > 1:
            value = sequence.pop(0)
        else:
            value = sequence[0]
        if isinstance(value, BaseException):
            raise value
        return value

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
                author=self.comment_author,
                author_type=self.comment_author_type,
            )
        return next(comment for comment in self.comments if comment.id == comment_id)

    def ensure_issue_label(
        self,
        repository: str,
        number: int,
        label: str,
    ) -> None:
        self.calls.append(f"label:{number}:{label}")
        self.labels.add(label)

    def remove_issue_label(
        self,
        repository: str,
        number: int,
        label: str,
    ) -> None:
        self.calls.append(f"unlabel:{number}:{label}")
        self.labels.discard(label)

    def authenticated_actor(self) -> tuple[str, str]:
        self.calls.append("authenticated-actor")
        return self.actor_login, self.actor_type

    def repository_permission(self, repository: str, login: str) -> str:
        self.calls.append(f"permission:{login}")
        return self.permission

    def append_issue_comment(
        self,
        repository: str,
        number: int,
        body: str,
    ) -> ContinuationComment:
        self.calls.append(f"append:{number}")
        if self.fail_append:
            raise GhError(["gh", "api"], 1, "append failed")
        self.body = body
        comment = ContinuationComment(
            id=self.next_comment_id,
            url=(
                "https://github.com/octo/example/issues/237"
                f"#issuecomment-{self.next_comment_id}"
            ),
            body=body,
            author=self.comment_author,
            author_type=self.comment_author_type,
        )
        self.next_comment_id += 1
        self.comments.append(comment)
        return comment

    def _carrier(self) -> ContinuationCarrier:
        comments = tuple(self.comments)
        if not comments and self.body:
            comments = (
                ContinuationComment(
                    id=9001,
                    url=(
                        "https://github.com/octo/example/issues/237#issuecomment-9001"
                    ),
                    body=self.body,
                    author=self.comment_author,
                    author_type=self.comment_author_type,
                ),
            )
        return ContinuationCarrier(
            number=237,
            state="OPEN",
            url="https://github.com/octo/example/issues/237",
            comments=comments,
            labels=tuple(sorted(self.labels)),
        )

    def list_continuation_carriers(
        self,
        repository: str,
        label: str,
    ) -> list[ContinuationCarrier]:
        self.calls.append("list-carriers")
        if self.carriers_override is not None:
            return self.carriers_override
        if not self.body:
            return []
        return [self._carrier()]

    def list_all_continuation_carriers(
        self,
        repository: str,
    ) -> list[ContinuationCarrier]:
        self.calls.append("list-all-carriers")
        if self.carriers_override is not None:
            return self.carriers_override
        if not self.body:
            return []
        return [self._carrier()]

    def read_issue(
        self,
        repository: str,
        number: int,
    ) -> ContinuationArtifact:
        self.calls.append(f"read-issue:{number}")
        state = self._resolve(f"issue:{number}", self.issues.get(number, "OPEN"))
        return ContinuationArtifact(
            number=number,
            state=state,
            url=f"https://github.com/octo/example/issues/{number}",
        )

    def read_pull_request(
        self,
        repository: str,
        number: int,
    ) -> ContinuationArtifact:
        self.calls.append(f"read-pull-request:{number}")
        state = self._resolve(f"pr:{number}", self.pull_requests.get(number, "OPEN"))
        return ContinuationArtifact(
            number=number,
            state=state,
            url=f"https://github.com/octo/example/pull/{number}",
        )

    def read_issue_labels(
        self,
        repository: str,
        number: int,
    ) -> ContinuationLabeledArtifact:
        self.calls.append(f"read-issue-labels:{number}")
        labels = self._resolve(
            f"labels:{number}", self.issue_labels.get(number, ())
        )
        return ContinuationLabeledArtifact(number=number, labels=tuple(labels))

    def read_issue_sub_issues(
        self,
        repository: str,
        number: int,
    ) -> ContinuationSubIssues:
        self.calls.append(f"read-sub-issues:{number}")
        total, completed = self._resolve(
            f"sub-issues:{number}", self.sub_issues.get(number, (0, 0))
        )
        return ContinuationSubIssues(number=number, total=total, completed=completed)

    def read_commit(self, repository: str, sha: str) -> ContinuationCommit:
        self.calls.append(f"read-commit:{sha}")
        exists = self._resolve(f"commit:{sha}", self.commits.get(sha, True))
        if not exists:
            raise GhError(["gh", "api"], 1, "404 Not Found")
        return ContinuationCommit(sha=sha)

    def read_branch(self, repository: str, name: str) -> ContinuationBranch:
        self.calls.append(f"read-branch:{name}")
        sha = self._resolve(f"branch:{name}", self.branches.get(name))
        if sha is None:
            raise GhError(["gh", "api"], 1, "404 Not Found")
        return ContinuationBranch(name=name, sha=sha)

    def read_pull_request_review(
        self,
        repository: str,
        pull_request: int,
        review_id: int,
    ) -> ContinuationReview:
        self.calls.append(f"read-review:{pull_request}:{review_id}")
        state = self._resolve(
            f"review:{pull_request}:{review_id}",
            self.reviews.get((pull_request, review_id)),
        )
        if state is None:
            raise GhError(["gh", "api"], 1, "404 Not Found")
        return ContinuationReview(review_id=review_id, state=state)


def _issue(number: int) -> dict[str, Any]:
    return {
        "kind": "issue",
        "repository": "octo/example",
        "number": number,
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


def _command_result(
    operation: str,
    request: dict[str, Any],
    github: _RecordingGitHub,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, dict[str, Any], str]:
    monkeypatch.setattr(continuation, "_make_github_client", lambda: github)
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps(request, ensure_ascii=False, separators=(",", ":"))),
    )
    exit_code = cli.main(["continuation", operation])
    captured = capsys.readouterr()
    return exit_code, json.loads(captured.out), captured.err


def test_python_reconcile_mints_empty_trusted_revision_observation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = _RecordingGitHub()

    exit_code, result, stderr = _command_result(
        "reconcile",
        {
            "repository": "octo/example",
            "trusted_producers": ["planner"],
            "trusted_apps": [],
            "revision_protocol": True,
        },
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    assert result["result"]["observation"] == {
        "heads": [],
        "token": (
            "sha256:b55895cf053aabb584c19ab3b824e3098bd0ac4c14d776d53052df37e4e2c142"
        ),
        "validators": [],
    }
    assert result["result"]["diagnostics"] == []
    assert github.calls == ["list-all-carriers"]
    assert stderr == ""


def test_python_publish_commits_first_trusted_revision_from_observation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = _RecordingGitHub()
    _exit, reconciled, _stderr = _command_result(
        "reconcile",
        {
            "repository": "octo/example",
            "trusted_producers": ["planner"],
            "trusted_apps": [],
            "revision_protocol": True,
        },
        github,
        monkeypatch,
        capsys,
    )
    github.calls.clear()
    request = _valid_publish_request("shared-continue")
    request.update(
        {
            "trusted_apps": [],
            "observation": reconciled["result"]["observation"],
            "parents": [],
        }
    )

    exit_code, result, stderr = _publish_result(
        request,
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    assert result["receipt"]["status"] == "committed"
    assert result["receipt"]["parents"] == []
    assert github.calls == [
        "authenticated-actor",
        "permission:planner",
        "list-all-carriers",
        "read-comment:7001",
        "label:237:git-loopy-continuation",
        "append:237",
        "read-comment:9001",
    ]
    assert '"parents":[]' in github.body
    assert stderr == ""


def test_python_reconcile_observes_trusted_live_revision_head(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = _RecordingGitHub()
    _exit, empty, _stderr = _command_result(
        "reconcile",
        {
            "repository": "octo/example",
            "trusted_producers": ["planner"],
            "trusted_apps": [],
            "revision_protocol": True,
        },
        github,
        monkeypatch,
        capsys,
    )
    request = _valid_publish_request("shared-continue")
    request.update(
        {
            "trusted_apps": [],
            "observation": empty["result"]["observation"],
            "parents": [],
        }
    )
    _exit, published, _stderr = _publish_result(
        request,
        github,
        monkeypatch,
        capsys,
    )
    github.calls.clear()

    exit_code, result, stderr = _command_result(
        "reconcile",
        {
            "repository": "octo/example",
            "trusted_producers": ["planner"],
            "trusted_apps": [],
            "revision_protocol": True,
        },
        github,
        monkeypatch,
        capsys,
    )

    observation = result["result"]["observation"]
    assert exit_code == 0
    assert observation["heads"] == [
        {
            "carrier": 237,
            "producer": "planner",
            "revision_id": published["receipt"]["revision_id"],
            "workstream_anchor": request["completion"]["workstream"]["anchor"],
        }
    ]
    assert observation["validators"] == [
        {
            "comment_id": 9001,
            "sha256": hashlib.sha256(github.body.encode("utf-8")).hexdigest(),
        }
    ]
    assert observation["token"].startswith("sha256:")
    assert result["result"]["diagnostics"] == []
    assert github.calls == [
        "list-all-carriers",
        "permission:planner",
        "read-issue:239",
    ]
    assert stderr == ""


def test_python_reconcile_authenticates_marker_author_before_parsing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = _RecordingGitHub()
    github.body = "<!-- git-loopy-continuation:1 -->\n```json\n{not-json}\n```"
    github.comment_author = "attacker"

    exit_code, result, stderr = _command_result(
        "reconcile",
        {
            "repository": "octo/example",
            "trusted_producers": ["planner"],
            "trusted_apps": [],
            "revision_protocol": True,
        },
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    assert result["result"]["status"] == "waiting"
    assert result["result"]["diagnostics"] == [
        {
            "author": "attacker",
            "carrier": 237,
            "code": "untrusted_marker_ignored",
            "comment_id": 9001,
        }
    ]
    assert github.calls == ["list-all-carriers"]
    assert stderr == ""


def test_python_reconcile_rejects_edited_carrier_comment(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = _RecordingGitHub()
    request = _valid_publish_request("shared-continue")
    _publish_result(request, github, monkeypatch, capsys)
    original = github.comments[0]
    github.comments[0] = ContinuationComment(
        id=original.id,
        url=original.url,
        body=original.body,
        author=original.author,
        created_at="2026-07-22T20:00:00Z",
        updated_at="2026-07-22T20:01:00Z",
    )
    github.calls.clear()

    exit_code, result, stderr = _command_result(
        "reconcile",
        {
            "repository": "octo/example",
            "trusted_producers": ["planner"],
            "trusted_apps": [],
            "revision_protocol": True,
        },
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    assert result["result"]["actions"] == []
    assert {
        "carrier": 237,
        "code": "mutated_revision",
        "comment_id": 9001,
    } in result["result"]["diagnostics"]
    assert stderr == ""


def test_python_publish_retry_finds_same_deterministic_revision(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = _RecordingGitHub()
    _exit, empty, _stderr = _command_result(
        "reconcile",
        {
            "repository": "octo/example",
            "trusted_producers": ["planner"],
            "trusted_apps": [],
            "revision_protocol": True,
        },
        github,
        monkeypatch,
        capsys,
    )
    request = _valid_publish_request("shared-continue")
    request.update(
        {
            "trusted_apps": [],
            "observation": empty["result"]["observation"],
            "parents": [],
        }
    )
    _exit, first, _stderr = _publish_result(
        request,
        github,
        monkeypatch,
        capsys,
    )
    github.calls.clear()

    exit_code, retry, stderr = _publish_result(
        request,
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    assert retry["receipt"]["status"] == "idempotent"
    assert retry["receipt"]["revision_id"] == first["receipt"]["revision_id"]
    assert retry["receipt"]["comment"]["id"] == 9001
    assert github.calls == [
        "authenticated-actor",
        "permission:planner",
        "list-all-carriers",
    ]
    assert len(github.comments) == 1
    assert stderr == ""


def test_python_stale_non_equivalent_publish_creates_visible_fork(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = _RecordingGitHub()
    reconcile_request = {
        "repository": "octo/example",
        "trusted_producers": ["planner"],
        "trusted_apps": [],
        "revision_protocol": True,
    }
    _exit, empty, _stderr = _command_result(
        "reconcile",
        reconcile_request,
        github,
        monkeypatch,
        capsys,
    )
    first_request = _valid_publish_request("shared-continue")
    first_request.update(
        {
            "trusted_apps": [],
            "observation": empty["result"]["observation"],
            "parents": [],
        }
    )
    _exit, first, _stderr = _publish_result(
        first_request,
        github,
        monkeypatch,
        capsys,
    )
    second_request = copy.deepcopy(first_request)
    second_request["completion"]["actions"][0]["instruction"]["value"] = (
        "/implement issue 239 with the alternate accepted semantics"
    )

    exit_code, second, stderr = _publish_result(
        second_request,
        github,
        monkeypatch,
        capsys,
    )
    _exit, reconciled, _stderr = _command_result(
        "reconcile",
        reconcile_request,
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    assert second["receipt"]["status"] == "conflict"
    assert second["receipt"]["conflicting_heads"] == sorted(
        [first["receipt"]["revision_id"], second["receipt"]["revision_id"]]
    )
    assert reconciled["result"]["actions"] == []
    assert {
        diagnostic["code"] for diagnostic in reconciled["result"]["diagnostics"]
    } == {"revision_fork"}
    assert len(reconciled["result"]["observation"]["heads"]) == 2
    assert len(github.comments) == 2
    assert stderr == ""


def test_python_equivalent_concurrent_heads_deduplicate_guidance(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = _RecordingGitHub()
    reconcile_request = {
        "repository": "octo/example",
        "trusted_producers": ["planner"],
        "trusted_apps": [],
        "revision_protocol": True,
    }
    _exit, empty, _stderr = _command_result(
        "reconcile",
        reconcile_request,
        github,
        monkeypatch,
        capsys,
    )
    first_request = _valid_publish_request("shared-continue")
    first_request.update(
        {
            "trusted_apps": [],
            "observation": empty["result"]["observation"],
            "parents": [],
        }
    )
    _publish_result(first_request, github, monkeypatch, capsys)
    equivalent = copy.deepcopy(first_request)
    equivalent["completion"]["actions"][0]["summary"] = (
        "Equivalent wording from a concurrent Producer read"
    )

    exit_code, receipt, stderr = _publish_result(
        equivalent,
        github,
        monkeypatch,
        capsys,
    )
    _exit, reconciled, _stderr = _command_result(
        "reconcile",
        reconcile_request,
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    assert receipt["receipt"]["status"] == "committed"
    assert len(reconciled["result"]["observation"]["heads"]) == 2
    assert len(reconciled["result"]["actions"]) == 1
    assert not any(
        diagnostic["code"] == "revision_fork"
        for diagnostic in reconciled["result"]["diagnostics"]
    )
    assert stderr == ""


def test_python_fork_resolution_names_every_current_head(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = _RecordingGitHub()
    reconcile_request = {
        "repository": "octo/example",
        "trusted_producers": ["planner"],
        "trusted_apps": [],
        "revision_protocol": True,
    }
    _exit, empty, _stderr = _command_result(
        "reconcile",
        reconcile_request,
        github,
        monkeypatch,
        capsys,
    )
    first_request = _valid_publish_request("shared-continue")
    first_request.update(
        {
            "trusted_apps": [],
            "observation": empty["result"]["observation"],
            "parents": [],
        }
    )
    _publish_result(first_request, github, monkeypatch, capsys)
    stale_request = copy.deepcopy(first_request)
    stale_request["completion"]["actions"][0]["instruction"]["value"] += " safely"
    _publish_result(stale_request, github, monkeypatch, capsys)
    _exit, fork, _stderr = _command_result(
        "reconcile",
        reconcile_request,
        github,
        monkeypatch,
        capsys,
    )
    resolution_request = copy.deepcopy(stale_request)
    resolution_request["completion"]["actions"][0]["instruction"]["value"] += (
        " using the resolved fork"
    )
    resolution_request["observation"] = fork["result"]["observation"]
    resolution_request["parents"] = [
        head["revision_id"] for head in fork["result"]["observation"]["heads"]
    ]

    exit_code, resolution, stderr = _publish_result(
        resolution_request,
        github,
        monkeypatch,
        capsys,
    )
    _exit, reconciled, _stderr = _command_result(
        "reconcile",
        reconcile_request,
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    assert resolution["receipt"]["status"] == "committed"
    assert resolution["receipt"]["parents"] == resolution_request["parents"]
    assert reconciled["result"]["observation"]["heads"] == [
        {
            "carrier": 237,
            "producer": "planner",
            "revision_id": resolution["receipt"]["revision_id"],
            "workstream_anchor": resolution_request["completion"]["workstream"][
                "anchor"
            ],
        }
    ]
    assert len(reconciled["result"]["actions"]) == 1
    assert not any(
        diagnostic["code"] == "revision_fork"
        for diagnostic in reconciled["result"]["diagnostics"]
    )
    assert stderr == ""


def test_python_publish_detects_mutated_observation_before_append(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = _RecordingGitHub()
    reconcile_request = {
        "repository": "octo/example",
        "trusted_producers": ["planner"],
        "trusted_apps": [],
        "revision_protocol": True,
    }
    _exit, empty, _stderr = _command_result(
        "reconcile",
        reconcile_request,
        github,
        monkeypatch,
        capsys,
    )
    root_request = _valid_publish_request("shared-continue")
    root_request.update(
        {
            "trusted_apps": [],
            "observation": empty["result"]["observation"],
            "parents": [],
        }
    )
    _publish_result(root_request, github, monkeypatch, capsys)
    _exit, observed, _stderr = _command_result(
        "reconcile",
        reconcile_request,
        github,
        monkeypatch,
        capsys,
    )
    original = github.comments[0]
    github.comments[0] = ContinuationComment(
        id=original.id,
        url=original.url,
        body=original.body + " ",
        author=original.author,
        author_type=original.author_type,
    )
    successor = copy.deepcopy(root_request)
    successor["completion"]["actions"][0]["instruction"]["value"] += " next"
    successor["observation"] = observed["result"]["observation"]
    successor["parents"] = [
        head["revision_id"] for head in observed["result"]["observation"]["heads"]
    ]
    github.calls.clear()

    exit_code, result, stderr = _publish_result(
        successor,
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 1
    assert result["error"]["code"] == "repair_required"
    assert "mutated" in result["error"]["message"]
    assert github.calls == [
        "authenticated-actor",
        "permission:planner",
        "list-all-carriers",
    ]
    assert len(github.comments) == 1
    assert "repair required" in stderr.lower()


def test_python_reconcile_quarantines_lineage_with_deleted_predecessor(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = _RecordingGitHub()
    reconcile_request = {
        "repository": "octo/example",
        "trusted_producers": ["planner"],
        "trusted_apps": [],
        "revision_protocol": True,
    }
    _exit, empty, _stderr = _command_result(
        "reconcile",
        reconcile_request,
        github,
        monkeypatch,
        capsys,
    )
    root_request = _valid_publish_request("shared-continue")
    root_request.update(
        {
            "trusted_apps": [],
            "observation": empty["result"]["observation"],
            "parents": [],
        }
    )
    _publish_result(root_request, github, monkeypatch, capsys)
    _exit, observed, _stderr = _command_result(
        "reconcile",
        reconcile_request,
        github,
        monkeypatch,
        capsys,
    )
    successor = copy.deepcopy(root_request)
    successor["completion"]["actions"][0]["instruction"]["value"] += " next"
    successor["observation"] = observed["result"]["observation"]
    successor["parents"] = [
        head["revision_id"] for head in observed["result"]["observation"]["heads"]
    ]
    _publish_result(successor, github, monkeypatch, capsys)
    del github.comments[0]

    exit_code, result, stderr = _command_result(
        "reconcile",
        reconcile_request,
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    assert result["result"]["actions"] == []
    assert result["result"]["observation"]["heads"] == []
    assert any(
        diagnostic["code"] == "missing_predecessor"
        and diagnostic["missing"] == successor["parents"]
        for diagnostic in result["result"]["diagnostics"]
    )
    assert stderr == ""


def test_python_authorized_reattestation_recovers_tainted_lineage(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = _RecordingGitHub()
    reconcile_request = {
        "repository": "octo/example",
        "trusted_producers": ["planner"],
        "trusted_apps": [],
        "revision_protocol": True,
    }
    _exit, empty, _stderr = _command_result(
        "reconcile",
        reconcile_request,
        github,
        monkeypatch,
        capsys,
    )
    root_request = _valid_publish_request("shared-continue")
    root_request.update(
        {
            "trusted_apps": [],
            "observation": empty["result"]["observation"],
            "parents": [],
        }
    )
    _publish_result(root_request, github, monkeypatch, capsys)
    _exit, root_observed, _stderr = _command_result(
        "reconcile",
        reconcile_request,
        github,
        monkeypatch,
        capsys,
    )
    tainted_request = copy.deepcopy(root_request)
    tainted_request["completion"]["actions"][0]["instruction"]["value"] += " next"
    tainted_request["observation"] = root_observed["result"]["observation"]
    tainted_request["parents"] = [
        head["revision_id"] for head in root_observed["result"]["observation"]["heads"]
    ]
    _exit, tainted, _stderr = _publish_result(
        tainted_request,
        github,
        monkeypatch,
        capsys,
    )
    del github.comments[0]
    _exit, quarantined, _stderr = _command_result(
        "reconcile",
        reconcile_request,
        github,
        monkeypatch,
        capsys,
    )
    repair_request = copy.deepcopy(tainted_request)
    repair_request["completion"]["actions"][0]["instruction"]["value"] += " repaired"
    repair_request["observation"] = quarantined["result"]["observation"]
    repair_request["parents"] = []

    bypass_exit, bypass, bypass_stderr = _publish_result(
        repair_request,
        github,
        monkeypatch,
        capsys,
    )

    assert bypass_exit == 1
    assert bypass["error"]["code"] == "repair_required"
    assert "re-attestation" in bypass["error"]["message"]
    assert len(github.comments) == 1
    assert "repair required" in bypass_stderr.lower()

    repair_request["trusted_reattesters"] = ["planner"]
    repair_request["reattestation"] = {
        "affected_heads": [tainted["receipt"]["revision_id"]],
        "authorized_by": "planner",
        "mode": "replace",
    }

    exit_code, repaired, stderr = _publish_result(
        repair_request,
        github,
        monkeypatch,
        capsys,
    )
    _exit, reconciled, _stderr = _command_result(
        "reconcile",
        reconcile_request,
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 0, (repaired, stderr)
    assert repaired["receipt"]["status"] == "committed"
    assert repaired["receipt"]["reattestation"] == repair_request["reattestation"]
    assert len(reconciled["result"]["actions"]) == 1
    assert (
        reconciled["result"]["observation"]["heads"][0]["revision_id"]
        == (repaired["receipt"]["revision_id"])
    )
    assert '"reattestation":' in github.body
    assert stderr == ""


def test_python_reattestation_can_name_unparseable_producer_comment(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = _RecordingGitHub()
    github.body = "<!-- git-loopy-continuation:1 -->\n```json\n{not-json}\n```"
    github.labels.add("git-loopy-continuation")
    reconcile_request = {
        "repository": "octo/example",
        "trusted_producers": ["planner"],
        "trusted_apps": [],
        "revision_protocol": True,
    }

    _exit, quarantined, _stderr = _command_result(
        "reconcile",
        reconcile_request,
        github,
        monkeypatch,
        capsys,
    )
    invalid = next(
        diagnostic
        for diagnostic in quarantined["result"]["diagnostics"]
        if diagnostic["code"] == "invalid_revision"
    )
    repair_request = _valid_publish_request("shared-continue")
    repair_request.update(
        {
            "trusted_apps": [],
            "trusted_reattesters": ["planner"],
            "observation": quarantined["result"]["observation"],
            "parents": [],
            "reattestation": {
                "affected_heads": [invalid["affected_head"]],
                "authorized_by": "planner",
                "mode": "replace",
            },
        }
    )

    exit_code, repaired, stderr = _publish_result(
        repair_request,
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    assert repaired["receipt"]["status"] == "committed"
    assert repaired["receipt"]["reattestation"] == repair_request["reattestation"]
    assert stderr == ""


def test_python_index_diagnostics_do_not_hide_records_and_repair_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = _RecordingGitHub()
    reconcile_request = {
        "repository": "octo/example",
        "trusted_producers": ["planner"],
        "trusted_apps": [],
        "revision_protocol": True,
    }
    _exit, empty, _stderr = _command_result(
        "reconcile",
        reconcile_request,
        github,
        monkeypatch,
        capsys,
    )
    publish_request = _valid_publish_request("shared-continue")
    publish_request.update(
        {
            "trusted_apps": [],
            "observation": empty["result"]["observation"],
            "parents": [],
        }
    )
    _publish_result(publish_request, github, monkeypatch, capsys)
    github.labels.clear()
    github.calls.clear()

    exit_code, reconciled, stderr = _command_result(
        "reconcile",
        reconcile_request,
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    assert len(reconciled["result"]["actions"]) == 1
    assert {"code": "index_label_missing", "carrier": 237} in reconciled["result"][
        "diagnostics"
    ]
    assert "label:237:git-loopy-continuation" not in github.calls
    github.calls.clear()

    repair_exit, repaired, repair_stderr = _command_result(
        "repair-index",
        {
            "repository": "octo/example",
            "trusted_producers": ["planner"],
            "trusted_apps": [],
        },
        github,
        monkeypatch,
        capsys,
    )

    assert repair_exit == 0
    assert repaired["result"] == {
        "added": [237],
        "index_label": "git-loopy-continuation",
        "removed": [],
        "status": "repaired",
    }
    assert github.calls == [
        "authenticated-actor",
        "permission:planner",
        "list-all-carriers",
        "permission:planner",
        "label:237:git-loopy-continuation",
    ]
    assert stderr == ""
    assert repair_stderr == ""


def test_python_publish_rechecks_human_write_permission_before_mutation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = _RecordingGitHub()
    github.permission = "READ"
    request = _valid_publish_request("shared-continue")
    request.update(
        {
            "trusted_apps": [],
            "observation": {
                "heads": [],
                "token": (
                    "sha256:"
                    "b55895cf053aabb584c19ab3b824e3098bd0ac4c14d776d53052df37e4e2c142"
                ),
                "validators": [],
            },
            "parents": [],
        }
    )

    exit_code, result, stderr = _publish_result(
        request,
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 1
    assert result["error"]["code"] == "invalid_request"
    assert "current write permission" in result["error"]["message"]
    assert github.calls == ["authenticated-actor", "permission:planner"]
    assert "current write permission" in stderr


def test_python_reconcile_quarantines_producer_after_permission_revocation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = _RecordingGitHub()
    request = _valid_publish_request("shared-continue")
    _publish_result(request, github, monkeypatch, capsys)
    github.permission = "READ"
    github.calls.clear()

    exit_code, result, stderr = _command_result(
        "reconcile",
        {
            "repository": "octo/example",
            "trusted_producers": ["planner"],
            "trusted_apps": [],
            "revision_protocol": True,
        },
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    assert result["result"]["actions"] == []
    assert result["result"]["diagnostics"] == [
        {
            "author": "planner",
            "carrier": 237,
            "code": "producer_permission_revoked",
            "comment_id": 9001,
        },
        {
            "carrier": 237,
            "code": "index_label_stale",
        },
    ]
    assert github.calls == ["list-all-carriers", "permission:planner"]
    assert stderr == ""


def test_python_publish_accepts_explicitly_allowlisted_app_without_human_permission(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = _RecordingGitHub()
    github.actor_login = "planner-bot"
    github.actor_type = "Bot"
    github.comment_author = "planner-bot"
    github.comment_author_type = "Bot"
    request = _valid_publish_request("shared-continue")
    request["completion"]["producer"]["login"] = "planner-bot"
    request["trusted_producers"] = []
    request.update(
        {
            "trusted_apps": ["planner-bot"],
            "observation": {
                "heads": [],
                "token": (
                    "sha256:"
                    "b55895cf053aabb584c19ab3b824e3098bd0ac4c14d776d53052df37e4e2c142"
                ),
                "validators": [],
            },
            "parents": [],
        }
    )

    exit_code, result, stderr = _publish_result(
        request,
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    assert result["receipt"]["status"] == "committed"
    assert not any(call.startswith("permission:") for call in github.calls)
    assert stderr == ""


def test_python_operational_publish_failure_is_repair_required(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = _RecordingGitHub()
    github.fail_append = True
    request = _valid_publish_request("shared-continue")
    request.update(
        {
            "trusted_apps": [],
            "observation": {
                "heads": [],
                "token": (
                    "sha256:"
                    "b55895cf053aabb584c19ab3b824e3098bd0ac4c14d776d53052df37e4e2c142"
                ),
                "validators": [],
            },
            "parents": [],
        }
    )

    exit_code, result, stderr = _publish_result(
        request,
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 1
    assert result["error"]["code"] == "repair_required"
    assert "append failed" in result["error"]["message"]
    assert result["ok"] is False
    assert "repair required" in stderr.lower()


def test_python_atomic_root_failure_after_durable_evidence_is_repair_required(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = _RecordingGitHub()
    github.fail_append = True

    exit_code, result, stderr = _publish_result(
        _valid_publish_request("shared-continue"),
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 1
    assert result["error"]["code"] == "repair_required"
    assert "append failed" in result["error"]["message"]
    assert result["ok"] is False
    assert "repair required" in stderr.lower()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("parents", ["0" * 64]),
        (
            "reattestation",
            {
                "affected_heads": ["0" * 64],
                "authorized_by": "planner",
                "mode": "copy",
            },
        ),
    ],
)
def test_python_publish_rejects_revision_fields_without_observation(
    field: str,
    value: object,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    github = _RecordingGitHub()
    request = _valid_publish_request("shared-continue")
    request[field] = value

    exit_code, result, stderr = _publish_result(
        request,
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 1
    assert result["error"]["code"] == "invalid_request"
    assert "observation is required" in result["error"]["message"]
    assert github.calls == []
    assert "observation is required" in stderr


@pytest.mark.parametrize(
    "case",
    FIXTURE["completion_records"]["valid_publish_cases"],
    ids=lambda case: case["id"],
)
def test_python_publish_accepts_fixture_completion_modes(
    case: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = _materialize_publish_case(case)
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


def _json_pointer_tokens(path: str) -> list[str]:
    assert path.startswith("/")
    return [
        token.replace("~1", "/").replace("~0", "~") for token in path[1:].split("/")
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


def _materialize_publish_case(case: dict[str, Any]) -> dict[str, Any]:
    records = FIXTURE["completion_records"]
    if "base_case" in case:
        base_case = next(
            item
            for item in records["valid_publish_cases"]
            if item["id"] == case["base_case"]
        )
        request = _materialize_publish_case(base_case)
    else:
        request = copy.deepcopy(records["publish_request_templates"][case["template"]])
    _apply_fixture_patch(request, case["patch"])
    return request


def _valid_publish_request(case_id: str) -> dict[str, Any]:
    records = FIXTURE["completion_records"]
    valid_case = next(
        case for case in records["valid_publish_cases"] if case["id"] == case_id
    )
    return _materialize_publish_case(valid_case)


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
    request = _materialize_publish_case(case)
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


def test_python_reconcile_supports_commit_exists_completion_condition(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = _valid_publish_request("shared-continue")
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
        io.StringIO('{"repository":"octo/example","trusted_producers":["planner"]}'),
    )
    reconcile_exit = cli.main(["continuation", "reconcile"])
    captured = capsys.readouterr()
    result = json.loads(captured.out)

    assert reconcile_exit == 0
    assert result["result"]["status"] == "waiting"
    assert result["result"]["actions"] == []
    assert result["result"]["diagnostics"] == []
    assert captured.err == ""

    github.commits[("a" * 40)] = False
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO('{"repository":"octo/example","trusted_producers":["planner"]}'),
    )
    reconcile_exit = cli.main(["continuation", "reconcile"])
    captured = capsys.readouterr()
    result = json.loads(captured.out)

    assert reconcile_exit == 0
    assert result["result"]["status"] == "guidance"
    assert len(result["result"]["actions"]) == 1
    assert result["result"]["actions"][0]["readiness"] == "Ready"
    assert result["result"]["diagnostics"] == []
    assert captured.err == ""


def test_python_reconcile_derives_ready_and_blocked_from_unsatisfied_prerequisites(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Readiness flips between Ready and Blocked as prerequisites change."""
    request = _valid_publish_request("shared-continue")
    action = request["completion"]["actions"][0]
    action["prerequisites"] = [
        {
            "kind": "issue-open",
            "target": {"kind": "issue", "repository": "octo/example", "number": 501},
        }
    ]
    github = _RecordingGitHub()
    github.issues[501] = "CLOSED"
    publish_exit, _publish, _stderr = _publish_result(
        request, github, monkeypatch, capsys
    )
    assert publish_exit == 0

    exit_code, result, stderr = _command_result(
        "reconcile",
        {"repository": "octo/example", "trusted_producers": ["planner"]},
        github,
        monkeypatch,
        capsys,
    )
    assert exit_code == 0
    assert result["result"]["status"] == "guidance"
    [blocked] = result["result"]["actions"]
    assert blocked["readiness"] == "Blocked"
    assert blocked["unsatisfied_prerequisites"] == [
        {
            "kind": "issue-open",
            "target": {"kind": "issue", "repository": "octo/example", "number": 501},
        }
    ]
    assert stderr == ""

    github.issues[501] = "OPEN"
    exit_code, result, stderr = _command_result(
        "reconcile",
        {"repository": "octo/example", "trusted_producers": ["planner"]},
        github,
        monkeypatch,
        capsys,
    )
    assert exit_code == 0
    [ready] = result["result"]["actions"]
    assert ready["readiness"] == "Ready"
    assert "unsatisfied_prerequisites" not in ready
    assert stderr == ""


def test_python_reconcile_dedups_matching_claims_and_unions_provenance(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Equivalent live claims for one identity merge under one Action."""
    github = _RecordingGitHub()
    first = _valid_publish_request("shared-continue")
    publish_exit, _publish, _stderr = _publish_result(
        first, github, monkeypatch, capsys
    )
    assert publish_exit == 0

    second = _valid_publish_request("shared-continue")
    second["trusted_producers"] = ["planner", "second-planner"]
    second["completion"]["producer"] = {"login": "second-planner", "role": "planning"}
    second["completion"]["actions"][0]["basis"] = [
        {"kind": "issue", "repository": "octo/example", "number": 500}
    ]
    github.comment_author = "second-planner"
    publish_exit, _publish, _stderr = _publish_result(
        second, github, monkeypatch, capsys
    )
    assert publish_exit == 0

    exit_code, result, stderr = _command_result(
        "reconcile",
        {
            "repository": "octo/example",
            "trusted_producers": ["planner", "second-planner"],
        },
        github,
        monkeypatch,
        capsys,
    )
    assert exit_code == 0
    [merged] = result["result"]["actions"]
    assert merged["readiness"] == "Ready"
    assert sorted(item["number"] for item in merged["basis"]) == [237, 500]
    provenance_logins = sorted(entry["login"] for entry in merged["provenance"])
    assert provenance_logins == ["planner", "second-planner"]
    assert result["result"]["diagnostics"] == []
    assert stderr == ""


def test_python_reconcile_reports_action_conflict_for_incompatible_claims(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Incompatible semantics under one identity quarantine, never pick a winner."""
    github = _RecordingGitHub()
    first = _valid_publish_request("shared-continue")
    publish_exit, _publish, _stderr = _publish_result(
        first, github, monkeypatch, capsys
    )
    assert publish_exit == 0

    second = _valid_publish_request("shared-continue")
    second["trusted_producers"] = ["planner", "second-planner"]
    second["completion"]["producer"] = {"login": "second-planner", "role": "planning"}
    second["completion"]["actions"][0]["instruction"] = {
        "mode": "skill",
        "value": "/to-spec 237 --different",
    }
    github.comment_author = "second-planner"
    publish_exit, _publish, _stderr = _publish_result(
        second, github, monkeypatch, capsys
    )
    assert publish_exit == 0

    exit_code, result, stderr = _command_result(
        "reconcile",
        {
            "repository": "octo/example",
            "trusted_producers": ["planner", "second-planner"],
        },
        github,
        monkeypatch,
        capsys,
    )
    assert exit_code == 0
    assert result["result"]["status"] == "waiting"
    assert result["result"]["actions"] == []
    [diagnostic] = result["result"]["diagnostics"]
    assert diagnostic["code"] == "action_conflict"
    assert len(diagnostic["semantic_fingerprints"]) == 2
    assert stderr == ""


def test_python_reconcile_detects_prerequisite_cycles_as_conflicts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A completion-condition cycle is a Continuation conflict, not a crash."""
    request = _valid_publish_request("shared-continue")
    base_action = request["completion"]["actions"][0]
    action_a = copy.deepcopy(base_action)
    action_a["key"] = "action-a"
    action_a["target"] = {"kind": "issue", "repository": "octo/example", "number": 239}
    action_a["completion_condition"] = {
        "kind": "action-completed",
        "action_key": "action-b",
    }
    action_b = copy.deepcopy(base_action)
    action_b["key"] = "action-b"
    action_b["occurrence"] = "v2"
    action_b["target"] = {"kind": "issue", "repository": "octo/example", "number": 240}
    action_b["completion_condition"] = {
        "kind": "action-completed",
        "action_key": "action-a",
    }
    request["completion"]["actions"] = [action_a, action_b]

    github = _RecordingGitHub()
    publish_exit, _publish, _stderr = _publish_result(
        request, github, monkeypatch, capsys
    )
    assert publish_exit == 0

    exit_code, result, stderr = _command_result(
        "reconcile",
        {"repository": "octo/example", "trusted_producers": ["planner"]},
        github,
        monkeypatch,
        capsys,
    )
    assert exit_code == 0
    assert result["result"]["actions"] == []
    cycles = [
        diagnostic
        for diagnostic in result["result"]["diagnostics"]
        if diagnostic["code"] == "prerequisite_cycle"
    ]
    assert len(cycles) == 1
    assert set(cycles[0]["actions"]) == {"action-a", "action-b"}
    assert stderr == ""


def test_python_reconcile_reports_unverified_completion_for_unavailable_reads(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Persistently unavailable evidence is Unverified, never optimistic."""
    request = _valid_publish_request("shared-continue")
    github = _RecordingGitHub()
    publish_exit, _publish, _stderr = _publish_result(
        request, github, monkeypatch, capsys
    )
    assert publish_exit == 0

    github.sequences["issue:239"] = [
        GhError(["gh", "issue", "view"], 1, "temporarily unavailable"),
        GhError(["gh", "issue", "view"], 1, "temporarily unavailable"),
    ]
    exit_code, result, stderr = _command_result(
        "reconcile",
        {"repository": "octo/example", "trusted_producers": ["planner"]},
        github,
        monkeypatch,
        capsys,
    )
    assert exit_code == 0
    assert result["result"]["status"] == "waiting"
    assert result["result"]["actions"] == []
    [diagnostic] = result["result"]["diagnostics"]
    assert diagnostic["code"] == "unverified_completion"
    assert diagnostic["action_key"] == "action"
    assert stderr == ""


def test_python_reconcile_reports_unverified_prerequisite_and_keeps_other_guidance(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unstable prerequisite read excludes only its own Action.

    A prerequisite Target whose read never stabilizes yields a typed
    ``unverified_prerequisite`` diagnostic and drops that Action from
    guidance rather than surfacing an optimistic Ready or Blocked claim;
    an independent Action with a stable fact set stays Ready, proving the
    quarantine is the smallest safe scope.
    """
    request = _valid_publish_request("shared-continue")
    actions = request["completion"]["actions"]
    blocked = copy.deepcopy(actions[0])
    blocked["key"] = "blocked"
    blocked["summary"] = "Publish the successor specification"
    blocked["target"] = _issue(241)
    blocked["completion_condition"] = {"kind": "issue-closed", "target": _issue(241)}
    blocked["prerequisites"] = [{"kind": "issue-open", "target": _issue(501)}]
    actions.append(blocked)

    github = _RecordingGitHub()
    publish_exit, _publish, _stderr = _publish_result(
        request, github, monkeypatch, capsys
    )
    assert publish_exit == 0

    github.sequences["issue:501"] = [
        GhError(["gh", "issue", "view"], 1, "temporarily unavailable"),
        GhError(["gh", "issue", "view"], 1, "temporarily unavailable"),
    ]
    exit_code, result, stderr = _command_result(
        "reconcile",
        {"repository": "octo/example", "trusted_producers": ["planner"]},
        github,
        monkeypatch,
        capsys,
    )
    assert exit_code == 0
    assert result["result"]["status"] == "guidance"
    [ready] = result["result"]["actions"]
    assert ready["readiness"] == "Ready"
    assert ready["target"] == _issue(239)
    assert "unsatisfied_prerequisites" not in ready
    [diagnostic] = result["result"]["diagnostics"]
    assert diagnostic["code"] == "unverified_prerequisite"
    assert diagnostic["action_key"] == "blocked"
    assert stderr == ""


def test_python_reconcile_revision_protocol_discovers_every_carrier(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Guidance aggregates every discovered carrier, not just the first."""

    def _record(*, carrier_number: int, target_number: int) -> str:
        completion = {
            "continuation_contract_version": "1.0",
            "record_format": 1,
            "publication": "shared",
            "disposition": "continue",
            "workstream": {
                "anchor": {
                    "kind": "issue",
                    "repository": "octo/example",
                    "number": carrier_number,
                },
                "destination": {
                    "kind": "issue-closed",
                    "target": {
                        "kind": "issue",
                        "repository": "octo/example",
                        "number": carrier_number,
                    },
                },
            },
            "transition": {
                "owner": "wayfinder",
                "evidence": [
                    {
                        "kind": "issue-comment",
                        "repository": "octo/example",
                        "issue": carrier_number,
                        "comment_id": 7001,
                    }
                ],
            },
            "producer": {"login": "planner", "role": "planning"},
            "carrier": {
                "kind": "issue",
                "repository": "octo/example",
                "number": carrier_number,
            },
            "actions": [
                {
                    "key": "action",
                    "summary": "Publish the specification",
                    "kind": "Publish spec",
                    "occurrence": "v1",
                    "instruction": {
                        "mode": "skill",
                        "value": f"/to-spec {carrier_number}",
                    },
                    "target": {
                        "kind": "issue",
                        "repository": "octo/example",
                        "number": target_number,
                    },
                    "basis": [
                        {
                            "kind": "issue",
                            "repository": "octo/example",
                            "number": carrier_number,
                        }
                    ],
                    "prerequisites": [],
                    "interaction": {
                        "classification": "AFK-safe",
                        "evidence": {
                            "kind": "transition-owner-attestation",
                            "noninteractive": True,
                            "owner": "wayfinder",
                        },
                    },
                    "completion_condition": {
                        "kind": "issue-closed",
                        "target": {
                            "kind": "issue",
                            "repository": "octo/example",
                            "number": target_number,
                        },
                    },
                }
            ],
        }
        _revision_id, _fingerprints, body = continuation._record_body(completion)
        return body

    def _carrier(number: int, comment_id: int, target_number: int) -> ContinuationCarrier:
        return ContinuationCarrier(
            number=number,
            state="OPEN",
            url=f"https://github.com/octo/example/issues/{number}",
            comments=(
                ContinuationComment(
                    id=comment_id,
                    url=(
                        f"https://github.com/octo/example/issues/{number}"
                        f"#issuecomment-{comment_id}"
                    ),
                    body=_record(carrier_number=number, target_number=target_number),
                    author="planner",
                    author_type="User",
                    created_at="2024-01-01T00:00:00Z",
                    updated_at="2024-01-01T00:00:00Z",
                ),
            ),
            labels=("git-loopy-continuation",),
        )

    github = _RecordingGitHub()
    github.carriers_override = [
        _carrier(300, 9101, 301),
        _carrier(400, 9201, 401),
    ]

    exit_code, result, stderr = _command_result(
        "reconcile",
        {
            "repository": "octo/example",
            "trusted_producers": ["planner"],
            "trusted_apps": [],
            "revision_protocol": True,
        },
        github,
        monkeypatch,
        capsys,
    )
    assert exit_code == 0
    assert result["result"]["status"] == "guidance"
    targets = sorted(action["target"]["number"] for action in result["result"]["actions"])
    assert targets == [301, 401]
    assert result["result"]["diagnostics"] == []
    assert stderr == ""


@pytest.mark.parametrize(
    "case",
    FIXTURE["completion_records"]["terminal_outcome_cases"],
    ids=lambda case: case["id"],
)
def test_python_publish_accepts_every_pinned_terminal_outcome(
    case: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = _materialize_publish_case(case)
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
    "case",
    FIXTURE["completion_records"]["canonical_json_rejections"],
    ids=lambda case: case["id"],
)
def test_python_publish_enforces_portable_canonical_json_profile(
    case: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = _materialize_publish_case(case)
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
    "case",
    FIXTURE["completion_records"]["canonical_json_acceptances"],
    ids=lambda case: case["id"],
)
def test_python_publish_accepts_portable_json_boundaries(
    case: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = _materialize_publish_case(case)
    if "canonical_completion_bytes" in case:
        canonical_completion = json.dumps(
            request["completion"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        assert len(canonical_completion) == case["canonical_completion_bytes"]
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
    request = _valid_publish_request("ephemeral-continue")
    action = request["completion"]["actions"][0]
    action["kind"] = kind
    action["interaction"] = copy.deepcopy(
        FIXTURE["completion_records"]["interaction_examples"][
            schema["example_interaction"]
        ]
    )
    github = _RecordingGitHub()

    exit_code, stdout, stderr = _publish_output(
        request,
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    assert stdout == schema["expected_stdout_exact"]
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
    request = _valid_publish_request("ephemeral-continue")
    action = request["completion"]["actions"][0]
    for supporting_key in schema["supporting_action_keys"]:
        predecessor = copy.deepcopy(action)
        predecessor["key"] = supporting_key
        request["completion"]["actions"].insert(0, predecessor)
    action["prerequisites"] = [copy.deepcopy(schema["example"])]
    github = _RecordingGitHub()

    exit_code, stdout, stderr = _publish_output(
        request,
        github,
        monkeypatch,
        capsys,
    )

    assert exit_code == 0
    assert stdout == schema["expected_stdout_exact"]
    assert stderr == ""
    assert github.calls == []


@pytest.mark.parametrize(
    "case",
    FIXTURE["completion_records"]["fingerprint_cases"],
    ids=lambda case: case["id"],
)
def test_python_publish_locks_semantic_fingerprint_cases(
    case: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = _materialize_publish_case(case)
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
