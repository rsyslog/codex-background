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

Plugins can run on independent schedules by setting `interval_seconds` on each
`[[plugins]]` entry. If omitted, the global `poll_interval_seconds` is used.
Set `interval_seconds = 0` to run a plugin every scheduler cycle.
