"""Read-only mining snapshot; run from the repository root with its PYTHONPATH."""

import collections
import dataclasses
import json
from pathlib import Path

from git_loopy.config import TASK_TYPE_KEYS
from git_loopy.gh import SubprocessGitHubClient
from git_loopy.git import SubprocessGitClient
from git_loopy.proving_set import closed_issues, labelling_candidates, mine_proving_set


def main():
    root = Path.cwd()
    git = SubprocessGitClient(root)
    page = closed_issues(SubprocessGitHubClient())
    if not page.complete:
        raise RuntimeError("Closed backlog read is incomplete")
    mined = mine_proving_set(page.issues, git, default_branch="origin/main")
    result = {
        "revision": git.head_sha(),
        "closed_issues": len(page.issues),
        "complete": page.complete,
        "counts": {
            key: len(mined.by_task_type().get(key, ())) for key in TASK_TYPE_KEYS
        },
        "exclusion_counts": dict(
            collections.Counter(item.reason.value for item in mined.exclusions)
        ),
        "candidates": [dataclasses.asdict(item) for item in mined.candidates],
        "unlabelled_otherwise_mineable": labelling_candidates(
            page.issues, git, default_branch="origin/main"
        ),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
