"""One scripted GitHub transport for Continuation reads and writes.

Continuation guidance is derived from issue state, labels, sub-issue counts,
commits, branch heads, pull requests and pull-request reviews, so a suite that
drives the *real* `publish` and `reconcile` needs all of them. Extracted here
because a second copy is a second decoder to disagree with: the module that
serves records to the derivation is exactly the module whose drift nothing else
would notice --- the same argument that put `tests/skill_templates.py` beside
these suites.

The transport is scripted, not simulated. Every read answers from a mutable
attribute a test sets, so "the Prerequisite became satisfied" is one assignment
rather than a state machine with opinions of its own.
"""

from __future__ import annotations

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
)


class RecordingGitHub:
    """A scripted GitHub transport that records every call it served."""

    def __init__(
        self,
        *,
        repository: str,
        producer: str,
        evidence_comments: frozenset[int] = frozenset(),
        evidence_issue: int | None = None,
    ) -> None:
        self.repository = repository
        self.producer = producer
        #: Comment ids that stand for durable transition evidence a Producer
        #: published outside this transport. `publish` reads its evidence before
        #: it appends, so a request naming one that does not exist must fail.
        self.evidence_comments = evidence_comments
        self.evidence_issue = evidence_issue
        self.calls: list[str] = []
        self.labels: dict[int, set[str]] = {}
        self.comments: dict[int, list[ContinuationComment]] = {}
        self.next_comment_id = 9001
        self.permission = "WRITE"
        self.actor_login = producer
        self.actor_type = "User"
        self.fail_append = False
        self.issues: dict[int, str] = {}
        self.missing_issues: set[int] = set()
        self.issue_labels: dict[int, tuple[str, ...]] = {}
        self.sub_issues: dict[int, tuple[int, int]] = {}
        self.commits: set[str] = set()
        self.branches: dict[str, str] = {}
        self.pull_requests: dict[int, str] = {}
        self.reviews: dict[int, str] = {}

    # -- writes ---------------------------------------------------------
    def ensure_issue_label(self, repository: str, number: int, label: str) -> None:
        self.calls.append(f"label:{number}:{label}")
        self.labels.setdefault(number, set()).add(label)

    def remove_issue_label(self, repository: str, number: int, label: str) -> None:
        self.calls.append(f"unlabel:{number}:{label}")
        self.labels.setdefault(number, set()).discard(label)

    def append_issue_comment(
        self,
        repository: str,
        number: int,
        body: str,
    ) -> ContinuationComment:
        self.calls.append(f"append:{number}")
        if self.fail_append:
            raise GhError(["gh", "api"], 1, "append failed")
        comment = ContinuationComment(
            id=self.next_comment_id,
            url=(
                f"https://github.com/{self.repository}/issues/{number}"
                f"#issuecomment-{self.next_comment_id}"
            ),
            body=body,
            author=self.actor_login,
            author_type=self.actor_type,
        )
        self.next_comment_id += 1
        self.comments.setdefault(number, []).append(comment)
        return comment

    # -- reads ----------------------------------------------------------
    def read_issue_comment(
        self,
        repository: str,
        comment_id: int,
    ) -> ContinuationComment:
        self.calls.append(f"read-comment:{comment_id}")
        for comments in self.comments.values():
            for comment in comments:
                if comment.id == comment_id:
                    return comment
        if comment_id in self.evidence_comments:
            issue = self.evidence_issue if self.evidence_issue is not None else 1
            return ContinuationComment(
                id=comment_id,
                url=(
                    f"https://github.com/{self.repository}/issues/{issue}"
                    f"#issuecomment-{comment_id}"
                ),
                body="Durable transition evidence.",
                author=self.producer,
            )
        raise GhError(["gh", "api"], 1, "404 Not Found")

    def authenticated_actor(self) -> tuple[str, str]:
        self.calls.append("authenticated-actor")
        return self.actor_login, self.actor_type

    def repository_permission(self, repository: str, login: str) -> str:
        self.calls.append(f"permission:{login}")
        return self.permission

    def _carriers(self) -> list[ContinuationCarrier]:
        return [
            ContinuationCarrier(
                number=number,
                state=self.issues.get(number, "OPEN"),
                url=f"https://github.com/{self.repository}/issues/{number}",
                comments=tuple(comments),
                labels=tuple(sorted(self.labels.get(number, set()))),
            )
            for number, comments in sorted(self.comments.items())
        ]

    def list_continuation_carriers(
        self,
        repository: str,
        label: str,
    ) -> list[ContinuationCarrier]:
        self.calls.append("list-carriers")
        return [carrier for carrier in self._carriers() if label in carrier.labels]

    def list_all_continuation_carriers(
        self,
        repository: str,
    ) -> list[ContinuationCarrier]:
        self.calls.append("list-all-carriers")
        return self._carriers()

    def read_issue(self, repository: str, number: int) -> ContinuationArtifact:
        self.calls.append(f"read-issue:{number}")
        if number in self.missing_issues:
            raise GhError(["gh", "api"], 1, "404 Not Found")
        return ContinuationArtifact(
            number=number,
            state=self.issues.get(number, "OPEN"),
            url=f"https://github.com/{self.repository}/issues/{number}",
        )

    def read_issue_labels(
        self,
        repository: str,
        number: int,
    ) -> ContinuationLabeledArtifact:
        self.calls.append(f"read-issue-labels:{number}")
        return ContinuationLabeledArtifact(
            number=number,
            labels=self.issue_labels.get(number, ()),
        )

    def read_issue_sub_issues(
        self,
        repository: str,
        number: int,
    ) -> ContinuationSubIssues:
        self.calls.append(f"read-sub-issues:{number}")
        total, completed = self.sub_issues.get(number, (0, 0))
        return ContinuationSubIssues(number=number, total=total, completed=completed)

    def read_pull_request(self, repository: str, number: int) -> ContinuationArtifact:
        self.calls.append(f"read-pull-request:{number}")
        state = self.pull_requests.get(number)
        if state is None:
            raise GhError(["gh", "api"], 1, "404 Not Found")
        return ContinuationArtifact(
            number=number,
            state=state,
            url=f"https://github.com/{self.repository}/pull/{number}",
        )

    def read_commit(self, repository: str, sha: str) -> ContinuationCommit:
        self.calls.append(f"read-commit:{sha}")
        if sha not in self.commits:
            raise GhError(["gh", "api"], 1, "404 Not Found")
        return ContinuationCommit(sha=sha)

    def read_branch(self, repository: str, name: str) -> ContinuationBranch:
        self.calls.append(f"read-branch:{name}")
        sha = self.branches.get(name)
        if sha is None:
            raise GhError(["gh", "api"], 1, "404 Not Found")
        return ContinuationBranch(name=name, sha=sha)

    def read_pull_request_review(
        self,
        repository: str,
        pull_request: int,
        review_id: int,
    ) -> ContinuationReview:
        self.calls.append(f"read-review:{review_id}")
        state = self.reviews.get(review_id)
        if state is None:
            raise GhError(["gh", "api"], 1, "404 Not Found")
        return ContinuationReview(review_id=review_id, state=state)
