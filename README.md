# SysDesign-Bench

**A fully deterministic benchmark for system design from requirements.**

LLMs are benchmarked extensively on writing code — and barely at all on the decisions that
happen *before* code: choosing an architecture from requirements, making tradeoffs the
constraints actually entail, refusing to design what physics forbids, asking when critical
information is missing, and migrating live data without downtime. Those are the expensive
decisions. SysDesign-Bench measures them, and it does so **without an LLM judge anywhere in
the grading path**.

## How it works

Each scenario gives the model a requirements document with numbered constraints and
requirements, plus three global, version-pinned catalogs: a **component menu** (databases,
brokers, caches, languages), a **pattern catalog** (~100 architectural and data patterns),
and a **role taxonomy**. The model responds with one structured JSON **design spec** — not a
prose essay:

- `mode` — `design`, `infeasible`, or `clarify`
- `design` mode: components with roles, selected patterns, per-flow decisions
  (consistency, transport, storage) for decision slots the scenario defines, and a
  coverage map linking every requirement ID to the choice that addresses it
- `infeasible` mode: the conflicting constraint IDs and a proposed relaxation from the
  scenario's enumerated options
- `clarify` mode: the missing-information keys, from a fixed taxonomy

Grading is pure set and enum matching in Python (`grade.py`). Every score is auditable by
reading the model's JSON next to the scenario's gold sets. An optional `notes` field lets
models explain themselves; it is **never graded**.

## Scored dimensions

| Dimension | What passes |
|---|---|
| Requirements coverage | Every requirement ID mapped to an acceptable addressing choice |
| Tradeoff selection | Required patterns present, forbidden ("attractive-but-wrong") patterns absent, decision slots in acceptable sets |
| Infeasibility detection | Over-constrained scenarios: names the conflicting constraint IDs and a valid relaxation instead of confidently designing |
| Clarification seeking | Underspecified scenarios: asks for the missing keys instead of silently assuming |
| Migration safety | Zero-downtime moves: expand/contract, dual-write/CDC, backfill, rollback, correct sequencing |
| Operability | Planted traps avoided: idempotency on retried writes, backpressure on fan-out, bounded queues |
| Parsimony | Over-engineering is scored: forbidden patterns plus a cap on total selections |

Headline metric: macro-average across dimensions, with the per-dimension breakdown and each
model's **format-compliance rate** (how often its spec validated) reported alongside.

## Philosophy

1. **Deterministic or it doesn't ship.** Respected benchmarks grade deterministically
   wherever the task allows — SWE-bench executes test suites, τ-bench compares final state,
   BFCL matches structured calls, IFEval runs programmatic checks, LiveBench is judge-free
   by design. Holistic LLM-judge scoring has documented biases and no place here. Our rule
   is enforced by the validator: a checklist item that cannot be expressed as a set check
   is not a valid item.
2. **Correctness by construction.** Every scenario's gold answer must be *entailed* by its
   stated constraints plus arithmetic — never by taste. Each scenario carries a
   `gold_rationale` with the worked math proving the gold answer uniquely satisfies the
   constraints and that each trap violates a specific, named one.
3. **Constrained answer space.** All choices come from global version-pinned catalogs, so
   claims are falsifiable against documented properties. The catalogs are identical for
   every scenario — a 100-option multiple choice leaks no per-scenario hints. Correct
   answers are pinned by constraint math, never by menu elimination: the wrong options are
   temptingly wrong, not absurdly wrong.
4. **Contamination resistance by twist, not obscurity.** Scenarios are de-labeled (described
   functionally, never by their famous name) and each carries a **constraint inversion**: one
   load-bearing constraint is perturbed so a specific piece of the canonical blog answer
   becomes a scored trap. Reciting training data doesn't just fail to help — it actively
   fails items. Every scenario must declare which canonical answer its twist invalidates.
5. **Anti-gaming symmetry.** Precision scoring punishes pattern-spraying. Fully-specified
   scenarios punish reflexive clarifying; feasible-but-scary scenarios punish crying
   infeasible whenever the numbers look tight.
6. **Auditable end to end.** Generation and grading are decoupled (SWE-bench convention):
   `sdbench run` writes a `predictions.jsonl`, `sdbench grade` scores it. Anyone can grade
   their own model's outputs, re-grade old runs, or dispute a score by reading the grader.

## Quickstart

    pip install -e ".[run]"          # plain `pip install -e .` suffices for grading only
    sdbench run --model <litellm-model-id> --scenarios scenarios/public   # → predictions.jsonl
    sdbench grade predictions.jsonl --scenarios scenarios/public          # → scores.json
    sdbench report scores.json               # → markdown leaderboard + failed-item detail

Protocol: temperature 0, pass@1, canonical prompt template, N-run variance reported.
Grading makes zero LLM calls; only `run` needs API keys. Always pass the public
scenario path for publishable runs: the CLI's generic default recursively includes
the local, gitignored held-out split.

## Dataset status

Version 0.3.0 contains **50 scenarios**: 38 public and 12 held-out. The v0.2
expansion contributed 22 scenarios (16 public and 6 held-out); v0.3 adds 20
more (14 public and 6 held-out) across coordination, causal and bitemporal
semantics, recovery, observability, scheduling, cryptographic membership,
ML correctness, and probabilistic latency. Held-out YAML is intentionally
gitignored and must be backed up in a private store.

## Baseline results (v0.3 — 38 public scenarios, reasoning effort HIGH)

| Model | Overall | Solution | clarify | infeas. | migration | operability | parsimony | coverage | tradeoffs | Mode | Format | Cost | Wall time |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| claude-fable-5 | **92%** | 100% | 100% | 100% | 100% | 100% | 50% | 96% | 99% | 100% | 100% | $6.67 | 25 min |
| grok-4.5 | **88%** | 100% | 100% | 100% | 100% | 100% | 24% | 94% | 99% | 100% | 100% | $1.17¹ | 15 min |
| gpt-5.6-sol | **81%** | 98% | 50% | 75% | 100% | 100% | 47% | 94% | 99% | 100% | 100% | ~$5² | 50 min |
| claude-opus-4-8 | **75%** | 98% | 0% | 100% | 100% | 100% | 32% | 95% | 100% | 95% | 100% | $4.21 | 30 min |
| GLM-5.2 (Baseten) | **72%** | 96% | 0% | 75% | 100% | 92% | 47% | 90% | 98% | 92% | 100% | $1.00 | 19 min |

Protocol: pass@1, single run, July 2026, reasoning effort high everywhere
(Anthropic adaptive thinking / OpenAI-style reasoning_effort; temperature at
provider default for reasoning modes). **Solution** = pass rate over the
mode-defining answer checks only (decision slots, conflict sets, missing-info
sets) — "did the model actually solve it." **Overall** macro-averages all
dimensions — "would you want it designing systems you operate."
¹ xAI console figure; recorded value predated the reasoning-token accounting
fix. ² gpt-5.6-sol run interrupted once and topped up via targeted rerun.

Headline findings:
- **Architectural correctness is saturated at high effort**: every model
  solves 96-100% of the mode-defining decisions. The spread lives in the
  supporting dimensions.
- **Over-engineering is the discriminator**: parsimony spans 24-50% and no
  model is good at restraint; the Solution-vs-Overall gap is a legible
  "brilliant but over-builds" signature (grok: 100%/88%).
- **Clarification-seeking is binary and thinking-invariant**: fable and grok
  always ask when load-bearing facts are missing; opus and GLM design anyway
  at every thinking budget tested (three consecutive pilots); gpt splits.
- **Haystack pair delta was zero-to-positive**: no model lost solution content
  when requirements were buried in ~1,200 words of documents with decoys —
  longer documents are needed to stress extraction (v0.4).

Calibration status: 327 items graded; 44 discriminate, 13 fail for all five
models and are under review per the all-fail-indicts-the-item rule (11 are
parsimony caps, 2 are coverage acceptable-sets). v0.1/v0.2 pilot tables are
retained in git history.

## What we consciously don't measure

- **Prose quality.** The ungraded `notes` field exists for human inspection, but reasoning
  eloquence earns nothing.
- **Out-of-catalog creativity.** A design outside the catalogs scores as an omission. The
  bounded answer space is the price of deterministic grading; catalog gaps are fixed by
  versioned catalog updates, not judge discretion.

## Contributing

**Scenarios** are the benchmark's real substance, and the bar is deliberately high. A
scenario PR must include:

1. Numbered `constraints` and `requirements` (some implicit but derivable), `flows` with
   decision slots, and complete `gold` sets (required/forbidden patterns, slot answers,
   coverage map, parsimony cap).
2. `traps` — each tagged with the specific constraint it violates and why it's attractive.
3. A `twist` naming the canonical answer it invalidates, with at least one gold item that
   canonical recitation fails (enforced by lint).
4. A `gold_rationale` with the worked math. This is the review artifact: a second reviewer
   checks the math, not vibes.
5. A clean `sdbench validate` pass, and (for inclusion in a released split) evidence the
   items discriminate — piloted against ≥3 models with non-uniform pass rates.

What gets rejected: scenarios whose answer requires taste rather than entailment; twists
that are flavor rather than inversion; giveaways where the menu eliminates itself; items
expressible only as free-text judgments.

**Catalog additions** are versioned and additive, and must cite the documented property
(vendor docs) that makes the new entry checkable.

## License

Code: MIT. Dataset (`scenarios/`, `catalogs/`): CC-BY-4.0.
