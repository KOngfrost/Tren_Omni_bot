---
name: claude-mem
description: Recalls and manages durable cross-session project memory, architecture decisions, and developer preferences. Use when retrieving past project context, storing important technical decisions, or preserving work state across sessions.
---

# Claude-Mem: Long-Term Memory Vault

Claude-Mem enables reliable context transfer and long-term memory across coding sessions.

## Memory Structure

Memories are organized into three scopes:
1. **Project Scope** (`.agents/memory/project/` or `.ecc/memory/project/`): Repository-specific architecture decisions, schema designs, domain rules, and coding conventions.
2. **Team Scope** (`.agents/memory/team/`): Version-controlled knowledge shared with the team.
3. **User Scope** (`~/.gemini/memory/` or `~/.claude/memory/`): Developer-specific preferences, workflow habits, and global notes.

## Memory Document Format

Each memory is recorded as structured Markdown:

```markdown
---
id: mem-20261007-auth-architecture
title: Authentication & RBAC Architecture
scope: project
created_at: 2026-10-07T22:30:00Z
tags: [auth, rbac, security, pgbouncer]
---

# Authentication & RBAC Architecture

## Context
Project uses a dual-engine model: FastAPI for web admin and Go Fiber for high-load API.

## Decisions Made
- All department modifications require SUPERADMIN role.
- Sessions use Redis with fail-closed security.
- Outbox pattern ensures at-least-once message delivery with VK random_id deduplication.
```

## Workflow: Recall Before Writing

1. **At Task Start**:
   - Check if project memory documents exist in `.agents/memory/` or `docs/`.
   - Read relevant decisions before making structural code changes.
2. **During Development**:
   - When encountering a non-obvious bug or architectural requirement, note the root cause.
3. **At Task Completion**:
   - Record newly established patterns or decisions in project memory.
