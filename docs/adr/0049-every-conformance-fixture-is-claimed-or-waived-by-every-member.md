# Every Conformance fixture is claimed or waived by every member

**Status:** accepted

Decided by [#466](https://github.com/bradcstevens/git-loopy/issues/466).

Every fixture in `git-loopy/conformance/` is **accounted for** by every member of the **Runner
family** — the Python reference **Orchestrator**, the shell and PowerShell Orchestrators, and the
Rust **Dashboard** core. Each (fixture, member) pair carries exactly one verdict: the member
**claims** the fixture and exercises it, or it **waives** the fixture with a named reason. Silence
is not a third verdict, and an empty cell is a failure rather than a gap in the paperwork.

No accounting data and no enforcement land here. What lands is the decision, the vocabulary, and
the manifest's declared shape, so the audit that follows implements one decision rather than four
compatible guesses. No member's behaviour changes.

## The hole this closes

No member globs `git-loopy/conformance/`. Python, shell, PowerShell and the Dashboard each name
the fixtures they read, one filename at a time, so **a fixture nobody implements passes silently**.
The single glob that exists —
`test_no_fixture_claims_a_contract_version_the_contract_has_not_reached` — reads every fixture's
`contract_version` and fails one claiming a version *ahead* of the written contract. It has never
been able to fail one that nobody implements, because it never asks who implements anything.

[ADR-0047](0047-a-blocked-issue-is-not-pickup-admissible.md) recorded the hole as a consequence of
its own landing: `issue-readiness.json` is "loaded by the Python conformance suite. The shell port,
the PowerShell port and the Dashboard do not load it and will not fail for ignoring it — the
Conformance suite has no completeness check". It closed nothing, deliberately. This is that
closure.

## What a claim is

A **Fixture claim** is a fact about *exercise*, not about a mention. A member claims a fixture when
its own automated suite reads that fixture's bytes while the suite runs, and asserts against values
taken out of those bytes, with the compared-against value produced by the member's production
decision seam. All three halves are required:

1. **The bytes are an input.** The member's test code opens the file by path or embeds it
   (`include_str!`), and the file is read on a run of that member's suite — the run `AGENTS.md`
   declares for that member, on the platforms that run declares. A test that only ever executes
   under a platform guard the Integration loop skips supports no claim; it is an owed waiver until
   the loop runs it.
2. **The fixture is the oracle.** At least one assertion takes its *expected* value out of the
   parsed fixture, rather than out of a literal a person transcribed from it.
3. **Production code answers.** The *actual* value comes from the seam the member ships, not from
   logic reimplemented inside the test and not from the fixture itself.

The **mutation test**, which is what two implementers apply to a borderline reference: **mutate an
expected field that one of the member's asserted cases reads, and that member's suite goes red.**
Not any field anywhere in the file — a fixture may pin fifty cases where a member owes three, and
mutating one of the other forty-seven proves nothing about either. Pick a field the member's own
asserted case consumes; if the suite still passes, the member does not claim the fixture, whatever
its source tree says about it. Every rule below is that test applied.

- **Prose is not a claim.** A filename in a README, a doc comment, a `Cargo.toml` comment or a
  commit message names a fixture without reading it. `git-loopy/tui/README.md` links
  `homebrew-tap.json`, `tui-artifacts.json` and `windows-channels.json`; no Rust test opens any of
  the three, and editing all three changes nothing about whether `cargo test` passes.
- **A runtime read is claimed by the test that drives it, not by the reader.**
  `git-loopy/shell/install.sh` resolves `tui-artifacts.json` at install time. That line is
  production code, and production code is not self-asserting. The claim belongs to
  `shell/tests/test-tui-install.sh`, which drives the installer over that fixture; a member whose
  production code reads a fixture that no test of that member exercises has not claimed it.
- **Transcription is not a claim.** Copying a fixture's numbers into test literals ends the
  fixture's authority at the moment of copying. The two then drift silently, which is the failure
  the suite exists to prevent, wearing the suite's own clothes.
- **Parsing is not exercising.** Loading a fixture and asserting only that it is valid JSON, or
  that its `schema_version` or `contract_version` is what it is, exercises no contract decision.
  A member cannot claim a fixture by proving the file exists.
- **Partial exercise is a full claim.** Claiming is per fixture, per member — never per case. A
  fixture already says which cases a member owes, through its own `distributions` selectors and the
  role-shaped obligation [ADR-0045](0045-a-conformance-distribution-is-obliged-by-a-stream.md)
  gave that axis, where a consumer folds a stream that a producer serializes. One qualifying case
  is a claim: a member that exercises every case its role permits claims the fixture, and a member
  that exercises one case of fifty *also* claims it. How much of a claimed fixture a member covers
  is a separate question with a separate answer, and it is not what this register records.
- **Shared helpers count.** Where the read happens — a test file, a helper module, a common
  adapter — is not the question. Whether the member's suite fails when its own asserted case's
  expected value changes is.

## Two waivers, and only one of them is owed

A waiver is not one thing. Two facts wear the same word and only one of them is debt, so they are
named apart.

A **Permanent waiver** says the fixture is not this member's business. The fixture pins a decision
the member has no path for and is not going to grow one: `homebrew-tap.json` for an Orchestrator
that ships no packaging channel, `discriminator.json` for a Dashboard core that never reads an
issue body. It carries a reason and nothing else, because there is nothing to fix and no future in
which it changes.

An **Owed waiver** says the member should exercise this fixture and does not. It is a gap, and its
entry MUST carry a **tracking issue** alongside its reason. The issue is the whole mechanism: the
register is where the debt is *recorded*, and the tracker is the only place it is ever *scheduled*.
An owed waiver without an issue is the silence this ADR exists to end, re-created one line lower
down.

The kind is a judgement about **role**, never about effort or appetite. "Nobody has got to it",
"it would be slow", and "the port is young" are reasons for an owed waiver and are never a
permanent one. Relabelling a gap as out-of-scope is the one move that defeats the whole mechanism,
which is why the two kinds are separate literals in the data rather than two shades of one reason
string a reader has to interpret.

"Not going to grow one" is a forecast, so it needs an authority rather than a reviewer's intuition.
The tie-break: **a permanent waiver is available only where the written contract or an accepted ADR
puts that responsibility outside the member's role.** §13's decision list, §12's per-role
obligations and ADR-0045's producer/consumer split are that kind of authority; a reviewer's sense
that a port will probably never do packaging is not. Anything the current contract *assigns* to a
member, and anything an accepted decision says the member owes but has not yet implemented, is an
owed waiver — including work nobody has scheduled, which is precisely the case the tracking issue
exists for. Where the contract is silent, the entry is owed until a decision makes it permanent:
the direction of doubt runs toward visible debt, because that is the error a later reader can
correct.

## Why a silent gap is a failure, not an absence

An absence is something you can see. This is not one.

The **Conformance suite** does not assert that a decision is written down; it asserts that the
family *agrees* on it. Green means "every member was asked this question and gave the same answer".
A fixture one member reads and three ignore produces exactly the same green, from a suite that
asked one member — so the signal an operator, a reviewer and an Integration gate all read as
family-wide agreement is, for that decision, one implementation's opinion of itself. The report is
not incomplete. It is wrong, and it is confidently wrong.

The **Wrapper contract** already states a promise this breaks: "No Orchestrator lands a contract
change alone — the Conformance suite fails any port left behind, which is the whole point of the
backbone" (§17). Today a new fixture only Python reads leaves every other port behind and fails
nothing, and nothing anywhere reports that it did. A contract change can land with a fixture, one
implementation, and four green suites. This decision does not make that sentence literally true —
no register compels a port to implement anything, and a fixture may legitimately land owed by three
members. What it removes is the *silent* case: a port left behind is now recorded as owing the
fixture, by name, with an issue, instead of passing green for never having been asked.

That is the difference between a failure and an absence. An unimplemented feature is visible in its
absence: the thing does not work, someone notices. An unclaimed fixture is invisible *by
construction* — the mechanism built to notice is the mechanism that did not look. So it is a
**Conformance failure**: the suite made a claim about the family that was not true, which is the
one thing a conformance suite must never do.

## The Python suite is the only reader

The Python suite enforces the manifest. The shell and PowerShell Orchestrators and the Rust
Dashboard core **do not read it at all**, and are not required to.

`AGENTS.md` makes the Python suite the Integration gate: every loop in its table runs over the
merged worktree, fail-fast, on every Lane merge. A check there is a check the whole family passes
through, so a second, third and fourth implementation of it buys no coverage. It buys the specific
risk this ADR was opened over — one decision implemented four ways, each subtly its own — for a
check whose only job is to notice when the four have diverged.

The stronger reason is that the manifest is not a fixture in the sense the others are. Every other
fixture pins a decision a member *executes*: given this issue body, exclude it; given this Event,
serialize these bytes. The manifest states a fact about the repository's test tree — who exercises
what — which is one static property of one tree. One reader is the cheapest way to have exactly one
answer to it; four readers is four parsers, four notions of a well-formed entry, and a new way for
the family to disagree about the file that exists to record the family's agreement.

What the single reader does *not* buy is honesty about a claim, and saying otherwise would be the
same overclaim this ADR is written against. Python is itself a member, so it checks its own row like
any other, and the check validates completeness and well-formedness rather than truth. The only
stated detector of a false `claimed` entry is a reviewer applying the mutation test above. The
`AGENTS.md` loops run the exercises that *exist*; they cannot notice one that was deleted, because a
member that stops reading a fixture goes green rather than red — which is the original hole,
narrowed to a single row someone has to edit rather than left open across the whole directory. The
register's job is to make sure the question was asked of every member, and to name where each
answer can be checked.

The cost is accepted and named: on a machine with no Python, running `bash tests/test-*.sh` alone
proves conformance for the cases that member claims and proves nothing about completeness. That is
the same shape as every other repository-wide invariant here, and the Integration gate is where
repository-wide invariants are settled.

The manifest carries **no entry for itself**, because it is not a fixture. It pins no contract
decision and no member executes it; it is the register *about* the fixtures, which happens to be
JSON and happens to live beside them. So the fixture universe the rule applies to is
`git-loopy/conformance/*.json` **minus `fixture-claims.json`**, and the exclusion is stated here
rather than expressed in the data — a row in which a register accounts for its own readership is
self-reference nobody learns anything from, and the alternative of omitting it in silence is the
exact thing this ADR forbids.

## The manifest, declared

Shape only. No data lands with this ADR.

- **Name and location:** `git-loopy/conformance/fixture-claims.json`. It is Conformance *data*
  without being a Conformance *fixture*, and it lives with the fixtures it accounts for; a register
  kept anywhere else is a register that drifts from the directory it describes.
- **`schema_version`:** `1`, as every fixture carries.
- **`contract_version`:** `"2.0"` — a **provenance stamp**, recording the **Wrapper contract**
  version at which this manifest's decision last changed. It is not a compatibility declaration: no
  member gates its read on it, and it does not narrow the claims below it to a contract range. It
  behaves exactly as every other fixture's stamp does, which also means the existing glob covers it
  for free the moment it lands.
- **`members`:** the closed, ordered set of family member identifiers — `python`, `shell`,
  `powershell`, `rust`. The first three are the identifiers the fixtures' own `distributions`
  selectors already use; `rust` is the identifier ADR-0045 named for the Dashboard core when it put
  a consumer on a fixture axis. A fifth member is added here and nowhere else.
- **`fixtures`:** an object keyed by fixture **file name** (`"issue-readiness.json"`), each value an
  object keyed by member identifier, with one entry per member of `members`.

Each entry carries:

| Field | Required | Meaning |
| --- | --- | --- |
| `kind` | always | One of `claimed`, `waived_out_of_scope`, `waived_owed`. A closed set — an unknown kind is a failure, never a default. |
| `reason` | always | Why this verdict. For a claim, it names where the exercise lives, so a reviewer can go and apply the mutation test. For a permanent waiver, it names the role the member does not have. For an owed waiver, it names what is missing. |
| `issue` | `waived_owed` only | The tracking issue the debt is scheduled on. MUST be absent for the other two kinds: an issue on a permanent waiver is a promise nobody intends to keep. |

`reason` is required on a claim as well as on a waiver, because the manifest's job is to be read by
someone deciding whether a claim is still true, and "claimed" alone tells them nothing about where
to look.

## It lands green, and what a new fixture owes

The manifest's first commit records **today's truth**: every claim that already exists is recorded
as a claim, and every gap is recorded as an owed waiver carrying a tracking issue. It is green on
the day it lands and red the first time a fixture arrives unaccounted for. It is a **ratchet**, not
an audit finding.

The rule a newly added fixture must satisfy, stated so the check can enforce it: **no tree is green
that contains a fixture without a verdict for every member of `members`.** In practice that means
the verdicts land in the change that adds the fixture — not "before the next release", not "in a
follow-up" — and the check enforces it by refusing the *state* rather than by inspecting the diff.
The distinction matters, because a state check cannot see provenance: it cannot tell one commit
from two, only an accounted tree from an unaccounted one. Landing the fixture and its verdicts
together is what makes the rule cheap to satisfy — authoring a fixture is the moment the family's
obligations to it are clearest — but what is actually gated is the merged result. An added fixture
whose members are all owed waivers is perfectly legal and perfectly visible, which is the entire
point: the decision the check forces is not "implement it now" but "say who owes it".

Enforcement, when it lands, checks **completeness and well-formedness**: every fixture on disk has
an entry, every entry names every member, every `kind` is in the closed set, every `waived_owed`
carries an issue and no other kind does, and no entry names a fixture that does not exist. It does
**not** try to prove that a claim is true — proving exercise means running four suites in four
toolchains, which is Integration's job rather than a unit test's, and a check that guessed at truth
from a grep would fail the very rule this ADR wrote about mentions. Claim truth is a reviewer's
judgement, and the mutation test above is what they apply.

Movement is deliberate in both directions. An entry moving from `waived_owed` to `claimed` is debt
being paid and needs no ceremony. A `claimed` entry moving back to either waiver is a member
dropping an exercise it had, so it is an edit a reviewer sees, with a reason and — where it is a
gap rather than a role change — an issue. The one move the shape forbids outright is the quiet one:
deleting the row.

## What the tree actually says today

Verified against `main` at `e79bfdc`. There are **21 fixtures**.

- **Python references all 21.** Its suite is the only place the whole directory is spoken for.
- **10 of 21 are named by neither the shell nor the PowerShell Orchestrator** —
  `attempt-lifecycle`, `calibration-search`, `effort-gate`, `homebrew-tap`, `issue-readiness`,
  `model-roster`, `release-trust`, `routing-resolution`, `skill-consultation`, `windows-channels`.
  The two ports name the same 11 as each other, exactly. #466's count is correct.
- **The Rust figure needed correcting, and the correction is this ADR's definition doing its job.**
  Four fixture names appear anywhere under `git-loopy/tui/` — leaving the 17 #466 cites — but three
  of the four are prose: `homebrew-tap.json` and `windows-channels.json` are README links, and
  `tui-artifacts.json` is a README link and a `Cargo.toml` comment. Exactly **one** fixture,
  `dashboard-insights.json`, is read by a Rust test, embedded with `include_str!` across nine files
  under `git-loopy/tui/tests/`. So the Dashboard core claims **1 of 21**, and **20 of 21** are
  exercised by no Rust Dashboard test.

The gap between 17 and 20 is small and it is the whole argument. Counting mentions credits a member
with three fixtures it has never read, and a register built by counting mentions would have landed
those three as claims — recording, permanently and in the file built to prevent it, that a
documentation link is conformance. Whoever fills the manifest in fills it in against the mutation
test, not against a grep.

All suites are green, and were green through every count above.

## Considered and rejected

- **Glob the directory independently in each member.** Every member enumerates
  `git-loopy/conformance/` and fails on a fixture it does not handle. Symmetrical, needs no new
  file, and each member polices itself. Rejected on both halves. A member globbing its own tree can
  only ever discover fixtures it does not read, which is the easy half; it cannot distinguish "not
  my business" from "my debt", so it must carry a per-member skip list, and four skip lists in four
  languages is four registers that disagree — the drift the Conformance backbone exists to prevent,
  reproduced inside the mechanism meant to detect it. And it leaves no *single* register: each skip
  list is an artifact, but it is read only by the member it excuses, so "what does the family owe?"
  has four answers in four languages and no place that reconciles them.

- **Leave the gap as it is.** It costs nothing, every suite is green, and the family has shipped
  this way from the start. Rejected because green is precisely the problem. The suite is the
  family's only evidence that four implementations agree, `AGENTS.md` makes it the Integration gate,
  and §17 tells readers in plain words that no port can be left behind. A mechanism that reports
  agreement it never checked is worse than no mechanism, because a missing check gets built and a
  false one gets trusted. It also gets steadily worse on its own: ADR-0047 landed a fixture three
  members ignore, and the next contract decision will land another.

- **Fail red on today's unclaimed fixtures.** Land the completeness check with no waivers, let the
  suite go red, and pay the debt down until it is green. Honest, and it removes any chance the
  waiver list becomes a place gaps go to be forgotten. Rejected on blast radius: the Python suite
  is a **blocking Integration gate**, so a red landing turns *every* Lane merge red until unrelated
  debt in three other languages is paid — every change in the repository blocked on 10 shell
  adapters, 10 PowerShell adapters and 20 Rust tests nobody asked for. A gate that is red for
  reasons unrelated to the change under it stops being read, and the pressure to make it green
  again would be paid in the cheapest currency available, which is fake claims. The ratchet gets
  the same end state and keeps the gate meaningful the whole way there.

- **Record each fixture's members inside the fixture itself**, extending the `distributions` axis
  from "which cases does this member owe" to "does this member owe this fixture at all". No new
  file, and the fact would live next to the decision it is about. Rejected because it overloads an
  axis that answers a different question — `distributions` selects cases *for members that read the
  fixture*, and a member that does not read the fixture cannot be told anything by a key inside it.
  It also scatters one register across 21 files, so "what does the shell port owe?" becomes 21
  reads and no single artifact anyone can review.

- **Derive the register by grep** — walk each member's tree for fixture filenames and require full
  coverage, with no hand-maintained data. Tempting, because it cannot go stale. Rejected because it
  measures mentions, and this ADR exists to say that a mention is not a claim. Against today's tree
  it would credit the Dashboard core with three fixtures it has never opened, and it can never see
  the distinction the whole decision turns on: out-of-scope and not-yet-done look identical to a
  grep, and only one of them is debt.

## Consequences

- **Nothing changes yet.** The Python, shell and PowerShell suites and the Rust Dashboard core are
  green unchanged. The decision, the vocabulary and the contract obligation land here; the manifest
  and its check land next.
- **`CONTEXT.md` gains three names** — **Fixture claim**, **Permanent waiver** and **Owed waiver** —
  each fenced against the others and all three against a **Pool exclusion**, which is a human's
  authoring mistake in an issue and shares nothing with them but the shape of the sentence.
- **`Lease`'s `_Avoid_` line is narrowed.** It told readers to avoid "claim", which was written when
  the word had one job here. It now avoids the *unqualified* word and points at the qualified one.
- **The written contract is not bumped.** §13 gains the obligation at contract **2.0**, the current
  version. The obligation constrains the *suite*, not an Orchestrator's runtime behaviour, and
  `test_bumping_the_contract_requires_bumping_an_affected_fixture` requires the written version to
  be pinned by some fixture — so a version bump in a ticket that lands no fixture data would fail
  the gate this ADR was opened to strengthen. The manifest lands at 2.0 with it.
- **Filling the manifest in will produce tracking issues.** Every owed waiver needs one before the
  data can land, so the register's first commit is also the moment the family's fixture debt becomes
  a countable list of issues instead of an anecdote.
- **The Dashboard core's real coverage is now on the record.** One fixture of 21, not four. Whatever
  the manifest decides about the other 20 — and most of them are plainly out of scope for a
  renderer — it will decide in writing.
