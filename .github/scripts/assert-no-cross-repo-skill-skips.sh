#!/usr/bin/env bash
#
# Refuse the cross-repo Skill proof reporting success by skipping (#632).
#
# `test_prompt_metadata.py::test_every_required_skill_exists_at_the_pinned_revision`,
# `test_skill_reference_routing.py::test_every_documented_skill_exists_in_the_pinned_catalog`,
# and `test_labels.py::test_the_template_setup_writes_into_a_consumer_repo_parses`
# each call `pytest.skip` when the pinned Skill catalog
# (`bradcstevens/git-loopy-skills`, ADR-0025) has not been acquired on disk. That
# skip is correct for an operator running the suite offline, but this job exists
# specifically because it *has* acquired the catalog -- so here the same skip
# means the proof did not run at all, and pytest exits 0 regardless.
#
# `count-passed.sh` already totals what a runner log says it passed; this reads
# the same log for the "skipped" pytest already prints and turns any into a
# failure that names the job responsible, rather than a quiet zero.
#
# Usage: assert-no-cross-repo-skill-skips.sh <runner-log>

set -euo pipefail

if (($# != 1)); then
  printf 'usage: %s <runner-log>\n' "${0##*/}" >&2
  exit 2
fi

log="$1"

if [[ ! -f "$log" ]]; then
  printf '%s: no such runner log: %s\n' "${0##*/}" "$log" >&2
  exit 2
fi

skipped="$(
  awk '
    {
      for (field = 2; field <= NF; field++) {
        if ($field ~ /^skipped[,;.]?$/ && $(field - 1) ~ /^[0-9]+$/) {
          total += $(field - 1)
        }
      }
    }
    END { print total + 0 }
  ' "$log"
)"

if ((skipped > 0)); then
  printf \
    'cross-repo Skill proof: %s test(s) reported skipped instead of executed; the pinned catalog was acquired for this job, so an absent catalog is a failure here, not a skip.\n' \
    "$skipped" >&2
  exit 1
fi

printf 'cross-repo Skill proof: 0 skipped\n'
