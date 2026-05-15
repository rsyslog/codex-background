# codex-bg

Plugin-driven scheduler for running Codex CLI tasks in the background.

The initial plugin polls GitHub issues, triages new untriaged issues with Codex,
then posts findings and applies allowlisted metadata through `gh`.

## Quick start

```bash
python -m pip install -e .
codex-bg once --config scheduler.toml --debug
codex-bg run --config scheduler.toml --debug
codex-bg status --config scheduler.toml
```

`gh`, `git`, and `codex` must be installed and authenticated for real runs.

## Development checks

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/bandit -c pyproject.toml -r src
PYTHONPATH=src python3 -m unittest discover -s tests
python3 -m compileall -q src tests
```

Plugins can run on independent schedules by setting `interval_seconds` on each
`[[plugins]]` entry. If omitted, the global `poll_interval_seconds` is used.
Set `interval_seconds = 0` to run a plugin every scheduler cycle.

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
