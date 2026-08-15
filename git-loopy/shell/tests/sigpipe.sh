#!/usr/bin/env bash
# shellcheck shell=bash
# Every name defined here is consumed by the suite that sources this file, and
# `bash_bin` is defined by that suite before it does.
# shellcheck disable=SC2034,SC2154

# Harness support for staging the SIGPIPE disposition a process is *started*
# with. Sourced by the suites that pin the Orchestrator's agent boundary; never
# run as a suite of its own (the runner globs `test-*.sh`).
#
# A suite cannot ask Bash for this on its own. POSIX specifies that a signal
# which is SIG_IGN at shell entry cannot be restored by the `trap` builtin, and a
# suite started under an ignored SIGPIPE is the normal case in CI: every GitHub
# Actions `run:` step inherits SIG_IGN from the Node-based runner that spawns it.
# So the harness resolves its own restorer, the same way and for the same reason
# the Orchestrator resolves one — but resolved *here*, so that staging a case
# never borrows the code the case is about.
#
# Requires `bash_bin` to already name the Bash the suite runs its children with.

# Starts a process with a DEFAULT SIGPIPE. Empty when this suite already has one,
# which is the only reason a suite may use it unconditionally.
# `sigpipe_default_stageable` is 0 only on a host that can serve neither — there
# the default-disposition half of a case cannot be staged and is skipped.
declare -a sigpipe_default_launcher=()
sigpipe_default_stageable=1

if [[ -n "$(trap -p PIPE)" ]]; then
  if env --default-signal=PIPE "$bash_bin" -c '' 2>/dev/null; then
    sigpipe_default_launcher=(env --default-signal=PIPE)
  elif command -v perl >/dev/null 2>&1; then
    sigpipe_default_launcher=(
      perl -e '$SIG{PIPE} = "DEFAULT"; exec { $ARGV[0] } @ARGV or die "$!\n"' --
    )
  elif command -v python3 >/dev/null 2>&1; then
    sigpipe_default_launcher=(
      python3 -c 'import os, signal, sys
signal.signal(signal.SIGPIPE, signal.SIG_DFL)
os.execvp(sys.argv[1], sys.argv[1:])'
    )
  else
    sigpipe_default_stageable=0
  fi
fi

# The other half needs no tool at all: setting an ignore and `exec`ing through it
# is exactly the condition that Bash can enter and cannot leave.
declare -a sigpipe_ignored_launcher=(
  "$bash_bin" -c 'trap "" PIPE; exec "$@"' git-loopy
)
