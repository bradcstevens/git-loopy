"""Fetch tracker history for every issue in runs.json via gh GraphQL (read-only).

Usage: python fetch_tracker.py runs.json > tracker.json
"""
import json
import subprocess
import sys

OWNER, NAME = "bradcstevens", "git-loopy"
FIELDS = """
 ... on Issue {
  number title state stateReason createdAt closedAt
  labels(first: 30) { nodes { name } }
  timelineItems(first: 100, itemTypes: [CLOSED_EVENT, REOPENED_EVENT, CROSS_REFERENCED_EVENT]) {
   nodes {
    __typename
    ... on ClosedEvent { createdAt actor { login } stateReason
      closer { __typename ... on Commit { oid messageHeadline } ... on PullRequest { number mergedAt } } }
    ... on ReopenedEvent { createdAt actor { login } }
    ... on CrossReferencedEvent { createdAt willCloseTarget
      source { __typename ... on Issue { number title createdAt labels(first: 20) { nodes { name } } }
                         ... on PullRequest { number title createdAt mergedAt } } }
   }
  }
 }
 ... on PullRequest { number title state createdAt closedAt mergedAt }
"""


def main(path):
    runs = json.load(open(path))
    nums = sorted({u["issue"] for r in runs for u in r["units"]})
    out = {}
    for i in range(0, len(nums), 20):
        chunk = nums[i:i + 20]
        body = " ".join(f"i{n}: issueOrPullRequest(number: {n}) {{ __typename {FIELDS} }}" for n in chunk)
        q = f'query {{ repository(owner: "{OWNER}", name: "{NAME}") {{ {body} }} }}'
        res = subprocess.run(["gh", "api", "graphql", "-f", f"query={q}"], capture_output=True, text=True)
        data = json.loads(res.stdout or "{}")
        if "errors" in data and not data.get("data"):
            sys.exit(res.stdout + res.stderr)
        for k, v in data["data"]["repository"].items():
            if v:
                out[k[1:]] = v
    json.dump(out, sys.stdout, indent=1)


if __name__ == "__main__":
    main(sys.argv[1])
