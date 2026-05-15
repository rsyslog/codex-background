# AGENTS.md

## Mandatory Testing

Before committing Python changes, run the project validation suite:

```bash
.venv/bin/ruff check .
.venv/bin/bandit -c pyproject.toml -r src
git ls-files -z | xargs -0 .venv/bin/detect-secrets-hook --baseline .secrets.baseline
PYTHONPATH=src python3 -m unittest discover -s tests
python3 -m compileall -q src tests
```

If a check cannot be run, state the reason in the final response and in the
commit body when the skipped check materially affects confidence.

## Commit Discipline

- Keep commits focused on one coherent change.
- Use imperative, descriptive commit subjects, for example `Add per-plugin scheduling state`.
- Prefer subjects under 72 characters.
- Include a body when the commit changes behavior, schema, safety policy, or operator workflow.
- Mention tests or validation in the body when useful.
- Do not mix harness/runtime cleanup with unrelated feature work unless they are required for the same behavior.
