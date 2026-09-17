#!/usr/bin/env bash

set -euo pipefail

if ((BASH_VERSINFO[0] < 4)); then
  printf 'Bash 4+ is required (found %s).\n' "$BASH_VERSION" >&2
  exit 1
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
port_dir="$(cd "$script_dir/.." && pwd)"
repository_root="$(cd "$port_dir/../.." && pwd)"
fixture="$port_dir/../conformance/release-line.json"

# shellcheck disable=SC1091
source "$port_dir/lib/release-version.sh"

fail() {
  printf 'FAIL: %s\n' "$*" >&2
  exit 1
}

assert_equal() {
  local expected="$1"
  local actual="$2"
  local description="$3"
  [[ "$actual" == "$expected" ]] ||
    fail "$description"$'\n'"expected: $expected"$'\n'"actual:   $actual"
}

shell_cases() {
  local key="$1"
  jq -c --arg key "$key" '.[$key][] | select(.distributions | index("shell"))' \
    "$fixture"
}

cases=0
while IFS= read -r case_json; do
  cases=$((cases + 1))
  assert_equal \
    "$(jq -r '.bump_class' <<<"$case_json")" \
    "$(git_loopy_resolve_bump_class "$(jq -c '.labels' <<<"$case_json")")" \
    "Release-line Bump-class decision: $(jq -r '.id' <<<"$case_json")"
done < <(shell_cases cases)
((cases > 0)) || fail "release-line fixture declares no Shell Bump-class cases"

cases=0
while IFS= read -r case_json; do
  cases=$((cases + 1))
  if diagnostic="$(
    git_loopy_resolve_bump_class "$(jq -c '.labels' <<<"$case_json")" 2>&1
  )"; then
    fail "Release-line Bump-class refusal was accepted: $(jq -r '.id' <<<"$case_json")"
  fi
  assert_equal \
    "$(jq -r '.reason' <<<"$case_json")" \
    "$(sed -n 's/^git-loopy: Release-line Bump class: //p' <<<"$diagnostic")" \
    "Release-line Bump-class refusal: $(jq -r '.id' <<<"$case_json")"
done < <(shell_cases refusal_cases)
((cases > 0)) || fail "release-line fixture declares no Shell refusal cases"

for group in ratchet_cases counter_cases; do
  cases=0
  while IFS= read -r case_json; do
    cases=$((cases + 1))
    result="$(
      git_loopy_advance_release_line \
        "$(jq -r '.last_stable_version' <<<"$case_json")" \
        "$(jq -r '.current_target' <<<"$case_json")" \
        "$(jq -r '.current_counter' <<<"$case_json")" \
        "$(jq -r '.bump_class' <<<"$case_json")"
    )"
    assert_equal \
      "$(jq -c '{target: .new_target, counter: .new_counter, version: .resulting_version}' \
        <<<"$case_json")" \
      "$result" \
      "Release-line $group: $(jq -r '.id' <<<"$case_json")"
  done < <(shell_cases "$group")
  ((cases > 0)) || fail "release-line fixture declares no Shell $group"
done

cases=0
while IFS= read -r case_json; do
  cases=$((cases + 1))
  assert_equal \
    "$(jq -c '.resulting_version' <<<"$case_json")" \
    "$(git_loopy_promote_closed_milestone \
      "$(jq -r '.current_version' <<<"$case_json")" \
      "$(jq -r '.milestone_title' <<<"$case_json")" \
      "$(jq -r '.milestone_state' <<<"$case_json")")" \
    "closed milestone Promotion: $(jq -r '.id' <<<"$case_json")"
done < <(shell_cases promotion_cases)
((cases > 0)) || fail "release-line fixture declares no Shell Promotion cases"

cases=0
while IFS= read -r case_json; do
  cases=$((cases + 1))
  advanced="$(git_loopy_advance_release_line \
    "$(jq -r '.last_stable_version' <<<"$case_json")" \
    "$(jq -r '.current_target' <<<"$case_json")" \
    "$(jq -r '.current_counter' <<<"$case_json")" \
    "$(jq -r '.bump_class' <<<"$case_json")")"
  assert_equal \
    "$(jq -r '.resulting_version' <<<"$case_json")" \
    "$(git_loopy_promote_release_line \
      "$advanced" "$(jq -r '.bump_class' <<<"$case_json")" | jq -r '.version')" \
    "Bump-class Promotion exemption: $(jq -r '.id' <<<"$case_json")"
done < <(shell_cases bump_promotion_cases)
((cases > 0)) || fail "release-line fixture declares no Shell Bump-class Promotion cases"

cases=0
while IFS= read -r case_json; do
  cases=$((cases + 1))
  outcomes='[]'
  while IFS= read -r integration_order; do
    target="$(jq -r '.current_target' <<<"$case_json")"
    counter="$(jq -r '.current_counter' <<<"$case_json")"
    while IFS= read -r bump_class; do
      result="$(
        git_loopy_advance_release_line \
          "$(jq -r '.last_stable_version' <<<"$case_json")" \
          "$target" "$counter" "$bump_class"
      )"
      target="$(jq -r '.target' <<<"$result")"
      counter="$(jq -r '.counter' <<<"$result")"
    done < <(jq -r '.[]' <<<"$integration_order")
    outcomes="$(jq -c --argjson result "$result" '. + [$result]' <<<"$outcomes")"
  done < <(jq -c '.integration_orders[]' <<<"$case_json")
  assert_equal \
    "$(jq -c '[.integration_orders[] | {
      target: $case.resulting_target,
      counter: $case.resulting_counter,
      version: $case.resulting_version
    }]' --argjson case "$case_json" <<<"$case_json")" \
    "$outcomes" \
    "Release-line Integration-order independence: $(jq -r '.id' <<<"$case_json")"
done < <(shell_cases order_independence_cases)
((cases > 0)) || fail "release-line fixture declares no Shell order-independence cases"

# The writer updates every distribution copy before the Release-line commit makes
# the advance visible. Drive its public seam in a real repository so a staging or
# commit regression cannot hide behind a pure ratchet test.
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
for path in "${GIT_LOOPY_RELEASE_VERSION_PATHS[@]}"; do
  mkdir -p "$scratch/$(dirname "$path")"
  cp "$repository_root/$path" "$scratch/$path"
done
git_loopy_write_repository_release_version "$scratch" "1.2.3" ||
  fail "could not establish a stable Release line in the writer fixture"
git -C "$scratch" init -q
git -C "$scratch" config user.email tester@example.invalid
git -C "$scratch" config user.name "Test Runner"
git -C "$scratch" add -A
git -C "$scratch" commit -qm "initial Release metadata"
git_loopy_advance_repository_release_line "$scratch" '["semver:patch"]' >/dev/null
assert_equal \
  '{"target":"1.2.4","counter":1,"version":"1.2.4-dev.1","bump_class":"patch"}' \
  "$GIT_LOOPY_RELEASE_ADVANCE_JSON" \
  "a closed patch issue advances and commits the Release line"
assert_equal "1.2.4-dev.1" "$(git_loopy_read_release_version "$scratch/VERSION")" \
  "the Release authority advanced"
assert_equal "1.2.4-dev.1" \
  "$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' \
    "$scratch/git-loopy/python/git_loopy/__init__.py")" \
  "the Python source copy advanced"
assert_equal "1.2.4-dev.1" \
  "$(sed -n 's/^version = "\(.*\)"$/\1/p' "$scratch/git-loopy/tui/Cargo.toml")" \
  "the Rust manifest copy advanced"
assert_equal "1.2.4.dev1" \
  "$(awk '/name = "git-loopy"/ { found = 1 } found && /^version = / {
    gsub(/"/, "", $3); print $3; exit
  }' "$scratch/git-loopy/python/uv.lock")" \
  "the Python lockfile uses its normalized Release version"
git_loopy_advance_repository_release_line "$scratch" '["semver:minor"]' >/dev/null
assert_equal "1.3.0-dev.2" "$(git_loopy_read_release_version "$scratch/VERSION")" \
  "a second closure keeps the in-Run Release target and counter without a stable tag"
assert_equal \
  "chore(release): advance Release line to 1.3.0-dev.2" \
  "$(git -C "$scratch" log -1 --format=%s)" \
  "the Release line is committed after every metadata copy changed"
git_loopy_advance_repository_release_line "$scratch" '["semver:major"]' >/dev/null
assert_equal "2.0.0" "$(git_loopy_read_release_version "$scratch/VERSION")" \
  "a major Bump class cuts stable without a milestone"
assert_equal \
  "chore(release): promote Release line to 2.0.0" \
  "$(git -C "$scratch" log -1 --format=%s)" \
  "a stable cut is committed as a Promotion rather than an advance"
git_loopy_advance_repository_release_line "$scratch" '["semver:patch"]' >/dev/null
assert_equal "2.0.1-dev.1" "$(git_loopy_read_release_version "$scratch/VERSION")" \
  "the issue after a major Promotion starts a fresh dev.N counter"
release_commit_count="$(git -C "$scratch" rev-list --count HEAD)"
assert_equal "null" \
  "$(git_loopy_advance_repository_release_line "$scratch" '["semver:none"]')" \
  "a deliberate no-bump does not advance the Release line"
assert_equal "$release_commit_count" "$(git -C "$scratch" rev-list --count HEAD)" \
  "a deliberate no-bump does not create a Release commit"

missing_metadata="$(mktemp -d)"
trap 'rm -rf "$scratch" "$missing_metadata"' EXIT
printf '1.2.3\n' >"$missing_metadata/VERSION"
GIT_LOOPY_RELEASE_LINE_INITIALIZED=false
GIT_LOOPY_RELEASE_LAST_STABLE=""
GIT_LOOPY_RELEASE_TARGET=""
GIT_LOOPY_RELEASE_COUNTER=0
if git_loopy_advance_repository_release_line \
  "$missing_metadata" '["semver:patch"]' >/dev/null 2>&1; then
  fail "a Release line advanced without every version metadata copy"
fi
[[ -z "$(find "$missing_metadata" -name '.git-loopy-release*' -print -quit)" ]] ||
  fail "a refused metadata write leaked a checkpointable temporary file"

printf 'shell Release-line conformance: ok\n'
