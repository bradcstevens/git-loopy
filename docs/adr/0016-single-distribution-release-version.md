# Use one Release version for the complete distribution

> **Note (superseded in part by [ADR-0046](0046-continuation-is-decommissioned.md)):**
> This decision references Workflow Continuation, which has since been decommissioned.
> The reasoning below is preserved as the record of what was decided at the time.

> **Note (superseded in part by [ADR-0052](0052-the-release-line-advances-per-issue.md)):**
> This decision requires *edited* `docs/releases/v<VERSION>.md` notes. The requirement has
> since relaxed to *authored and committed*: an agent writes a `dev.N` fragment on every
> Release-line advance and composes a stable draft on Promotion, a human note present
> beforehand is preserved, and publication never waits on a person to write prose. The
> notes must still exist, be non-empty, and be committed at the tag. The reasoning below is
> preserved as the record of what was decided at the time.

> **Note (one sentence superseded by [ADR-0052](0052-the-release-line-advances-per-issue.md) and [#492](https://github.com/bradcstevens/git-loopy/issues/492)):**
> The sentence “Artifacts selected as one packaged distribution require exact
> Release-version equality” is superseded only for installed TUI helpers. Their
> installer resolves the newest published Release at or below the tree's
> declared Release version, then verifies the helper's identity against that
> resolved version. No other part of this ADR changes.


Git-loopy publishes one shared Semantic Versioning Release version for every
Orchestrator, packaged Skill set, and TUI helper included in a distribution,
with a root `VERSION` file as its source of truth. This gives operators one
product identity across languages and package channels; Wrapper, Event, and
Continuation versions remain independent compatibility identities because
release equality alone does not determine runtime interoperability.

Artifacts selected as one packaged distribution require exact Release-version equality. Externally
discovered TUI helpers negotiate Event-schema capabilities instead: a compatible helper from a
different Release may be used with a warning, while Release equality by itself never proves
compatibility.

Source publication starts only from a commit that explicitly changes `VERSION`, carries matching
package metadata and edited `docs/releases/v<VERSION>.md` notes, and is selected by an annotated
`v<VERSION>` tag. The tagged tree is archived and all three Orchestrator identity and Continuation
capability seams are verified after Runner-family Conformance and before GitHub creates the Release.
Stable versions become stable Releases; Semantic Versioning prereleases remain GitHub prereleases.
