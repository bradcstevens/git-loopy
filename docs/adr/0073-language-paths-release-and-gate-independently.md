# Language paths release and gate independently inside one compatibility family

**Status:** accepted

**Supersedes in part:** [ADR-0013](0013-multi-language-runner-family.md),
[ADR-0016](0016-single-distribution-release-version.md),
[ADR-0045](0045-a-conformance-distribution-is-obliged-by-a-stream.md), and
[ADR-0049](0049-every-conformance-fixture-is-claimed-or-waived-by-every-member.md).

Git-loopy remains one monorepo and one **Runner family**, but that family is a
compatibility family rather than one synchronized product. Python, shell,
PowerShell, and Rust are independent **Language paths**: each owns its Release
line and blocking **Path gate**, while the repository keeps one brand, Wrapper
contract series, Event schema, Conformance fixture set, and domain model.

Each path has a **Path manifest** declaring its Release authority, monotonic
Wrapper-contract and Event-schema support, members, single-owner artifacts, and
Path gate. Existing paths begin from a **Path baseline** that snapshots the last
shared family Release and the exact compatibility surface their current tests
prove. Python inherits the existing Release history and unqualified `vX.Y.Z`
namespace; other paths use path-qualified namespaces. A dormant path remains at
its last declared baseline until resumed and neither advances nor accrues debt
for another path's newer contract surface.

The Wrapper contract and Event schema remain shared versioned authorities.
Paths may support different versions, and interchangeability exists only within
their common declared surface. Every member of a path must claim or role-waive
every Conformance fixture inside that path's declared Contract support; fixtures
introduced after that support require no row and create no owed waiver. Support
only advances. Removing a compatibility claim requires an explicit retirement
decision rather than an ordinary manifest edit.

Integration runs the owning path's feedback loops. A shared-authority change
fans out to every Path gate whose declared support includes the affected surface,
while unrelated paths do not block one another. One published artifact belongs
to exactly one path; other paths consume a compatible release through the
relevant contract or schema instead of sharing its Release identity.

Every executable issue belongs to exactly one path through a `path:<key>` label
and may advance only that path's Release target. Missing path labels temporarily
mean Python during migration and are refused afterward. Work that deliberately
changes several paths is split into linked path-owned issues; shared-authority
fan-out changes no issue ownership.

This replaces ADR-0013's full-current-feature parity and synchronized family
delivery promises, while retaining its monorepo, shared-brand, shared-authority,
and anti-drift decisions. It replaces ADR-0016's one Release version for the
complete family, while retaining separate contract/schema compatibility
identities. It keeps ADR-0045's producer-versus-consumer fixture semantics but
scopes them to each path's declared support. It keeps ADR-0049's claim/waiver
vocabulary, mutation-test rigor, and no-silence rule, while replacing
every-fixture/every-member completeness with
every-supported-fixture/every-member-of-that-path.

The split is a prerequisite for Python `v1.0.0`. Until Path manifests,
Python-owned Release identity, support-scoped Conformance, and Path gates are
active, the repository still describes one family-wide distribution and cannot
truthfully publish a Python-only 1.0.0.
