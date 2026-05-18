from __future__ import annotations

# subprocess calls are constrained to explicit argv lists and used for trusted
# local CLIs configured by the scheduler (gh, git, codex).
import subprocess  # nosec B404
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


class CommandError(RuntimeError):
    def __init__(self, result: CommandResult):
        super().__init__(result.stderr.strip() or result.stdout.strip() or "command failed")
        self.result = result


class Runner:
    def run(
        self,
        args: list[str],
        *,
        cwd: str | Path | None = None,
        input_text: str | None = None,
        check: bool = True,
        timeout: int | float | None = None,
    ) -> CommandResult:
        try:
            completed = subprocess.run(
                args,
                cwd=str(cwd) if cwd else None,
                input=input_text,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                args=args,
                returncode=124,
                stdout=_timeout_output(exc.stdout),
                stderr=f"command timed out after {timeout} seconds",
            )
        result = CommandResult(
            args=args,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
        if check and result.returncode != 0:
            raise CommandError(result)
        return result


def _timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value
