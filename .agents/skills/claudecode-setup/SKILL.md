---
name: claudecode-setup
description: Configures Claude Code environment, synchronizes workspace rules across CLAUDE.md, AGENTS.md, and GEMINI.md, and sets up MCP servers and tools. Use when setting up Claude Code in a repository, auditing CLI configuration, or ensuring cross-harness compatibility.
---

# Claude Code Setup & Interoperability

This skill guides the setup and synchronization of Claude Code configuration and cross-harness instruction parity.

## Instruction File Hierarchy

To maintain consistency across AI coding assistants in the same repository:
- `AGENTS.md`: The single source of truth for architectural constraints, validation commands, and operating rules.
- `CLAUDE.md`: Symlinked or synchronized with `AGENTS.md` for Claude Code CLI.
- `GEMINI.md`: Project-level instructions for Gemini CLI / Antigravity.

## Setup Steps

1. **Rule Synchronization**:
   - Verify `AGENTS.md` has the primary project rules (architecture, conventions, validation commands).
   - If `CLAUDE.md` exists, ensure it imports or mirrors `AGENTS.md` validation rules without drifting.
2. **MCP Server Integration**:
   - Verify `.mcp.json` or Antigravity `mcp_config.json` contains required development servers (e.g., PostgreSQL, Redis, filesystem).
3. **Environment Checks**:
   - Check available runtimes: Python version, Node.js, Go compiler, Docker CLI.
   - Verify non-interactive execution flags and test commands.
