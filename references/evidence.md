---
title: Evidence
impact: REFERENCE
description: Single source of truth for every measured claim about SKILL.state, transcribed from arXiv:2608.26263 with table citations, plus the negative results and boundary conditions the headline numbers hide.
tags: skill-state, evidence, benchmarks, measurements, limitations
---

# Evidence

Every quantitative claim in this skill resolves here. No other file may introduce a number that is not on this page. If a figure elsewhere disagrees with this file, this file is correct and the other file is a bug.

**Source:** Badhe, Tiwari & Chung, *SKILL.state: Scalable Long-Horizon Agent Skills*, arXiv:2608.26263v3 (submitted 26 Aug 2026, v3 02 Sep 2026), cs.AI / cs.MA, accepted at EMNLP. Google LLC / Purdue University. License CC BY 4.0.

**Experimental setup (§5.1).** Temperature 0.0, top-p 1.0 throughout. All synthetic results are mean ± sample SD over 5 procedural generator seeds. Differences between SKILL.state and baselines are statistically significant only at **T ≥ 50** (paired t-test, p < 0.01). Models: Gemini-3-Flash (primary), Gemma-4-31B-it, Qwen-3-8B-it.

**Metric caveat.** The paper defines *Average Prompt Size* as "the mean string character length per LLM invocation" (§4.3), and Table 1 labels the column "Avg Prompt (Chars)". Table 5's caption says "Budget ~1,800 tokens" while §5.6 says "~1,800 characters" for the same control. Treat prompt-size figures as **characters** and the ~1,800 budget as approximate; only the "Total Tokens" columns are token counts.

---

## 1. Long-horizon scaling — Warehouse (Table 1, Gemini-3-Flash)

| T | Runtime | Score | Avg prompt (chars) | Total tokens |
|---|---|---|---|---|
| 10 | ReAct | 0.90 ±0.02 | 3,249 | 9,438 |
| 10 | Memory | 1.00 ±0.00 | 3,300 | 9,972 |
| 10 | Stateful | 1.00 ±0.00 | 3,430 | 10,337 |
| 10 | **SKILL.state** | **1.00 ±0.00** | **1,775** | **5,870** |
| 25 | ReAct | 0.92 ±0.02 | 6,052 | 42,689 |
| 25 | Memory | 0.99 ±0.00 | 6,357 | 43,067 |
| 25 | Stateful | 1.00 ±0.00 | 5,858 | 41,238 |
| 25 | **SKILL.state** | **1.00 ±0.00** | **1,736** | **14,714** |
| 50 | ReAct | 0.88 ±0.04 | 11,931 | 171,658 |
| 50 | Memory | 0.93 ±0.03 | 7,582 | 131,455 |
| 50 | Stateful | 0.94 ±0.00 | 11,594 | 170,992 |
| 50 | **SKILL.state** | **0.96 ±0.01** | **1,773** | **30,151** |
| 100 | ReAct | 0.84 ±0.07 | 36,362 | 1,245,413 |
| 100 | Memory | 0.87 ±0.05 | 29,607 | 1,082,154 |
| 100 | Stateful | 0.91 ±0.02 | 31,354 | 1,062,387 |
| 100 | **SKILL.state** | **0.94 ±0.01** | **1,905** | **65,408** |
| 200 | ReAct | 0.74 ±0.14 | 48,007 | 2,608,755 |
| 200 | Memory | 0.84 ±0.09 | 84,364 | 6,175,509 |
| 200 | Stateful | 0.88 ±0.03 | 72,305 | 5,041,164 |
| 200 | **SKILL.state** | **0.94 ±0.02** | **1,811** | **122,384** |

**Headline figures, exactly as derivable from this table:**

- **16.2× token reduction at T=100** — 1,062,387 (Stateful) vs 65,408 (SKILL.state).
- **T=200: 122,384 tokens vs 6,175,509 (Memory)** — 50.5×. The Memory baseline is the *worst* at T=200, not the best; its summary inflates faster than ReAct's raw transcript.
- **Flat prompt:** SKILL.state average prompt stays in 1,736–1,905 chars across a 20× horizon increase. Baselines grow 15–25×.

**Boundary condition:** at T=10 and T=25, Memory and Stateful also score 1.00. SKILL.state's *accuracy* advantage appears at T≥50; below that the only benefit is token cost.

---

## 2. Long-horizon scaling — Software Repository (Table 6, Gemini-3-Flash)

| T | ReAct | Memory | Stateful | SKILL.state |
|---|---|---|---|---|
| 10 | 0.89 | 0.93 | 1.00 | **1.00** |
| 25 | 0.84 | 0.89 | **0.94** | 0.88 |
| 50 | 0.71 | 0.65 | 0.74 | **0.86** |
| 100 | 0.53 | 0.57 | 0.63 | **0.78** |

SKILL.state prompt is flat at ~2,298–2,545 chars; tokens 7,608 → 90,200 across T=10 → 100, against Stateful's 14,120 → 2,308,000.

**Negative result:** at T=25 on entangled relational state, **SKILL.state loses to Stateful (0.88 vs 0.94)**. The paper's summary claim "matches or exceeds baseline accuracy across all horizons" (§5.2) holds for Warehouse, not for this table. Do not repeat the unqualified version.

---

## 3. Noise robustness (Tables 2 and 9, T=50, Gemini-3-Flash)

Distractors are injected under a `--- BACKGROUND TELEMETRY ---` header: randomly generated, strictly irrelevant, and non-state-altering (Appendix C).

**Warehouse (flat 500-slot state) — Table 2:**

| Noise / turn | ReAct | Memory | Stateful | SKILL.state |
|---|---|---|---|---|
| 5 | 0.68 | 1.00 | 1.00 | **1.00** |
| 20 | 0.61 | 1.00 | 0.98 | 0.97 |
| 50 | 0.53 | 0.96 | 0.98 | 0.98 |

**Software Repository (entangled relational state) — Table 9:**

| Noise / turn | ReAct | Memory | Stateful | SKILL.state |
|---|---|---|---|---|
| 0 | 0.76 | 0.85 | 0.88 | **0.90** |
| 5 | 0.62 | 0.85 | 0.86 | **0.88** |
| 20 | 0.48 | 0.83 | 0.85 | **0.86** |
| 50 | 0.11 | 0.74 | 0.78 | **0.80** |

**How to state this honestly:** the durable result is that the *ReAct-style* runtime collapses under noise (0.68 → 0.53 on Warehouse; 0.76 → 0.11 on Software Repo) while every state-carrying runtime holds. SKILL.state's "≥0.97 at 50 events/turn" is a **Warehouse-only** figure. On entangled state it degrades to 0.80, and Stateful (0.78) and Memory (0.74) are within 2–6 points. Claiming universal noise immunity overstates the paper.

---

## 4. External drift recovery (Tables 3 and 10)

Recovery steps = consecutive turns of hallucinated action after the world changes outside the agent's action loop.

**Warehouse (Table 3):**

| Scenario | ReAct / Memory / Stateful | SKILL.state |
|---|---|---|
| A: Secret Audit | 5–8 | **0** |
| B: Secret Barcode | 6–8 | **0** |
| C: Secret Move | 5–8 | **0** |
| D: Canceled Order | **fails** | **fails** |

**Software Repository (Table 10):**

| Scenario | ReAct | Memory | Stateful | SKILL.state |
|---|---|---|---|---|
| A: Force Push | 12 | 8 | 10 | **0** |
| B: Flaky CI Test | 14 | 9 | 11 | **0** |
| C: PR Closed | **fails** | **fails** | **fails** | **fails** |

This is the cleanest and most transferable result in the paper: **zero-turn recovery in every scenario any runtime solved, across both environments.** It is also the one result with an explicit counterexample — scenarios D and C are unsolved by *all* runtimes, including SKILL.state. Zero-turn recovery is not universal correction; it is zero *lag* on corrections the agent can act on at all.

---

## 5. Public interactive benchmarks (Table 4, Gemini-3-Flash)

| Runtime | CTF pass@1 | CTF prompt | CTF tokens | τ-Retail | τ-Retail prompt | τ-Retail tokens | τ-Airline | τ-Airline prompt | τ-Airline tokens |
|---|---|---|---|---|---|---|---|---|---|
| ReAct | 43.2% | 1,909 | 977k | 48.2% | 2,819 | 4.48M | 21.8% | 5,100 | 4.85M |
| Memory | 46.4% | 1,797 | 1.03M | 29.9% | 2,737 | 4.24M | 23.6% | 4,700 | 4.65M |
| Stateful | 41.8% | 1,946 | 1.13M | 51.7% | 3,065 | 3.92M | 28.1% | 5,400 | 5.28M |
| **SKILL.state** | **54.2%** | **813** | **387k** | **58.3%** | 3,325 | **3.47M** | **32.4%** | **2,800** | **2.88M** |

- **InterCode CTF:** 54.2% pass@1, **+7.8 points** over the strongest baseline (Memory 46.4%) and +12.4 over Stateful. Tokens cut 60.4% vs ReAct, 65.9% vs Stateful. The paper attributes the gain to `tested_hypotheses` and `discovered_flags` in Σ preventing repeated failed commands (§5.5).
- **τ-Bench Airline:** 32.4% pass rate at a flat ~2,800 tokens/step, against baseline prompts peaking **above 11,000 tokens/step**.
- **τ-Bench Retail:** 58.3% pass rate — but note SKILL.state has the *largest* average prompt of any runtime here (3,325 vs 2,737–3,065) and still the lowest cumulative tokens. Bounded ≠ small. A well-populated state can exceed a short transcript per step and still win on the horizon.

---

## 6. Budget-matched controls — the decisive experiment (Tables 5 and 11)

Every baseline is pinned to SKILL.state's ~1,800 budget. This isolates *structure* from *brevity*, and is the answer to "why not just truncate the context?".

**T=100, Warehouse, Gemini-3-Flash (Table 5):**

| Configuration | Score | Avg prompt | Total tokens |
|---|---|---|---|
| Full ReAct (unbounded) | 0.84 | 36,362 | 1,245,413 |
| Truncated (sliding window) | **0.18** | 1,800 | 62,100 |
| Summary-capped | **0.52** | 1,840 | 63,400 |
| ReAct + LLMLingua | **0.22** | 1,810 | 62,350 |
| **SKILL.state (structured)** | **0.94** | 1,905 | 65,408 |

**Across horizons (Table 11):**

| T | SKILL.state | Summary-capped | Truncated | ReAct+LLMLingua | ReAct (full) |
|---|---|---|---|---|---|
| 10 | 1.00 | 0.92 | 0.90 | 0.88 | 0.90 |
| 25 | 1.00 | 0.76 | 0.62 | 0.60 | 0.92 |
| 50 | 0.96 | 0.64 | 0.35 | 0.38 | 0.88 |
| 100 | 0.94 | 0.52 | 0.18 | 0.22 | 0.84 |

**Why this matters more than the 16.2×:** at an identical token budget, sliding-window truncation scores 0.18 and perplexity-based compression 0.22, while structured state scores 0.94. Truncation evicts early inventory allocations; LLMLingua strips "redundant" slot identifiers that are semantically load-bearing (§5.6). Cheap context is not the mechanism — *lossless projection of the past into a schema* is. A budget-matched comparison also shows the crossover: at T=10 everything is within 0.12; by T=100 the spread is 0.76.

---

## 7. Open-weight models (Tables 7 and 8, Warehouse)

**Gemma-4-31B-it:**

| T | ReAct | Memory | Stateful | SKILL.state |
|---|---|---|---|---|
| 10 | 0.90 | 0.85 | 0.90 | **0.98** |
| 25 | 0.64 | 0.72 | 0.76 | **0.84** |
| 50 | 0.31 | 0.41 | 0.55 | **0.68** |
| 100 | — | — | — | **0.42** (§5.7) |

**Qwen-3-8B-it:**

| T | ReAct | Memory | Stateful | SKILL.state |
|---|---|---|---|---|
| 10 | 0.84 | 0.80 | 0.84 | **0.94** |
| 25 | 0.54 | 0.62 | 0.66 | **0.76** |
| 50 | 0.24 | 0.33 | 0.44 | **0.58** |
| 100 | 0.15 | 0.18 | 0.31 | **0.34** |

**Read this before deploying on a small model.** SKILL.state wins at every horizon on both open-weight models — but from a collapsing absolute base. Qwen-3-8B at T=100 scores **0.34**; Gemma-4-31B scores **0.42**. Gemini-3-Flash scores 0.94 on the identical task. The runtime does not rescue a small model over a long horizon; it only loses less. Budget for grammar-constrained decoding, or shorten the horizon.

---

## 8. Error taxonomy (§5.7, Gemma-4-31B at T=100, score 0.42)

| Mode | Share | Description |
|---|---|---|
| Premature state overwrite / deletion | **68%** | Model omits existing keys during update rather than merging in place |
| Schema comprehension / type coercion | **20%** | Inconsistencies between expected nested lists and dictionaries |
| JSON syntax / formatting slips | **12%** | Malformed delimiters, trailing commas |

~80% of failures on open-weight models are structured-output adherence, not reasoning capacity. The paper's prescribed remedy is grammar-constrained decoding.

---

## 9. What the paper does *not* establish

Stating these plainly is part of using the pattern correctly.

- **No multi-agent results.** §7 is explicit that concurrent writes require conflict-resolution semantics in ⊕ that the single-agent design never exercises.
- **No result for a model operating inside a host runtime it does not control.** Every measurement is of a purpose-built harness implementing Algorithm 1. See `references/host-runtime-adaptation.md` for what does and does not transfer.
- **No bound on |Σ| itself.** O(1) is proven with respect to the horizon T *given* bounded |Σ| (Eq. 6). The paper's own CTF schema contains three append-only list fields. See `rules/state-boundedness.md`.
- **No adversarial-observation results.** Noise is defined as "non-state-altering" and randomly generated (Appendix C). Observations that *deliberately* induce a wrong patch are untested. See `rules/untrusted-observations.md`.
- **No single-model generality claim for accuracy.** Only 3 models, one primary. Significance is claimed only for T ≥ 50.

## Citation

```bibtex
@article{badhe2026skillstate,
  title   = {SKILL.state: Scalable Long-Horizon Agent Skills},
  author  = {Badhe, Sanket and Tiwari, Priyanka and Chung, Jonghyun},
  journal = {arXiv preprint arXiv:2608.26263},
  year    = {2026},
  note    = {Accepted at EMNLP}
}
```
