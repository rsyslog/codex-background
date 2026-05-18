# codex-bg

Plugin-driven scheduler for running Codex CLI tasks in the background.

This project is a very early implementation-stage proof of concept. It can run
AI tooling against repository content and, when configured for real runs, can
post GitHub comments or edit issue metadata. Nothing is guaranteed: not
correctness, safety, availability, or suitability for production. Use it at
your own risk, start with `dry_run = true`, and keep credentials tightly scoped.
Feedback and review are welcome, especially around safety and operational
failure modes.

The initial plugin polls GitHub issues, triages new untriaged issues with Codex,
then posts findings and applies allowlisted metadata through `gh`.

## Quick start

```bash
python -m pip install -e .
cp scheduler.example.toml scheduler.toml
codex-bg --config scheduler.toml --debug once
codex-bg --config scheduler.toml status
```

`gh`, `git`, and `codex` must be installed and authenticated for real runs.
Do not run against an important repository until the dry-run output is
acceptable. Debug mode prints prompts and AI replies, which may include issue
contents and local paths; keep those logs private.

To run continuously after validation:

```bash
codex-bg --config scheduler.toml --debug run
```

## Safety Notes

- `dry_run = true` is the recommended starting mode.
- The built-in defaults use a read-only Codex sandbox and on-request approval.
- Use a dedicated GitHub identity or token with the smallest useful
  permissions.
- The scheduler stores task payloads, prompts, model replies, run artifacts, and
  SQLite state locally. These can contain private issue or repository content.
- Real `scheduler.toml` files, databases, logs, artifacts, and workdirs should
  not be committed.
- The GitHub issue triage plugin constrains labels and milestones to configured
  allowlists, but AI output still needs review before trusting it on important
  repositories.

## Development checks

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
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

Optional local pre-commit checks use the same installed development tools:

```bash
.venv/bin/pre-commit install
.venv/bin/pre-commit run --all-files
```

Plugins can run on independent schedules by setting `interval_seconds` on each
`[[plugins]]` entry. If omitted, the global `poll_interval_seconds` is used.
Set `interval_seconds = 0` to run a plugin every scheduler cycle.
Plugins can also set `rate_limit_per_hour` and `rate_limit_per_day`; events
above the configured limits are dropped and reported through operator
notifications. The issue triage plugin defaults to 15 accepted events per hour
and 30 accepted events per day.

## Event Sources

The continuous `run` command uses an internal producer/consumer loop. Event
sources submit work to the durable SQLite queue, and the worker waits on a
notification condition until new tasks arrive. The worker does not poll plugins
directly.

Supported source modes:

- **Scheduled plugin source:** implement `generate_events(context)`. The
  scheduler runs it when the plugin's `interval_seconds` is due. This is the
  right mode for GitHub issue polling and other APIs without push delivery.
- **Plugin-owned listener:** optionally implement
  `run_event_source(context, sink, stop_event)`. The plugin owns the blocking
  wait, such as a webhook server, message queue consumer, or filesystem watcher,
  and calls `sink.submit([...])` as soon as it detects work.
- **One-shot harness:** `codex-bg once` still runs workspace refresh, event
  generation, and one worker pass synchronously for smoke tests.

Shared repository checkouts are read-only context under `workspace_root`.
Plugins or Codex tasks that need code changes must create separate git
worktrees under the scheduler workdir instead of mutating the shared checkout.

## License

Apache License 2.0. See `LICENSE`.
