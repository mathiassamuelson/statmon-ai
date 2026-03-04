"""Tests for statmon_chat.security_tools — domain/IP investigation tools."""

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from statmon_chat.security_tools import (
    SECURITY_TOOL_NAMES,
    dispatch,
    get_tool_definitions,
    _normalize_domain,
    _validate_ip,
    _format_date,
)


class TestInputValidation:
    def test_normalize_domain_valid(self):
        domain, err = _normalize_domain("Example.COM.")
        assert domain == "example.com"
        assert err is None

    def test_normalize_domain_strips_whitespace(self):
        domain, err = _normalize_domain("  example.com  ")
        assert domain == "example.com"

    def test_normalize_domain_rejects_url(self):
        _, err = _normalize_domain("https://example.com")
        assert "URL" in err

    def test_normalize_domain_rejects_ip(self):
        _, err = _normalize_domain("93.184.216.34")
        assert "IP address" in err

    def test_normalize_domain_rejects_empty(self):
        _, err = _normalize_domain("")
        assert err is not None

    def test_normalize_domain_rejects_invalid(self):
        _, err = _normalize_domain("not a domain!")
        assert "Invalid" in err

    def test_validate_ip_v4(self):
        ip, err = _validate_ip("93.184.216.34")
        assert err is None
        assert str(ip) == "93.184.216.34"

    def test_validate_ip_v6(self):
        ip, err = _validate_ip("2001:db8::1")
        assert err is None

    def test_validate_ip_invalid(self):
        _, err = _validate_ip("not-an-ip")
        assert "Invalid" in err

    def test_validate_ip_domain_rejected(self):
        _, err = _validate_ip("example.com")
        assert "Invalid" in err

    def test_format_date_datetime(self):
        dt = datetime(2024, 1, 15, 12, 0, 0)
        assert _format_date(dt) == "2024-01-15T12:00:00"

    def test_format_date_list(self):
        result = _format_date([datetime(2024, 1, 15), datetime(2024, 2, 1)])
        assert result == "2024-01-15T00:00:00"

    def test_format_date_none(self):
        assert _format_date(None) is None

    def test_format_date_string(self):
        assert _format_date("2024-01-15") == "2024-01-15"


class TestWhoisLookup:
    @pytest.mark.asyncio
    async def test_success(self):
        mock_result = MagicMock()
        mock_result.registrar = "Example Registrar, Inc."
        mock_result.creation_date = datetime(2020, 1, 1)
        mock_result.expiration_date = datetime(2026, 1, 1)
        mock_result.updated_date = datetime(2024, 6, 1)
        mock_result.name_servers = ["NS1.EXAMPLE.COM", "NS2.EXAMPLE.COM"]
        mock_result.org = "Example Inc."
        mock_result.country = "US"
        mock_result.dnssec = "unsigned"
        mock_result.status = ["clientDeleteProhibited", "clientTransferProhibited"]
        mock_result.text = "Domain Name: EXAMPLE.COM\nRegistrar: Example"

        with patch("statmon_chat.security_tools.whois.whois", return_value=mock_result):
            result = json.loads(await dispatch("whois_lookup", {"domain": "example.com"}))

        assert result["status"] == "success"
        assert result["result"]["registrar"] == "Example Registrar, Inc."
        assert result["result"]["name_servers"] == ["ns1.example.com", "ns2.example.com"]
        assert result["result"]["creation_date"] == "2020-01-01T00:00:00"

    @pytest.mark.asyncio
    async def test_domain_not_found(self):
        from whois.parser import WhoisDomainNotFoundError

        with patch("statmon_chat.security_tools.whois.whois", side_effect=WhoisDomainNotFoundError("No match")):
            result = json.loads(await dispatch("whois_lookup", {"domain": "nonexistent.xyz"}))

        assert result["status"] == "error"
        assert "No match" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_domain_url(self):
        result = json.loads(await dispatch("whois_lookup", {"domain": "https://example.com"}))
        assert result["status"] == "error"
        assert "URL" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_domain_ip(self):
        result = json.loads(await dispatch("whois_lookup", {"domain": "1.2.3.4"}))
        assert result["status"] == "error"
        assert "IP address" in result["error"]

    @pytest.mark.asyncio
    async def test_raw_truncation(self):
        mock_result = MagicMock()
        mock_result.registrar = "Test"
        mock_result.creation_date = None
        mock_result.expiration_date = None
        mock_result.updated_date = None
        mock_result.name_servers = []
        mock_result.org = None
        mock_result.country = None
        mock_result.dnssec = None
        mock_result.status = []
        mock_result.text = "x" * 10000

        with patch("statmon_chat.security_tools.whois.whois", return_value=mock_result):
            result = json.loads(await dispatch("whois_lookup", {"domain": "example.com"}))

        assert result["status"] == "success"
        assert "[truncated]" in result["result"]["raw"]
        assert len(result["result"]["raw"]) < 5000


class TestDnsResolve:
    @pytest.mark.asyncio
    async def test_success(self):
        def mock_resolve(name, rtype):
            records = {
                "A": ["93.184.216.34"],
                "AAAA": ["2606:2800:220:1:248:1893:25c8:1946"],
                "MX": ["10 mail.example.com"],
            }
            if rtype in records:
                return [MagicMock(__str__=lambda self, v=v: v) for v in records[rtype]]
            raise dns_module.resolver.NoAnswer()

        import dns.resolver as dns_module

        mock_resolver = MagicMock()
        mock_resolver.resolve = MagicMock(side_effect=mock_resolve)

        with patch("statmon_chat.security_tools._make_resolver", return_value=mock_resolver):
            result = json.loads(await dispatch("dns_resolve", {
                "name": "example.com", "record_types": ["A", "AAAA", "MX", "TXT"]
            }))

        assert result["status"] == "success"
        assert result["result"]["A"] == ["93.184.216.34"]
        assert result["result"]["TXT"] == []
        assert "query_time_ms" in result

    @pytest.mark.asyncio
    async def test_nxdomain(self):
        import dns.resolver as dns_module

        mock_resolver = MagicMock()
        mock_resolver.resolve = MagicMock(side_effect=dns_module.NXDOMAIN())

        with patch("statmon_chat.security_tools._make_resolver", return_value=mock_resolver):
            result = json.loads(await dispatch("dns_resolve", {
                "name": "nonexistent.example", "record_types": ["A"]
            }))

        assert result["status"] == "error"
        assert "NXDOMAIN" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_name(self):
        result = json.loads(await dispatch("dns_resolve", {"name": ""}))
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_partial_failure(self):
        """MX fails with NoAnswer but A succeeds."""
        import dns.resolver as dns_module

        def mock_resolve(name, rtype):
            if rtype == "A":
                return [MagicMock(__str__=lambda self: "1.2.3.4")]
            raise dns_module.NoAnswer()

        mock_resolver = MagicMock()
        mock_resolver.resolve = MagicMock(side_effect=mock_resolve)

        with patch("statmon_chat.security_tools._make_resolver", return_value=mock_resolver):
            result = json.loads(await dispatch("dns_resolve", {
                "name": "example.com", "record_types": ["A", "MX"]
            }))

        assert result["status"] == "success"
        assert result["result"]["A"] == ["1.2.3.4"]
        assert result["result"]["MX"] == []


class TestIpGeolocation:
    @pytest.mark.asyncio
    async def test_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "status": "success",
            "country": "United States",
            "countryCode": "US",
            "regionName": "California",
            "city": "Los Angeles",
            "isp": "Edgecast Inc.",
            "org": "Verizon Digital Media",
            "as": "AS15133 Edgecast Inc.",
            "reverse": "",
            "query": "93.184.216.34",
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("statmon_chat.security_tools.httpx.AsyncClient", return_value=mock_client):
            with patch("statmon_chat.security_tools._make_resolver") as mock_resolver:
                mock_resolver.return_value.resolve.side_effect = Exception("no PTR")
                result = json.loads(await dispatch("ip_geolocation", {"ip": "93.184.216.34"}))

        assert result["status"] == "success"
        assert result["result"]["country"] == "United States"
        assert result["result"]["as_number"] == 15133
        assert result["result"]["as_name"] == "Edgecast Inc."
        assert result["result"]["is_private"] is False

    @pytest.mark.asyncio
    async def test_private_ip(self):
        with patch("statmon_chat.security_tools._make_resolver") as mock_resolver:
            mock_resolver.return_value.resolve.side_effect = Exception("no PTR")
            result = json.loads(await dispatch("ip_geolocation", {"ip": "10.0.5.42"}))

        assert result["status"] == "success"
        assert result["result"]["is_private"] is True
        assert "private" in result.get("warning", "").lower()

    @pytest.mark.asyncio
    async def test_invalid_ip(self):
        result = json.loads(await dispatch("ip_geolocation", {"ip": "example.com"}))
        assert result["status"] == "error"
        assert "Invalid" in result["error"]

    @pytest.mark.asyncio
    async def test_rate_limited(self):
        mock_response = MagicMock()
        mock_response.status_code = 429

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("statmon_chat.security_tools.httpx.AsyncClient", return_value=mock_client):
            with patch("statmon_chat.security_tools._make_resolver") as mock_resolver:
                mock_resolver.return_value.resolve.side_effect = Exception("no PTR")
                result = json.loads(await dispatch("ip_geolocation", {"ip": "1.2.3.4"}))

        assert result["status"] == "error"
        assert "Rate limited" in result["error"]


class TestReverseDnsLookup:
    @pytest.mark.asyncio
    async def test_success_with_forward_verified(self):
        def mock_resolve(name, rtype):
            name_str = str(name)
            if rtype == "PTR":
                return [MagicMock(__str__=lambda self: "host.example.com.")]
            if rtype == "A" and "host.example.com" in name_str:
                return [MagicMock(__str__=lambda self: "1.2.3.4")]
            raise Exception("unexpected")

        mock_resolver = MagicMock()
        mock_resolver.resolve = MagicMock(side_effect=mock_resolve)

        with patch("statmon_chat.security_tools._make_resolver", return_value=mock_resolver):
            with patch("statmon_chat.security_tools.dns.reversename.from_address", return_value="4.3.2.1.in-addr.arpa"):
                result = json.loads(await dispatch("reverse_dns_lookup", {"ip": "1.2.3.4"}))

        assert result["status"] == "success"
        assert result["result"]["ptr_records"] == ["host.example.com"]
        assert result["result"]["forward_verified"] is True

    @pytest.mark.asyncio
    async def test_forward_mismatch(self):
        def mock_resolve(name, rtype):
            if rtype == "PTR":
                return [MagicMock(__str__=lambda self: "host.example.com.")]
            if rtype == "A":
                return [MagicMock(__str__=lambda self: "5.6.7.8")]
            raise Exception("unexpected")

        mock_resolver = MagicMock()
        mock_resolver.resolve = MagicMock(side_effect=mock_resolve)

        with patch("statmon_chat.security_tools._make_resolver", return_value=mock_resolver):
            with patch("statmon_chat.security_tools.dns.reversename.from_address", return_value="4.3.2.1.in-addr.arpa"):
                result = json.loads(await dispatch("reverse_dns_lookup", {"ip": "1.2.3.4"}))

        assert result["status"] == "success"
        assert result["result"]["forward_verified"] is False

    @pytest.mark.asyncio
    async def test_no_ptr(self):
        import dns.resolver as dns_module

        mock_resolver = MagicMock()
        mock_resolver.resolve = MagicMock(side_effect=dns_module.NXDOMAIN())

        with patch("statmon_chat.security_tools._make_resolver", return_value=mock_resolver):
            with patch("statmon_chat.security_tools.dns.reversename.from_address", return_value="4.3.2.1.in-addr.arpa"):
                result = json.loads(await dispatch("reverse_dns_lookup", {"ip": "1.2.3.4"}))

        assert result["status"] == "success"
        assert result["result"]["ptr_records"] == []
        assert result["result"]["forward_verified"] is None

    @pytest.mark.asyncio
    async def test_invalid_ip(self):
        result = json.loads(await dispatch("reverse_dns_lookup", {"ip": "not-an-ip"}))
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_skip_forward_verify(self):
        def mock_resolve(name, rtype):
            if rtype == "PTR":
                return [MagicMock(__str__=lambda self: "host.example.com.")]
            raise Exception("should not be called")

        mock_resolver = MagicMock()
        mock_resolver.resolve = MagicMock(side_effect=mock_resolve)

        with patch("statmon_chat.security_tools._make_resolver", return_value=mock_resolver):
            with patch("statmon_chat.security_tools.dns.reversename.from_address", return_value="4.3.2.1.in-addr.arpa"):
                result = json.loads(await dispatch("reverse_dns_lookup", {
                    "ip": "1.2.3.4", "verify_forward": False
                }))

        assert result["status"] == "success"
        assert result["result"]["forward_verified"] is None


class TestDispatch:
    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        result = json.loads(await dispatch("nonexistent_tool", {}))
        assert result["status"] == "error"
        assert "Unknown" in result["error"]

    @pytest.mark.asyncio
    async def test_routes_to_whois(self):
        mock_handler = AsyncMock(return_value='{"status": "success"}')
        with patch.dict("statmon_chat.security_tools._DISPATCH", {"whois_lookup": mock_handler}):
            await dispatch("whois_lookup", {"domain": "example.com"})
            mock_handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_routes_to_dns_resolve(self):
        mock_handler = AsyncMock(return_value='{"status": "success"}')
        with patch.dict("statmon_chat.security_tools._DISPATCH", {"dns_resolve": mock_handler}):
            await dispatch("dns_resolve", {"name": "example.com"})
            mock_handler.assert_called_once()


class TestToolDefinitions:
    def test_returns_all_tools(self):
        defs = get_tool_definitions()
        names = {d["name"] for d in defs}
        assert names == SECURITY_TOOL_NAMES

    def test_has_required_fields(self):
        for d in get_tool_definitions():
            assert "name" in d
            assert "description" in d
            assert "input_schema" in d
            assert d["input_schema"]["type"] == "object"
            assert "required" in d["input_schema"]


class TestSecurityToolNames:
    def test_all_names_present(self):
        assert SECURITY_TOOL_NAMES == {"whois_lookup", "dns_resolve", "ip_geolocation", "reverse_dns_lookup"}
