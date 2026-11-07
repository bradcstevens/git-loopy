# Chain hooks

`.github/hooks/git-loopy-chain.json` registers two hooks for `/next`, both
through the repository-relative resolver `.github/hooks/git-loopy-chain.sh`.
`subagentStop` runs `chain.sh complete`, closing the finished chain hop in the
per-clone `.git-loopy/` ledger. `agentStop` runs `reenter`
(`.github/hooks/git-loopy-agent-stop.py`), which holds the parent agent open
with "Run /next now" while a completed hop is still unrouted, and gives up on a
hop after three unconfirmed requests.

The resolver finds the `/next` script at
`${COPILOT_HOME:-$HOME/.copilot}/skills/next/scripts/chain.sh`. If it is
missing, the resolver reports `no chain.sh found` on stderr and exits `0`,
rather than failing an agent that has nothing to do with the chain.
`.github/hooks/git-loopy-hook-log.sh` appends every invocation, including that
stand-down, to `.git-loopy/hook-invocations.jsonl`, so a chain that closes
nothing still leaves a record. Copilot runs repository hooks only in trusted
folders. All four files are written by `/setup-git-loopy-skills`; the
`agentStop` helper is a copy of the one bundled with that Skill, so re-run it
rather than editing the copy.
