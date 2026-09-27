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
    "$(git_loopy_resolve_bump_class "$(jq -c '.labels' <<<"$case_json")" \
      "$(jq -r '.last_stable_version' <<<"$case_json")")" \
    "Release-line Bump-class decision: $(jq -r '.id' <<<"$case_json")"
done < <(shell_cases cases)
((cases > 0)) || fail "release-line fixture declares no Shell Bump-class cases"

cases=0
while IFS= read -r case_json; do
  cases=$((cases + 1))
  id="$(jq -r '.id' <<<"$case_json")"
  if diagnostic="$(
    git_loopy_resolve_bump_class "$(jq -c '.labels' <<<"$case_json")" \
      "$(jq -r '.last_stable_version' <<<"$case_json")" 2>&1
  )"; then
    fail "Release-line Bump-class refusal was accepted: $id"
  fi
  assert_equal \
    "$(jq -r '.reason' <<<"$case_json")" \
    "$(sed -n 's/^git-loopy: Release-line Bump class: //p' <<<"$diagnostic")" \
    "Release-line Bump-class refusal: $id"
  if jq -e 'has("refused_label")' <<<"$case_json" >/dev/null; then
    assert_equal \
      "$(jq -r '.refused_label' <<<"$case_json")" \
      "$(sed -n 's/^git-loopy: refused label: //p' <<<"$diagnostic")" \
      "Release-line refused label: $id"
  fi
  if jq -e 'has("conflicting_labels")' <<<"$case_json" >/dev/null; then
    assert_equal \
      "$(jq -r '.conflicting_labels | join(",")' <<<"$case_json")" \
      "$(sed -n 's/^git-loopy: conflicting labels: //p' <<<"$diagnostic")" \
      "Release-line conflicting labels: $id"
  fi
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
        "$(jq -r '.current_stage // ""' <<<"$case_json")" \
        "$(jq -r '.current_counter' <<<"$case_json")" \
        "$(jq -r '.bump_class' <<<"$case_json")"
    )"
    assert_equal \
      "$(jq -c '{target: .new_target, stage: .new_stage, counter: .new_counter,
        version: .resulting_version}' <<<"$case_json")" \
      "$result" \
      "Release-line $group: $(jq -r '.id' <<<"$case_json")"
  done < <(shell_cases "$group")
  ((cases > 0)) || fail "release-line fixture declares no Shell $group"
done

cases=0
while IFS= read -r case_json; do
  cases=$((cases + 1))
  assert_equal \
    "$(jq -r '.resulting_version' <<<"$case_json")" \
    "$(git_loopy_advance_release_stage \
      "$(jq -r '.current_version' <<<"$case_json")" \
      "$(jq -r '.stage' <<<"$case_json")")" \
    "Release-line stage advance: $(jq -r '.id' <<<"$case_json")"
done < <(shell_cases stage_cases)
((cases > 0)) || fail "release-line fixture declares no Shell stage cases"

cases=0
while IFS= read -r case_json; do
  cases=$((cases + 1))
  if diagnostic="$(
    git_loopy_advance_release_stage \
      "$(jq -r '.current_version' <<<"$case_json")" \
      "$(jq -r '.stage' <<<"$case_json")" 2>&1
  )"; then
    fail "Release-line stage refusal was accepted: $(jq -r '.id' <<<"$case_json")"
  fi
  assert_equal \
    "$(jq -r '.reason' <<<"$case_json")" \
    "$(sed -n 's/^git-loopy: Release-line stage: //p' <<<"$diagnostic")" \
    "Release-line stage refusal: $(jq -r '.id' <<<"$case_json")"
done < <(shell_cases stage_refusal_cases)
((cases > 0)) || fail "release-line fixture declares no Shell stage refusal cases"

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
  outcomes='[]'
  while IFS= read -r integration_order; do
    target="$(jq -r '.current_target' <<<"$case_json")"
    stage="$(jq -r '.current_stage // ""' <<<"$case_json")"
    counter="$(jq -r '.current_counter' <<<"$case_json")"
    while IFS= read -r bump_class; do
      result="$(
        git_loopy_advance_release_line \
          "$(jq -r '.last_stable_version' <<<"$case_json")" \
          "$target" "$stage" "$counter" "$bump_class"
      )"
      target="$(jq -r '.target' <<<"$result")"
      stage="$(jq -r '.stage // ""' <<<"$result")"
      counter="$(jq -r '.counter' <<<"$result")"
    done < <(jq -r '.[]' <<<"$integration_order")
    outcomes="$(jq -c --argjson result "$result" '. + [$result]' <<<"$outcomes")"
  done < <(jq -c '.integration_orders[]' <<<"$case_json")
  assert_equal \
    "$(jq -c '[.integration_orders[] | {
      target: $case.resulting_target,
      stage: $case.resulting_stage,
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
#
# Seed every copied metadata file to a known stable value first, so this suite
# never depends on whatever the live Release line happens to be.
seed_release_metadata() {
  local root="$1" live live_python path
  live="$(cat "$repository_root/VERSION")"
  live_python="$(jq -r '.expected_python_distribution_version' \
    "$repository_root/git-loopy/conformance/release-version.json")"
  for path in "${GIT_LOOPY_RELEASE_VERSION_PATHS[@]}"; do
    mkdir -p "$root/$(dirname "$path")"
    LIVE="$live" LIVE_PY="$live_python" perl -pe '
      s/\Q$ENV{LIVE}\E/1.2.3/g; s/\Q$ENV{LIVE_PY}\E/1.2.3/g
    ' "$repository_root/$path" >"$root/$path"
  done
}
scratch="$(mktemp -d)"
missing_metadata=""
preserve_notes=""
preserve_fragment=""
trap 'rm -rf "$scratch" "$missing_metadata" "$preserve_notes" "$preserve_fragment"' EXIT
seed_release_metadata "$scratch"
git_loopy_write_repository_release_version "$scratch" "1.2.3" ||
  fail "could not establish a stable Release line in the writer fixture"
git -C "$scratch" init -q
git -C "$scratch" config user.email tester@example.invalid
git -C "$scratch" config user.name "Test Runner"
git -C "$scratch" add -A
git -C "$scratch" commit -qm "initial Release metadata"
git -C "$scratch" tag v1.2.3
fixture_without_live_versions="$(
  jq -c 'del(.expected_release_version, .expected_python_distribution_version)' \
    "$scratch/git-loopy/conformance/release-version.json"
)"
git_loopy_advance_repository_release_line "$scratch" '["v1.2.4"]' >/dev/null
assert_equal \
  '{"target":"1.2.4","stage":"alpha","counter":1,"version":"1.2.4-alpha.1","bump_class":"patch"}' \
  "$GIT_LOOPY_RELEASE_ADVANCE_JSON" \
  "a closed patch-target issue advances and commits the Release line"
assert_equal "1.2.4-alpha.1" "$(git_loopy_read_release_version "$scratch/VERSION")" \
  "the Release authority advanced"
assert_equal "1.2.4-alpha.1" \
  "$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' \
    "$scratch/git-loopy/python/git_loopy/__init__.py")" \
  "the Python source copy advanced"
assert_equal "1.2.4-alpha.1" \
  "$(sed -n 's/^version = "\(.*\)"$/\1/p' "$scratch/git-loopy/tui/Cargo.toml")" \
  "the Rust manifest copy advanced"
assert_equal "1.2.4a1" \
  "$(awk '/name = "git-loopy"/ { found = 1 } found && /^version = / {
    gsub(/"/, "", $3); print $3; exit
  }' "$scratch/git-loopy/python/uv.lock")" \
  "the Python lockfile uses its normalized Release version"
assert_equal "1.2.4-alpha.1" \
  "$(jq -r '.expected_release_version' \
    "$scratch/git-loopy/conformance/release-version.json")" \
  "the live conformance Release version advances"
assert_equal "1.2.4a1" \
  "$(jq -r '.expected_python_distribution_version' \
    "$scratch/git-loopy/conformance/release-version.json")" \
  "the live conformance Python distribution version is normalized"
assert_equal "$fixture_without_live_versions" \
  "$(jq -c 'del(.expected_release_version, .expected_python_distribution_version)' \
    "$scratch/git-loopy/conformance/release-version.json")" \
  "the live conformance fixture preserves all non-version data"
assert_equal "git-loopy/conformance/release-version.json" \
  "$(git -C "$scratch" diff-tree --no-commit-id --name-only -r HEAD -- \
    git-loopy/conformance)" \
  "only the designated live Release conformance fixture advances"
assert_equal \
  $'# git-loopy 1.2.4-alpha.1\n\nThis development fragment advances the Release line to `1.2.4-alpha.1` on the way to stable `1.2.4`.' \
  "$(cat "$scratch/docs/releases/v1.2.4-alpha.1.md")" \
  "an advance writes its committed development Release-note fragment"
git -C "$scratch" diff-tree --no-commit-id --name-only -r HEAD |
  grep -Fqx "docs/releases/v1.2.4-alpha.1.md" ||
  fail "the advance commit includes its development Release-note fragment"
git_loopy_advance_repository_release_line "$scratch" '["v1.3.0"]' >/dev/null
assert_equal "1.3.0-alpha.2" "$(git_loopy_read_release_version "$scratch/VERSION")" \
  "a second closure keeps the in-Run Release target and counter without a stable tag"
assert_equal \
  "chore(release): advance Release line to 1.3.0-alpha.2" \
  "$(git -C "$scratch" log -1 --format=%s)" \
  "the Release line is committed after every metadata copy changed"
if git_loopy_advance_repository_release_line "$scratch" '["v1.4.0"]' >/dev/null 2>&1; then
  fail "an unreachable Release target advanced the Release line"
fi
assert_equal "1.3.0-alpha.2" "$(git_loopy_read_release_version "$scratch/VERSION")" \
  "a refused Release target leaves the Release line untouched"
git_loopy_advance_repository_release_line "$scratch" '["v2.0.0"]' >/dev/null
assert_equal "2.0.0-alpha.3" "$(git_loopy_read_release_version "$scratch/VERSION")" \
  "a major target raises the line but never promotes it to stable on its own"
assert_equal \
  "chore(release): advance Release line to 2.0.0-alpha.3" \
  "$(git -C "$scratch" log -1 --format=%s)" \
  "a major target is committed as an advance rather than a Promotion"
GIT_LOOPY_RELEASE_LINE_INITIALIZED=false
GIT_LOOPY_RELEASE_LAST_STABLE=""
GIT_LOOPY_RELEASE_TARGET=""
GIT_LOOPY_RELEASE_STAGE=""
GIT_LOOPY_RELEASE_COUNTER=0
git_loopy_advance_repository_release_line "$scratch" '["v1.2.4"]' >/dev/null
assert_equal "2.0.0-alpha.4" "$(git_loopy_read_release_version "$scratch/VERSION")" \
  "a fresh Run reads the current line and last stable tag from the repository"
release_commit_count="$(git -C "$scratch" rev-list --count HEAD)"
assert_equal "null" \
  "$(git_loopy_advance_repository_release_line "$scratch" '["ready-for-agent"]')" \
  "an issue with no Release-target label does not advance the Release line"
assert_equal "$release_commit_count" "$(git -C "$scratch" rev-list --count HEAD)" \
  "an issue with no Release-target label does not create a Release commit"

# A stable draft composes every fragment for its target across all stages,
# ordered alpha < beta < rc and then by counter.
mkdir -p "$scratch/docs/releases"
printf '# git-loopy 2.0.0-rc.1\n\nRelease-candidate fragment.\n' \
  >"$scratch/docs/releases/v2.0.0-rc.1.md"
printf '# git-loopy 2.0.0-beta.1\n\nBeta fragment.\n' \
  >"$scratch/docs/releases/v2.0.0-beta.1.md"
printf '# git-loopy 2.0.0-alpha.10\n\nLate alpha fragment.\n' \
  >"$scratch/docs/releases/v2.0.0-alpha.10.md"
preserve_notes="$(mktemp -d)"
cp -R "$scratch/." "$preserve_notes"
preserve_fragment="$(mktemp -d)"
cp -R "$scratch/." "$preserve_fragment"
stable_line='{"target":"2.0.0","stage":null,"counter":0,"version":"2.0.0"}'
git_loopy_write_repository_release_notes "$scratch" \
  '{"target":"2.0.0","stage":"rc","counter":2,"version":"2.0.0-rc.2"}' "$stable_line" ||
  fail "a stable draft could not be composed"
assert_equal \
  $'2.0.0-alpha.3\n2.0.0-alpha.4\n2.0.0-alpha.10\n2.0.0-beta.1\n2.0.0-rc.1\n2.0.0-rc.2' \
  "$(sed -n 's/^### //p' "$scratch/docs/releases/v2.0.0.md")" \
  "a stable draft orders fragments alpha < beta < rc, then by counter"
grep -Fqx "Late alpha fragment." "$scratch/docs/releases/v2.0.0.md" ||
  fail "a stable draft composes the accumulated development fragments"
authored_fragment=$'# git-loopy 2.0.0-rc.2\n\nAn authored final development fragment.'
printf '%s\n' "$authored_fragment" >"$preserve_fragment/docs/releases/v2.0.0-rc.2.md"
git_loopy_write_repository_release_notes "$preserve_fragment" \
  '{"target":"2.0.0","stage":"rc","counter":2,"version":"2.0.0-rc.2"}' "$stable_line" ||
  fail "a stable draft could not be composed over an authored fragment"
assert_equal "$authored_fragment" \
  "$(cat "$preserve_fragment/docs/releases/v2.0.0-rc.2.md")" \
  "a stable draft preserves an authored current development fragment"
grep -Fqx "An authored final development fragment." \
  "$preserve_fragment/docs/releases/v2.0.0.md" ||
  fail "a stable draft composes the authored current development fragment"
printf '# Human release essay\n\nThis is deliberately not generated.\n' \
  >"$preserve_notes/docs/releases/v2.0.0.md"
git_loopy_write_repository_release_notes "$preserve_notes" \
  '{"target":"2.0.0","stage":"rc","counter":2,"version":"2.0.0-rc.2"}' "$stable_line" ||
  fail "a stable draft could not be written beside human notes"
assert_equal \
  $'# Human release essay\n\nThis is deliberately not generated.' \
  "$(cat "$preserve_notes/docs/releases/v2.0.0.md")" \
  "a human stable Release note is preserved"
printf '%s\n' "${GIT_LOOPY_RELEASE_NOTE_COMMIT_PATHS[@]}" |
  grep -Fqx "docs/releases/v2.0.0.md" ||
  fail "a stable draft must commit its preserved human stable Release note"
if _git_loopy_validate_release_line_version "2.0.0-dev.1" 2>/dev/null; then
  fail "a retired -dev.N value was accepted as a Release line"
fi
assert_equal "null" "$(git_loopy_promote_closed_milestone 2.0.0-dev.1 v2.0.0 closed)" \
  "a retired -dev.N value is never promoted by a milestone"

missing_metadata="$(mktemp -d)"
trap 'rm -rf "$scratch" "$preserve_notes" "$preserve_fragment" "$missing_metadata"' EXIT
printf '1.2.3\n' >"$missing_metadata/VERSION"
GIT_LOOPY_RELEASE_STAGE=""
GIT_LOOPY_RELEASE_LINE_INITIALIZED=false
GIT_LOOPY_RELEASE_LAST_STABLE=""
GIT_LOOPY_RELEASE_TARGET=""
GIT_LOOPY_RELEASE_COUNTER=0
if git_loopy_advance_repository_release_line \
  "$missing_metadata" '["v1.2.4"]' >/dev/null 2>&1; then
  fail "a Release line advanced without every version metadata copy"
fi
[[ -z "$(find "$missing_metadata" -name '.git-loopy-release*' -print -quit)" ]] ||
  fail "a refused metadata write leaked a checkpointable temporary file"

invalid_fixture="$(mktemp -d)"
trap 'rm -rf "$scratch" "$preserve_notes" "$preserve_fragment" "$missing_metadata" "$invalid_fixture"' EXIT
seed_release_metadata "$invalid_fixture"
git_loopy_write_repository_release_version "$invalid_fixture" "1.2.3" ||
  fail "could not establish Release metadata before fixture validation refusals"
while IFS=$'\t' read -r id fixture_content; do
  printf '%s\n' "$fixture_content" \
    >"$invalid_fixture/git-loopy/conformance/release-version.json"
  if git_loopy_write_repository_release_version "$invalid_fixture" "1.2.4" >/dev/null 2>&1; then
    fail "a $id live Release conformance fixture was accepted"
  fi
  assert_equal "1.2.3" "$(git_loopy_read_release_version "$invalid_fixture/VERSION")" \
    "a refused $id live Release conformance fixture leaves metadata untouched"
  [[ -z "$(find "$invalid_fixture" -name '.git-loopy-release*' -print -quit)" ]] ||
    fail "a refused $id live Release conformance fixture leaked a checkpointable temporary file"
done <<'EOF'
malformed	{"expected_release_version":"1.2.3",
missing	{"expected_release_version":"1.2.3"}
duplicate	{"expected_release_version":"1.2.3","expected_release_version":"1.2.3","expected_python_distribution_version":"1.2.3"}
invalid-type	{"expected_release_version":123,"expected_python_distribution_version":"1.2.3"}
EOF

printf 'shell Release-line conformance: ok\n'
