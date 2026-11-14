# Project Sieve — grounded AML alert triage

An AML alert-triage agent for a mid-sized Indian bank. It builds a frozen evidence pack for
each alert, scores it with transparent config-driven rules, asks an LLM for a disposition and
a **cited** narrative, refuses to close anything whose narrative is not fully grounded in the
evidence, and routes uncertain cases to a human review queue with an append-only audit trail.

The design rule throughout: **the LLM never sees raw data and never has the final word.** It
re-words a deterministic evidence pack; code validates every citation and every number before
an alert can be closed.

## What is in the repo

| Task | Delivered |
|---|---|
| 1 — Grounded vertical slice | Seeded generator, stable evidence IDs, rule engine, `LLMProvider` + `FakeProvider`, prompt with delimited data block, grounding validator, FastAPI triage/evidence endpoints |
| 2 — Evaluation | Stratified dev/test split, rules-only vs rules+LLM metrics, per-scenario breakdown, numeric faithfulness, `eval/report.json` |
| 3 — Human loop | Routing policy, QA sampling, review API, append-only audit table, metrics endpoint |

## Architecture

```
                       ┌──────────────────────────────┐
alerts/*.db ──────────▶│ evidence builder             │
(customers, txns)      │  KYC.*  TXN-*  F.*  (stable) │
                       └──────────────┬───────────────┘
                                      │ EvidencePack (canonical JSON, sha256 hash)
                     ┌────────────────┴─────────────────┐
                     ▼                                  ▼
            deterministic rules               prompt builder (<data> block)
            rule_score ∈ [0,1]                │
            reason codes + evidence IDs       ▼
                     │                  LLMProvider (fake | real)
                     │                          │ raw JSON
                     │                          ▼
                     │                  parse TriageDecision
                     │                          │
                     └──────────────┬───────────┘
                                    ▼
                     grounding validator
                     - every sentence has ≥1 citation
                     - every citation ∈ this pack
                     - every number/date/count matches cited evidence
                                    │
                     fail ──────────┴── pass
                       │                 │
                       ▼                 ▼
                 ESCALATE        routing policy (config thresholds)
                 grounding=false  AUTO_CLOSE | QA_SAMPLE | HUMAN_REVIEW
                                    │
                                    ▼
                     SQLite triage_results + append-only audit
```

Module map:

```
sieve/
  models.py        Pydantic models for every structured LLM input/output
  config.py        loads config/thresholds.json (all thresholds live here)
  generate.py      python -m sieve.generate --seed 7
  db.py            SQLite schema; audit triggers block UPDATE/DELETE
  facts.py         derived facts (F.*) computed in code
  evidence.py      builds the evidence pack with stable IDs
  rules.py         deterministic rule engine, config-weighted
  prompt.py        prompt + delimited <data> block
  grounding.py     citation + numeric faithfulness validator
  providers/       LLMProvider interface, FakeProvider, optional OpenAI-compatible provider
  routing.py       AUTO_CLOSE / QA_SAMPLE / HUMAN_REVIEW policy
  triage.py        service + CLI (--all --provider fake --out runs/fake.jsonl)
  review.py        reviewer confirmation/override service
  api.py           FastAPI app
eval/
  split.py         deterministic stratified dev(40)/test(60) split
  metrics.py       pure precision/recall/F1 + confusion matrix
  run.py           rules-only vs rules+LLM report
tests/             54 offline tests
config/thresholds.json
```

## Data

`python -m sieve.generate --seed 7` writes:

- `data/sieve.db` — `customers`, `transactions`, `alerts` (720 customers, 360 alerts,
  ~4,770 transactions by default)
- `data/labels.jsonl` — `alert_id, label, planted_reason, ambiguous`; read **only** by
  `sieve/generate.py` (writer) and `eval/` (reader)

Three scenario codes: `CASH_STRUCTURING`, `HIGH_RISK_COUNTRY`, `INCOME_MISMATCH`. Alerts are
~36% truly suspicious and ~10% deliberately ambiguous: the ambiguous group carries a plausible
signal on both sides, and its label is assigned randomly, so no method can reach 100%.

Documented false-positive patterns (all visible in the data):

1. **Salary from the KYC employer** — two NEFT credits equal to declared income, counterparty
   equals `KYC.employer_name`.
2. **Jeweller seasonal cash** — a declared `Jeweller`/`Goldsmith` with repeated sub-₹50k cash
   credits narrated as festive counter sales, consistent with declared turnover.
3. **One-off property sale** — a single RTGS credit ≥5× monthly income with a "sale deed"
   narration from an individual counterparty.
4. **Trade invoice** — an SME/CORPORATE import payment debited to a high-risk country with an
   invoice narration.

## Quickstart

```bash
python3.11 -m venv .venv          # 3.11+; the code also runs on 3.9
source .venv/bin/activate
pip install -r requirements.txt

python -m sieve.generate --seed 7  # dataset + labels
pytest -q                          # 54 offline tests, no API key needed

python -m sieve.triage --all --provider fake --out runs/fake.jsonl
python -m eval.run --provider fake # writes eval/report.json and prints a table

uvicorn sieve.api:app --reload     # http://127.0.0.1:8000/docs
```

Optional real provider (never used by tests):

```bash
cp .env.example .env
# set SIEVE_PROVIDER=real, SIEVE_LLM_BASE_URL, SIEVE_LLM_API_KEY, SIEVE_LLM_MODEL
```

Docker:

```bash
docker build -t project-sieve .
docker run -p 8000:8000 project-sieve
```

## API

```bash
curl -X POST localhost:8000/alerts/ALERT-000001/triage
curl localhost:8000/alerts/ALERT-000001/evidence
curl "localhost:8000/queue?route=HUMAN_REVIEW"
curl -X POST localhost:8000/alerts/ALERT-000001/review \
     -H 'Content-Type: application/json' \
     -d '{"reviewer_id":"analyst-7","decision":"ESCALATE","comment":"Unexplained cash pattern"}'
curl localhost:8000/metrics
```

`POST /alerts/{id}/triage` returns `alert_id, disposition, confidence, rule_score,
reason_codes, narrative, grounding{passed,errors}, provider, route`. Unknown IDs return 404.
Reviews require a comment when overriding; duplicate reviews return 409.

## Grounding and numeric faithfulness

Every narrative sentence must cite ≥1 evidence ID (`KYC.occupation`, `TXN-000123`, `F.*`). A
citation outside the pack, an uncited sentence, a malformed provider response, or an
unfaithful number forces `ESCALATE` with `grounding.passed=false` — the system never closes on
an ungrounded narrative.

Numbers are normalised before comparison: `₹45,000`, `45000`, `INR 45,000.00` all equal
`45000`; `YYYY-MM-DD` dates must match a cited transaction's date; counts must equal the cited
derived fact's value. Provider output is treated as untrusted; any provider exception or parse
failure escalates rather than 500s.

## Rules vs FakeProvider

The rule engine reads only structured fields (amounts, channels, countries, dates, segment,
counterparty equality). The `FakeProvider` deliberately uses signals the rules do not:

- transaction **narration text** ("sale deed", "invoice", "festive", "salary"),
- KYC **risk_rating**, and
- fuzzy employer-name narration matching.

Config, not code, carries all thresholds (`config/thresholds.json`). The rule engine emits 8
reason codes (4 risk-raising, 4 benign-explaining), each naming the evidence IDs that fired it.

## Evaluation

Rules-only threshold is tuned **on dev only** by maximising recall, then F1, then the higher
threshold (recall is the metric that matters most). Split is deterministic and stratified by
scenario (144 dev / 216 test).

Chosen threshold: **0.15** (test split):

| system | precision | recall | F1 | auto-close | confusion (tp/fp/fn/tn) |
|---|---|---|---|---|---|
| rules-only | 0.743 | 0.951 | 0.834 | 51.4% | 78 / 27 / 4 / 107 |
| rules + LLM (fake) | 0.974 | 0.915 | 0.943 | 13.9% | 75 / 2 / 7 / 132 |

Per scenario (test): `CASH_STRUCTURING` rules R=0.87/P=1.00, LLM R=1.00/P=0.97;
`HIGH_RISK_COUNTRY` rules R=1.00/P=0.86, LLM R=0.88/P=1.00; `INCOME_MISMATCH` rules
R=1.00/P=0.55, LLM R=0.86/P=0.96.

Narrative quality: citation validity **100%**, numeric faithfulness **100%** over 216 test
narratives. Routing on test: auto-close **13.9%**, human review **86.1%**, QA sample **1.4%**,
and recall of SUSPICIOUS after routing (human-routed alerts count as caught) **100%**.

`eval/report.json` contains dev and test metrics, per-scenario breakdowns, confusion matrices,
narrative-quality rates and routing figures. Running it twice produces byte-identical output.

### Analysis

Rules-only achieves higher raw recall (95.1% vs 91.5%) because the recall-maximising threshold
(0.15) escalates every alert with any rule signal. That costs precision: at 0.15 the rules
escalate all 18 property-sale false positives in test (they score exactly 0.15) plus 9
ambiguous alerts, so precision falls to 74.3%. Rules + LLM recovers precision (97.4%) because
the FakeProvider reads sale-deed/invoice/salary narrations and closes 25 of those 27 false
positives (18 property-sale and 7 ambiguous). The LLM loses recall only on the 7 ambiguous
mixed-signal alerts, where the narration looks benign but the random label is suspicious — by
design, no method can resolve those. Property-sale false positives are the single biggest error
source for rules-only.
Recall matters more here because a missed STR carries regulatory and legal exposure, while a
false positive costs an analyst 20–30 minutes; that is exactly why the routing layer sends all
low-confidence closes to a human, lifting routed recall to 100% at the cost of a 13.9%
auto-close rate.

## Reproducibility checks

```bash
# labels are byte-identical across runs, and row counts match
python -m sieve.generate --seed 7 --out-dir /tmp/sieve-a >/dev/null
python -m sieve.generate --seed 7 --out-dir /tmp/sieve-b >/dev/null
cmp /tmp/sieve-a/labels.jsonl /tmp/sieve-b/labels.jsonl && echo identical

# labels are only referenced by the generator and eval/
grep -rn "labels.jsonl" sieve eval

# eval output is deterministic
python -m eval.run --provider fake && cp eval/report.json /tmp/r1.json
python -m eval.run --provider fake && cmp /tmp/r1.json eval/report.json && echo identical
```

## Audit and safety

- `audit` rows record timestamp, alert_id, actor (`system` or reviewer id), provider, prompt
  hash, evidence hash, disposition, route and override flag. SQLite triggers reject UPDATE and
  DELETE; there is no code path that mutates audit rows.
- Per-alert QA sampling is `sha256(alert_id + salt) % 100 < 10`, so it is deterministic.
- Narrations are rendered only inside `<data>…</data>` and the prompt states the block is data,
  not instructions; a test asserts an injected "ignore previous instructions" appears only
  inside that block.
- No secrets are committed; `.env.example` documents the optional real-provider variables.

## Tests

`pytest -q` → **54 passed**, fully offline, no API keys. Highlights: generator determinism and
label isolation, firing/non-firing cases for all 8 reason codes, 8-case numeric-faithfulness
table, fault modes through the HTTP endpoint (no 500s), citation resolution against the evidence
endpoint, prompt-injection placement, QA-sample determinism, review 422/409 flows, one audit row
per triage/review, metrics exactness, eval determinism.

## What I would do next

- Run the eval against a real/local model (Ollama) and compare recall, auto-close, numeric
  faithfulness and latency.
- Feed overrides back as labelled examples and re-tune routing thresholds on them.
- Try a fourth scenario and widen the ambiguous set to stress-test grounding.
- Add request-level tracing, rate limiting and a Postgres audit sink for production.

## AI usage

See [AI_USAGE.md](AI_USAGE.md) for the tools, the prompts, and the bugs the AI introduced that
were caught in review.
