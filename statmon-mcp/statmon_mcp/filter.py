"""Command allow/deny filtering logic.

Uses deny-first, then allow, then default-deny approach.
Glob matching is case-insensitive using fnmatch.
"""

import fnmatch


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
