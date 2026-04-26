"""Tests for statmon_mcp.filter — command allow/deny logic."""

from statmon_mcp.filter import check_command, check_paths, glob_match


class TestGlobMatch:
    def test_wildcard_prefix(self):
        assert glob_match("cache.statistics", "*.statistics")
        assert glob_match("dns.statistics", "*.statistics")

    def test_wildcard_suffix(self):
        assert glob_match("querystore.top-clients", "querystore.*")
        assert glob_match("querystore.count", "querystore.*")
        assert glob_match("querystore.replay", "querystore.*")

    def test_trailing_wildcard_args(self):
        assert glob_match("dns.config show zones", "dns.config show *")
        assert glob_match("dns.config show all", "dns.config show *")

    def test_no_match(self):
        assert not glob_match("cache.flush", "*.statistics")
        assert not glob_match("server.shutdown", "querystore.*")

    def test_case_insensitive(self):
        assert glob_match("Cache.Statistics", "*.statistics")
        assert glob_match("QUERYSTORE.COUNT", "querystore.*")
        assert glob_match("querystore.count", "QUERYSTORE.*")

    def test_exact_match(self):
        assert glob_match("server.version", "server.version")
        assert not glob_match("server.version", "server.status")

    def test_double_wildcard_segment(self):
        assert glob_match("querystore.reset", "querystore.reset")


class TestCheckCommand:
    STATMON_RULES = {
        "deny": ["querystore.reset"],
        "allow": ["querystore.*"],
    }

    CACHESERVE_RULES = {
        "deny": [
            "*.flush", "*.clear", "*.reset", "*.delete",
            "server.shutdown", "server.restart",
            "config.set *", "config.write",
        ],
        "allow": [
            "*.statistics", "*.info", "*.status",
            "server.version", "cache.summary",
            "dns.config show *",
        ],
    }

    def test_deny_takes_precedence(self):
        allowed, reason = check_command("querystore.reset", self.STATMON_RULES)
        assert not allowed
        assert "deny rule" in reason

    def test_allow_match(self):
        allowed, reason = check_command("querystore.top-clients", self.STATMON_RULES)
        assert allowed
        assert reason == "OK"

    def test_default_deny(self):
        allowed, reason = check_command("system.reboot", self.STATMON_RULES)
        assert not allowed
        assert "does not match any allow rule" in reason

    def test_empty_command(self):
        allowed, reason = check_command("", self.STATMON_RULES)
        assert not allowed
        assert "Empty command" in reason

    def test_whitespace_command(self):
        allowed, reason = check_command("   ", self.STATMON_RULES)
        assert not allowed

    def test_command_with_args_allowed(self):
        allowed, _ = check_command(
            'querystore.top-clients duration 3600 max-results 10',
            self.STATMON_RULES,
        )
        assert allowed

    def test_cacheserve_deny_flush(self):
        allowed, reason = check_command("cache.flush", self.CACHESERVE_RULES)
        assert not allowed
        assert "deny rule" in reason

    def test_cacheserve_deny_shutdown(self):
        allowed, _ = check_command("server.shutdown", self.CACHESERVE_RULES)
        assert not allowed

    def test_cacheserve_deny_config_set(self):
        allowed, _ = check_command("config.set max-cache-size 1000", self.CACHESERVE_RULES)
        assert not allowed

    def test_cacheserve_allow_statistics(self):
        allowed, _ = check_command("cache.statistics", self.CACHESERVE_RULES)
        assert allowed

    def test_cacheserve_allow_dns_config_show(self):
        allowed, _ = check_command("dns.config show zones", self.CACHESERVE_RULES)
        assert allowed

    def test_cacheserve_default_deny_unknown(self):
        allowed, _ = check_command("cache.dump-all", self.CACHESERVE_RULES)
        assert not allowed

    def test_case_insensitive_deny(self):
        allowed, _ = check_command("QUERYSTORE.RESET", self.STATMON_RULES)
        assert not allowed

    def test_case_insensitive_allow(self):
        allowed, _ = check_command("QUERYSTORE.COUNT", self.STATMON_RULES)
        assert allowed

    def test_no_rules(self):
        allowed, _ = check_command("anything", {})
        assert not allowed


class TestCheckPaths:
    def test_no_deny_passes(self):
        ok, _ = check_paths("/etc/passwd", [])
        assert ok

    def test_absolute_path_denied(self):
        ok, reason = check_paths("/etc/shadow", ["/etc/shadow"])
        assert not ok
        assert "/etc/shadow" in reason

    def test_glob_pattern(self):
        ok, _ = check_paths("/var/log/secure -n 5", ["/var/log/*"])
        assert not ok

    def test_relative_path_caught(self):
        ok, _ = check_paths("./secret.key", ["*.key"])
        assert not ok
        ok, _ = check_paths("../etc/passwd", ["../etc/*"])
        assert not ok

    def test_non_path_token_ignored(self):
        # "etcpasswd" doesn't start with /, ./, or ../ so it's not checked.
        ok, _ = check_paths("etcpasswd", ["/etc/passwd"])
        assert ok

    def test_safe_path_allowed(self):
        ok, _ = check_paths("/var/log/messages", ["/etc/shadow"])
        assert ok
