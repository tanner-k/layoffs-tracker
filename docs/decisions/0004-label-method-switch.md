# 0004 — Paper's audited labels through Sep 2026, Jev only after

**Status:** Accepted
**Date:** 2026-11-14

## Context
The working paper labeled every 8-K Item 2.05 filing from Jan 2019 to Sep 2026 with an audited method (an LLM pass plus a blind human audit). The paper is final. A monthly job cannot run a human audit, and Tanner dropped the Claude Haiku second pass ("Let's keep out the Claude Haiku second pass.").

## Decision
Filings dated on or before **2026-09-30** keep the paper's audited labels (`label_method = "paper_audited"`) and are never re-fetched or re-labeled. Filings from **2026-10-01** on are labeled by **Jev** alone (`label_method = "jev"`, model `jev-1.13.0`). Tanner's call: "I'm thinking B because I'm not going to change the paper".

## Rationale
The tracker then agrees with the paper exactly for every month the paper covers (2026 Jan–Sep: 26 of 153, 17%). The switch falls on a quarter boundary, so no quarter mixes methods.

## Consequences
- (positive) No API spend on history; the portfolio and the paper never disagree.
- (tradeoff) On the paper's own filings Jev alone found fewer AI-cited filings (2026 Jan–Sep: 17 vs 26, with 14 unresolved). New quarters show a band from `ai_true` to `ai_true + unresolved`, and every chart marks the switch.
- (followup) Yearly metrics group by year and method, so 2026 has two rows.

## Alternatives considered
- **Jev for every year** — consistent method, but disagrees with the paper on every past year.
- **Jev plus a Haiku second pass** — rejected by Tanner.
