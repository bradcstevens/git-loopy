# Jev usage contract: typed decisions are not a correctness boundary

**Research cutoff:** September 21, 2026. Public documentation and live OpenAPI inspected on that date; SDK source revisions pinned below.

**Research ticket:** [#621](https://github.com/bradcstevens/git-loopy/issues/621). **Parent map:** [#619](https://github.com/bradcstevens/git-loopy/issues/619).

**Scope:** The practical complement to [the existing Jev due-diligence report](typesafe-ai-s-model-jev.md), not a repeat of its credibility, benchmark, pricing, or legal investigation. No authenticated requests, inference, credentials, package installations, or benchmark reruns were used.

**Evidence labels:** **Documented** means a public first-party statement or schema. **Source-verified** means behavior found in the pinned client/adapter implementation and, where cited, its tests; those tests were read, not executed here. **Derived** means arithmetic or an integration implication, not a provider guarantee. **Not established** identifies an actual documentation gap.

## 1. Findings that matter most for binding classification

1. **Untrusted issue bodies can steer the decision, not just supply evidence.** The Jev 1.13 limitations explicitly include injected instructions, misleading framing, and text arguing for its own classification. The RAG cookbook goes further: its injection detector is only a filter, and explicitly not a security boundary. Precise criteria and adversarial testing are documented mitigations, not immunity. **Derived:** a correctly shaped, high-confidence answer cannot by itself authorize a binding task-type, Bump-class, or Route selection from attacker-influenceable text.[^jag-adversarial][^rag]
2. **`confidence` is distribution shape, not P(correct), and the exact production formula is not established.** The public adapter's Choice formula depends only on the largest normalized probability and option count; changing the number of options changes the meaning of a fixed threshold. The documentation explorer calls its formula an approximation. In the classification cookbook, a `0.9` confidence cutoff still accepts three wrong narrow labels among 30 accepted cases. That older, selected-domain experiment is not an acceptable-error guarantee for git-loopy.[^confidence][^adapter-math][^classification]
3. **The problematic features of real issues are named limitations, not hypothetical corner cases.** Literal negation, double negatives, multi-hop references, conflicting instructions/criteria, and irrelevant long context all affect reliability. Fitting under 32k/64k is an admissibility limit, not a quality guarantee. Separate questions can disagree even about a proposition and its apparent negation. **Derived:** adoption needs task-specific evidence on messy issues and deterministic handling of inconsistent or unsupported answers; batching and thresholding do not remove that requirement.[^jag][^models]

The useful opportunity is narrower: ask several **independently decidable** classifications about the same carefully selected issue state in one request, pay for that state once, and compose the results in code. If Route selection actually consumes the newly inferred task type or Bump class, that dependency must be implemented in code, through speculative precomputed branches, or with a second call; naming three questions in one request does not create a pipeline.[^primitives][^parallel]

## 2. Snapshot, versions, and changes since the existing report

The current Models page still identifies `jev-1.13.0`, with both `jev-latest` and `jev-preview` pointing to it. The limitations page still says it applies to `jev-1.13` and was last reviewed **2026-09-17**. Both inconsistencies flagged in the existing report remain: instructions are required in the HTTP guide but optional in OpenAPI, and Score minimum sizes differ across surfaces.[^models][^jag][^api][^openapi]

| Public source | Revision observed | Source-declared version | Comparison with prior report |
| --- | --- | --- | --- |
| `typesafe-ai/typesafe-sdk-python` | `0ffd094c72ed9445223060b24ffd7a56aa781fb4` | `typesafe-sdk` **0.7.1** | Same commit |
| `typesafe-ai/typesafe-sdk-js` | `66880ccded6cb642dc1809620c2b108c33730214` | `@typesafe-ai/sdk` **0.6.0** | Same commit |
| `typesafe-ai/system-one-adapter-python` | `adffc2eab300a4fa3c0e92252d4ffd6ceaa53700` | `system-one-adapter` **0.2.0** | Same commit |

These versions come from source manifests, not package-registry queries or an installed environment.[^versions] The live specification declares **OpenAPI 3.1.0**, API `info.version: "0.2.0"`; that is not the Jev model version. Its downloaded bytes had SHA-256 `a191f8a7df6bd6fedced8120dd0fd106f88575d1d1c8360d08900a6c7c0360d5`.[^openapi]

**Dating limit:** most prose pages are undated. New detail in this report is not evidence that the documentation changed after the earlier September 21 snapshot. No changed SDK revision or model alias was found. Additional present-day contradictions are recorded below, but their introduction dates cannot be established from these pages.[^index][^versions][^models]

## 3. Full HTTP request contract

### 3.1 Endpoint and envelope

**Documented:** `POST https://api.typesafe.ai/v1/systemone`, JSON request/response. Send `Content-Type: application/json` and the API key in the `Authorization` header using the OpenAPI `HTTPBearer` authentication scheme. This research fetched only the public OpenAPI document, not the authenticated evaluation or model-list endpoints.[^api][^openapi]

| Field | Live OpenAPI shape and requirement | Semantics / constraints |
| --- | --- | --- |
| `state` | **Required**: string, object, or array; not a bare number, boolean, or `null` | One shared state for every question. Objects allow arbitrary JSON properties; array items have no further schema restriction. Nested numbers/booleans/null are therefore different from an unsupported top-level scalar. |
| `model` | **Required** string | Model name or alias. No schema enum, name pattern, or default. SDK defaults are conveniences, not an HTTP default. |
| `questions` | **Required** object, `minProperties: 1` | Maps caller-chosen IDs to discriminated Question objects. No published schema maximum question count, ID length, or ID pattern. |
| `questions.<id>.type` | **Required** constant: `choice`, `score`, or `noul` | Selects the question schema through the `type` discriminator. |
| `questions.<id>.instructions` | **Optional** in all three live schemas: string, object, array, or `null` | The HTTP guide instead marks it required and lists string/object/array. Supply explicit instructions for portability and clarity. |
| `questions.<id>.criteria` | Type-dependent; see below | Required for Choice and Score, optional for Noul. |

The table's machine constraints are the live `SystemOneRequest`, `Question`, and individual Question schemas.[^openapi] The question ID is **not sent to the underlying model**; an ID such as `is_breaking_change` does not replace the instruction. Choice option names **are** sent, together with their descriptions. Use names for programmatic lookup and full instructions for the judgment.[^api][^choice]

The documented request has no first-class `messages`, chat roles, `temperature`, `seed`, reasoning controls, streaming switch, tools, output-token limit, or `demonstrations` field. OpenAPI does not close these objects with `additionalProperties: false`; that omission does **not** document behavior for invented fields. SDK passthrough of extras likewise does not establish server support.[^openapi][^sdk-extras]

This complete **illustrative request body** uses all three primitives and the conservative common subset of the contract. It was not sent; the examples do not imply a particular prediction.[^api][^openapi][^models]

```json
{
  "model": "jev-1.13.0",
  "state": {
    "message": "My payouts have failed for three days. Please fix this today."
  },
  "questions": {
    "department": {
      "type": "choice",
      "instructions": "Which team should handle `message`?",
      "criteria": {
        "billing": "Payments, invoices, or refunds",
        "technical": "Bugs, outages, or integrations",
        "sales": "Pricing or new accounts"
      }
    },
    "frustration": {
      "type": "score",
      "instructions": "How frustrated is the author of `message`?",
      "criteria": ["Calm", "Frustrated", "Very angry"]
    },
    "is_urgent": {
      "type": "noul",
      "instructions": "Does `message` express urgency?",
      "criteria": {
        "true": "An explicitly time-sensitive request",
        "false": "No urgency expressed"
      }
    }
  }
}
```

### 3.2 Choice

**Documented purpose:** choose one member of a finite set, such as a category or handler.[^choice]

| Field | Contract |
| --- | --- |
| `type` | Required literal `"choice"`. |
| `instructions` | Decision instruction; required by prose, optional/nullable in live OpenAPI. |
| `criteria` | Required **object**, mapping option names to string/object/array/`null` descriptions. `null` means interpret that option by its name alone. |
| Option count | HTTP guide and Choice page: **maximum 255**. Live schema has neither `maxProperties: 255` nor a minimum option count. Absence of a schema minimum does not prove useful zero- or one-option server behavior. |
| Option names | String keys. No documented name regex/length limit. They carry meaning, unlike question IDs. |

Use the full relevant candidate set where practical, with `other` or `none of the above` when it is not exhaustive. A Choice remains relative to its supplied options; it cannot invent an absent category. Use hierarchy traversal/beam search for a taxonomy that does not fit a single question.[^choice][^openapi]

The older classification cookbook separately says Choice works reliably up to **roughly 240 options**. That is qualitative guidance in a `jev-1.12` example, not a different formal API ceiling or a measured reliability guarantee at 255 options.[^classification][^choice]

**SDK compatibility:** neither current typed SDK accepts a list of labels as the supported Choice shape. Supply `{"label_a": null, "label_b": null}`, not `["label_a", "label_b"]`. Python typed construction rejects the list; its raw-dictionary path can forward it without making it valid. The JavaScript helper explicitly rejects the removed list shorthand.[^sdk-choice]

### 3.3 Score

**Documented purpose:** position on an ordered, descriptive rubric, not measurement of an arbitrary number.[^score]

| Field | Contract |
| --- | --- |
| `type` | Required literal `"score"`. |
| `instructions` | What to rate; the same required/optional discrepancy applies. |
| `criteria` | Required ordered **array** of string/object/array level descriptions in live OpenAPI. An individual `null` level is not in that schema. |
| Minimum size | Live OpenAPI: **1** (`minItems: 1`). HTTP and Score guides: **should have at least 2**. Python send-time validation permits one; JavaScript types and send-time validation require at least two. |
| Maximum size | HTTP and Score guides: **10**. No `maxItems: 10` in live OpenAPI or corresponding maximum in the reviewed SDK validators. |
| Scale | Array positions are levels `0..n-1`; no `min`, `max`, or arbitrary numeric scale parameter. |

Each level is judged from its description. The Score page says the model does **not** see that level's numerical position or neighboring levels. Consequently, descriptions such as "worse than the previous level" or criteria consisting only of `"0"`, `"1"`, `"2"` do not supply a meaningful rubric. Describe each situation independently, keep a Score to one dimension, and combine multiple dimensions in code.[^score][^openapi][^sdk-score]

The returned scalar is the expectation:

```text
score = sum(i * probabilities[str(i)] for i in 0..n-1)
```

It lies on the zero-based level scale, may be fractional, and is not necessarily the modal level. The same mean can hide very different distributions: all mass on level 1 and half on each of levels 0 and 2 both have mean 1. To combine different-length rubrics, the documented example divides by `n-1` before applying caller-owned weights. That is scale normalization, not evidence of numerical calibration.[^score]

### 3.4 Noul

| Field | Contract |
| --- | --- |
| `type` | Required literal `"noul"`. |
| `instructions` | A yes/no question or statement to evaluate; same required/optional discrepancy. |
| `criteria` | Optional object or `null` in OpenAPI. |
| `criteria.true` | Optional string/object/array/`null`: what an affirmative/true answer means. |
| `criteria.false` | Optional string/object/array/`null`: what a negative/false answer means. |

Only `type` is schema-required on a Noul, but a semantically empty question is not a recommended integration. Ask one proposition, align high values with its affirmative meaning, and do not invert `true` to describe "no." A middle `noul` is uncertainty about a binary proposition, not a middle degree of severity or skill.[^openapi][^noul][^jag-contradiction]

### 3.5 Reconciled discrepancies

| Surface mismatch | Current state | Conservative interoperable request |
| --- | --- | --- |
| Instructions | HTTP: required; OpenAPI and SDK question types: optional/nullable. Advanced-structure page also allows null. | Always send explicit, non-null instructions. |
| Score minimum | Prose recommends 2; OpenAPI/Python send path accept 1; JS requires 2. Python public `Score` construction can even accept `[]`, but send-time normalization rejects it. | **2-10** meaningful levels. |
| Null Score descriptions | Advanced-structure page and JS `EntryType` allow them; HTTP/OpenAPI/Python do not. | Non-null descriptions. |
| Null state | JS request type and mocked transport tests permit `null`; HTTP/OpenAPI/Python public type exclude it. Python's normal send path does not itself apply the full state schema. | Non-null string/object/array. |
| Maxima | 255 choices and 10 levels are prose limits, absent as schema/SDK maximum constraints. | Enforce those limits locally. |
| Score `legend` | HTTP guide describes `map<string,string>`; OpenAPI and worked structured-level example allow string/object/array values. | Preserve the original structured descriptions. |
| Probability sum | HTTP prose says sum 1; OpenAPI says **approximately** 1, without a numeric tolerance. | Validate with a deliberately selected floating-point tolerance; do not invent a vendor tolerance. |

These are documented/source-verified differences, not live inference results.[^api][^openapi][^advanced][^sdk-score][^sdk-state][^score]

## 4. Full response contract and reading it safely

### 4.1 Envelope and answer objects

The live `SystemOneResponse` requires `model`, `answers`, and `usage`. `answers` has at least one entry and is documented to preserve submitted IDs and corresponding question types. The Models page says `model` reports the versioned model that answered; OpenAPI describes it as the model name that may differ from the requested alias, and its example still uses `jev-latest`. Log the actual returned value rather than assuming an alias itself is a revision pin.[^openapi][^models]

| Object | Every defined field | Meaning |
| --- | --- | --- |
| Noul answer | `type: "noul"`; `noul: number` | Both required. `noul` is P(yes/true), documented `[0,1]`. **No separate `confidence`, `probabilities`, or `legend`.** |
| Choice answer | `type: "choice"`; `choice: string`; `probabilities: object<string,number>`; `confidence: number` | All required. `choice` is a highest-probability option; probabilities cover the supplied options; confidence summarizes distribution shape. |
| Score answer | `type: "score"`; `score: number`; `legend: object<string,string\|object\|array>`; `probabilities: object<string,number>`; `confidence: number` | All required. Legend/probability keys are stringified zero-based levels. Legend repeats the requested descriptions; score is their index expectation. |
| Usage | `input_tokens: integer`; `output_tokens: integer` | Both required by OpenAPI. Input tokens are described as billable; output tokens are currently free. |

Probability/confidence bounds and approximate normalization are descriptions, **not** OpenAPI `minimum`/`maximum` or sum validators. The schema does not enforce that a choice belongs to the originating request, that its probability keys match, that it is an argmax, or that Score keys/range agree with that request. No production tie-breaking rule, guaranteed output precision, or per-question partial-error object is documented in these schemas.[^openapi]

The following is a **synthetic format illustration**, assembled to show all three answer shapes; it is not a recorded prediction, observed token bill, or specification of the confidence formula.[^api][^openapi]

```json
{
  "model": "jev-1.13.0",
  "answers": {
    "department": {
      "type": "choice",
      "choice": "billing",
      "probabilities": {
        "billing": 0.88,
        "technical": 0.12,
        "sales": 0.0
      },
      "confidence": 0.81
    },
    "frustration": {
      "type": "score",
      "score": 1.05,
      "legend": {
        "0": "Calm",
        "1": "Frustrated",
        "2": "Very angry"
      },
      "probabilities": {
        "0": 0.0,
        "1": 0.95,
        "2": 0.05
      },
      "confidence": 0.92
    },
    "is_urgent": {
      "type": "noul",
      "noul": 0.95
    }
  },
  "usage": {
    "input_tokens": 1000,
    "output_tokens": 72
  }
}
```

Python converts Score `legend` and `probabilities` keys to **integers** in public answer objects. Raw HTTP JSON and JavaScript retain object keys representing those levels; do not copy Python integer-key indexing into a JSON parser without accounting for the distinction.[^score][^sdk-responses]

### 4.2 SDK types are not complete boundary validation

**Source-verified:** Python validates answer structures, uses strict public response models, and raises `TypeSafeAPIResponseValidationError` for malformed typed fields. However, its default response model permits missing answers, logs and removes unknown answer kinds, ignores extra fields, and does not compare results against the submitted question/option sets. Its public usage counters can be `None`, despite the wire schema requiring integers. JavaScript parses the body and returns a TypeScript type assertion, without a System One runtime response validator; non-JSON success bodies can therefore survive as text. Neither SDK normalizes returned probability maps.[^sdk-responses][^sdk-js-response]

**Derived integration requirement:** validate the exact raw question-key set, per-key discriminator, allowed selected option, full probability key set, finite bounded numeric values, chosen sum tolerance, Score legend/range, and returned model before acting. Missing/unknown/malformed answers should be an explicit non-action/error path, not a fallback category. Python offers a `response_model=` hook for caller-defined Pydantic validation, but the caller must define those extra invariants; the default model does not supply them.[^sdk-responses][^sdk-response-model]

## 5. Jev 1.13 jaggedness: complete failure-mode coverage

This section covers **every numbered category, every mathematical subcategory, the worked invariant examples, and the closing cautions** on the current page. It is a detailed paraphrase, not a verbatim reproduction. The page applies to `jev-1.13`, was last reviewed September 17, and describes current weaknesses rather than measured error rates or guaranteed mitigation effectiveness.[^jag]

### 5.1 Literal reading

**Documented failure:** Jev answers the wording rather than the author's unstated intent. Scope words, negations, and implied conditions are taken literally; human readers may supply an intended qualification that the model does not. This is not a claim that every negative sentence fails.[^jag-literal]

**Documented mitigation:** write the exact condition in `instructions`, make boundaries explicit in `criteria`, and incorporate the clarification one would otherwise give after a wrong answer. When interpretation is unavoidable, ask two literal questions and combine their answers in code.[^jag-literal]

**Derived relevance:** distinctions such as "this ticket discusses a breaking change" versus "this ticket requests a breaking change" must be specified. An issue quoting a rejected proposal is not equivalent to requesting that proposal.[^jag-literal]

### 5.2 Math and numbers: all three subcases

**General warning:** the model is not a calculator; deterministic mathematical logic belongs in code, and semantic judgments are a better fit.[^jag-math]

| Subcase | Documented failure presentation | Documented mitigation |
| --- | --- | --- |
| **Counting** | Unreliable character counts, term-occurrence counts, and long-list item counts. It recognizes answer patterns rather than reliably tallying; errors increase with the size being counted. | Use a regex/parser and count in code when possible. For semantic matching, enumerate candidates in code, ask one question per candidate, then sum caller-thresholded answers in code. |
| **Numeric representations** | Hex/RGB color representations underperform English color names; numeric proximity is unreliable. High-level programming language representations work better than assembly or binary-encoded instructions. | Convert or compute in code; send a computed value or named bucket. Reserve Jev for semantic interpretation, such as whether a color conveys a warning. |
| **Math using Score** | Score levels have weak numerical calibration. Interpolating between criterion levels cannot reconstruct an exact magnitude reliably. | A Score expectation may drive an application threshold, but do not treat it as an exact recovered number or use it to replace arithmetic. |

All three subcases and their mitigations are on the Math and Numbers section.[^jag-math] **Derived:** summing thresholded semantic decisions still inherits their classification errors; it fixes the counting step, not the judgments. Semver version arithmetic, threshold comparisons, and deterministic label mappings do not need a Jev judgment.

### 5.3 Date and time comparison

**Documented failure:** dates are read as text rather than ordered quantities. Ordering two dates, computing distance, and testing membership in a time window are unreliable; mixed formats, relative expressions, quarters, settlement windows, and accrual periods make matters worse.[^jag-dates]

**Documented mitigation:** use bounded Choices for stated month/day/year components, with an explicit not-stated option; assemble and validate an actual date and perform all ordering, duration, offset, and weekday operations in code. The linked cookbook also fixes the reference date and the interpretation of relative weekdays in code and routes incomplete/impossible/low-confidence dates to review. Its small worked run is not a general date-extraction guarantee.[^jag-dates][^dates]

### 5.4 Indirection, including double negation

**Documented failure:** double negatives, questions about a property of a property, and multi-hop reasoning reduce reliability.[^jag-indirection]

**Documented mitigation:** make instructions direct and identify relevant state fields by name. The broader question guide recommends explicit backticked dot-and-index paths, such as `issue.body`, to disambiguate the input being judged.[^jag-indirection][^primitives]

**Derived relevance:** "not an issue that should not be classified as..." and references through several quoted specs should not be treated as equivalent to a direct statement merely because the answer space is typed.[^jag-indirection]

### 5.5 Large state containing irrelevant detail

**Documented failure:** accuracy declines as unrelated material is added; distractions also make a wrong answer harder to trace to the responsible input. The page calls this context rot.[^jag-context]

**Documented mitigation:** retrieve/filter in code and include only needed fields. If deterministic filtering is unavailable, a Noul can filter for relevance; the linked RAG cookbook demonstrates that pattern. Context-window compliance does not override this warning.[^jag-context][^rag][^models]

**Derived relevance:** wholesale issue bodies, copied logs, historic comments, and embedded unrelated instructions deserve explicit evaluation. Filtering must preserve evidence relevant to breaking behavior; silently truncating until a request fits is not a documented correctness mitigation.

### 5.6 Adversarial content

**Documented failure:** state is not treated as hostile by default. Injected instructions, deliberately misleading framing, and a passage arguing for its own classification can change the answer. The page expresses an expectation of future improvement, not a present robustness guarantee.[^jag-adversarial]

**Documented mitigation:** explicit criteria and thorough integration testing before broad deployment. The page supplies no guaranteed sanitizer, attack-success bound, or instruction/data isolation mechanism. Section 9 below covers the additional untrusted-input guidance and the limits of the guardrail examples.[^jag-adversarial][^rag]

### 5.7 Contradictory instructions and criteria

**Documented failure:** disagreement between `instructions` and `criteria` confuses the model. A Noul mapping `true` to "no" and `false` to "yes" is the concrete example of worse behavior.[^jag-contradiction]

**Documented mitigation:** make the criteria an aligned extension of the instruction, using plain, precise language. Do not rely on a rubric to secretly reverse the meaning of the question.[^jag-contradiction]

**Derived relevance:** mutually inconsistent examples, a permissive instruction paired with restrictive category definitions, or contradictory policy fragments need reconciliation before inference, not just a higher acceptance threshold.

### 5.8 Common-sense structural invariants

**Documented failure:** expected consistency is not a promise of mathematical identities between different questions or primitives. The page gives these concrete outputs:[^jag-invariants]

| Comparison | Published example |
| --- | --- |
| Refund intent asked as Noul versus yes/no Choice, on a dissatisfied customer asking about available options | Noul **0.22**; Choice yes **0.01**, no **0.99**, confidence **0.97**. The relevant comparison is Noul versus Choice's **yes probability**, not Choice confidence. |
| Refund versus something-other-than-refund asked as separate Nouls, on a duplicate-charge complaint | **0.72** and **0.47**, totaling **1.19**, not 1. |

**Documented mitigation:** ask the intended decision directly, do not transfer a threshold between Noul and Choice, do not require separate questions to satisfy complementary arithmetic, and enforce identities in code where needed. A Choice asks which supplied option fits **relatively**; a Noul per option asks separate **absolute** propositions, and all of those Nouls may be low. The skill-suggestion cookbook uses relative selection plus absolute fit checks rather than pretending these are interchangeable distributions.[^jag-invariants][^skills-cookbook]

**Derived relevance:** if code needs P(not X), `1 - p` from the **same** binary answer preserves that identity arithmetically. A separately prompted "not X" is another model judgment. Independent evaluation also does not establish statistically independent errors or justify multiplying confidences into a joint correctness probability.[^jag-invariants][^confidence]

### 5.9 Generation

**Documented failure:** Jev is not trained for text generation. Chaining Choices to force generation works poorly and slowly.[^jag-generation]

**Documented mitigation:** when extraction has a bounded answer space, enumerate candidates using a regex or generative model and have Jev select one. Use a generative model when actual free-form text is required.[^jag-generation]

The page's closing advice adds no tenth category: avoid exact computations, several hidden judgments in one question, System Two/multi-indirection tasks, and unnecessary state. Its claim that many jagged edges will be fixed later is not a dated fix commitment.[^jag]

## 6. Few-shot examples: supported form, token accounting, and evidence

### 6.1 What is actually documented

**Yes, Jev can receive examples as ordinary request content.** The State page explicitly allows related context and examples in a JSON object/array. The workflow guide recommends named example fields beside the question. Choice and Score pages show structured criteria containing `examples` arrays; Advanced structure does the same for `criteria.true` and `criteria.false` on Noul.[^state][^build][^choice][^score][^advanced]

**But no separate few-shot protocol was found.** The live request schema and both SDK request interfaces expose no typed `demonstrations` collection, prior-answer object, training endpoint, or reserved `{input, output}` schema. Searching the public documentation index/full-text export did not find a formal few-shot/demonstration contract. Arbitrary names such as `examples`, `what`, and `not_for` are ordinary model-visible JSON fields, explicitly not reserved API fields on the Choice page.[^openapi][^sdk-extras][^index][^choice]

For example, this **illustrative question fragment** follows the documented structured-criteria idiom; its `examples` arrays are descriptions associated with the containing option, not a new top-level API feature:[^choice]

```json
{
  "type": "choice",
  "instructions": "Which kind of returns information does the message request?",
  "criteria": {
    "policy": {
      "what": "Rules for whether and how a return is allowed",
      "examples": ["Can an opened item be returned?"]
    },
    "status": {
      "what": "Progress of a return already initiated",
      "examples": ["Has the returned parcel reached you?"]
    },
    "other": "Neither kind of returns information"
  }
}
```

**Documented:** customization is through state/instructions/criteria, not customer-specific fine-tuning or LoRA weights.[^models] **Not established:** a provider-defined maximum number of demonstrations, an optimal number, a guarantee that labeled input/output pairs are interpreted in a particular way, or a separate demonstration billing tier.

### 6.2 Context and billing implications

Let `S` be the tokenized shared state, including any examples placed there, and `Q_i` be each question's content, including instructions, criteria, and any examples placed inside them. The documented budgets are:[^models]

```text
S + sum(Q_i) <= 64k tokens per request
S + max(Q_i) <= 32k tokens for the longest state/question pair
```

**Derived from those rules:** shared-state examples are paid for once within that request, but consume space in every state-plus-question ceiling. Question-local examples enlarge their own `Q_i`; copying them into three questions is not the same as sharing them in state. Splitting the questions into separate calls re-sends and re-pays their shared examples/state. No special free-example allowance is documented.[^models][^parallel]

**Not established:** exact tokenizer/serialization overhead, whether "k" is 1,000 or 1,024, a public preflight token-count endpoint, or an exact demonstration-specific counting algorithm. Treat these expressions as the published budgeting model, leave headroom, and use returned `usage.input_tokens` for observed billable usage; do not claim byte counts or local character heuristics are exact.[^models][^openapi][^index]

### 6.3 Evidence for usefulness is narrower than "few-shot improves accuracy"

The Score page supplies a small first-party example ablation on `jev-1.13.0`:[^score]

| Rubric variation on the same Safari-only export failure | Score | Confidence |
| --- | --- | --- |
| Plain descriptive levels | 1.43 | 0.35 |
| Levels with a matching cross-browser-workaround example | 1.03 | 0.96 |
| Levels with an unrelated search/browsing-workaround example | 1.43 | 0.35 |

Another example moves from **1.11 / 0.84** to **1.09 / 0.87** with structured examples. These demonstrate that relevant examples can change outputs and concentrate distributions. The page explicitly cautions that higher confidence does **not** establish correctness, recommends examples with known expected levels, and says to test revised descriptions on separate inputs.[^score]

**Not established in the reviewed first-party material:** a controlled few-shot study reporting held-out classification accuracy and probability-calibration changes versus zero-shot for Jev 1.13. The structured-example demonstrations establish steering, not such an accuracy/calibration result.[^score][^index]

## 7. Confidence gating: useful policy input, not verification

### 7.1 Documented meaning and approximation caveat

Choice and Score return full `probabilities`; `confidence` compresses their shape into `[0,1]`. The confidence explorer approximates a three-option Choice with `(3 * max_probability - 1) / 2`. The page explicitly permits computing another statistic from the full distribution, and recommends domain/risk-dependent thresholds validated on the application's data. Noul has no separate confidence field: use a yes threshold, a no threshold, and a review band if appropriate.[^confidence][^noul]

Calibration concerns groups of predictions; it does not prove an individual answer correct. A narrow distribution can be confidently wrong, and even confidence 1 on a Score is expressly not a correctness guarantee.[^system-one][^score]

### 7.2 Exactly what the public adapter computes

**Source-verified, not Jev backend disclosure:** `system-one-adapter-python` calls external generative providers and constructs compatible answer objects locally. Its `_utils/confidence_metrics.py` is evidence about that adapter, not proof of the production Jev formula.[^adapter-client][^adapter-math]

For an input vector `p` of length `n`, it normalizes to `q_i = p_i / sum(p)`, using uniform `q_i = 1/n` if the sum is zero. Both confidence functions return 1 immediately for `n == 1`; their standalone empty-input normalization has no guard. Normal adapter validation requires at least two Choice/Score criteria, so those degenerate sizes are not ordinary client requests.[^adapter-math][^adapter-schema]

**Choice, `n > 1`:**

```text
C_choice = (max(q) - 1/n) / (1 - 1/n)
```

There is no explicit clamp in this function; for valid nonnegative normalized probabilities its range is 0 at uniform and 1 at a point mass. The committed test maps `[0.82, 0.18]` to **0.64**, not 0.82.[^adapter-math][^adapter-tests]

**Derived threshold interpretation:**

```text
C_choice >= t  <=>  max(q) >= [1 + (n - 1) * t] / n
```

At `t = 0.8`, required peak probabilities are **0.90 for 2 options**, **0.8667 for 3**, **0.85 for 4**, and **0.84 for 5**. None is P(correct). For fixed `n` and peak, this formula cannot distinguish a strong runner-up from probability dispersed among the remaining options.[^adapter-math]

**Unresolved documentation conflict:** the classification cookbook says a winner at 0.45 against a 0.44 runner-up is distinguished by confidence from a 0.45 winner with the rest scattered. The published adapter formula and explorer approximation do **not** make that distinction at a fixed option count. This could reflect a different production computation or imprecise prose; public evidence does not resolve it. Do not silently equate all three surfaces.[^classification][^confidence][^adapter-math]

**Score, `n > 1`:**

```text
m = first index with maximal q_i
D = sum(q_i * abs(i - m))
U_n = sum(abs(i - (n - 1)/2) for i in 0..n-1) / n
C_score = max(0, 1 - D / U_n)
```

This is distance from the first modal position, normalized against uniform mean absolute deviation around the support's center. It has a lower clamp at zero, depends on rubric ordering, and is not the probability assigned to the rounded expected score. With five levels, `[0,.5,.5,0,0]` produces approximately **0.5833**, whereas `[.5,0,0,0,.5]` produces **0**. These are derived formula examples, not Jev observations.[^adapter-math]

For comparison-harness users, the adapter normalizes internally for these derived values even when `normalize_probabilities=False` leaves the returned map unnormalized. Its **discrete-output mode** turns a selected Choice/Score into a one-hot distribution and consequently confidence 1; thresholding that confidence cannot measure external-model uncertainty. Neither behavior should be attributed to hosted Jev.[^adapter-normalization]

### 7.3 What the confidence cookbooks actually establish

**Classification using confidence:** the published `jev-1.12` run is dated **2026-08-12**. It classifies 60 selected SEC business sections into 75 groups. Filings were filtered to ones whose text supports their self-reported labels; this is not an unselected production sample.[^classification]

| Policy / subset | Published outcome |
| --- | --- |
| Always return a narrow group | 39/60 correct |
| `confidence >= 0.9`: 30 accepted narrow groups | 27/30 correct |
| Remaining 30, if forced to narrow groups | 12/30 correct |
| Remaining 30, mapped in code to their predicted group's broader division | 21/30 correct |
| Combined specific-or-broad policy | 48/60 useful labels |

Broadening requires **no second model call**, because the taxonomy mapping is deterministic. It is not abstention: every item still receives a label, and a wrong narrow prediction can also map to a wrong broad division. If a broad label cannot drive a safe action, the cookbook says that branch should go to a person. The threshold's result is measured for that recipe, not guaranteed elsewhere.[^classification]

**Self-consistency: choices:** a published September 11 run requests `jev-latest` and records `jev-1.13.0` for 15 calls. On one borderline post with eight questions, raw label agreement is 90.8%; thresholding **top probability**, not `confidence`, at **0.60** raises policy agreement to **99.2%**, with **74.2%** automatic decisions and **25.8%** abstentions. This measures repeatability, not accuracy. Each repeat changes an irrelevant `uid`, so it cannot isolate identical-input randomness from sensitivity to that change.[^consistency-choice]

**Self-consistency: nouls:** the companion example uses 14 questions over one insurance claim, repeated 15 times, and routes `[0.30, 0.70]` to uncertainty. A coverage answer varies from **0.43 to 0.53**, crossing a naive 0.5 action threshold. It carries the same changed-`uid` caveat; low variance is not proof of correctness.[^consistency-noul]

**Additional prose inconsistency:** the confidence-routing pattern's bank example calls automatic approval above 0.85 safe, while the general Confidence page's example still confirms a high-confidence transfer and uses different illustrative cutoffs. Neither example establishes a validated safe threshold for consequential actions.[^confidence-routing][^confidence]

**Derived policy for git-loopy:** select thresholds separately by seam, model revision, prompt/rubric, option set, and error cost; measure errors **among accepted decisions**, rejected coverage, and adversarial cases. Re-evaluate when any of those inputs change. A threshold that merely suppresses unstable answers is insufficient evidence for binding authority.

## 8. Parallel-question economics and real dependencies

### 8.1 What one request buys

**Documented:** all questions evaluate the same state independently and in parallel; one answer is not hidden context for another. Questions can mix primitive types. An array in `state` is still **one state**, not an automatic batch returning one result per element. To evaluate array elements, explicitly construct questions referencing the corresponding paths, or make separate requests.[^state][^primitives]

Shared state is billed once within a combined request; extra questions still add input tokens. Ignoring a speculative answer afterward does not make its question free. "Little additional latency" is the vendor's documented expectation, not an unlimited-question latency SLA.[^models][^parallel][^fan-out]

**Derived accounting illustration:** ignoring unspecified framing overhead, three separate calls cost approximately `3*S + Q1 + Q2 + Q3` input tokens, while one combined call costs `S + Q1 + Q2 + Q3`, saving `2*S`. The advantage approaches 3x only when state dominates. Sharing examples and policy context can matter as much as sharing the issue text.[^models][^parallel]

The parallel cookbook reports **13 questions**, a **53,777-character** GDPR article, and **five repetitions** using `jev-1.12`: approximately **$0.000497 / 0.27s** batched versus **$0.006090 / 2.71s** for sequential single-question calls, or **12.2x cheaper / 10.0x faster**. Most outputs match exactly; two Nouls show small variations. Concurrent separate calls would narrow the latency advantage but not the repeated-state token cost.[^parallel]

**Documentation drift within current pages:** the general Primitives page still summarizes that cookbook as **11.5x / 9.6x**, not its current **12.2x / 10.0x** table. Prefer the underlying table and its experimental conditions; neither ratio is a promise for three git-loopy classifications.[^primitives][^parallel]

### 8.2 Task type, Bump class, and Route in one call

**Derived designs consistent with the documented contract:**

| Desired behavior | One call? | Why |
| --- | --- | --- |
| Infer task type and Bump class independently from the same issue plus explicit policy | Yes | Both complete questions can be built before inference. Code must still handle inconsistent combinations. |
| Choose a Route from the issue and the full eligible route set, without consuming another answer | Yes | It is a third independent judgment, not a downstream step. |
| Ask speculative Route questions for each possible task type, then select the relevant answer in code | Potentially | All branches must be constructible up front and fit the context budget. Unused branches still cost tokens. |
| Apply a fixed task-type-to-Route policy | No extra inference needed | Code can map the accepted first answer directly. |
| Let Route selection read the newly predicted task type or Bump class as input | **Second call**, unless replaced by prebuilt branches/deterministic composition | Another question in the original request cannot see that answer. |
| Fetch richer route/candidate metadata based on a first-stage shortlist; filter/reconstruct state using first-stage outputs | **Second call** | The second state or option set did not exist when the first request was built. |

These distinctions follow the explicit dependency rule in Primitives. Its examples of legitimate second calls are skill suggestion (fetch fuller text for the selected top three), structure recovery (classify newly assembled blocks), and hierarchical classification (choose the next node's children).[^primitives]

The skill-suggestion cookbook is particularly relevant but **not evidence for binding replacement**: its selected skill is a suggestion that the agent can ignore, and the code can reject all shortlisted candidates. Its efficacy must not be transferred to a design where git-loopy acts directly on the returned route.[^skills-cookbook]

## 9. Adversarial exposure and guidance for untrusted input

The strongest direct warning is the limitations page: Jev does not treat state as hostile by default, and both explicit instruction injection and subtler self-serving framing can move the answer. No text generation is needed for that failure: influencing the selected label is enough.[^jag-adversarial] The latter consequence is **derived** from the typed-decision interface.

Two first-party cookbooks demonstrate detection, with important boundaries:

| Source | Documented demonstration | What it does not establish |
| --- | --- | --- |
| **Classifying RAG passages** | `jev-1.12`, August 27: four Nouls per query/passage, with injection detection checked first. An 81-passage corpus includes one planted injection, which scores 0.99 and is dropped. Example injection cutoff is 0.70, explicitly tuned to that corpus rather than a default. | The cookbook explicitly says this filter is **not a security boundary** and requires downstream passages to remain untrusted regardless of score. |
| **Guardrails for LLMs** | `jev-1.12`, August 15: 10 input messages and 5 replies, four hazard Nouls plus a severity Score; caller-owned review/action thresholds and deterministic action precedence. Some examples are real published jailbreak prompts. | The introductory claim that an instruction to ignore rules is detected rather than obeyed is an example-oriented claim, not a documented proof of injection resistance. It does not supersede the 1.13 limitations. |

Both cookbooks advise fitting policy to one's own traffic.[^rag][^guardrails] The adapter separately wraps external-LLM state in escaped document delimiters and tells that external model to treat it as untrusted; that is adapter prompt construction, **not** evidence of equivalent protection inside Jev.[^adapter-untrusted]

**Documented guidance:** distinguish state content from questions, structure named fields, keep instructions/criteria precise and aligned, narrow context, and test adversarial edge cases. **Not established:** a trusted-system/untrusted-state hierarchy enforced by Jev, a reserved untrusted-content marker, an effective universal sanitization recipe, immunity from adversarial examples in state, or a quantitative attack-success guarantee.[^state][^build][^jag-adversarial][^openapi]

**Derived implication for this ticket:** wrapping an issue in JSON is useful organization, not a trust boundary. A second "is this injection?" question evaluated on the same attacker-influenced state is another fallible judgment, not a certificate for accepting the first answer. Binding authority must be justified by external constraints and measured residual risk, not just a closed answer enum.[^jag-adversarial][^rag]

## 10. Errors, throughput, retries, and time budgets

### 10.1 Service errors and validation-body schema

| Status | Documented service meaning | Default SDK retry posture |
| --- | --- | --- |
| `401 Unauthorized` | Missing or invalid API key; check authorization | No |
| `422 Unprocessable Entity` | Invalid body, missing required field, or malformed question; body identifies offending field | No |
| `429 Too Many Requests` | Rate limit exceeded; back off | Yes, subject to retry count/delay rules |
| `529 Overloaded` | Temporary provider overload; retry after delay | Yes; included in 500-599, mapped to an internal-server-error subclass |

The HTTP guide documents these four statuses. The live endpoint schema formally enumerates only **200 and 422**, so it does not provide a complete status inventory or stable body schemas for 401/429/529. Both SDKs additionally retry 408 and the rest of 500-599 by default.[^api][^openapi][^py-retry][^js-retry][^sdk-errors]

The live 422 schema is an object with optional `detail`, an array of validation errors. Each error requires `loc` (array of field names/indices), `msg` (human-readable string), and `type` (machine-readable string), and may include `input` (arbitrary offending value) and `ctx` (object). Avoid treating error bodies as safe to log indiscriminately: **derived**, `input` can contain supplied issue content.[^openapi]

**Published throughput:** **250,000 tokens/second** and **1,200 requests/minute**; exceeding either produces 429. Limits can change without notice; custom/enterprise increases are offered. The page does not establish the exact enforcement window, burst allowance, per-key versus per-account accounting, or a guaranteed concurrency limit.[^models]

### 10.2 Both SDKs: common defaults

**Source-verified:** both default to **two retries after the first attempt**, at most **three attempts**, for HTTP **408, 429, 500-599**, connection failures, and timeouts. Normal authentication/validation errors are not retried. Both use exponential backoff with initial **0.5 seconds**, maximum **5 seconds**, and **subtractive jitter 0.25**. Without server hints, the first two waits are approximately **0.375-0.500s** and **0.750-1.000s**.[^py-retry][^js-retry]

For retry index `r = 0,1,...`, the delay before rounding is:

```text
min(0.5 * 2^r, 5) * (1 - U * 0.25), where U is uniform in [0,1)
```

Python rounds to milliseconds expressed in seconds; JavaScript rounds milliseconds. Both prefer a valid `retry-after-ms` to `Retry-After`; the latter supports numeric seconds and HTTP dates. Honored server delays bypass ordinary jitter, but their limits differ substantially.[^py-retry][^py-hints][^js-retry]

### 10.3 Important Python/JavaScript differences

| Behavior | Python SDK 0.7.1 | JavaScript SDK 0.6.0 |
| --- | --- | --- |
| Default HTTP timeout | **10 seconds per HTTP operation**: connect/read/write/pool. Not a total attempt deadline. | **10,000 ms per attempt**, including delivery/buffering of the response body. |
| Total retry scheduling budget | `RetryPolicy.timeout = 30.0` seconds, using Tenacity `stop_before_delay`. | **No total retry budget** setting. |
| What that budget guarantees | Stops before a retry whose proposed sleep would reach/exceed the budget; does not interrupt an active attempt or reduce its HTTP timeout to remaining budget. | Every retry starts a fresh attempt timer; backoff waits are outside it. |
| Long server delay | No separate maximum for a valid header; the retry budget can prevent sleeping/retrying. `RetryPolicy(timeout=None)` removes that budget. | `maxRetryAfterMs = 60_000`. Delays **over** the cap use normal backoff, not a capped 60-second wait. |
| Per-call retry overrides | A supplied `RetryPolicy` **replaces** the client policy. | Partial retry fields **merge** with client/default values. |
| Cancellation | Async cancellation propagates as `asyncio.CancelledError`; no AbortSignal-style sync API. | Caller `AbortSignal` covers attempts and backoff; caller abort becomes `APIUserAbortError`, never retried. |

Timeout/budget distinctions are verified through the implementations and their targeted tests, not inferred from the word "timeout."[^py-timeout][^py-budget][^py-cancel][^js-timeout][^js-overrides][^js-retry]

**Python details:** the shared sync/async transport maps `httpx2.TimeoutException` to `TypeSafeAPITimeoutError` and other `httpx2.RequestError`s to `TypeSafeAPIConnectionError`. Timeout and connection retry toggles are independent. `timeout=None` on the SDK inherits a timeout; it does not disable one (an HTTPX2 timeout object can do that). An existing test models **two 20-second attempts under a 30-second retry budget**, demonstrating that budget is not a hard wall-clock deadline. A long `Retry-After` can instead cause retry refusal and immediate propagation of the last error, rather than waiting to the budget limit.[^py-transport][^py-timeout][^py-budget]

**JavaScript details:** the attempt timer covers body delivery even for `.asResponse()`/`.withResponse()`, and interrupted bodies can be retried after 200 headers. Request serialization, success-body JSON parsing, and backoff sleeps sit outside the attempt timer. A custom `fetch` that never resolves and ignores abort is not made deadline-safe by an unconditional promise race.[^js-timeout]

**Derived nominal JS totals, not hard guarantees:** three full default attempts plus maximum ordinary backoffs are about **31.5 seconds**. With two honored 60-second server delays, that becomes about **150 seconds**, before other overhead. `Retry-After: 61` takes the fallback-backoff path rather than waiting 61 seconds. Thus a generic documentation statement that the SDKs honor Retry-After needs these qualifications.[^js-retry][^js-timeout][^models]

### 10.4 Failure handling and accounting

Both SDKs retain terminal HTTP status, body, response headers, and a request ID when available. Python adds a response-validation error; JS instead has caller-abort handling but no default System One response validator. Python exposes `raw_http_response`; its `request_id` accessor raises if absent. JavaScript `.withResponse()` returns `data`, `response`, and an optional `requestId`.[^sdk-errors][^sdk-metadata]

Both attach `X-TypeSafe-Retry-Count` on retry attempts. Neither inspected transport automatically generates an idempotency key or aggregates token usage across attempts. **Not established:** whether a timed-out attempt was evaluated/billed, whether cancellation stops provider work, whether repeated requests are deduplicated, or whether a retry is free. The returned success usage is not a documented all-attempt cost ledger.[^sdk-retry-accounting][^openapi]

**Derived integration requirement:** set an explicit end-to-end decision deadline and deliberate retry policy. For Python async, an enclosing task deadline/cancellation is distinct from `RetryPolicy.timeout`; for JavaScript, use a caller signal for a shared deadline. Define what git-loopy does on exhaustion, timeout, malformed output, missing credentials, and changed rate limits; none should become a success-shaped classification by accident.[^py-cancel][^js-timeout][^sdk-responses]

## 11. Other guidance needed to use Jev well

| Topic | Documented contract / practical boundary |
| --- | --- |
| **State formatting** | Prefer objects with descriptive fields for most requests; a string is sufficient for one simple text input. Arrays can represent related records/messages, but remain one state. Pass native JSON structure instead of flattening everything into a templated string. Explicit backticked paths clarify what to inspect.[^state][^build][^primitives] |
| **Modality** | Text only. Preprocess images, audio, video, and binary content to text/structured fields; those inputs are not directly supported.[^models] |
| **Language** | English is the primary training language and currently strongest. Other languages, including CJK scripts, are accepted but not equally accurate; no exhaustive support/quality matrix is supplied.[^models] |
| **Version pinning** | Both SDKs default to `jev-latest`; both aliases currently resolve to `jev-1.13.0`. Aliases can change answers without application changes. Pin the explicit model when tuning thresholds, and log the response model. The documented model-list endpoint currently lists aliases; fixed IDs can work even when absent from that list.[^models][^sdk-models] |
| **Model pin versus SDK pin** | They are separate. Python `extra_body` can override the prepared `model`, `state`, and `questions`; JS forwards extra request properties. Do not let untrusted input construct the API envelope or override a trusted pin/rubric.[^sdk-extras] |
| **Repeatability** | Independence and consistency are not bit-for-bit determinism. Cookbooks show small probability changes and some label flips. No public seed/temperature control appears in the System One request schema.[^consistency-choice][^consistency-noul][^openapi] |
| **Question design** | One narrow judgment per question; explicit category boundaries, affirmative Noul meaning, and independent concrete Score descriptions. A Noul probability is not a severity scale. Use code for deterministic comparisons/composition.[^build][^noul][^score] |
| **Caching** | Cookbook `JsonCache` is application-side caching. It is not evidence of server prompt-cache pricing or retention. A cache must distinguish model, state, and question/rubric changes if used for evaluation.[^parallel][^consistency-choice] |

**Recommendation, not a TypeSafe guarantee:** a classification-only prototype should pin its model and SDK, preserve raw probabilities/returned model/rubric version, compare accepted results with independently reviewed labels, and exercise contradictions, quotations, self-classification instructions, long irrelevant additions, and language variation. Keep task-type/Bump-class allowlists and deterministic Route constraints outside model control. This documentation review does not establish that any of those seams has an acceptable residual error rate.

## 12. Unresolved questions before a binding integration

The documentation-reading ticket is answerable without inference, but it leaves real adoption questions open:

| Unknown | What public evidence does establish |
| --- | --- |
| Production Choice/Score confidence formula, its stability across revisions, and its relationship to cookbook prose | A distribution-derived statistic, an explicitly approximate explorer, and a separately implemented external-LLM adapter; not a verified production algorithm.[^confidence][^adapter-math][^classification] |
| Error rate of high-confidence accepted task-type, Bump-class, or Route decisions on real git-loopy issues | No such first-party evaluation was found; vendor examples concern other domains and often older models.[^classification][^index] |
| Resistance to deliberate misclassification inside issue text | Explicit vulnerability to adversarial framing/instructions and guidance to test; no security-boundary guarantee.[^jag-adversarial][^rag] |
| A formal few-shot protocol or guaranteed calibration improvement | Inline structured examples are documented; a separate demonstration schema and controlled current-version calibration ablation were not found.[^choice][^score][^openapi][^index] |
| Exact preflight token accounting and behavior at every prose-only limit | Budget descriptions, billable usage counters, and general 422 validation behavior; not an exact tokenizer, boundary probe, or exhaustive failure contract.[^models][^openapi] |
| Failed/retried/cancelled-request billing and server deduplication | Client retry mechanics and final-response usage only.[^sdk-retry-accounting][^openapi] |
| Resolution of the schema/prose/SDK discrepancies | Conservative interoperable inputs are identifiable, but authenticated server behavior was deliberately not tested.[^api][^openapi][^sdk-score] |

**Bottom line:** Jev supplies a useful constrained decision interface and a credible shared-state batching pattern. Its public usage contract does **not** make a binding classification correct or resistant to hostile issue text. The next adoption decision must turn on measured accepted-decision errors and an explicit failure/authority policy, not merely format validity, high confidence, or cheap inference.

## Sources

All web sources below were accessed September 21, 2026. Unless a date is stated in the report, the page is undated. Code links include immutable commit SHAs and line ranges. Documentation/code-example observations are not newly executed API observations.

[^index]: TypeSafe, [documentation index](https://docs.typesafe.ai/llms.txt) and [full-text documentation export](https://docs.typesafe.ai/llms-full.txt). The latter was searched for few-shot/demonstration, token-accounting, and untrusted-input guidance; downloaded snapshot SHA-256 `0e0bc00a4498cf116cd8cecba6cab81c6d80f07456043c9890900a5b70885edf`.
[^api]: TypeSafe, [HTTP API reference](https://docs.typesafe.ai/api), particularly Request body, Question types, Answer types, Errors, and Handling rate limits.
[^openapi]: TypeSafe, [live OpenAPI JSON](https://api.typesafe.ai/openapi.json): `paths./v1/systemone.post`, `securitySchemes.HTTPBearer`, and schemas `SystemOneRequest`, `Question`, `ChoiceQuestion`, `ScoreQuestion`, `NoulQuestion`, `NoulCriteria`, `SystemOneResponse`, `Answer`, `ChoiceAnswer`, `ScoreAnswer`, `NoulAnswer`, `Usage`, `HTTPValidationError`, and `ValidationError`.
[^models]: TypeSafe, [Models](https://docs.typesafe.ai/models): Current models, Aliases, Customizing Jev, Language support, and Listing models.
[^state]: TypeSafe, [State](https://docs.typesafe.ai/concepts/state).
[^primitives]: TypeSafe, [Primitives](https://docs.typesafe.ai/primitives), especially Define a question, Ask multiple questions together, and When one question depends on another.
[^choice]: TypeSafe, [Choice](https://docs.typesafe.ai/primitives/choice), particularly Request structure, Good practice, and Structured instructions and criteria.
[^score]: TypeSafe, [Score](https://docs.typesafe.ai/primitives/score), particularly Levels, Reading a Score, Writing good levels, and Structured level descriptions.
[^noul]: TypeSafe, [Noul](https://docs.typesafe.ai/primitives/noul), particularly Reading a Noul and Writing a Noul question.
[^advanced]: TypeSafe, [Advanced: structure](https://docs.typesafe.ai/primitives/advanced), particularly Where structure is allowed and Structured Noul criteria.
[^build]: TypeSafe, [How to build with TypeSafe](https://docs.typesafe.ai/concepts/how-to-build-with-system-one), especially Decompose the input state, Use structure in the questions, and Route on uncertainty.
[^system-one]: TypeSafe, [System One](https://docs.typesafe.ai/concepts/system-one), particularly How it differs from an LLM.
[^jag]: TypeSafe, [Jev 1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13), last reviewed 2026-09-17; all nine categories and concluding cautions.
[^jag-literal]: TypeSafe, [Jev 1.13: Literal reading](https://docs.typesafe.ai/model-jaggedness/jev-1.13#literal-reading).
[^jag-math]: TypeSafe, [Jev 1.13: Math and Numbers](https://docs.typesafe.ai/model-jaggedness/jev-1.13#math-and-numbers), including Counting, Numeric representations, and Math using score.
[^jag-dates]: TypeSafe, [Jev 1.13: Date and time comparison](https://docs.typesafe.ai/model-jaggedness/jev-1.13#date-and-time-comparison).
[^jag-indirection]: TypeSafe, [Jev 1.13: Indirection](https://docs.typesafe.ai/model-jaggedness/jev-1.13#indirection).
[^jag-context]: TypeSafe, [Jev 1.13: Large state full of irrelevant detail](https://docs.typesafe.ai/model-jaggedness/jev-1.13#large-state-full-of-irrelevant-detail).
[^jag-adversarial]: TypeSafe, [Jev 1.13: Adversarial content](https://docs.typesafe.ai/model-jaggedness/jev-1.13#adversarial-content).
[^jag-contradiction]: TypeSafe, [Jev 1.13: Contradictory instructions and criteria](https://docs.typesafe.ai/model-jaggedness/jev-1.13#contradictory-instructions-and-criteria).
[^jag-invariants]: TypeSafe, [Jev 1.13: Common-sense structural invariants](https://docs.typesafe.ai/model-jaggedness/jev-1.13#common-sense-structural-invariants).
[^jag-generation]: TypeSafe, [Jev 1.13: Generation](https://docs.typesafe.ai/model-jaggedness/jev-1.13#generation).
[^dates]: TypeSafe, [Date extraction cookbook](https://docs.typesafe.ai/cookbooks/date_extraction_cookbook), question definitions, resolution code, and worked outputs.
[^confidence]: TypeSafe, [Confidence](https://docs.typesafe.ai/confidence), including the explorer's approximation disclosure and threshold examples.
[^classification]: TypeSafe, [Classification using confidence](https://docs.typesafe.ai/cookbooks/classification_using_confidence), published `jev-1.12` run dated 2026-08-12.
[^consistency-choice]: TypeSafe, [Self-consistency: choices](https://docs.typesafe.ai/cookbooks/consistency_choice_cookbook), September 11 run, returned-model log, uncertainty policy, agreement/coverage results, and changed-`uid` caveat.
[^consistency-noul]: TypeSafe, [Self-consistency: nouls](https://docs.typesafe.ai/cookbooks/consistency_noul_cookbook), September 11 run, 14-question setup, coverage-probability range, and uncertainty policy.
[^confidence-routing]: TypeSafe, [Confidence-gated routing](https://docs.typesafe.ai/patterns/confidence-routing), voice-banking example.
[^parallel]: TypeSafe, [Parallel questions cookbook](https://docs.typesafe.ai/cookbooks/parallel_questions), `jev-1.12` setup, five-repeat answer table, and cost/latency table.
[^fan-out]: TypeSafe, [Speculative fan-out](https://docs.typesafe.ai/patterns/fan-out), support-ticket example and caller-side relevance filtering.
[^skills-cookbook]: TypeSafe, [Skill suggestion cookbook](https://docs.typesafe.ai/cookbooks/skill_suggestion), two-stage shortlist/full-text design, opt-out behavior, and advisory system-prompt insertion.
[^rag]: TypeSafe, [Classifying RAG passages](https://docs.typesafe.ai/cookbooks/classifying_rag_passages), `jev-1.12` run dated 2026-08-27; four questions, threshold routing, and explicit security-boundary disclaimer before Build the prompt from the accepted evidence.
[^guardrails]: TypeSafe, [Guardrails for LLMs](https://docs.typesafe.ai/cookbooks/llm_guardrails), `jev-1.12` run dated 2026-08-15; input/output batteries, published examples, policy thresholds, and traffic-specific tuning.
[^versions]: Source manifests: [typesafe-ai/typesafe-sdk-python:pyproject.toml:1-4](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/pyproject.toml#L1-L4); [typesafe-ai/typesafe-sdk-js:package.json:1-4](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/package.json#L1-L4); [typesafe-ai/system-one-adapter-python:pyproject.toml:1-4](https://github.com/typesafe-ai/system-one-adapter-python/blob/adffc2eab300a4fa3c0e92252d4ffd6ceaa53700/pyproject.toml#L1-L4). Unauthenticated HEAD metadata was checked through each repository's public `/commits/HEAD` API during the source-code investigation.
[^sdk-choice]: [typesafe-ai/typesafe-sdk-python:src/typesafe_sdk/_core/question_types.py:39-49](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_core/question_types.py#L39-L49), [tests/test_questions.py:120-123](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/tests/test_questions.py#L120-L123), and [tests/test_clients.py:183-198](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/tests/test_clients.py#L183-L198); [typesafe-ai/typesafe-sdk-js:src/questions.ts:55-89](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/src/questions.ts#L55-L89) and [test/client.test.ts:271-275](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/test/client.test.ts#L271-L275).
[^sdk-score]: [typesafe-ai/typesafe-sdk-python:src/typesafe_sdk/_core/question_types.py:52-113](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_core/question_types.py#L52-L113), [src/typesafe_sdk/_core/questions.py:10-29](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_core/questions.py#L10-L29), and [src/typesafe_sdk/_schemas/models.py:135-148](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_schemas/models.py#L135-L148); [typesafe-ai/typesafe-sdk-js:src/types.ts:10-58](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/src/types.ts#L10-L58) and [src/questions.ts:74-87](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/src/questions.ts#L74-L87).
[^sdk-state]: [typesafe-ai/typesafe-sdk-python:src/typesafe_sdk/_core/json_types.py:11-21](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_core/json_types.py#L11-L21) and [src/typesafe_sdk/_core/endpoints.py:17-34](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_core/endpoints.py#L17-L34); [typesafe-ai/typesafe-sdk-js:src/types.ts:1-11](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/src/types.ts#L1-L11) and [test/client.test.ts:324-355](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/test/client.test.ts#L324-L355).
[^sdk-extras]: [typesafe-ai/typesafe-sdk-python:src/typesafe_sdk/_core/endpoints.py:27-34](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_core/endpoints.py#L27-L34), [src/typesafe_sdk/_schemas/models.py:197-216](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_schemas/models.py#L197-L216), and [tests/test_clients.py:129-148](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/tests/test_clients.py#L129-L148); [typesafe-ai/typesafe-sdk-js:src/types.ts:155-172](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/src/types.ts#L155-L172) and [src/client.ts:311-324](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/src/client.ts#L311-L324).
[^sdk-responses]: [typesafe-ai/typesafe-sdk-python:src/typesafe_sdk/_core/response_types.py:22-109](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_core/response_types.py#L22-L109) and [src/typesafe_sdk/_core/response_types.py:127-139](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_core/response_types.py#L127-L139); structural-failure tests [tests/test_responses.py:29-58](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/tests/test_responses.py#L29-L58), unknown-kind tests [tests/test_responses.py:140-175](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/tests/test_responses.py#L140-L175), and missing-answer example [tests/test_clients.py:201-243](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/tests/test_clients.py#L201-L243).
[^sdk-js-response]: [typesafe-ai/typesafe-sdk-js:src/client.ts:328-346](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/src/client.ts#L328-L346) and [src/client.ts:472-489](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/src/client.ts#L472-L489).
[^sdk-response-model]: [typesafe-ai/typesafe-sdk-python:tests/test_pydantic_response_models.py:19-56](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/tests/test_pydantic_response_models.py#L19-L56) and [tests/test_pydantic_response_models.py:72-117](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/tests/test_pydantic_response_models.py#L72-L117).
[^adapter-math]: [typesafe-ai/system-one-adapter-python:src/system_one_adapter/_utils/confidence_metrics.py:4-32](https://github.com/typesafe-ai/system-one-adapter-python/blob/adffc2eab300a4fa3c0e92252d4ffd6ceaa53700/src/system_one_adapter/_utils/confidence_metrics.py#L4-L32).
[^adapter-tests]: [typesafe-ai/system-one-adapter-python:tests/utils/test_confidence_metrics.py:13-31](https://github.com/typesafe-ai/system-one-adapter-python/blob/adffc2eab300a4fa3c0e92252d4ffd6ceaa53700/tests/utils/test_confidence_metrics.py#L13-L31).
[^adapter-client]: [typesafe-ai/system-one-adapter-python:src/system_one_adapter/_client.py:118-165](https://github.com/typesafe-ai/system-one-adapter-python/blob/adffc2eab300a4fa3c0e92252d4ffd6ceaa53700/src/system_one_adapter/_client.py#L118-L165), [src/system_one_adapter/_client.py:381-400](https://github.com/typesafe-ai/system-one-adapter-python/blob/adffc2eab300a4fa3c0e92252d4ffd6ceaa53700/src/system_one_adapter/_client.py#L381-L400), and [src/system_one_adapter/providers/openai.py:141-155](https://github.com/typesafe-ai/system-one-adapter-python/blob/adffc2eab300a4fa3c0e92252d4ffd6ceaa53700/src/system_one_adapter/providers/openai.py#L141-L155).
[^adapter-schema]: [typesafe-ai/system-one-adapter-python:src/system_one_adapter/_schema.py:54-66](https://github.com/typesafe-ai/system-one-adapter-python/blob/adffc2eab300a4fa3c0e92252d4ffd6ceaa53700/src/system_one_adapter/_schema.py#L54-L66).
[^adapter-normalization]: [typesafe-ai/system-one-adapter-python:src/system_one_adapter/_client.py:125-165](https://github.com/typesafe-ai/system-one-adapter-python/blob/adffc2eab300a4fa3c0e92252d4ffd6ceaa53700/src/system_one_adapter/_client.py#L125-L165), [src/system_one_adapter/_client.py:328-367](https://github.com/typesafe-ai/system-one-adapter-python/blob/adffc2eab300a4fa3c0e92252d4ffd6ceaa53700/src/system_one_adapter/_client.py#L328-L367), and [src/system_one_adapter/_utils/probability_normalization.py:95-107](https://github.com/typesafe-ai/system-one-adapter-python/blob/adffc2eab300a4fa3c0e92252d4ffd6ceaa53700/src/system_one_adapter/_utils/probability_normalization.py#L95-L107).
[^adapter-untrusted]: [typesafe-ai/system-one-adapter-python:src/system_one_adapter/_client.py:66-94](https://github.com/typesafe-ai/system-one-adapter-python/blob/adffc2eab300a4fa3c0e92252d4ffd6ceaa53700/src/system_one_adapter/_client.py#L66-L94) and [src/system_one_adapter/_client.py:216-225](https://github.com/typesafe-ai/system-one-adapter-python/blob/adffc2eab300a4fa3c0e92252d4ffd6ceaa53700/src/system_one_adapter/_client.py#L216-L225).
[^py-retry]: [typesafe-ai/typesafe-sdk-python:src/typesafe_sdk/_core/retry.py:27-33](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_core/retry.py#L27-L33), [src/typesafe_sdk/_core/retry.py:52-126](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_core/retry.py#L52-L126), and [tests/test_retry.py:161-202](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/tests/test_retry.py#L161-L202). Also [Python RetryPolicy reference](https://docs.typesafe.ai/sdk/python/api/retries).
[^py-hints]: [typesafe-ai/typesafe-sdk-python:src/typesafe_sdk/_core/errors.py:16-37](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_core/errors.py#L16-L37) and [tests/test_retry.py:253-274](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/tests/test_retry.py#L253-L274).
[^py-timeout]: [typesafe-ai/typesafe-sdk-python:src/typesafe_sdk/constants.py:21-22](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/constants.py#L21-L22), [tests/test_config.py:44-67](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/tests/test_config.py#L44-L67), [tests/test_config.py:134-170](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/tests/test_config.py#L134-L170), and [src/typesafe_sdk/_core/transport.py:149-176](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_core/transport.py#L149-L176).
[^py-budget]: [typesafe-ai/typesafe-sdk-python:src/typesafe_sdk/_core/retry.py:118-126](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_core/retry.py#L118-L126), [tests/test_retry.py:73-158](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/tests/test_retry.py#L73-L158), and [tests/test_retry.py:205-232](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/tests/test_retry.py#L205-L232).
[^py-transport]: [typesafe-ai/typesafe-sdk-python:src/typesafe_sdk/_core/transport.py:67-91](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_core/transport.py#L67-L91) and [src/typesafe_sdk/_core/transport.py:149-176](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_core/transport.py#L149-L176).
[^py-cancel]: [typesafe-ai/typesafe-sdk-python:tests/test_retry.py:480-496](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/tests/test_retry.py#L480-L496) and [tests/test_clients.py:559-596](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/tests/test_clients.py#L559-L596).
[^js-retry]: [typesafe-ai/typesafe-sdk-js:src/retry.ts:5-83](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/src/retry.ts#L5-L83) and [test/retry.test.ts:79-124](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/test/retry.test.ts#L79-L124). Also [JavaScript RetryPolicy reference](https://docs.typesafe.ai/sdk/javascript/api/interfaces/RetryPolicy).
[^js-timeout]: [typesafe-ai/typesafe-sdk-js:src/types.ts:174-205](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/src/types.ts#L174-L205), [src/client.ts:342-400](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/src/client.ts#L342-L400), [src/client.ts:403-468](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/src/client.ts#L403-L468), and [test/native-transport.test.ts:55-145](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/test/native-transport.test.ts#L55-L145).
[^js-overrides]: [typesafe-ai/typesafe-sdk-js:src/client.ts:111-153](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/src/client.ts#L111-L153) and [test/reliability.test.ts:383-400](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/test/reliability.test.ts#L383-L400).
[^sdk-errors]: [typesafe-ai/typesafe-sdk-python:src/typesafe_sdk/_core/errors.py:72-200](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_core/errors.py#L72-L200) and [tests/test_retry.py:459-477](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/tests/test_retry.py#L459-L477); [typesafe-ai/typesafe-sdk-js:src/errors.ts:39-122](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/src/errors.ts#L39-L122).
[^sdk-metadata]: [typesafe-ai/typesafe-sdk-python:src/typesafe_sdk/_core/schemas/base.py:80-94](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_core/schemas/base.py#L80-L94); [typesafe-ai/typesafe-sdk-js:src/api-promise.ts:1-14](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/src/api-promise.ts#L1-L14) and [src/api-promise.ts:36-49](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/src/api-promise.ts#L36-L49).
[^sdk-retry-accounting]: [typesafe-ai/typesafe-sdk-python:src/typesafe_sdk/_core/transport.py:67-76](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_core/transport.py#L67-L76) and [src/typesafe_sdk/_core/transport.py:149-176](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_core/transport.py#L149-L176); [typesafe-ai/typesafe-sdk-js:src/client.ts:350-400](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/src/client.ts#L350-L400).
[^sdk-models]: [typesafe-ai/typesafe-sdk-python:src/typesafe_sdk/constants.py:9-22](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/constants.py#L9-L22) and [src/typesafe_sdk/_core/config.py:50-65](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_core/config.py#L50-L65); [typesafe-ai/typesafe-sdk-js:src/client.ts:267-278](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/src/client.ts#L267-L278) and [src/client.ts:311-325](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/src/client.ts#L311-L325).
