#!/usr/bin/env bash
#
# Refuse a family member's gate that ran to completion having asserted nothing.
#
# The **assertion census** (issue #317). A gate that dies loudly goes red and CI
# says so. A gate that is neutered into silence goes *green*, and nothing in the
# system says anything at all -- which is strictly the more dangerous of the two.
# An empty suite is the neatest way to get there: `cargo test` on a crate with no
# tests prints `test result: ok. 0 passed` and exits 0, and a PowerShell loop over
# a glob that matched nothing exits 0 too.
#
# So every member's gate states, in its own runner's reporting unit, how many
# assertions it executed, and hands that number here. The guarantee is **non-zero**
# and deliberately not a pinned floor: a floor has to be bumped every time a test
# is added, and a gate that must be edited to stay green is a gate that eventually
# gets edited to stay quiet.
#
# Usage: assert-nonzero-census.sh <member-label> <census>

set -euo pipefail

if (($# != 2)); then
  printf 'usage: %s <member-label> <census>\n' "${0##*/}" >&2
  exit 2
fi

label="$1"
census="$2"

if [[ ! "$census" =~ ^[0-9]+$ ]]; then
  printf '%s assertion census: %s is not a count -- the %s gate could not say what it asserted.\n' \
    "$label" "${census:-<empty>}" "$label" >&2
  exit 1
fi

printf '%s assertion census: %s\n' "$label" "$census"

if ((census < 1)); then
  printf '%s assertion census is zero: the %s gate ran to completion having asserted nothing, so it proved nothing.\n' \
    "$label" "$label" >&2
  exit 1
fi
