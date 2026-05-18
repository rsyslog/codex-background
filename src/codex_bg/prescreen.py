from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from codex_bg.config import AppConfig
from codex_bg.runner import Runner


def _noop_debug(message: str) -> None:
    return None


@dataclass(frozen=True)
class ScreeningRequest:
    """Plugin-supplied request for a go/no-go safety gate.

    The request carries untrusted subject data plus plugin-specific policy. A
    screener may use static rules, AI, external systems, or a combination of
    them, but it returns only whether the scheduler should create follow-up
    work. Denied requests are intentionally ignored and left for humans.
    """

    plugin_name: str
    subject_type: str
    subject_id: str
    payload: dict[str, Any]
    policy: str


@dataclass(frozen=True)
class ScreeningResult:
    allowed: bool
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PreScreenContext:
    app: AppConfig
    runner: Runner
    debug: Callable[[str], None] = _noop_debug


class PreScreener(Protocol):
    """Extension point for plugin pre-screening.

    Operators can register an implementation with Scheduler(..., pre_screener=...)
    or through config. The default implementation accepts everything so plugins
    can call this API without forcing every deployment to use AI screening.
    """

    def screen(self, context: PreScreenContext, request: ScreeningRequest) -> ScreeningResult:
        raise NotImplementedError


class AcceptAllPreScreener:
    def screen(self, context: PreScreenContext, request: ScreeningRequest) -> ScreeningResult:
        return ScreeningResult(True, "pre-screening disabled")
