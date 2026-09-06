# AGENTS.md — repo rules

## Context
Source of truth: `docker-compose.yml`, `transformer/app.py`, `configs/*`; docs may lag — trust code. Grep those files for exact ports / token fields / metric labels when needed. Local pipeline only, no external SaaS; transformer is the only component with logic.

## Standards
- Python 3.11, PEP 8. Lint only changed files, and only if asked; no lint configs in repo.
- Transformer stays simple, single-responsibility, no heavy frameworks.
- Behavior changes stay consistent with compose + configs + tests.
- Port/topology changes (compose port mapping, configs, service layout) need explicit confirmation before editing.

## Workflow & budget
- Ambiguous scope or decision: ask once before coding.
- Before editing: one-line task restatement + affected files.
- Only what's asked: no tests/docs/extra code unless requested; mirror `tests/` when tests are wanted.
- Report result as short as possible: bare comma-separated list of changed file paths and, if truly needed, one ≤5-word note. No prose, no summary sentences, no rationale, no restating the task. When nothing changed: reply "No changes.".
- Never read whole files/logs: grep first, read only matching ranges; cap command output to error tail.

## Done
Full requested scope addressed, no partial hand-off; then report per Workflow & budget.
