---
name: omniroute
description: Recommends and configures the optimal LLM tier and provider routing based on task complexity, reasoning requirements, latency, and budget. Use when deciding model selection, setting up multi-model workflows, or optimizing token costs.
---

# Omniroute: Model & Provider Routing

Omniroute provides systematic heuristics for routing AI coding tasks to the best suited model tier.

## Model Tiers & Decision Matrix

| Tier | Representative Models | Best Suited For | Characteristics |
|---|---|---|---|
| **Tier 1: Fast / Mechanical** | Claude 3.5 Haiku, Gemini 1.5/2.0 Flash | Quick lookups, lint fixes, single-line edits, docstrings, formatting | Sub-second latency, lowest token cost |
| **Tier 2: Standard Workhorse** | Claude 3.5/3.7 Sonnet, Gemini 1.5/2.5 Pro | Feature implementation, refactoring, writing unit tests, debugging | Balanced reasoning, strong coding synthesis |
| **Tier 3: Deep Reasoning** | Claude 3.7 Sonnet (Extended Thinking), Opus | Complex architecture, security audits, root cause analysis, concurrency | High reasoning depth, comprehensive verification |

## Routing Heuristic

1. **Analyze Task Surface Area**:
   - Single file, obvious fix -> Tier 1 (Fast)
   - Multi-file feature, tests required -> Tier 2 (Standard)
   - Cross-service redesign, auth/security boundary -> Tier 3 (Deep Reasoning)
2. **Evaluate Risk**:
   - Low risk (documentation, styling, tests) -> Tier 1 or 2
   - High risk (database migrations, cryptography, Docker socket proxy) -> Tier 3
3. **Budget Calibration**:
   - High budget / critical: Route to Tier 3 with verification loop.
   - Low budget / high iteration: Route to Tier 1 first, escalate to Tier 2 on test failure.
