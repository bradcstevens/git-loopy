# The terminal interface is not optional

**Status:** accepted

Settles what `git-loopy init` asks an operator and how. The starting complaint was narrow — the
setup wizard prompts by number while a keyboard-driven picker already ships — but the reason it
prompts by number is a packaging decision, so the narrow complaint could not be answered without
reopening that decision and everything it gates.

## What was already true

Three facts shaped every decision below, and two of them contradict how the problem was first
stated.

**The keyboard picker exists and `init` already uses it.** `SkillPickerApp`
(`interactive/skill_picker_app.py:121-129`) binds `up`/`down` to move, `space` to toggle, `enter`
to confirm. `init` reaches it through `skillscmd.collect_skill_policy` (`init.py:700-741`), which
resolves a renderer through `select_skill_picker` (`skillscmd.py:242-257`). An operator with the
`[tui]` extra installed has had arrow keys in `init` all along.

**The fallback is numbered, not the wizard.** `select_skill_picker` returns the Textual picker only
when stdout is a terminal *and* Textual imports; otherwise it returns `run_plain_skill_picker`
(`skillscmd.py:163-215`), which renders `1) [x] name` and asks the operator to type an ordinal. So
"numbers" was never `init`'s design. It was what a base installation got.

**Everything else in `init` is numbered unconditionally.** Scope, model, reasoning effort, the
routing action, and both yes/no questions go through `_choice` and `_ask_yes_no`
(`init.py:119-158`), which have no Textual renderer at any installation. `init` reimplements
model-and-effort selection as numbered prompts (`init.py:288-328`) while `ModelPickerApp` — a
tested two-stage Textual picker over the same `ModelChoice` rows — ships on the run-start path and
is never called by `init`.

## The decisions

**Textual becomes a base dependency; the `[tui]` extra is removed.** The optional extra bought a
lighter install and cost a second interactive UI to build, test, and keep in agreement with the
first. It also silently decided which operators got which experience. Since the point of this work
is that keyboard interaction is the normal path, an extra that withholds it from the default
installation defeats it.

The version cap `textual>=0.86,<6` becomes structural rather than incidental. Textual 7.0.0 raised
its floor to `rich>=14.2.0`; the base install pins `rich>=13.7.0,<14` for the run renderer's
panels and tables. Textual 6 and below require only `rich>=13.3.3`, so today's pins already agree
and no `rich` bump is owed. Crossing to Textual 7 forces a `rich` major bump on the base install,
which is now a decision about the run renderer and not only about the wizard.

**The numbered renderers are deleted rather than kept as a fallback.** `_choice`, `_ask_yes_no`,
`run_plain_skill_picker`, and the `textual_importable` arm of `select_skill_picker` all go. Their
only remaining justification was a terminal that cannot render, and `--yes` already owns that case
(`cli.py:826-831`, `init.py:33`) by persisting a deliberate **Minimal Skill policy**.

`--yes` is not merely an adequate substitute; it is a better contract. A piped prompt chain answers
by ordinal position, and ordinals move when the model roster changes, so a script that pipes
`1\n2\ny` into `init` silently selects something else the day a model is added. Refusing is more
honest than answering wrongly. `git-loopy init` on a non-TTY without `--yes` therefore fails with
an error naming `--yes`.

**`init` becomes one continuous wizard rather than a sequence of prompts.** A single Textual flow
walks scope, model, effort, routing, scaffold, and Skill policy, with backward navigation between
steps. The alternative — calling several one-shot apps in series — seizes and releases the alt
screen once per question and cannot go backward.

Backward navigation is the whole point. `init` asks for scope first and Skill policy last, and the
ordering is deliberate: the policy must answer to the Run instructions the scaffold decision leaves
behind (`init.py:719`). A wrong answer to the first question currently costs the entire wizard.

**The wizard ends on a review screen, and nothing is printed after it.** The last step lists scope,
config path, model, effort, routing, scaffold, and the count of enabled Skills, over `Save`,
`Back`, and `Cancel`. This is the terminus backward navigation moves back *from*, and it is the
only place the collect-then-commit guarantee (`init.py:26-32`) becomes visible instead of remaining
a comment in the source. A fullscreen app erases itself on exit, so a wizard with no review screen
would leave an operator no record of what was written; a scrollback receipt was considered for that
role and rejected as noise.

**Pickers become Screens with thin App hosts.** `ModelPickerApp` and `SkillPickerApp` are Screens
composed into the wizard, each retaining a small App wrapper so `skills edit` and the run-start
model picker keep working standalone. The wizard Screen gains a second host: on the auto-init path
(`cli.py:2237`) the Dashboard pushes it as its first screen, so a first run is one continuous
session rather than a fullscreen wizard collapsing into a fullscreen Dashboard.

The wizard Screen shares the App shell and nothing else. It must not read `LiveRunState`
(`interactive/state.py`), which is what keeps "the Dashboard can host the wizard" from becoming
"setup depends on the run Dashboard."

**UI state lives in the app; domain models stay headless.** There is no wizard model mirroring the
widget tree. Step order, reachability, cursor, and query state live in the Textual app and are
tested with `run_test()`.

The seam that survives is a different kind. `SkillSelectionModel` (`skillscmd.py:56-91`) is not a
widget model — it is the Skill policy's domain model, and `validation_errors` is where the
Required-Skill and tracked-project-skill rules live. `collect_skill_policy` and the `--yes` path
consume it with no UI present, so folding it into a Screen would duplicate policy validation
between the wizard and `--yes`, where disagreement means a wrong policy on disk rather than a wrong
rendering. `ModelChoice` and `to_model_choices` stay headless for the same reason.

The justification for the split is now testability alone. The original one — stated at
`skill_picker_app.py:6-10`, that "the two pickers cannot disagree about what a valid saved policy
is" — expired with the second picker.

**The run Dashboard becomes the default on a terminal, and this is announced rather than
incidental.** `resolve_interactive` (`interactive/detect.py:59-89`) gates the interactive path on
flag, then environment, then TTY — and additionally on Textual being importable. Making Textual a
base dependency makes that last term unconditionally true, so every operator on a terminal begins
getting the Dashboard instead of what the module calls "today's byte-for-byte line-printer
behavior."

Decoupling was considered: replace the vanished term so TTY alone keeps the line printer and the
Dashboard needs an explicit flag. It was rejected because it preserves the default by penalising
the operators who installed `[tui]` deliberately to get the Dashboard. The flip is accepted, and
accepted openly: it ships as a named change with `--no-interactive` and `GIT_LOOPY_INTERACTIVE=0`
documented as the way back, rather than arriving as a side effect of a dependency edit.

**`esc` goes back, and cancels where there is nowhere back to.** `ctrl+c` always cancels outright.
In `skills edit`, which has no previous step, `esc` cancels — identical to today
(`skill_picker_app.py:128-129`). One sentence governs both hosts of the same Screen.

The picker types to search, so letters are unavailable, and `left`/`right` move the cursor inside
the search input; back had to be `esc` or a chord. A chord nobody discovers without reading the
footer is a poor home for the most-used new action. The cost is that an operator pressing `esc` to
abandon setup steps backward instead, which is non-destructive: collect-then-commit means nothing
was written either way, and the review screen carries an explicit `Cancel`.

## Consequences

The change is Python-only. The shell and PowerShell members are Orchestrators with no `init`
command, and `conformance/skill-policy.json` pins policy resolution semantics — startup states,
resolution cases, event payloads — not picker presentation. No fixture moves.

Eight test modules gate on `pytest.importorskip("textual")` so a base installation could skip them.
Those gates become dead. Assertions that pin the numbered rendering — `test_init.py:231` ("the
plain-text numbered list was rendered (no [tui])") and the renderer-selection tests at
`test_skills_cmd.py:154-177` — assert behavior that will no longer exist, and go with it.

`resolve_interactive` loses its `textual_importable` parameter and `detect.textual_available`
loses its caller, collapsing the documented precedence to flag, environment, TTY.

The one-time Skill policy migration picker (`cli.py:2047-2066`, issue #230) inherits the Screen and
needs no separate decision. It is already TTY-gated and already falls back to the Minimal Skill
policy without persisting it.
