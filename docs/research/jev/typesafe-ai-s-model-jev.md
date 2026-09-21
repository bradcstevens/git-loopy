# TypeSafe AI's Jev: capabilities, evidence, and deployment considerations

**Research cutoff:** September 21, 2026  
**Query classification:** Conceptual explanation with technical and commercial due diligence.

## Executive Summary

Jev is TypeSafe AI's hosted model for **typed, probabilistic decisions**, rather than conversational text generation or a standalone autonomous agent; it launched in early access on September 15, 2026.[^1][^2] Applications supply a shared state and questions, receive constrained choices or scores, and retain control of what happens next; the current documented version is `jev-1.13.0`, priced at **$0.042 per million input tokens**, with output tokens free.[^3][^4] Third-party experiments provide encouraging evidence of inexpensive, low-latency inference, but results depend strongly on the task and baseline: one artifact-backed comparison found approximately **1.22x lower median latency and 26.06x lower recorded run cost** than a fast non-reasoning Qwen baseline, with mixed task quality.[^5][^6] TypeSafe's much larger headline multipliers are workload-specific vendor claims, and its "zero hallucinations" claim means schema matching, not zero incorrect decisions.[^1][^7] Jev merits task-specific evaluation for bounded automation, but its undisclosed model internals, limited calibration evidence, documented failure modes, and data-retention terms make it inappropriate to treat typed output as a correctness or safety guarantee.[^8][^9][^10]

## 1. What Jev is

TypeSafe describes Jev as its first public **"System One Model"**: a model intended to make fast decisions inside ordinary software. Its metaphor draws on the distinction between fast, intuitive and slower, deliberative thinking; the name Jev references William Stanley Jevons. The company says the model does not produce prose, code, or explanations of its reasoning.[^1][^2]

The practical distinction is straightforward:

- A chat model usually returns text, possibly constrained to a schema.
- Jev's native interface returns decisions and distributions over caller-defined alternatives.
- The surrounding application supplies state, executes business logic, validates results, and performs actions. Jev itself is not the complete agent or workflow.[^1][^2][^4]

TypeSafe AI, Inc. is the operator named in its agreement. Its official team page lists **Diogo Almeida, CEO; Sasha Sheng, COO; and Erik Gafni, CTO**. The company reported being founded in 2024 and announced approximately $40 million in financing at launch; lead investor DCVC corroborated a $40 million seed round. Investor endorsement is not independent validation of model performance.[^10][^11][^12][^13]

### Release and access status

| Date or observation | What it establishes |
|---|---|
| September 15, 2026 | Official Jev announcement and early-access launch, not general availability.[^1] |
| September 17, 2026 | Last-review date on the current Jev 1.13 limitations document.[^8] |
| September 21, 2026 | Documentation lists `jev-1.13.0`; `jev-latest` and `jev-preview` both currently resolve to it.[^3] |
| September 21, 2026 | The website still directs prospective users to a waitlist. Public console sign-in and key-management documentation do not establish unrestricted account activation.[^14][^15] |

The current documentation does not establish the release date of `jev-1.13.0`. Moving aliases should not be confused with immutable model versions.[^3]

## 2. How the model interface works

The native endpoint is **`POST https://api.typesafe.ai/v1/systemone`**, authenticated with an API key. Its main inputs are `model`, `state`, and a map of named `questions`. State can be text, a JSON object, or an array; an array remains one shared state rather than an independent batch of states. Images, audio, and video are not supported.[^3][^4][^15][^16]

Questions in one request independently evaluate the same state in parallel. One answer does not become context for another question in that request; answer-dependent steps require a subsequent call. Sharing state across questions also avoids repeatedly billing that state within the combined request.[^2][^17]

### Decision primitives

| Primitive | Meaning | Main return values |
|---|---|---|
| **Choice** | Select among caller-defined alternatives, with up to 255 options. | Selected `choice`, option `probabilities`, and `confidence`.[^4] |
| **Score** | Evaluate an ordered rubric, with up to 10 levels. | `score`, level `legend`, `probabilities`, and `confidence`.[^4] |
| **Noul** | Estimate the probability of yes/true. | `noul`, a number in `[0,1]`; no separate confidence field.[^4] |

Score is a probability-weighted average of zero-based rubric indices:

```text
score = sum(level_index * probability_of_that_level)
```

It is not a direct measurement of an arbitrary numerical quantity. In particular, the vendor warns against using descriptive score levels to reconstruct exact numbers.[^4][^8]

### Minimal documented request body

The following body is taken from the API documentation, with its moving model alias replaced by the currently documented fixed version. It illustrates the interface; no authenticated inference request was executed during this research.[^3][^4]

```json
{
  "state": "Help! My payouts have been failing for 3 days.",
  "model": "jev-1.13.0",
  "questions": {
    "is_urgent": {
      "type": "noul",
      "instructions": "Does this convey urgency?"
    }
  }
}
```

The answer is read from `answers.is_urgent.noul`. The response also reports the model used and token usage. No particular prediction is implied by this example.[^4]

### Architecture and training: what is disclosed

TypeSafe claims a new model architecture, a parallel sampler, and **Reinforcement Learning for Calibrated Decisions (RLCD)**. Its stated training goal is to optimize decisions and probabilities against outcomes, rather than optimize generated replies. Its introductory material places this approach downstream of pretrained language models, but does not identify Jev's underlying checkpoint.[^1][^2][^9]

These claims do **not** amount to a reproducible technical specification:

| Topic | Public evidence found |
|---|---|
| Backbone and parameter count | No named base checkpoint, numerical parameter count, or detailed network specification in reviewed sources.[^1][^9] |
| Parallel inference | Independent question evaluation is documented; exact neural computation and sampler algorithm are not specified.[^1][^2] |
| RLCD | A training objective is described, but no complete loss/reward equation, update algorithm, or isolating ablation study was located.[^9] |
| Training data | TypeSafe says it makes its own data and that English is the primary training language; detailed corpus composition, scale, provenance, and generation methods remain undisclosed in reviewed material.[^1][^3] |
| Weights and research publication | No downloadable Jev weights, open-weight license, or complete Jev/RLCD scientific paper was identified in the reviewed official index, technical pages, and repositories.[^10][^18] |

**Interpretation:** "new model class" is TypeSafe's positioning. The public evidence is sufficient to describe the product contract, but not to independently establish the novelty or exact implementation of its internals.[^1][^9]

## 3. Calibration, confidence, and "zero hallucinations"

Three different properties must be kept separate.

**Schema validity** means the output respects the allowed format or alternatives. It does not mean the selected alternative is correct. TypeSafe explicitly says its plotted zero-hallucination result is **"not empirical"**, because it represents guaranteed schema matching.[^1]

**Probability calibration** concerns agreement between predicted probabilities and observed event frequencies across suitable groups of cases. A calibrated probability near 0.8 should correspond to the event occurring approximately 80% of the time; it is not a guarantee about any individual case.[^9]

**The `confidence` field** is a statistic derived from distribution shape. It is not a separate verification of the answer, and is not automatically the probability that the chosen answer is correct.[^19]

For example, the published comparison adapter computes Choice confidence, for more than one option, as:

```text
confidence = (max_probability - 1 / option_count)
             / (1 - 1 / option_count)
```

Its tests map probabilities `[0.82, 0.18]` to confidence `0.64`. That illustrates why a confidence value must not simply be interpreted as an accuracy probability. This code belongs to the external-LLM adapter, not a disclosed Jev production implementation; the documentation labels its confidence explorer an approximation.[^19][^20]

Consequently, computing conventional accuracy-versus-probability calibration error directly from this concentration statistic tests a different quantity than calibrating the predicted probability of an event. Threshold usefulness, probability calibration, and confidence concentration are related evaluation topics, not interchangeable claims.[^9][^19][^20]

### How strong is the published calibration evidence?

A vendor example using `jev-1.12` classified 60 selected SEC filings into 75 industry groups. Its higher-confidence half achieved 27/30 correct, versus 12/30 in the other half. That supports confidence-based ranking on that selected task, not universal calibration or a guaranteed error rate for Jev 1.13.[^21]

A third-party synthetic advertising experiment reported probability-quality metrics on 400 requests, but did not publish a complete standalone reproduction package. Another public tool-risk test included highly confident errors. No comprehensive, independently administered, current-version calibration study across representative domains was identified in this research.[^22][^23]

**Practical conclusion:** establish thresholds on the actual task, use the correct probability or decision statistic, and measure the errors remaining among accepted predictions. Do not treat a documentation example's threshold as a production safety guarantee.[^8][^19][^21][^23]

## 4. Performance evidence: promising, but baseline-dependent

### The provider's headline

TypeSafe advertises **193.6x faster** and **444.6x cheaper** in its launch material. Its evaluation overview averages accuracy, cost per case, and time per case across four workflows. The reference outputs are model-derived, rather than independently adjudicated ground truth; competitors also face a probability-output contract that can cost more than producing a discrete decision. TypeSafe acknowledges that this represents the higher end of real-world gains.[^1][^7]

The available workflow artifacts declare 711 cases in aggregate, but publish only five illustrative examples per workflow. They also disclose exceptions to the summarized reference-model setup, including replacement reference judgments where a model refused or failed. Researchers could not reconstruct the exact headline denominators or establish that today's artifacts reproduce an immutable launch snapshot.[^24]

The appropriate interpretation is **vendor evidence for particular workflows**, not a universal multiplier against all LLMs, deployment platforms, or output requirements.[^1][^7][^24]

### Third-party findings

These are firsthand external experiments, not all equally independent, representative, or reproducible. Financial independence and free-credit arrangements were not comprehensively verified.

| Evaluation | Main result | Important qualification |
|---|---|---|
| **iammrduncan: seven application workloads**, September 17 | Jev 1.13 vs Qwen 3.8 27B on Cerebras: median successful-request latency **175.9 vs 214.9 ms**; recorded costs **$0.01191936 vs $0.31058150**.[^5][^6] | One paired run per scene, synthetic/repeated fixtures, differing stateful trajectories, and mixed task quality. Strong public artifact availability, not production-tail evidence.[^5] |
| **aahf: synthetic advertising outcomes**, September 17 | With eight demonstrations: macro AUC **0.6916 vs 0.6971** for Jev and GPT-5.6 Sol without reasoning; median latency **377 vs 2,043 ms**, estimated cost about **64x lower**.[^22] | 400 requests; the reported AUC-difference interval does not prove equivalence. Materials are not a complete runnable experiment.[^22] |
| **Mike Moore: tool-call risk**, September 19 publication | **55/60** correct, median **422 ms**, with public labels/code/results.[^23] | No competing-model baseline; small hand-labelled sample; highly confident errors; aliases complicate revision pinning.[^23] |
| **wondertwins: NPC detection and chess**, September 16 | Addressee detection F1 **0.962** on clean text and **0.927** with simulated transcription errors, about **170 ms** median; chess solved **6/25** mate-in-one cases.[^25] | Authored cases and exclusions; no LLM baseline for detection; chess success depended on external tactical information.[^25] |
| **Near Here: event validation**, September 16 | Jev matched **48/50** initial expected decisions; on 21 additional records it matched **19/21**, equal to Mistral and below Gemini's **20/21**.[^26] | Initial cases informed prompt selection; labels were assistant-written. Different reasoning/output settings complicate speed comparisons.[^26] |
| **Good Start Labs: rubric judgments**, September 15 | **91.5% agreement** with Fable across **6,003 checks / 1,203 answers**.[^27] | Agreement is not accuracy; comparison reused July Jev verdicts with an unspecified revision. Early access was disclosed.[^27] |
| **Every: launch-day demonstrations**, September 15 | Reported **777 judgments** across 37 documents and 21 questions in under 0.7 seconds.[^28] | Not 777 serial requests; a separate small writing test missed one of seven planted defects. Not a comprehensive calibration evaluation.[^28] |

### The most concrete comparison in context

The Qwen comparison supplies raw exports, a pinned environment, and an offline summarizer. Ratios calculated from its committed totals are **1.2217x** for pooled median latency and **26.0569x** for known run cost. Those costs cover unequal successful-request totals, 475 for Qwen and 479 for Jev; canceled requests with unknown usage are excluded.[^5][^6]

The cost estimate also uses Jev's recorded **$0.04/M** rate rather than today's published **$0.042/M**. Repricing the same recorded Jev usage at the latter rate, while leaving Qwen's snapshot price unchanged, yields approximately **24.82x** lower run cost. This is arithmetic on the existing measurement, not a new experiment.[^3][^6]

Quality results resist a simple winner:

- Tickets: both matched **75/100** fixture decisions.
- Guardrails: both matched **100/100**.
- Approvals: Qwen **100/100**, Jev **95/100**.
- Scoring: Qwen **93/100**, Jev **100/100**, with different decomposition strategies.
- Home control: Qwen **24/24**, Jev **15/24**.[^5][^29]

**Assessment:** substantial savings are plausible for some decision workloads. Neither this experiment nor the vendor benchmark establishes universal quality parity, production p99 latency, or a general speed/cost multiplier.[^5][^7]

## 5. Integration, limits, and pricing

| Item | Documented behavior |
|---|---|
| Price | **$0.042 per million input tokens**; output tokens free.[^3] |
| Total request context | **64k tokens** for state plus all questions.[^3] |
| Individual question context | State plus the longest question must also fit **32k tokens**.[^3] |
| Published throughput limits | **250,000 tokens/second** and **1,200 requests/minute**, subject to change without notice.[^3] |
| Python | Official `typesafe-sdk`, Python 3.10+, synchronous and asynchronous clients.[^15][^30] |
| JavaScript/TypeScript | Official `@typesafe-ai/sdk`, documented Node.js 20+, ESM/CommonJS support.[^15][^30] |
| Errors | Documented `401` authentication, `422` validation, `429` rate limiting, and `529` overload.[^4] |
| SDK retries | Both reviewed SDKs default to two retries after the initial attempt for retryable failures; their timeout/budget behavior differs.[^31] |

At the published rate, **one million requests with 1,000 billable input tokens each would cost $42**, excluding taxes or separately negotiated terms. This is an illustrative multiplication, not an estimate for a particular workload.[^3][^10]

Billing uses managed credits; purchased credits normally expire at the earlier of contract termination/end or 12 months after purchase, unless the order says otherwise. Automatic refills require opt-in. Minimum purchase, guaranteed promotional credits, and the billing treatment of failed/retried requests were not established by the reviewed sources.[^10]

Within-request state sharing is documented. A cookbook's local result cache should not be mistaken for evidence of provider-side prompt-cache retention or a cached-token tariff, neither of which was verified.[^17]

There are minor contract/documentation inconsistencies: for example, the HTTP guide marks instructions required while live OpenAPI permits omission; Score minimum-length rules also differ across surfaces. A conservative request supplies explicit instructions, non-null state, and at least two Score levels. Pinning the model and validating against the chosen SDK/API version is preferable to copying older cookbook model IDs.[^4][^16][^32]

## 6. Limitations and suitable use

The vendor's Jev 1.13 limitations page is unusually consequential. It acknowledges weaknesses in counting, exact arithmetic, dates, numerical scoring, negation, contradictory criteria, and irrelevant long context. Independently posed questions need not obey complementary-probability identities. It also warns that adversarial instructions or misleading framing inside state can alter decisions.[^8]

**Assessment:** plausible applications include classification, routing, rubric evaluation, and bounded semantic checks where ordinary code can validate inputs and outputs and safely handle uncertainty. Exact computation, authorization, and irreversible actions should remain under deterministic application controls. This recommendation follows from the documented primitive contract and failure modes, not from a claim that any particular deployment has been certified safe.[^4][^8]

For a meaningful pilot:

1. Use representative held-out cases and independently reviewed labels, including confusing, adversarial, and out-of-scope inputs.
2. Compare with tuned inexpensive baselines under matched output requirements; keep decision-only and probability-output comparisons separate.
3. Measure task quality, cost per correct task, latency distribution, and remaining error among automatically accepted decisions.
4. Include unknown/abstain outcomes and explicit no-action or review paths.
5. Pin the model version and re-evaluate prompts and thresholds before upgrades.

These are evaluation recommendations derived from the weaknesses and methodological differences above, not guarantees supplied by TypeSafe.[^3][^5][^8][^19]

## 7. Data governance and commercial caveats

**No training is not zero retention.** The customer agreement prohibits including Customer Data in weight-training datasets without prior consent, but separately permits processing for telemetry, fraud/abuse monitoring, and legal compliance, including language allowing certain processing in perpetuity. It permits backup retention and provides qualified confidentiality rather than an unconditional promise that no third party receives data.[^10]

| Topic | Verified position and unresolved boundary |
|---|---|
| Hosting/residency | Privacy policy says the services are hosted in the United States. EU/UK transfer mechanisms are not an EU-residency commitment.[^33][^34] |
| Subprocessors | Public list includes AWS storage/processing and Modal, Nebius, and CoreWeave compute processing; the latter are described as processing prompts without storing them. Listed locations are USA, not exact cloud regions.[^35] |
| Standard retention | Public provisions use necessity/business-purpose language rather than a universal fixed deletion interval. Backup expiry and payload/log distinctions require clarification.[^10][^33][^34] |
| Enterprise ZDR | Zero Data Retention is offered through enterprise contact; its exact exclusions, backup treatment, and activation conditions were not established.[^36] |
| SOC 2 | Trust Center lists a request-access **"SOC 2 Type II - 2026"** report. Its auditor, opinion, period, exceptions, and deployment scope were not reviewed.[^37] |
| Rights and restrictions | Customers retain Input IP and receive TypeSafe's rights in Output; TypeSafe retains Telemetry rights. Terms restrict distillation, model imitation, competing-product development, reverse engineering, and standalone resale.[^10] |
| Responsibility | Customers must independently evaluate output; inaccurate results remain possible. Output is excluded from the provider's stated IP indemnity.[^10] |

The reviewed terms did not establish blanket approval for regulated or high-stakes use. Before sending sensitive production data, obtain applicable order/DPA terms, retention and deletion commitments, ZDR scope if needed, residency/subprocessor assurances, and the actual audit report. This is procurement guidance, not a legal or compliance certification.[^10][^33][^34][^36][^37]

## 8. Public repositories relevant to evaluation

These are client and evaluation surfaces, not published Jev model weights or its training implementation.[^18][^20][^30]

| Repository | Relevance |
|---|---|
| [typesafe-ai/typesafe-sdk-python](https://github.com/typesafe-ai/typesafe-sdk-python) | Official Python client and retry behavior.[^30][^31] |
| [typesafe-ai/typesafe-sdk-js](https://github.com/typesafe-ai/typesafe-sdk-js) | Official JavaScript/TypeScript client and request types.[^30][^32] |
| [typesafe-ai/system-one-adapter-python](https://github.com/typesafe-ai/system-one-adapter-python) | External-model comparison adapter; useful for auditing output and confidence transformations, not a Jev backend disclosure.[^20] |
| [iammrduncan/typesafe-ai-benchmark](https://github.com/iammrduncan/typesafe-ai-benchmark) | Independent workload harness, raw results, and offline summary artifacts.[^5][^6] |
| [wondertwins/jev-benchmark](https://github.com/wondertwins/jev-benchmark) | NPC and chess tests with public methods/results.[^25] |

## Confidence Assessment

**High confidence:** Jev's identity as a hosted decision model, its announced early-access status, current documented model ID, primitive contract, published price, and explicit limitations. These are directly documented first-party facts about the offering, not independent validation of performance.[^1][^3][^4][^8]

**Moderate confidence:** Jev can be fast and inexpensive on several bounded workloads. Multiple firsthand reports support this, and one comparison provides substantial raw artifacts; however, small samples, synthetic tasks, inconsistent output contracts, pricing snapshots, and differing model versions limit generalization.[^5][^22][^23][^25][^26][^27][^28]

**Low confidence / unresolved:** universal calibration, frontier-equivalent quality across tasks, exact architectural novelty, training-data details, launch-headline reproducibility, sustainable production-tail performance, and standard-account retention guarantees. These were not established by the sources reviewed.[^1][^7][^9][^10][^24]

**Scope and assumptions:** The short query was interpreted as a request to understand and assess the publicly offered Jev model, rather than to purchase access or benchmark it in this repository. Six focused research dispatches covered identity, technical foundations, independent evidence, API/access, benchmark artifacts, and governance. No paid inference or benchmark reruns were performed. Relevant authenticated GitHub discovery was limited to the identified TypeSafe organization; visible repositories were public and private-organization access was not established. No unrelated employer information was used. Undated web documentation is a September 21, 2026 snapshot, and "not found" statements describe this research's limits, not proof of nonexistence.

## Footnotes

[^1]: TypeSafe AI, Diogo Almeida, [Introducing System One Models & Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev), September 15, 2026. Launch, positioning, naming, architecture/RLCD claims, headline ratios, benchmark caveats, and schema-guarantee explanation.
[^2]: TypeSafe documentation, [System One](https://docs.typesafe.ai/concepts/system-one), undated; accessed September 21, 2026.
[^3]: TypeSafe documentation, [Models](https://docs.typesafe.ai/models), undated; accessed September 21, 2026. Version aliases, price, limits, modality/language, and training-use statements.
[^4]: TypeSafe documentation, [API reference](https://docs.typesafe.ai/api), undated; accessed September 21, 2026. Request/response contract, primitive definitions, example, and errors.
[^5]: [iammrduncan/typesafe-ai-benchmark:docs/benchmarks/README.md:8-135](https://github.com/iammrduncan/typesafe-ai-benchmark/blob/cf348cd291bb538ad9fa902334649ef5dd937e92/docs/benchmarks/README.md#L8-L135), commit `cf348cd291bb538ad9fa902334649ef5dd937e92`. Measurements dated September 17, 2026.
[^6]: [iammrduncan/typesafe-ai-benchmark:docs/benchmarks/comparison/summary.json:1797-1835](https://github.com/iammrduncan/typesafe-ai-benchmark/blob/cf348cd291bb538ad9fa902334649ef5dd937e92/docs/benchmarks/comparison/summary.json#L1797-L1835), commit `cf348cd291bb538ad9fa902334649ef5dd937e92`. Unrounded request, latency, and cost totals used for derived ratios.
[^7]: TypeSafe AI, [Workflow evals](https://evals.typesafe.ai/), accessed September 21, 2026. Current overview, macro-averaging, model-reference methodology, and displayed comparisons.
[^8]: TypeSafe documentation, [Jev 1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13), last reviewed September 17, 2026.
[^9]: TypeSafe documentation, [AI primer](https://docs.typesafe.ai/introduction/machine-learning-primer), undated; accessed September 21, 2026. Calibration and RLCD explanation.
[^10]: TypeSafe AI, [Master customer agreement](https://typesafe.ai/legal/mca), updated September 19, 2026; especially sections 1-5, 8-14, and 16.12.
[^11]: TypeSafe AI, [Team](https://typesafe.ai/team), undated; accessed September 21, 2026.
[^12]: TypeSafe AI/Business Wire, [TypeSafe AI Emerges From Stealth With $40M in Funding With New Model for Composable AI](https://finance.yahoo.com/technology/ai/articles/typesafe-ai-emerges-stealth-40m-190000776.html), September 15, 2026. Verified syndicated release; the originating Business Wire page timed out during research.
[^13]: DCVC, James Hardiman, [TypeSafe emerges from stealth with a new way of doing AI](https://www.dcvc.com/news-insights/typesafe-emerges-from-stealth-with-a-new-way-of-doing-ai/), September 15, 2026. First-party investor source.
[^14]: TypeSafe AI, [Homepage and FAQ](https://typesafe.ai/), accessed September 21, 2026. Current early-access/waitlist positioning.
[^15]: TypeSafe documentation, [Quick start](https://docs.typesafe.ai/introduction/quickstart), undated; accessed September 21, 2026. Endpoint, key setup, and SDK introduction.
[^16]: TypeSafe AI, [Live OpenAPI specification](https://api.typesafe.ai/openapi.json), accessed September 21, 2026. Request types, validation schema, authentication, and billable usage.
[^17]: TypeSafe documentation, [Parallel questions](https://docs.typesafe.ai/cookbooks/parallel_questions) and [Primitives (Questions)](https://docs.typesafe.ai/primitives), undated; accessed September 21, 2026. Shared-state economics, independent evaluation, and local result caching.
[^18]: TypeSafe documentation, [Documentation index](https://docs.typesafe.ai/llms.txt), accessed September 21, 2026. Official-document discovery boundary; absence findings also reflect the technical and legal sources cited above.
[^19]: TypeSafe documentation, [Confidence](https://docs.typesafe.ai/confidence), undated; accessed September 21, 2026. Distribution-statistic definition and approximation caveat.
[^20]: [typesafe-ai/system-one-adapter-python:src/system_one_adapter/_utils/confidence_metrics.py:4-24](https://github.com/typesafe-ai/system-one-adapter-python/blob/adffc2eab300a4fa3c0e92252d4ffd6ceaa53700/src/system_one_adapter/_utils/confidence_metrics.py#L4-L24) and [tests/utils/test_confidence_metrics.py:13-23](https://github.com/typesafe-ai/system-one-adapter-python/blob/adffc2eab300a4fa3c0e92252d4ffd6ceaa53700/tests/utils/test_confidence_metrics.py#L13-L23), commit `adffc2eab300a4fa3c0e92252d4ffd6ceaa53700`.
[^21]: TypeSafe documentation, [Classification using confidence](https://docs.typesafe.ai/cookbooks/classification_using_confidence), accessed September 21, 2026; experiment reports `jev-1.12`, August 12, 2026.
[^22]: aahf, [Can typed decisions make frontier-model quality much cheaper? A Jev benchmark](https://huggingface.co/spaces/aahf/JevBenchmark/raw/36c280885103ec382ab5ed1fd264fcf3ee8e5620/article.md), September 17, 2026, and [materials/reproduction limitations](https://huggingface.co/spaces/aahf/JevBenchmark/raw/36c280885103ec382ab5ed1fd264fcf3ee8e5620/materials.html). Pinned Hugging Face snapshot `36c280885103ec382ab5ed1fd264fcf3ee8e5620`.
[^23]: Mike Moore, [I Benchmarked Jev on Agent Tool-Call Risk. Calibration Held.](https://webofmike.com/jev-benchmark/), September 19, 2026; measurements September 17. The article's title is the author's conclusion, not an endorsement of broad calibration by this report.
[^24]: TypeSafe public evaluation artifacts, accessed September 21, 2026: [security incidents](https://evals.typesafe.ai/security_incidents-cases.js?v=6c96b19f), [agent traces](https://evals.typesafe.ai/agent_trace_observability-cases.js?v=4a3821a2), [invoices](https://evals.typesafe.ai/invoice_processing-cases.js?v=8c2f8869), and [customer service](https://evals.typesafe.ai/customer_service-cases.js?v=066f789b). These query-string URLs are the observed artifacts, not independently verified immutable archives.
[^25]: [wondertwins/jev-benchmark:README.md:154-198](https://github.com/wondertwins/jev-benchmark/blob/1c2509ac7d5508b8a7ce00ae4df6d7652d05de8b/README.md#L154-L198) and [README.md:303-309](https://github.com/wondertwins/jev-benchmark/blob/1c2509ac7d5508b8a7ce00ae4df6d7652d05de8b/README.md#L303-L309), commit `1c2509ac7d5508b8a7ce00ae4df6d7652d05de8b`; report dated September 16, 2026.
[^26]: Jon Reed, Near Here, [TypeSafe Jev vs Mistral vs Gemini: Event Validation Test](https://nearhere.events/blog/typesafe-jev-mistral-gemini-event-validation), September 16, 2026.
[^27]: Alex Duffy, Good Start Labs, [Verification is the bottleneck](https://goodstartlabs.com/research/verification-is-the-bottleneck), September 15, 2026.
[^28]: Mike Taylor, Every, [Mini-Vibe Check: TypeSafe's Jev Judged Everything I've Written in 0.7 Seconds](https://every.to/vibe-check/mini-vibe-check-typesafe-s-jev-judged-everything-i-ve-written-in-0-7-seconds), September 15, updated September 21, 2026.
[^29]: [iammrduncan/typesafe-ai-benchmark:packages/demos/lib/jev.ts:17-79](https://github.com/iammrduncan/typesafe-ai-benchmark/blob/cf348cd291bb538ad9fa902334649ef5dd937e92/packages/demos/lib/jev.ts#L17-L79) and [packages/demos/lib/theater/evaluations.ts:6-33](https://github.com/iammrduncan/typesafe-ai-benchmark/blob/cf348cd291bb538ad9fa902334649ef5dd937e92/packages/demos/lib/theater/evaluations.ts#L6-L33), commit `cf348cd291bb538ad9fa902334649ef5dd937e92`. Scoring decomposition and fixture repetition.
[^30]: TypeSafe documentation, [Python SDK](https://docs.typesafe.ai/sdk/python) and [JavaScript SDK](https://docs.typesafe.ai/sdk/javascript), undated; accessed September 21, 2026.
[^31]: [typesafe-ai/typesafe-sdk-python:src/typesafe_sdk/_core/retry.py:52-85](https://github.com/typesafe-ai/typesafe-sdk-python/blob/0ffd094c72ed9445223060b24ffd7a56aa781fb4/src/typesafe_sdk/_core/retry.py#L52-L85), commit `0ffd094c72ed9445223060b24ffd7a56aa781fb4`; [typesafe-ai/typesafe-sdk-js:src/retry.ts:5-67](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/src/retry.ts#L5-L67) and [src/types.ts:196-241](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/src/types.ts#L196-L241), commit `66880ccded6cb642dc1809620c2b108c33730214`.
[^32]: [typesafe-ai/typesafe-sdk-js:src/types.ts:10-57](https://github.com/typesafe-ai/typesafe-sdk-js/blob/66880ccded6cb642dc1809620c2b108c33730214/src/types.ts#L10-L57), commit `66880ccded6cb642dc1809620c2b108c33730214`; compare with the HTTP guide and OpenAPI specification cited above.
[^33]: TypeSafe AI, [Privacy policy](https://typesafe.ai/legal/privacy-policy), updated November 19, 2025; sections on use, retention, disclosure, and international visitors.
[^34]: TypeSafe AI, [Data processing addendum](https://typesafe.ai/legal/data-processing), updated April 24, 2026; especially sections 1-3, 5-6 and Schedule I.
[^35]: TypeSafe AI Trust Center, [Subprocessors](https://trust.typesafe.ai/subprocessors), accessed September 21, 2026.
[^36]: TypeSafe documentation, [Legal](https://docs.typesafe.ai/legal), undated; accessed September 21, 2026. Enterprise Zero Data Retention availability.
[^37]: TypeSafe AI Trust Center, [Resources](https://trust.typesafe.ai/resources), accessed September 21, 2026. Request-access listing for "SOC 2 Type II - 2026"; report contents not inspected.
