"""Subprocess execution for CLI tools.

Uses shlex.split() to correctly handle S-expression filter strings
with spaces and parentheses in quoted arguments.

The executor builds the full command as: binary [subsystem] command [args...].
Example: /usr/local/nom/sbin/nom-tell statmon querystore.top-clients duration=3600
"""

import asyncio
import json
import shlex
import time


async def run_cli(
    binary: str, subsystem: str, command: str, timeout: int
) -> dict:
    """Execute a CLI command as a subprocess and return a structured result.

    Args:
        binary: Path to the CLI binary (e.g., /usr/local/nom/sbin/nom-tell).
        subsystem: Subsystem name to pass as first arg (e.g., 'statmon').
                   If empty, omitted from the command line.
        command: The command string (e.g., 'querystore.top-clients duration=3600').
        timeout: Maximum execution time in seconds.

    Returns:
        Dict with status, exit_code, execution_time_ms, and result or error.
    """
    cmd_parts = shlex.split(command)
    if subsystem:
        cmd_parts = [subsystem] + cmd_parts

    start = time.monotonic()

    try:
        proc = await asyncio.create_subprocess_exec(
            binary,
            *cmd_parts,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        elapsed_ms = int((time.monotonic() - start) * 1000)

        result = {
            "exit_code": proc.returncode,
            "execution_time_ms": elapsed_ms,
        }

        if proc.returncode == 0:
            result["status"] = "success"
            try:
                result["result"] = json.loads(stdout.decode())
            except json.JSONDecodeError:
                result["result"] = stdout.decode().strip()
        else:
            result["status"] = "error"
            result["error"] = stderr.decode().strip() or stdout.decode().strip()

        return result

    except asyncio.TimeoutError:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "status": "error",
            "error": f"Command timed out after {timeout}s",
            "execution_time_ms": elapsed_ms,
        }
    except FileNotFoundError:
        return {
            "status": "error",
            "error": f"Binary not found: {binary}",
        }
