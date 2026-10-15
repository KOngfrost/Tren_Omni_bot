---
name: headroom
description: Audits context window headroom, tracks token consumption across skills, rules, and tools, and optimizes prompt budgets to prevent response truncation. Use when session responses degrade, context fills up quickly, or when preparing long multi-step tasks.
---

# Headroom: Context Window & Token Budget Optimizer

Headroom ensures that AI coding agents always maintain sufficient token headroom for deep reasoning and full code outputs.

## Core Headroom Principles

1. **Headroom Buffer**: Maintain at least 20-30% of the active context window free for model reasoning and complete response output.
2. **Progressive Disclosure**:
   - Do not dump entire large files into context. Use targeted line slicing (`StartLine`, `EndLine`).
   - Skills should keep short descriptions and load detailed reference manuals only on demand.
3. **Log Sanitization**: Filter command outputs (e.g. pipe through grep/jq or summarize) rather than capturing thousands of lines of raw build/test logs.

## Audit Workflow

### Step 1: Component Sizing
- **Rules (`AGENTS.md`, `GEMINI.md`)**: Keep under 24 KB (approx 6,000 tokens).
- **Skills (`SKILL.md`)**: Keep descriptions under 30 words, instruction bodies under 400 lines.
- **Tool Outputs**: Always limit search and file-reading results; paginate or grep.

### Step 2: Context Recovery Actions
When headroom falls below 20%:
1. Summarize completed task steps.
2. Rely on persistent artifacts or disk files instead of repeating previous outputs.
3. Delegate intensive research or long scans to dedicated subagents with their own isolated conversation context.
