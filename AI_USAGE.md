# AI usage

## Tools

- **Qoder (Claude-based coding agent)** — used as the primary pair-programmer for scaffolding,
  drafting modules, and writing the first pass of the test suite. Every file was read and
  reviewed; design decisions (evidence-ID contract, grounding strictness, routing policy,
  conservative auto-close threshold) were made and checked by me.

No Copilot/Cursor/Codex was used, and no generator output or label was ever pasted into the
triage path.

## Key prompts (paraphrased)

1. "Implement Project Sieve from the attached brief: seeded generator, stable evidence IDs,
   config-driven rules, provider interface with fault modes, citation + numeric grounding,
   FastAPI, eval harness, review queue with append-only audit."
2. "Keep the LLM strictly downstream of a frozen evidence pack; the model only returns
   `{disposition, confidence, narrative[{text, citations}]}` and code validates it."
3. "Design the generator so each benign alert has a visible explanation and ~10% of alerts are
   genuinely ambiguous with randomly assigned labels."
4. "Write tests against the brief's 'Done means' list, including a table-driven numeric
   faithfulness test and endpoint tests for every faulty FakeProvider mode."
5. "Review this module for label leakage, non-determinism, and places where a provider failure
   could close an alert."

## Where the AI was wrong, and how I caught it

- **Customer reuse contaminated every lookback window.** The first generator reused 90
  customers across 360 alerts, so transactions from other alerts leaked into 30-day windows.
  Benign salary alerts scored 0.73 and some suspicious alerts scored 0.0. I caught it by
  printing rule scores grouped by planted reason, then fixed it by giving each alert a
  dedicated customer and scenario-specific customer pools, and added a determinism test.
- **The benign-salary rule was too permissive.** An early version fired whenever a salary
  credit existed, so six suspicious cash alerts were "explained" by one salary credit and
  scored below the threshold. Caught by inspecting the lowest-scoring suspicious cash alerts;
  fixed by requiring salary to dominate (non-salary credits < 0.5× income) and rejecting
  high-risk debit flows in the same pack.
- **The FakeProvider had the same blind spot.** It treated a baseline salary as a benign
  explanation for cash structuring (recall 87.7%). Caught in the confusion matrix; fixed with a
  cash-dominance check so salary only explains alerts where it actually dominates.
- **Numeric-grounding fixture was off by hours.** A fixture put a "7-day" cash transaction at
  07:00 while the cutoff was 09:00, so the derived count was 2 and the table test failed for
  the wrong reason. Caught by the failing case and fixed the fixture windows.
- **A label-isolation test was wrong, not the code.** `"import eval"` matched
  `evaluate_rules`; replaced with an anchored regex.

## What I verified by hand

- `pytest -q` passes offline with no keys (53 tests).
- Generator labels are byte-identical across runs; row counts match.
- `grep -rn "labels.jsonl" sieve eval` shows only the generator and `eval/` touch labels.
- Every endpoint was exercised with `TestClient` (404s, 409s, 422s, faulty provider modes) and
  a manual `curl` smoke test.
- The eval report was generated twice and compared byte-for-byte.
- Audit UPDATE/DELETE are rejected by SQLite triggers.

## What I would not delegate

The trade-off between the recall-maximising rules-only threshold (0.15) and the LLM's precision
gain, the auto-close floor, and the "never close on an ungrounded narrative" rule are risk
decisions. I chose conservative routing (13.9% auto-close, 100% routed recall) and documented
the trade-off in the README; the AI did not decide those policy values.
