# Project Sieve — Codebase Design

Grounded AML alert triage for a mid-sized Indian bank. This document describes the current
codebase: modules, data flow, invariants, and the reasoning behind them. It reflects the
code as committed, not a target state.

---

## 1. Purpose and design principles

An alert is either escalated to an investigator or closed as a false positive. A missed
suspicious alert carries regulatory and legal exposure; a false escalation costs an analyst
20–30 minutes. The codebase therefore optimises for **auditable recall with conservative
closure**, not for raw model quality.

Five invariants hold throughout:

1. **Evidence is frozen before inference.** An `EvidencePack` is built once per alert from a
   deterministic lookback window. Nothing downstream reads the database.
2. **Stable, citable identifiers.** `KYC.*`, `TXN-%06d`, `F.*`. Citations are meaningful
   across runs, prompts, hashes and audit rows.
3. **The model proposes, code disposes.** The provider returns a `TriageDecision`; only code
   may close an alert, and only after grounding and routing checks.
4. **Fail closed.** Any parse error, provider exception, unknown citation or unfaithful
   number forces `ESCALATE`. There is no code path that closes an alert on an ungrounded
   narrative.
5. **Ground truth is isolated.** `data/labels.jsonl` is written by the generator and read
   only by `eval/`. The triage path cannot reach it, and `sieve/` never imports `eval/`.

---

## 2. Repository layout

```
project-sieve/
  config/thresholds.json     all rule weights, rule parameters, routing thresholds, eval seed
  sieve/
    models.py                Pydantic domain models + enums (single source of truth for shapes)
    config.py                Config loader (path from arg, SIEVE_CONFIG, or config/thresholds.json)
    generate.py              deterministic synthetic dataset builder + CLI
    db.py                    SQLite schema, CRUD, audit triggers
    evidence.py              window slice -> EvidencePack
    facts.py                 derived facts (F.*) computed in code
    rules.py                 deterministic rule engine -> (rule_score, [ReasonCode])
    prompt.py                prompt builder with delimited, escaped <data> block
    grounding.py             citation + numeric-faithfulness validator
    routing.py               AUTO_CLOSE / QA_SAMPLE / HUMAN_REVIEW policy
    triage.py                orchestration (triage_core), persistence, CLI
    review.py                reviewer confirmation / override service
    api.py                   FastAPI app factory + endpoints
    providers/
      base.py                LLMProvider ABC
      fake.py                deterministic offline provider + fault modes
      real.py                optional OpenAI-compatible /chat/completions provider
      __init__.py            provider_from_env()
  eval/
    split.py                 deterministic stratified dev/test split
    metrics.py               pure binary precision/recall/F1 + confusion matrix
    run.py                   threshold tuning, both-system scoring, report writer
    report.json              committed evaluation output
  tests/                     offline pytest suite (54 tests) + helpers.py
  data/                      generated at runtime; only .gitkeep is committed
  runs/                      generated JSONL decisions; gitignored
```

---

## 3. Dependency rules

Import direction is deliberate and is what makes the isolation guarantees hold:

```
sieve/models.py        (no internal imports)
sieve/config.py        (stdlib only)
sieve/db.py            -> models
sieve/facts.py         -> models, config
sieve/evidence.py      -> models, config, facts
sieve/rules.py         -> models, config
sieve/prompt.py        -> models
sieve/grounding.py     -> models
sieve/routing.py       -> models, config
sieve/providers/*      -> models, prompt, providers.base
sieve/triage.py        -> db, config, evidence, grounding, models, prompt, providers, routing, rules
sieve/review.py        -> db
sieve/api.py           -> db, config, evidence, models, providers, review, triage
sieve/generate.py      -> db, models
eval/*                 -> sieve (one-way; sieve never imports eval)
tests/*                -> sieve, eval
```

Consequences:

- The evidence pack is the only input the rule engine, prompt builder and provider see.
- `eval/` may depend on the production path (and does, via `triage_core`), never the reverse.
- A regression test (`tests/test_generator.py`) scans sources and asserts that only
  `sieve/generate.py` and `eval/run.py` reference labels, and that no `sieve/` file imports
  `eval`.

---

## 4. Domain models (`sieve/models.py`)

| Model | Fields | Role |
|---|---|---|
| `Customer` | id, segment, occupation, declared income, employer, risk rating, kyc_date | KYC snapshot |
| `Transaction` | id, customer_id, txn_ts, amount_inr, direction, channel, counterparty name/country, narration | ledger row |
| `Alert` | id, customer_id, scenario_code, created_at, lookback_days | unit of work |
| `EvidenceItem` | id, kind (`kyc`/`txn`/`fact`), label, value, meta | citable atom |
| `EvidencePack` | alert/customer/scenario/created_at/lookback + items | frozen inference input |
| `ReasonCode` | code, evidence_ids, detail | explainable rule signal |
| `NarrativeSentence` | text, citations | one grounded claim |
| `TriageDecision` | disposition, confidence [0,1], narrative | provider output contract |
| `GroundingResult` | passed, errors | validator verdict |
| `TriageResult` | alert_id, disposition, confidence, rule_score, reason_codes, narrative, grounding, provider, route | persisted / API response |

Enums (`Segment`, `Direction`, `Channel`, `Disposition`, `Label`, `Route`) constrain every
string field that later appears in comparisons, JSON and SQL.

---

## 5. Runtime architecture

`POST /alerts/{id}/triage` and `python -m sieve.triage` both funnel into the same
orchestration in `sieve/triage.py`:

```
triage_alert(alert_id)
  1. db.fetch_alert / fetch_customer
  2. db.fetch_transactions_window(customer, created_at - lookback_days, created_at)
  3. core = triage_core(alert, customer, txns, provider, cfg)
  4. db.save_triage_result(core.result, prompt_hash, evidence_hash)
  5. db.insert_audit(event_type="triage", actor="system", ...)

triage_core(...)                      # pure: no I/O, reused by eval
  3.1 pack         = build_evidence_pack(alert, customer, txns, cfg)
  3.2 score, codes = evaluate_rules(pack, cfg)
  3.3 prompt       = build_prompt(pack, codes)
  3.4 hashes       = sha256(prompt), sha256(pack canonical JSON)
  3.5 raw          = provider.triage(pack, codes)          # may raise
  3.6 decision     = TriageDecision.model_validate_json(raw)
  3.7 grounding    = validate_grounding(decision, pack)
      (any exception in 3.5-3.7 -> GroundingResult(passed=False, errors=["PROVIDER: ..."]))
  3.8 disposition  = ESCALATE if not grounding.passed else decision.disposition
  3.9 route        = decide_route(disposition, confidence, grounding, score, alert_id, cfg)
  3.10 TriageResult(...)
```

`triage_core` has no database or clock dependency, which is why the evaluation harness can
score every alert in-process and why results are reproducible.

---

## 6. Evidence pack

Built by `sieve/evidence.py` with facts from `sieve/facts.py`.

**ID scheme**

| Prefix | Example | Source |
|---|---|---|
| `KYC.` | `KYC.occupation`, `KYC.declared_monthly_income_inr`, `KYC.risk_rating` | customer row |
| `TXN-` | `TXN-000123` | generator counter, never random |
| `F.` | `F.cash_credits_40k_50k_count_7d` | computed in code |

**Derived facts (all six, per pack)**

| ID | Value | Used by |
|---|---|---|
| `F.cash_credits_40k_50k_count_7d` | count of CR CASH in band over the rule window | cash rule, seasonal benign rule |
| `F.credits_to_declared_income_ratio` | total credits / declared income (2 dp) | both ratio rules, salary veto |
| `F.total_credits_lookback_inr` | sum of CR amounts in window | ratio rule evidence, narrative |
| `F.salary_credits_count` | CR from KYC employer (non-"Self") | salary benign rule |
| `F.high_risk_country_txn_count` | txns ≥ min amount to/from listed countries | high-risk and trade rules |
| `F.max_single_credit_inr` | largest single CR | property-sale rule, provider |

Facts exist so that no arithmetic is delegated to the model: the prompt states values, and
the validator can check any count/ratio/amount a narrative mentions.

---

## 7. Rule engine (`sieve/rules.py`)

Eight rules, each returning `Optional[List[str]]` (evidence IDs) or `None`. Weights and every
threshold live in `config/thresholds.json`; the engine sums weights and clips to `[0,1]`.

| Code | Weight | Fires when |
|---|---|---|
| `RC_CASH_BELOW_50K_REPEATED` | +0.35 | ≥ `min_count` CR CASH in `[band_low, band_high)` within `window_days` |
| `RC_CREDITS_GT_5X_INCOME` | +0.40 | `F.credits_to_declared_income_ratio` ≥ 5.0 |
| `RC_CREDITS_GT_2X_INCOME` | +0.15 | ratio ≥ 2.0 |
| `RC_HIGH_RISK_COUNTRY_FLOW` | +0.35 | any txn ≥ 100,000 INR with a listed country |
| `RC_BENIGN_EMPLOYER_SALARY` | −0.35 | NEFT CR from the KYC employer within ±35% of income |
| `RC_BENIGN_SEASONAL_CASH` | −0.35 | declared jeweller occupation + cash-band pattern |
| `RC_BENIGN_PROPERTY_SALE` | −0.40 | exactly one large RTGS CR (≥ 5× income), domestic |
| `RC_BENIGN_TRADE_INVOICE` | −0.35 | SME/CORPORATE segment + high-risk DR flow |

Deliberate self-vetoes (the difference between a heuristic and a false-positive generator):

- Salary benign is suppressed when the ratio is already ≥ 5×, when other (non-salary) credits
  exceed 50% of income, or when a high-risk debit is present.
- Property-sale benign requires **exactly one** large credit, so a burst of large credits
  never inherits a benign explanation.
- Seasonal-cash benign requires both the occupation and the cash pattern.

The rule algebra is designed so documented false positives net to ~0 (jeweller cash
`0.35 − 0.35 = 0`, salary `0.15 − 0.35 → 0`, trade `0.35 − 0.35 = 0`, property sale
`0.40 + 0.15 − 0.40 = 0.15`), while suspicious patterns fire only the positive leg.

---

## 8. Prompt construction (`sieve/prompt.py`)

Sections, in order: system prompt → alert metadata → KYC evidence → derived facts →
deterministic rule signals (code + evidence IDs) → `<data>` transaction block → output schema.

Trust boundaries:

- Everything inside `<data>` is declared untrusted in the system prompt.
- Counterparty names and narrations are HTML-escaped (`html.escape(..., quote=False)`), so a
  narration containing `</data>` renders as `&lt;/data&gt;` and cannot close the block.
- The model is told to cite only IDs shown above and to copy amounts/dates from cited items.

The provider interface returns a **string**, not a parsed object, so malformed output reaches
the validator/fail-safe path instead of crashing the request.

---

## 9. Provider layer

```python
class LLMProvider(ABC):
    name: str
    def triage(self, pack, reason_codes) -> str: ...
```

| Provider | Selection | Behaviour |
|---|---|---|
| `FakeProvider` | default (`SIEVE_PROVIDER=fake`) | deterministic heuristics over narration keywords, KYC risk rating and fuzzy employer matching — signals the rule engine deliberately ignores |
| `OpenAICompatibleProvider` | `SIEVE_PROVIDER=real` / `openai-compatible` | POSTs `{model, temperature: 0, messages}` to `<SIEVE_LLM_BASE_URL>/chat/completions` with a bearer key |

`FakeProvider` fault modes (`--fake-mode` or `SIEVE_FAKE_MODE`): `normal`, `uncited`,
`foreign_citation`, `malformed_json`, `bad_amount`, `overconfident`. They exist to prove the
grounding gate and fail-safe behaviour end to end; tests never need an API key.

`bad_amount` adds 40,000 INR to the amount of the transaction its sentence cites, producing an
otherwise valid, fully cited narrative with one wrong number. It is caught by the numeric
check and escalated.

---

## 10. Grounding validator (`sieve/grounding.py`)

Per narrative sentence:

1. **Citation presence** — at least one citation, else `CITATION:` error.
2. **Citation membership** — every cited ID must exist in *this* pack, else `CITATION:` error.
3. **Numeric faithfulness** — the set of allowed numbers/dates is the union over cited items:
   - numeric `value` of any item,
   - `meta.amount_inr` and the `YYYY-MM-DD` prefix of `meta.txn_ts` for transactions,
   - string `value` matching a date for date-valued items.
4. **Extraction** — evidence IDs are stripped from the sentence first, then `YYYY-MM-DD`
   dates, then numeric tokens (`\d[\d,]*(?:\.\d+)?`). Remaining numbers are compared as
   `Decimal`, so `₹45,000`, `45000` and `INR 45,000.00` all normalise to `45000`.
5. Any unmatched number/date yields a `NUMERIC:` error; any error fails grounding, and the
   orchestrator forces `ESCALATE`.

Scope note: the validator certifies **faithfulness** (the narrative says only what cited
evidence supports), not **correctness** of the disposition. The routing layer is the second
control for that.

---

## 11. Routing and the human loop

`decide_route` (thresholds from `config.routing`):

| Condition | Route |
|---|---|
| disposition = CLOSE_FALSE_POSITIVE **and** grounding passed **and** confidence ≥ 0.8 **and** rule_score < 0.5 | eligible to close |
| eligible and `sha256(alert_id + qa_salt)[:8] % 100 < 10` | `QA_SAMPLE` |
| eligible and not sampled | `AUTO_CLOSE` |
| anything else (escalated, ungrounded, low confidence, material rule score) | `HUMAN_REVIEW` |

QA sampling is deterministic per alert ID, so the same alert always lands in the same bucket.

`review_alert` state machine:

- alert missing → `LookupError` → HTTP 404
- no triage row → `NotTriaged` → 409
- already reviewed → `AlreadyReviewed` → 409
- decision differs from the system disposition and comment is blank → `CommentRequired` → 422
- otherwise insert `reviews` row (`override_flag`) and an `audit` row with the reviewer's id

Queues are SQL views over `triage_results LEFT JOIN reviews` (unreviewed items only),
ordered by descending rule score.

---

## 12. Persistence and audit (`sieve/db.py`)

Tables: `customers`, `transactions`, `alerts`, `triage_results`, `reviews`, `audit`.

- `triage_results` (one row per alert) stores the decision, narrative and grounding JSON,
  rule score, route, provider, and both hashes.
- `audit` stores timestamp, event type (`triage`/`review`), alert id, actor, provider, prompt
  hash, evidence hash, disposition, route, override flag.
- **Immutability is enforced by the database**: `BEFORE UPDATE` and `BEFORE DELETE` triggers
  on `audit` raise `ABORT`. No code path updates or deletes audit rows.
- `prompt_hash` and `evidence_hash` let an auditor prove which exact inputs produced a
  decision and detect any drift between a stored result and a recomputed pack.

---

## 13. HTTP API (`sieve/api.py`)

| Endpoint | Behaviour | Errors |
|---|---|---|
| `POST /alerts/{id}/triage` | run pipeline, persist result + audit, return `TriageResult` | 404 unknown alert |
| `GET /alerts/{id}/evidence` | return the `EvidencePack` (citations resolve against this) | 404 |
| `GET /queue?route=HUMAN_REVIEW\|QA_SAMPLE` | unreviewed queue rows | — |
| `POST /alerts/{id}/review` | reviewer confirm/override | 404 / 409 / 422 |
| `GET /metrics` | triaged count, auto-close/human/QA rates, reviewer override rate, raw counts | — |

`create_app(db_path, provider, config)` is a factory, so tests inject a temp database and a
fault-mode provider without environment mutation. `app = create_app()` is the uvicorn target.

---

## 14. Configuration and environment

`config/thresholds.json`:

- `rules.<CODE>` — `weight` plus that rule's parameters (bands, windows, multiples, country
  and segment lists, tolerances)
- `routing` — `min_confidence`, `rule_score_floor`, `qa_sample_pct`, `qa_salt`
- `rules_only_threshold_grid` — candidate cut-offs for the dev-tuned baseline
- `eval` — `seed`, `dev_fraction`

Environment: `SIEVE_PROVIDER`, `SIEVE_FAKE_MODE`, `SIEVE_DB`, `SIEVE_CONFIG`, and for the real
provider `SIEVE_LLM_BASE_URL`, `SIEVE_LLM_API_KEY`, `SIEVE_LLM_MODEL`. `.env` is gitignored;
only `.env.example` is committed.

---

## 15. Evaluation harness (`eval/`)

```
alerts + labels
   │
   ├─ stratified_split(seed=7, dev_fraction=0.4)      eval/split.py
   │     per-scenario shuffle with Random("7:<scenario>"), round(n*0.4) to dev
   │     -> dev 144 (48/48/48), test 216 (72/72/72)
   │
   ├─ triage_core() for every alert (production code, no persistence)
   │
   ├─ pick_threshold(scores, labels, dev_ids, grid)    eval/run.py
   │     maximise (recall, f1, threshold) lexicographically on DEV only -> 0.15
   │
   ├─ systems scored on TEST:
   │     rules-only : ESCALATE iff rule_score >= threshold
   │     rules+LLM  : ESCALATE iff TriageResult.disposition == ESCALATE
   │
   ├─ per system: precision, recall, F1, confusion (SUSPICIOUS positive),
   │     auto_close_rate, and the same per scenario
   │
   ├─ narrative_quality over test narratives:
   │     citation_validity_rate, numeric_faithfulness_rate (+ error counts)
   │     PROVIDER failures count against both; each class counts only its own errors
   │
   ├─ routing_test: AUTO_CLOSE/QA_SAMPLE/HUMAN_REVIEW counts and rates,
   │     routed_recall (suspicious caught if escalated OR not closed without a human)
   │
   └─ eval/report.json (sort_keys, indent=2) + printed summary table
```

Report schema: `config`, `split`, `rules_only_threshold` (`value`, `tuned_on`, `objective`),
`dev`, `test`, `per_scenario_test`, `narrative_quality`, `routing_test`.

Fairness details:

- "Closed without a human" is defined identically in spirit for both systems: rules-only has
  no routing layer, so closed = not escalated; rules+LLM closed = route ≠ HUMAN_REVIEW.
- Test labels are used only for scoring, never for tuning.
- `binary_metrics` is a pure function, unit-tested against a hand-made 10-item prediction set
  with exact expected values.

Current committed results (test split, `--provider fake`):

| system | precision | recall | F1 | auto-close | tp/fp/fn/tn |
|---|---|---|---|---|---|
| rules-only (0.15) | 0.743 | 0.951 | 0.834 | 51.4% | 78/27/4/107 |
| rules + FakeProvider | 0.974 | 0.915 | 0.943 | 13.9% | 75/2/7/132 |

Narrative quality 1.000 / 1.000; routing 86.1% human review, routed recall 1.000.

---

## 16. Determinism and reproducibility

| Guarantee | Mechanism |
|---|---|
| Same seed → same dataset | single `random.Random(seed)`, fixed `BASE_DATE`, counter IDs, no wall clock or UUIDs |
| Byte-identical `labels.jsonl` | sorted by alert id, `json.dumps(sort_keys=True, separators=(",", ":"))` |
| Same split | per-scenario seeded shuffle over sorted alert ids |
| Byte-identical `eval/report.json` | deterministic provider, canonical JSON, no timestamps |
| Stable QA sampling | `sha256(alert_id + salt)` |
| Reproducible decisions | `triage_core` is pure; prompt/evidence hashes are content-derived |

All of the above are covered by tests, and `eval/report.json` is committed so a reviewer can
diff a fresh run against it.

---

## 17. Testing (`tests/`, 54 tests, fully offline)

| File | Covers |
|---|---|
| `test_generator.py` | generation determinism, dataset shape, label-isolation source scan |
| `test_rules.py` | firing/non-firing cases for all 8 reason codes |
| `test_grounding.py` | 8-case numeric-faithfulness table, uncited sentence, foreign citation |
| `test_prompt.py` | data-block placement, `</data>` injection escaping |
| `test_providers.py` | determinism, narration-driven behaviour, fault modes, no label/DB access |
| `test_api.py` | endpoint contracts, 404/409/422, fault modes through HTTP (no 500s) |
| `test_routing.py` | route decision table, QA-sample determinism |
| `test_metrics.py` | exact metric values on a 10-item set, degenerate inputs |
| `test_eval.py` | report determinism, schema completeness, narrative-quality rates |

`tests/helpers.py` builds minimal evidence packs so rule and validator tests need no dataset.

---

## 18. Safety boundaries

- **Prompt injection**: narrations are escaped and confined to `<data>`; a test asserts an
  injected "ignore previous instructions" appears only inside that block.
- **Untrusted provider output**: parsed with Pydantic, validated, and any failure escalates.
- **Label leakage**: labels are a side file, gitignored, read only by `eval/`; the provider
  source is asserted not to reference labels or SQLite.
- **Audit tampering**: database triggers, not application discipline.
- **Secrets**: none committed; the real provider reads credentials from the environment only.

---

## 19. Runtime and deployment

Python 3.9+ (3.11 recommended). Dependencies: FastAPI, uvicorn, Pydantic v2, httpx, pytest.
Storage is SQLite (single file, `SIEVE_DB`). The real provider uses stdlib `urllib`, so no
extra SDK is required.

Docker: `python:3.11-slim`, installs requirements, generates the dataset at build time
(`python -m sieve.generate --seed 7`), runs `uvicorn sieve.api:app` on port 8000.

---

## 20. Extension points and known limitations

**Extension points**

- New rule: add a `RuleFn`, register it in `RULES`, add its weight/params to
  `config/thresholds.json`, cite its evidence IDs, add firing/non-firing tests.
- New scenario: extend the generator's scenario plan and lookback map; the split and
  per-scenario report pick it up automatically.
- New provider: implement `LLMProvider.triage`, register it in `build_provider` /
  `provider_from_env`. Nothing else changes, because grounding and routing are downstream.
- New derived fact: add an `F.*` item in `facts.py`; it becomes citable and numerically
  checkable immediately.

**Known limitations (deliberate scope, not bugs)**

- The evaluated "LLM" is a deterministic heuristic stand-in; the numbers describe the harness
  and the rules/text division of labour, not real model quality. A real-model run is the next
  step.
- Grounding certifies faithfulness, not correctness of the disposition; qualitative claims are
  unverifiable by construction. Routing (`rule_score_floor`, confidence floor, human review)
  is the compensating control.
- The dataset is synthetic with a designed ~10% ambiguity floor; metrics are properties of
  this generator.
- No retry/repair loop on grounding failure, no authn/authz, no rate limiting, no request
  tracing; SQLite is single-writer. These are production hardening items, listed in the
  README's "what I would do next".
