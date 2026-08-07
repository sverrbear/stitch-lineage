"""Run `dbt docs generate` as a subprocess before resolving (SPEC.md section 7.1)."""

import subprocess
import sys
from collections import deque
from pathlib import Path

_STDERR_TAIL_LINES = 20


class StitchDbtRunnerError(Exception):
    """`dbt docs generate` could not run or failed; the message names the fix."""


def run_docs_generate(project_dir: Path, extra_args: list[str] | None = None) -> None:
    """Run `dbt docs generate` in project_dir, streaming its output to the terminal.

    stdout/stdin are inherited so dbt progress and interactive prompts (e.g. MFA
    pushes) reach the user directly; stderr is echoed through while keeping a tail
    for the error message. extra_args are appended to the command (e.g.
    ["--target", "prod"]).

    Raises:
        StitchDbtRunnerError: dbt is not on PATH, or exited nonzero -- with the last
            stderr lines included so the failure is diagnosable from the message.
    """
    command = ["dbt", "docs", "generate", *(extra_args or [])]
    try:
        process = subprocess.Popen(
            command,
            cwd=project_dir,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise StitchDbtRunnerError(
            "dbt executable not found on PATH -- install dbt or activate the "
            "environment it lives in, or run 'dbt docs generate' yourself and "
            "re-run stitch build without --docs"
        ) from exc

    tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)
    if process.stderr is not None:
        for line in process.stderr:
            sys.stderr.write(line)
            tail.append(line.rstrip("\n"))
    returncode = process.wait()
    if returncode != 0:
        detail = "\n".join(tail)
        raise StitchDbtRunnerError(
            f"'{' '.join(command)}' exited with code {returncode}"
            + (f" -- last stderr lines:\n{detail}" if detail else "")
        )
