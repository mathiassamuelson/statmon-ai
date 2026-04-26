"""Subprocess execution for catalog tools.

Hard guarantees the chat side relies on:
  * timeout actually kills the subprocess (not just cancels the await)
  * stdout is read with a hard byte cap; the producer is killed on cap
  * every subprocess gets a sanitized env (PATH/LANG/LC_ALL only)
  * the executor never invokes a shell — pipelines (stage 4) chain
    create_subprocess_exec calls directly

run_tool() is the single-segment entry point used by server.call_tool().
run_pipeline() is the multi-segment entry point used by stage 4.
"""

from __future__ import annotations

import asyncio
import json
import shlex
import time
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from .catalog import ToolEntry


SAFE_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LANG": "C",
    "LC_ALL": "C",
}

STDERR_CAP_BYTES = 8192


async def _read_capped(stream: asyncio.StreamReader, cap: int) -> tuple[bytes, bool]:
    """Read up to `cap` bytes from stream. Returns (data, truncated)."""
    if stream is None:
        return b"", False
    chunks: list[bytes] = []
    total = 0
    truncated = False
    while total < cap:
        chunk = await stream.read(min(65536, cap - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    if total >= cap:
        # Probe one more byte to detect overflow.
        more = await stream.read(1)
        if more:
            truncated = True
    return b"".join(chunks), truncated


async def _kill(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            return
        try:
            await proc.wait()
        except Exception:
            pass


def _build_argv(entry: "ToolEntry", args: str) -> list[str]:
    return [entry.binary, *entry.prepend_args, *shlex.split(args)]


async def run_tool(entry: "ToolEntry", args: str) -> dict:
    """Execute a single catalog tool with `args` and return the envelope dict."""
    if entry.binary is None:
        return {
            "status": "error",
            "error": entry.unhealthy_reason or f"Binary not found: {entry.binary_raw}",
        }

    argv = _build_argv(entry, args)
    start = time.monotonic()

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=SAFE_ENV,
        )
    except FileNotFoundError:
        return {"status": "error", "error": f"Binary not found: {entry.binary}"}

    stdout_task = asyncio.create_task(_read_capped(proc.stdout, entry.max_bytes))
    stderr_task = asyncio.create_task(_read_capped(proc.stderr, STDERR_CAP_BYTES))

    try:
        stdout, stdout_trunc = await asyncio.wait_for(stdout_task, timeout=entry.timeout_seconds)
    except asyncio.TimeoutError:
        await _kill(proc)
        stdout_task.cancel()
        stderr_task.cancel()
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "status": "error",
            "error": f"Command timed out after {entry.timeout_seconds}s",
            "execution_time_ms": elapsed_ms,
        }

    if stdout_trunc:
        # Producer is still alive (or just finished overflowing); reap it.
        await _kill(proc)
        stdout = stdout + f"\n[output truncated at {entry.max_bytes} bytes]".encode()

    try:
        stderr, _ = await asyncio.wait_for(stderr_task, timeout=2)
    except asyncio.TimeoutError:
        stderr_task.cancel()
        stderr = b""

    await proc.wait()
    elapsed_ms = int((time.monotonic() - start) * 1000)

    result: dict = {
        "exit_code": proc.returncode,
        "execution_time_ms": elapsed_ms,
    }

    # A cap-induced kill is treated as success-with-truncation: we got what we
    # asked for, then stopped the producer on purpose.
    if proc.returncode == 0 or stdout_trunc:
        result["status"] = "success"
        text = stdout.decode(errors="replace").strip()
        try:
            result["result"] = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            result["result"] = text
        if stdout_trunc:
            result["truncated"] = True
    else:
        result["status"] = "error"
        result["error"] = (
            stderr.decode(errors="replace").strip()
            or stdout.decode(errors="replace").strip()
            or f"exit code {proc.returncode}"
        )

    return result


async def run_pipeline(stages: Sequence[tuple["ToolEntry", str]]) -> dict:
    """Execute a pipeline of catalog tools. The lead's timeout bounds the
    whole pipeline; the last stage's max_bytes caps captured stdout.
    """
    if not stages:
        return {"status": "error", "error": "empty pipeline"}
    if len(stages) == 1:
        return await run_tool(*stages[0])

    lead_entry, _ = stages[0]
    last_entry, _ = stages[-1]
    timeout = lead_entry.timeout_seconds
    cap = last_entry.max_bytes

    procs: list[asyncio.subprocess.Process] = []
    start = time.monotonic()
    prev_stdout: int | asyncio.StreamReader | None = asyncio.subprocess.DEVNULL

    try:
        for i, (entry, args) in enumerate(stages):
            if entry.binary is None:
                for p in procs:
                    await _kill(p)
                return {
                    "status": "error",
                    "error": f"segment {i} ({entry.name}): {entry.unhealthy_reason}",
                }
            argv = _build_argv(entry, args)
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=prev_stdout,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=SAFE_ENV,
            )
            procs.append(proc)
            # Once handed off to the next stage's stdin, our reference to the
            # prior pipe is no longer ours to read from.
            prev_stdout = proc.stdout
    except FileNotFoundError as e:
        for p in procs:
            await _kill(p)
        return {"status": "error", "error": f"Binary not found: {e}"}

    last = procs[-1]
    stdout_task = asyncio.create_task(_read_capped(last.stdout, cap))
    stderr_task = asyncio.create_task(_read_capped(last.stderr, STDERR_CAP_BYTES))

    try:
        stdout, stdout_trunc = await asyncio.wait_for(stdout_task, timeout=timeout)
    except asyncio.TimeoutError:
        for p in procs:
            await _kill(p)
        stdout_task.cancel()
        stderr_task.cancel()
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "status": "error",
            "error": f"Pipeline timed out after {timeout}s",
            "execution_time_ms": elapsed_ms,
        }

    if stdout_trunc:
        for p in procs:
            await _kill(p)
        stdout = stdout + f"\n[output truncated at {cap} bytes]".encode()

    try:
        stderr, _ = await asyncio.wait_for(stderr_task, timeout=2)
    except asyncio.TimeoutError:
        stderr_task.cancel()
        stderr = b""

    for p in procs:
        try:
            await asyncio.wait_for(p.wait(), timeout=2)
        except asyncio.TimeoutError:
            await _kill(p)

    elapsed_ms = int((time.monotonic() - start) * 1000)
    last_rc = last.returncode

    warnings = []
    for i, p in enumerate(procs[:-1]):
        if p.returncode not in (0, None):
            warnings.append({"segment": i, "tool": stages[i][0].name, "exit_code": p.returncode})

    result: dict = {
        "exit_code": last_rc,
        "execution_time_ms": elapsed_ms,
        "pipeline": [{"tool": e.name, "args": a} for e, a in stages],
    }

    if last_rc == 0:
        result["status"] = "success"
        result["result"] = stdout.decode(errors="replace").rstrip("\n")
    else:
        result["status"] = "error"
        result["error"] = (
            stderr.decode(errors="replace").strip()
            or stdout.decode(errors="replace").strip()
            or f"exit code {last_rc}"
        )

    if warnings:
        result["warning"] = warnings

    return result


# Back-compat shim retained until all call sites are migrated.
async def run_cli(binary: str, subsystem: str, command: str, timeout: int) -> dict:
    """Deprecated: prefer run_tool(entry, args). Used only by legacy tests."""
    from .catalog import ToolEntry

    prepend = [subsystem] if subsystem else []
    entry = ToolEntry(
        name="<legacy>",
        description="",
        binary_raw=binary,
        binary=binary,
        prepend_args=prepend,
        timeout_seconds=timeout,
        max_bytes=1048576,
        pipe_stage=False,
        rules={"deny": [], "allow": ["*"]},
    )
    return await run_tool(entry, command)
