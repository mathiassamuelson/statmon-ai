"""Command allow/deny filtering logic.

Uses deny-first, then allow, then default-deny approach.
Glob matching is case-insensitive using fnmatch.
"""

import fnmatch
import shlex


def glob_match(command: str, pattern: str) -> bool:
    """Match a command string against a glob pattern (case-insensitive).

    Pattern examples:
        '*.statistics'       -> matches 'cache.statistics', 'dns.statistics'
        'querystore.*'       -> matches 'querystore.top-clients', 'querystore.count'
        'dns.config show *'  -> matches 'dns.config show zones'
    """
    return fnmatch.fnmatch(command.lower(), pattern.lower())


def check_command(command: str, rules: dict) -> tuple[bool, str]:
    """Check if a command is allowed by the deny/allow rules.

    Returns:
        (allowed, reason) — True if allowed, False if denied.
    """
    cmd = command.strip()
    if not cmd:
        return False, "Empty command"

    for pattern in rules.get("deny", []):
        if glob_match(cmd, pattern):
            return False, f"Command matches deny rule: {pattern}"

    for pattern in rules.get("allow", []):
        if glob_match(cmd, pattern):
            return True, "OK"

    return False, "Command does not match any allow rule"


def check_paths(args: str, deny: list[str]) -> tuple[bool, str]:
    """Reject any path-shaped token in `args` that matches a deny pattern.

    A "path-shaped token" begins with /, ./, or ../ after shlex tokenization.
    Returns (True, "OK") if no token matches; (False, reason) on first match.
    Pattern matching is case-insensitive glob via fnmatch.
    """
    if not deny:
        return True, "OK"
    try:
        tokens = shlex.split(args)
    except ValueError as e:
        return False, f"path check: failed to tokenize args ({e})"
    for tok in tokens:
        if not (tok.startswith("/") or tok.startswith("./") or tok.startswith("../")):
            continue
        for pattern in deny:
            if glob_match(tok, pattern):
                return False, f"path matches deny rule: {pattern}"
    return True, "OK"
