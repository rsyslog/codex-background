# AGENTS.md

## Mandatory Testing

Before committing Python changes, run the project validation suite:

```bash
.venv/bin/ruff check .
.venv/bin/bandit -c pyproject.toml -r src
git ls-files -z | xargs -0 .venv/bin/detect-secrets-hook --baseline .secrets.baseline
.venv/bin/pip-audit -r requirements-dev.txt
.venv/bin/actionlint
.venv/bin/zizmor --offline .github
PYTHONPATH=src python3 -m unittest discover -s tests
python3 -m compileall -q src tests
.venv/bin/python -m build
.venv/bin/python -m pip install --force-reinstall dist/*.whl
.venv/bin/codex-bg --help
```

If a check cannot be run, state the reason in the final response and in the
commit body when the skipped check materially affects confidence.

## Contribution Flow

- Do not push directly to `main`.
- Create a topic branch for every change.
- Run the mandatory validation suite before opening a PR. The same commands
  are documented in README.md under "Development checks".
- Open a PR against `main`.
- Wait for the required GitHub branch-protection checks named exactly `python`
  and `codeql`.
- Obtain approval and resolve all review comments and conversations before
  merge.
- Keep commits focused and use the commit discipline below.

## Commit Discipline

- Keep commits focused on one coherent change.
- Use imperative, descriptive commit subjects, for example `Add per-plugin scheduling state`.
- Prefer subjects under 72 characters.
- Include a body when the commit changes behavior, schema, safety policy, or operator workflow.
- Mention tests or validation in the body when useful.
- Do not mix harness/runtime cleanup with unrelated feature work unless they are required for the same behavior.
