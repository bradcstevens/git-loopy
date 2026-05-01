#!/usr/bin/env bash
#
# ralph/grill.sh — autonomous /grill-me + /write-prd + /prd-to-issues runner.
#
# Phase 1 (looped, single named Copilot session):
#   Run /grill-me on the supplied filename or quote. Auto-accepts every
#   recommendation the agent makes (it acts as both interviewer and answerer)
#   and resumes the same session each iteration so the design context
#   accumulates. Exits the loop when the assistant message contains
#   <promise>GRILLING COMPLETE</promise>.
#
# Phase 1.5 (same session, single turn):
#   A validation turn that asks the agent to list any open / ambiguous
#   decisions and resolve them before producing the PRD. Guards against
#   premature GRILLING COMPLETE.
#
# Phase 2 (same session, single turn):
#   Run /write-prd. The agent emits <prd-path>/abs/path/to/file.md</prd-path>;
#   the script captures it (with a newest-prds/*.md fallback) and verifies
#   the file exists.
#
# Phase 3 (NEW session, looped):
#   Start a brand-new Copilot session and run /prd-to-issues with the PRD
#   path discovered above. Exits when the assistant message contains
#   <promise>ISSUES COMPLETE</promise>.
#
# Usage:
#   bash ralph/grill.sh <file-or-quote> [<max-grill-iterations>]
#
# Examples:
#   bash ralph/grill.sh client-brief.md
#   bash ralph/grill.sh "Build a recipes app for amateur chefs"
#   bash ralph/grill.sh client-brief.md 30
#   MODEL=gpt-5.4 EFFORT=high bash ralph/grill.sh client-brief.md
#   MAX_ISSUES_ITERS=10 bash ralph/grill.sh client-brief.md
#
# Environment:
#   MODEL              Copilot model (default: claude-opus-4.7-1m-internal)
#   EFFORT             Reasoning effort (default: xhigh)
#   MAX_ISSUES_ITERS   Cap on /prd-to-issues iterations (default: unlimited)
#
# Prereqs (one-time):
#   - copilot, jq, git on PATH.
#   - /grill-me, /write-prd, /prd-to-issues skills installed at
#     ~/.copilot/skills/.
#   - GitHub Copilot CLI signed in.

set -euo pipefail

on_err() {
  local rc=$?
  local line=${BASH_LINENO[0]:-?}
  printf '\nralph/grill.sh aborted at line %s (exit %s): %s\n' \
    "$line" "$rc" "${BASH_COMMAND}" >&2
}
trap on_err ERR
trap 'echo "interrupted" >&2; exit 130' INT TERM

# ---- args ----
INPUT="${1:-}"
MAX_GRILL_ITERS="${2:-0}"
MAX_ISSUES_ITERS="${MAX_ISSUES_ITERS:-0}"

if [ -z "$INPUT" ]; then
  cat >&2 <<'USAGE'
Usage:
  bash ralph/grill.sh <file-or-quote> [<max-grill-iterations>]

Examples:
  bash ralph/grill.sh client-brief.md
  bash ralph/grill.sh "Build a recipes app for amateur chefs"
  bash ralph/grill.sh client-brief.md 30
  MODEL=gpt-5.4 EFFORT=high bash ralph/grill.sh client-brief.md
  MAX_ISSUES_ITERS=10 bash ralph/grill.sh client-brief.md

If <file-or-quote> resolves to an existing file, the agent is told to read it
for context. Otherwise the value is treated as a verbatim quote and embedded
in the kickoff prompt directly.
USAGE
  exit 2
fi

if ! [[ "$MAX_GRILL_ITERS" =~ ^[0-9]+$ ]]; then
  echo "Error: <max-grill-iterations> must be a non-negative integer (got: $MAX_GRILL_ITERS)." >&2
  exit 2
fi
if ! [[ "$MAX_ISSUES_ITERS" =~ ^[0-9]+$ ]]; then
  echo "Error: MAX_ISSUES_ITERS must be a non-negative integer (got: $MAX_ISSUES_ITERS)." >&2
  exit 2
fi

MODEL="${MODEL:-claude-opus-4.7-1m-internal}"
EFFORT="${EFFORT:-xhigh}"

for cmd in copilot jq git; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Error: '$cmd' not found on PATH." >&2
    case "$cmd" in
      copilot) echo "  Install: npm install -g @github/copilot" >&2 ;;
      jq)      echo "  Install: brew install jq" >&2 ;;
      git)     echo "  Install: brew install git" >&2 ;;
    esac
    exit 1
  fi
done

# ---- single-run lock (atomic mkdir) ----
LOCK_DIR=".ralph-grill.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Error: another ralph/grill.sh run appears to be in progress (lock: $LOCK_DIR)." >&2
  echo "       Remove the directory if you are sure it is stale: rmdir $LOCK_DIR" >&2
  exit 1
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

# ---- mode detection (file vs quote) ----
if [ -f "$INPUT" ]; then
  CONTEXT_DIRECTIVE="see file \`$INPUT\` (in this repository) for context. Read it before proceeding."
  INPUT_MODE="file"
else
  CONTEXT_DIRECTIVE="context (verbatim quote): $INPUT"
  INPUT_MODE="quote"
fi

GRILL_SESSION="grill-$(date +%Y%m%d-%H%M%S)-$$"
ISSUES_SESSION="prd-to-issues-$(date +%Y%m%d-%H%M%S)-$$"

GRILL_DONE='<promise>GRILLING COMPLETE</promise>'
ISSUES_DONE='<promise>ISSUES COMPLETE</promise>'
PRD_OPEN='<prd-path>'
PRD_CLOSE='</prd-path>'

# jq filters tuned to the Copilot CLI --output-format json event shape.
stream_text='if .type == "assistant.message_delta" then (.data.deltaContent // "") elif .type == "assistant.message" then "\n" else empty end'
final_result='[inputs | select(.type == "assistant.message") | .data.content] | last // empty'

# RESULT is set by run_iter to the last terminal assistant.message content.
RESULT=""

run_iter() {
  local label="$1" prompt="$2" mode="$3" session="$4"
  local tmp
  tmp="$(mktemp -t ralph-grill.XXXXXX)"

  local -a args=(--model "$MODEL" --effort "$EFFORT" --yolo --no-ask-user
                 --output-format json -p "$prompt")
  if [ "$mode" = "new" ]; then
    args=(--name "$session" "${args[@]}")
  else
    args=(--resume="$session" "${args[@]}")
  fi

  echo "=== $label (session: $session) ==="
  copilot "${args[@]}" \
    | grep --line-buffered '^{' \
    | tee "$tmp" \
    | jq --unbuffered -rj "$stream_text"
  printf '\n'

  RESULT="$(jq -nr "$final_result" "$tmp" 2>/dev/null || true)"
  rm -f "$tmp"
}

manual_resume_hint() {
  local session="$1"
  echo "    Resume manually with: copilot --resume=\"$session\"" >&2
}

# ============================================================================
# Phase 1 — /grill-me loop in a single named session
# ============================================================================
KICK_PROMPT="/grill-me — $CONTEXT_DIRECTIVE

Run grill-me autonomously without asking the user any questions. For every
question you would normally ask, present your recommended answer and accept
it as the chosen direction (treat yourself as the answerer, accepting your own
recommendation). Walk every branch of the design tree and resolve dependencies
between decisions one-by-one.

When the shared design is fully resolved end-to-end, summarize the agreed
design and emit the literal sentinel as the FINAL line of your message:

$GRILL_DONE

Do NOT emit the sentinel until the design is fully resolved."

run_iter "Grill iteration 1 (kickoff)" "$KICK_PROMPT" "new" "$GRILL_SESSION"

i=1
while [[ "$RESULT" != *"$GRILL_DONE"* ]]; do
  i=$((i + 1))
  if [ "$MAX_GRILL_ITERS" -ne 0 ] && [ "$i" -gt "$MAX_GRILL_ITERS" ]; then
    echo "=== Grill iteration cap ($MAX_GRILL_ITERS) reached without $GRILL_DONE." >&2
    manual_resume_hint "$GRILL_SESSION"
    exit 1
  fi

  CONT_PROMPT="Continue /grill-me from where we left off. Accept your previously
recommended answer for the last branch, then move to the next unresolved branch
of the design tree. Do NOT ask the user any questions.

When the design is fully resolved, emit the literal sentinel as the FINAL line
of your message:

$GRILL_DONE"

  run_iter "Grill iteration $i" "$CONT_PROMPT" "resume" "$GRILL_SESSION"
done

echo "=== Grilling sentinel detected after $i iteration(s). ==="

# ============================================================================
# Phase 1.5 — Validation turn (guard against premature sentinel)
# ============================================================================
VAL_PROMPT="Before producing the PRD, do a final consistency check on the
design we just agreed on.

List any decisions that are still open, ambiguous, or unresolved. If any
exist, resolve each by accepting your own recommended answer, then re-emit
the sentinel. If everything is fully resolved, simply re-emit the sentinel.

The sentinel must be the FINAL line of your message:

$GRILL_DONE"

run_iter "Grill validation turn" "$VAL_PROMPT" "resume" "$GRILL_SESSION"

if [[ "$RESULT" != *"$GRILL_DONE"* ]]; then
  echo "=== Validation turn did not re-emit $GRILL_DONE; aborting before PRD generation." >&2
  manual_resume_hint "$GRILL_SESSION"
  exit 1
fi

# ============================================================================
# Phase 2 — /write-prd in the same session
# ============================================================================
PRD_PROMPT="/write-prd — Use the shared design consensus we just reached to
write the PRD.

Choose the output path per the skill's <output-path-rules> WITHOUT asking the
user for confirmation. This session was seeded from a $INPUT_MODE; if you
need a slug for the filename and no source file basename is available, derive
a kebab-case slug from the agreed design.

After the file is successfully written, emit its absolute path on a line by
itself wrapped in this exact tag (the runner script captures it):

${PRD_OPEN}/absolute/path/to/the.md${PRD_CLOSE}

Emit the tag exactly once, only after the PRD file write succeeds, as the
FINAL content of your message."

run_iter "/write-prd (same session)" "$PRD_PROMPT" "resume" "$GRILL_SESSION"

PRD_PATH="$(printf '%s' "$RESULT" \
  | tr '\r' ' ' \
  | sed -nE "s|.*${PRD_OPEN}([^<]+)${PRD_CLOSE}.*|\\1|p" \
  | tail -n 1 \
  | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"

if [ -z "${PRD_PATH:-}" ] || [ ! -f "$PRD_PATH" ]; then
  echo "=== <prd-path> tag missing or path not found; falling back to newest prds/*.md ==="
  PRD_PATH="$(ls -t prds/*.md 2>/dev/null | head -n 1 || true)"
fi

if [ -z "${PRD_PATH:-}" ] || [ ! -f "$PRD_PATH" ]; then
  echo "Error: could not locate a generated PRD under prds/." >&2
  manual_resume_hint "$GRILL_SESSION"
  exit 1
fi

# Normalize to absolute path so the new session can find it regardless of cwd.
PRD_PATH="$(cd "$(dirname "$PRD_PATH")" && pwd)/$(basename "$PRD_PATH")"

echo "=== PRD written: $PRD_PATH ==="
echo "=== Closing grill session: $GRILL_SESSION ==="

# ============================================================================
# Phase 3 — /prd-to-issues in a NEW session, looped
# ============================================================================
ISSUES_KICK="/prd-to-issues — PRD path: $PRD_PATH

Run prd-to-issues autonomously without asking the user any questions. For
every question you would normally ask, accept your recommended answer and
proceed.

Locate and read the PRD at the path above, propose vertical-slice issues,
then write each issue file under issues/<core-name>/ per the skill's
<output-path-rules>.

When all issue files have been written, emit the literal sentinel as the
FINAL line of your message:

$ISSUES_DONE"

run_iter "/prd-to-issues iteration 1 (kickoff)" "$ISSUES_KICK" "new" "$ISSUES_SESSION"

j=1
while [[ "$RESULT" != *"$ISSUES_DONE"* ]]; do
  j=$((j + 1))
  if [ "$MAX_ISSUES_ITERS" -ne 0 ] && [ "$j" -gt "$MAX_ISSUES_ITERS" ]; then
    echo "=== /prd-to-issues iteration cap ($MAX_ISSUES_ITERS) reached without $ISSUES_DONE." >&2
    manual_resume_hint "$ISSUES_SESSION"
    exit 1
  fi

  ISSUES_CONT="Continue /prd-to-issues. Accept your own recommendations and
finish writing any remaining issue files for the PRD at:

$PRD_PATH

When all issue files for this PRD are written, emit the literal sentinel as
the FINAL line of your message:

$ISSUES_DONE"

  run_iter "/prd-to-issues iteration $j" "$ISSUES_CONT" "resume" "$ISSUES_SESSION"
done

echo "=== Done. ==="
echo "    PRD:           $PRD_PATH"
echo "    Grill session: $GRILL_SESSION"
echo "    Issues session: $ISSUES_SESSION"
