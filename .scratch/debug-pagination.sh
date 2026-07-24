#!/usr/bin/env bash
# Scratch harness: reproduce the "paginated Reconciliation" probe in isolation.
set -uo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
port_dir="$repo_root/git-loopy/shell"
fixture="$repo_root/git-loopy/conformance/continuation-scenarios.json"
entrypoint="$port_dir/git-loopy.sh"
scripted_github="$port_dir/tests/scripted-github.sh"
real_jq_dir="$(dirname "$(command -v jq)")"
bash_bin="$(command -v bash)"
tmp="$script_dir/work"
rm -rf "$tmp"
mkdir -p "$tmp/bin"
cp "$scripted_github" "$tmp/bin/gh"
chmod +x "$tmp/bin/gh"

scenario="$(
  jq -c '
    first(
      .scenarios[]
      | select(.id == "missing-index-label-does-not-hide-revision")
    )
  ' "$fixture"
)"
request="$(jq -c '.request.json' <<<"$scenario")"
github_script="$tmp/reconciliation-pagination-github-script.json"
github_state="$tmp/reconciliation-pagination-github-state"
github_log="$tmp/reconciliation-pagination-github-calls"
jq -c '
  .github_script[0] as $list
  | .github_script[1] as $comments
  | [
      $list + {
        stdout_json: [
          range(1000; 1100) as $number
          | {
              number: $number,
              state: "open",
              html_url: "https://github.com/octo/example/issues/\($number)",
              labels: [],
              comments: 0
            }
        ]
      },
      $list + {
        command: "api repos/octo/example/issues?state=all&per_page=100&page=2",
        stdout_json: [
          $list.stdout_json[0] + {comments: 101}
        ]
      },
      $comments + {
        stdout_json: [
          range(8000; 8100) as $id
          | {
              id: $id,
              html_url: (
                "https://github.com/octo/example/issues/237"
                + "#issuecomment-\($id)"
              ),
              body: "Ordinary issue discussion.",
              user: {login: "maintainer", type: "User"},
              created_at: "2026-07-22T19:00:00Z",
              updated_at: "2026-07-22T19:00:00Z"
            }
        ]
      },
      $comments + {
        command: (
          "api repos/octo/example/issues/237/comments"
          + "?per_page=100&page=2"
        )
      }
    ] + .github_script[2:]
' <<<"$scenario" >"$github_script"
: >"$github_log"

stdout_path="$tmp/reconciliation-pagination.stdout"
stderr_path="$tmp/reconciliation-pagination.stderr"
printf '%s' "$request" |
  PATH="$tmp/bin:$real_jq_dir:/usr/bin:/bin" \
  GIT_LOOPY_SCRIPTED_GITHUB_LOG="$github_log" \
  GIT_LOOPY_SCRIPTED_GITHUB_SCRIPT="$github_script" \
  GIT_LOOPY_SCRIPTED_GITHUB_STATE="$github_state" \
  "$bash_bin" "$entrypoint" continuation reconcile \
    >"$stdout_path" 2>"$stderr_path"
status="${PIPESTATUS[1]}"

echo "STATUS=$status"
echo "--- stdout (first 2000 chars)"
head -c 2000 "$stdout_path"; echo
echo "--- stderr"
cat "$stderr_path"
echo "--- github calls"
cat "$github_log"
