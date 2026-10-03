# AGENTS.md

This repository is the OSS student support platform: a Python service stack for bot/web/admin flows, plus a Go API under `backend_go/` and containerized infrastructure via Docker Compose.

## Project shape

- `core/` — shared business logic, models, DB/session/security helpers, task orchestration, audit and startup guards.
- `web/` — FastAPI web admin application and related routes/security utilities.
- `bots/` — Telegram/VK bot integrations.
- `backend_go/` — Go Fiber API for high-load endpoints.
- `tests/` — pytest coverage for unit/integration/smoke and UI flows.
- `scripts/` — operational and admin helper scripts.
- `docs/` — deployment, operations, database, security, and admin guidance.

## Before making changes

- Prefer the smallest root-cause fix that matches the codebase’s existing patterns.
- Read the relevant module and its nearby tests before broad refactoring.
- For security, auth, DB, or deployment changes, review the project documentation before editing defaults or flows.
- Keep changes aligned with the existing layered architecture instead of introducing ad hoc abstractions.

## Validation commands

Run the project checks that are expected by the repo before considering a change complete:

```bash
ruff check .
ruff format --check .
mypy core web scripts bots
pytest
```

If a change affects Docker or environment wiring, also validate with the project’s runtime flow described in [README.md](README.md) and [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Important conventions

- Python target is 3.11+; config is centralized in [pyproject.toml](pyproject.toml) and [mypy.ini](mypy.ini).
- Ruff configuration lives in [pyproject.toml](pyproject.toml); do not duplicate a second `ruff.toml` or `pytest.ini` in this repo.
- The project intentionally uses a layered design: shared business rules in `core`, API/UI behavior in `web`, and integrations in `bots`.
- Thread safety, session handling, and multi-worker safety are significant concerns; follow the patterns already used in `core/` and `web/` rather than simplifying them blindly.
- Tests are authoritative for behavior; when adding or changing workflows, add or update the relevant pytest coverage.

## Recommended reading

- [README.md](README.md) — system overview, quickstart, architecture, and stack.
- [CONTRIBUTING.md](CONTRIBUTING.md) — contribution and validation workflow.
- [TECHNICAL_SPEC.md](TECHNICAL_SPEC.md) — deeper architecture and business constraints.
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — deployment and environment setup.
- [SECURITY.md](SECURITY.md) — security model and operational safeguards.
- [docs/DATABASE_SCHEMA.md](docs/DATABASE_SCHEMA.md) — schema and data model references.

## Working style for AI agents

- Keep edits surgical and easy to review.
- Favor reusing existing helpers and established patterns over introducing new frameworks or libraries.
- Preserve existing naming, async patterns, and operational assumptions.
- If a task spans multiple subsystems, document the assumptions in the patch and keep the change set focused.
- Avoid “cleanup-only” refactors in the same patch as functional changes unless required to make the fix coherent.

## When in doubt

Use the repository docs as the source of truth, then validate with the repo’s normal checks. This project is intentionally opinionated about architecture, safety, and test-first verification.
