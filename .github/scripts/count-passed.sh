#!/usr/bin/env bash
#
# Total the assertions a test runner reported it passed, read from its own log.
#
# Part of the Runner-family gate's **assertion census** (issue #317). A runner that
# is handed an empty suite exits zero having asserted nothing -- `cargo test` prints
# `test result: ok. 0 passed` and succeeds -- so the gate cannot trust the exit code
# alone. It has to read what the runner said it did.
#
# Deliberately no new reporting protocol: `pytest` ("2754 passed, 2 skipped in 42s")
# and `cargo test` ("test result: ok. 12 passed; 0 failed; ...") already print the
# same `<N> passed` idiom, once per test binary, and the total is their sum. A log
# with no such token totals 0, which is the honest answer for a runner that
# collected nothing.
#
# Usage: count-passed.sh <runner-log>
# Prints the total to stdout. Callers pass it to assert-nonzero-census.sh.

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

awk '
  {
    for (field = 2; field <= NF; field++) {
      if ($field ~ /^passed[,;.]?$/ && $(field - 1) ~ /^[0-9]+$/) {
        total += $(field - 1)
      }
    }
  }
  END { print total + 0 }
' "$log"
