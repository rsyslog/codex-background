from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CodexConfig:
    sandbox: str = "read-only"
    approval_policy: str = "on-request"
    model: str | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class WorkspaceConfig:
    key: str
    repo: str | None = None
    branch: str = "main"


@dataclass(frozen=True)
class PreScreenConfig:
    module: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    timeout_seconds: int = 20
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PluginConfig:
    name: str
    module: str
    interval_seconds: int | None = None
    rate_limit_per_hour: int | None = None
    rate_limit_per_day: int | None = None
    base_dir: Path = Path(".")
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AppConfig:
    database_path: Path = Path("./codex-bg.sqlite3")
    workdir_root: Path = Path("./workdirs")
    workspace_root: Path = Path("~/aibot")
    workspace_refresh_interval_seconds: int = 900
    poll_interval_seconds: int = 900
    debug: bool = False
    dry_run: bool = False
    codex: CodexConfig = field(default_factory=CodexConfig)
    prescreen: PreScreenConfig = field(default_factory=PreScreenConfig)
    workspaces: dict[str, WorkspaceConfig] = field(default_factory=dict)
    plugins: list[PluginConfig] = field(default_factory=list)


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    with config_path.open("rb") as fh:
        raw = tomllib.load(fh)

    base_dir = config_path.parent.resolve()
    codex = CodexConfig(**raw.get("codex", {}))
    prescreen = _prescreen_config(raw.get("prescreen", {}))
    workspaces = {
        item["key"]: WorkspaceConfig(
            key=item["key"],
            repo=item.get("repo"),
            branch=item.get("branch", "main"),
        )
        for item in raw.get("workspaces", [])
    }

    plugins: list[PluginConfig] = []
    for item in raw.get("plugins", []):
        values = dict(item)
        name = values.pop("name")
        module = values.pop("module")
        interval_seconds = values.pop("interval_seconds", None)
        rate_limit_per_hour = values.pop("rate_limit_per_hour", None)
        rate_limit_per_day = values.pop("rate_limit_per_day", None)
        plugins.append(
            PluginConfig(
                name=name,
                module=module,
                interval_seconds=int(interval_seconds) if interval_seconds is not None else None,
                rate_limit_per_hour=_optional_int(rate_limit_per_hour),
                rate_limit_per_day=_optional_int(rate_limit_per_day),
                base_dir=base_dir,
                values=values,
            )
        )

    return AppConfig(
        database_path=_resolve(base_dir, raw.get("database_path", "./codex-bg.sqlite3")),
        workdir_root=_resolve(base_dir, raw.get("workdir_root", "./workdirs")),
        workspace_root=_resolve(base_dir, raw.get("workspace_root", "~/aibot")),
        workspace_refresh_interval_seconds=int(raw.get("workspace_refresh_interval_seconds", 900)),
        poll_interval_seconds=int(raw.get("poll_interval_seconds", 900)),
        debug=bool(raw.get("debug", False)),
        dry_run=bool(raw.get("dry_run", False)),
        codex=codex,
        prescreen=prescreen,
        workspaces=workspaces,
        plugins=plugins,
    )


def _resolve(base_dir: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _prescreen_config(raw: dict[str, Any]) -> PreScreenConfig:
    values = dict(raw)
    module = values.pop("module", None)
    model = values.pop("model", None)
    reasoning_effort = values.pop("reasoning_effort", None)
    timeout_seconds = values.pop("timeout_seconds", 20)
    return PreScreenConfig(
        module=module,
        model=str(model) if model is not None else None,
        reasoning_effort=str(reasoning_effort) if reasoning_effort is not None else None,
        timeout_seconds=int(timeout_seconds),
        values=values,
    )
