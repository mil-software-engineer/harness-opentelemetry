# AGENTS.md — repo rules

## Context
Source of truth: `docker-compose.yml`, `transformer/app.py`, `configs/*`; docs may lag — trust code. Grep those files for exact ports / token fields / metric labels when needed. Local pipeline only, no external SaaS; transformer is the only component with logic.

## Standards
- Python 3.11, PEP 8. Lint only changed files, and only if asked; no lint configs in repo.
- Transformer stays simple, single-responsibility, no heavy frameworks.
- Behavior changes stay consistent with compose + configs + tests.
- Port/topology changes (compose port mapping, configs, service layout) need explicit confirmation before editing.

## Workflow & budget
- Ambiguous scope or decision: ask once (one round) before coding.
- Before editing: restate task in one line + affected files/layers; confirm scope when ambiguous.
- Only what's asked: no tests/migrations/docs/extra code unless requested; when tests wanted, mirror `tests/` patterns.
- Report minimally: bare comma-separated changed paths +, if needed, one ≤5-word note. Return diff hunks only — never regenerate untouched files. Long explanations: summary first, details on request. When nothing changed: reply "No changes.".
- Never read a whole file or log: grep first, read only matching ranges; re-reading a file you already summarized is forbidden — reuse known content.
- Cap command/log output to the model: pipe to `head`/`tail`/`grep` and show only relevant lines or the error tail (e.g. last lines of a pytest/flake8 run), never the full dump.
- Reuse known values; avoid repeating identical operations within a session.
- Save temp artifacts (plans, lists, research results) under `.dsh/docs/`, each ≤40 lines.
- Reference files as path + line range, never full content.
- Past ~20 messages or large context: suggest summarization or a new session.

## Done
Full requested scope addressed, no partial hand-off; then report per Workflow & budget.
