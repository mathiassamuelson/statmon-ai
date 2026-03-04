"""Domain and IP investigation tools for SecOps workflows.

Provides whois_lookup, dns_resolve, ip_geolocation, and reverse_dns_lookup
as local tools that run inside the chat app (no MCP server needed).
"""

import ipaddress
import json
import logging
import re
import time
from datetime import datetime

import dns.name
import dns.resolver
import dns.reversename
import httpx
import whois

logger = logging.getLogger(__name__)

SECURITY_TOOL_NAMES = {"whois_lookup", "dns_resolve", "ip_geolocation", "reverse_dns_lookup"}

DEFAULT_CONFIG = {
    "dns_resolver": "system",
    "whois_timeout_seconds": 10,
    "geolocation_provider": "ip-api",
}

WHOIS_RAW_LIMIT = 4096


def _merge_config(user_config: dict) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(user_config)
    return cfg


def _format_date(val) -> str | None:
    """Format a date value to ISO 8601 string."""
    if val is None:
        return None
    if isinstance(val, list):
        val = val[0] if val else None
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


def _normalize_domain(domain: str) -> tuple[str, str | None]:
    """Normalize and validate a domain input. Returns (domain, error)."""
    domain = domain.strip().lower().rstrip(".")
    if not domain:
        return "", "Empty domain"
    if "://" in domain:
        return "", "Input appears to be a URL, not a domain. Remove the scheme (e.g., use 'example.com' not 'https://example.com')."
    try:
        ipaddress.ip_address(domain)
        return "", "Input appears to be an IP address, not a domain. Use ip_geolocation or reverse_dns_lookup instead."
    except ValueError:
        pass
    if not re.match(r'^[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)*$', domain):
        return "", f"Invalid domain format: {domain}"
    return domain, None


def _validate_ip(ip_str: str) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address | None, str | None]:
    """Validate an IP address input. Returns (ip_obj, error)."""
    ip_str = ip_str.strip()
    try:
        return ipaddress.ip_address(ip_str), None
    except ValueError:
        return None, f"Invalid IP address: {ip_str}. Provide a valid IPv4 or IPv6 address."


def _make_resolver(config: dict) -> dns.resolver.Resolver:
    """Create a DNS resolver based on config."""
    resolver = dns.resolver.Resolver()
    dns_server = config.get("dns_resolver", "system")
    if dns_server != "system":
        resolver.nameservers = [dns_server]
    return resolver


async def _whois_lookup(arguments: dict, config: dict) -> str:
    domain = arguments.get("domain", "")
    domain, err = _normalize_domain(domain)
    if err:
        return json.dumps({"tool": "whois_lookup", "domain": arguments.get("domain", ""), "status": "error", "error": err})

    try:
        w = whois.whois(domain)
    except whois.parser.WhoisDomainNotFoundError as e:
        return json.dumps({"tool": "whois_lookup", "domain": domain, "status": "error", "error": str(e)})
    except Exception as e:
        return json.dumps({"tool": "whois_lookup", "domain": domain, "status": "error", "error": f"WHOIS query failed: {e}"})

    name_servers = w.name_servers
    if isinstance(name_servers, list):
        name_servers = [ns.lower() for ns in name_servers]
    elif isinstance(name_servers, str):
        name_servers = [name_servers.lower()]
    else:
        name_servers = []

    status = w.status
    if isinstance(status, str):
        status = [status]
    elif not isinstance(status, list):
        status = []

    raw_text = str(w.text) if hasattr(w, "text") and w.text else ""
    if len(raw_text) > WHOIS_RAW_LIMIT:
        raw_text = raw_text[:WHOIS_RAW_LIMIT] + "\n[truncated]"

    result = {
        "registrar": w.registrar,
        "creation_date": _format_date(w.creation_date),
        "expiration_date": _format_date(w.expiration_date),
        "updated_date": _format_date(w.updated_date),
        "name_servers": name_servers,
        "registrant_organization": getattr(w, "org", None),
        "registrant_country": getattr(w, "country", None),
        "dnssec": getattr(w, "dnssec", None),
        "status": status,
        "raw": raw_text,
    }

    return json.dumps({"tool": "whois_lookup", "domain": domain, "status": "success", "result": result})


async def _dns_resolve(arguments: dict, config: dict) -> str:
    name = arguments.get("name", "").strip()
    if not name:
        return json.dumps({"tool": "dns_resolve", "name": "", "status": "error", "error": "Name is required"})

    record_types = arguments.get("record_types", ["A", "AAAA", "CNAME", "MX", "NS", "TXT"])
    resolver = _make_resolver(config)

    start = time.monotonic()
    results = {}

    for rtype in record_types:
        try:
            answer = resolver.resolve(name, rtype)
            results[rtype] = [str(rdata) for rdata in answer]
        except dns.resolver.NXDOMAIN:
            return json.dumps({
                "tool": "dns_resolve", "name": name, "status": "error",
                "error": f"Domain does not exist (NXDOMAIN): {name}",
                "query_time_ms": int((time.monotonic() - start) * 1000),
            })
        except dns.resolver.NoAnswer:
            results[rtype] = []
        except dns.resolver.NoNameservers:
            results[rtype] = []
        except dns.exception.Timeout:
            results[rtype] = ["TIMEOUT"]
        except Exception:
            results[rtype] = []

    query_time_ms = int((time.monotonic() - start) * 1000)
    return json.dumps({
        "tool": "dns_resolve", "name": name, "status": "success",
        "result": results, "query_time_ms": query_time_ms,
    })


async def _ip_geolocation(arguments: dict, config: dict) -> str:
    ip_str = arguments.get("ip", "")
    ip_obj, err = _validate_ip(ip_str)
    if err:
        return json.dumps({"tool": "ip_geolocation", "ip": ip_str, "status": "error", "error": err})

    is_private = ip_obj.is_private

    # PTR lookup
    reverse_dns = None
    try:
        rev_name = dns.reversename.from_address(str(ip_obj))
        resolver = _make_resolver(config)
        answer = resolver.resolve(rev_name, "PTR")
        reverse_dns = str(list(answer)[0]).rstrip(".")
    except Exception:
        pass

    if is_private:
        result = {
            "country": None, "country_code": None, "region": None, "city": None,
            "isp": None, "org": None, "as_number": None, "as_name": None,
            "reverse_dns": reverse_dns, "is_private": True,
        }
        return json.dumps({
            "tool": "ip_geolocation", "ip": str(ip_obj), "status": "success",
            "warning": "This is a private/reserved IP address. Geolocation data is not available.",
            "result": result,
        })

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"http://ip-api.com/json/{ip_obj}",
                params={"fields": "status,message,country,countryCode,regionName,city,isp,org,as,reverse,query"},
            )

        if resp.status_code == 429:
            return json.dumps({
                "tool": "ip_geolocation", "ip": str(ip_obj), "status": "error",
                "error": "Rate limited by geolocation API. Try again shortly.",
            })

        data = resp.json()

        if data.get("status") == "fail":
            return json.dumps({
                "tool": "ip_geolocation", "ip": str(ip_obj), "status": "error",
                "error": data.get("message", "Geolocation lookup failed"),
            })

        as_field = data.get("as", "")
        as_number = None
        as_name = None
        if as_field:
            parts = as_field.split(" ", 1)
            if parts[0].startswith("AS"):
                try:
                    as_number = int(parts[0][2:])
                except ValueError:
                    pass
                as_name = parts[1] if len(parts) > 1 else None

        result = {
            "country": data.get("country"),
            "country_code": data.get("countryCode"),
            "region": data.get("regionName"),
            "city": data.get("city"),
            "isp": data.get("isp"),
            "org": data.get("org"),
            "as_number": as_number,
            "as_name": as_name,
            "reverse_dns": reverse_dns or data.get("reverse") or None,
            "is_private": False,
        }

        return json.dumps({"tool": "ip_geolocation", "ip": str(ip_obj), "status": "success", "result": result})

    except httpx.TimeoutException:
        return json.dumps({
            "tool": "ip_geolocation", "ip": str(ip_obj), "status": "error",
            "error": "Geolocation API request timed out",
        })
    except Exception as e:
        return json.dumps({
            "tool": "ip_geolocation", "ip": str(ip_obj), "status": "error",
            "error": f"Geolocation lookup failed: {e}",
        })


async def _reverse_dns_lookup(arguments: dict, config: dict) -> str:
    ip_str = arguments.get("ip", "")
    ip_obj, err = _validate_ip(ip_str)
    if err:
        return json.dumps({"tool": "reverse_dns_lookup", "ip": ip_str, "status": "error", "error": err})

    verify_forward = arguments.get("verify_forward", True)
    resolver = _make_resolver(config)

    try:
        rev_name = dns.reversename.from_address(str(ip_obj))
        answer = resolver.resolve(rev_name, "PTR")
        ptr_records = [str(rdata).rstrip(".") for rdata in answer]
    except dns.resolver.NXDOMAIN:
        return json.dumps({
            "tool": "reverse_dns_lookup", "ip": str(ip_obj), "status": "success",
            "result": {"ptr_records": [], "forward_verified": None, "forward_addresses": []},
        })
    except dns.exception.Timeout:
        return json.dumps({
            "tool": "reverse_dns_lookup", "ip": str(ip_obj), "status": "error",
            "error": "PTR lookup timed out",
        })
    except Exception as e:
        return json.dumps({
            "tool": "reverse_dns_lookup", "ip": str(ip_obj), "status": "error",
            "error": f"PTR lookup failed: {e}",
        })

    forward_verified = None
    forward_addresses = []

    if verify_forward and ptr_records:
        ptr_name = ptr_records[0]
        rtype = "A" if isinstance(ip_obj, ipaddress.IPv4Address) else "AAAA"
        try:
            answer = resolver.resolve(ptr_name, rtype)
            forward_addresses = [str(rdata) for rdata in answer]
            forward_verified = str(ip_obj) in forward_addresses
        except Exception:
            forward_verified = False

    return json.dumps({
        "tool": "reverse_dns_lookup", "ip": str(ip_obj), "status": "success",
        "result": {
            "ptr_records": ptr_records,
            "forward_verified": forward_verified,
            "forward_addresses": forward_addresses,
        },
    })


_DISPATCH = {
    "whois_lookup": _whois_lookup,
    "dns_resolve": _dns_resolve,
    "ip_geolocation": _ip_geolocation,
    "reverse_dns_lookup": _reverse_dns_lookup,
}


async def dispatch(tool_name: str, arguments: dict, config: dict | None = None) -> str:
    """Route a security tool call to the correct handler."""
    config = _merge_config(config or {})
    handler = _DISPATCH.get(tool_name)
    if not handler:
        return json.dumps({"tool": tool_name, "status": "error", "error": f"Unknown security tool: {tool_name}"})
    return await handler(arguments, config)


def get_tool_definitions() -> list[dict]:
    """Return Anthropic-format tool definitions for all security tools."""
    return [
        {
            "name": "whois_lookup",
            "description": (
                "Look up WHOIS registration data for a domain. "
                "Returns registrar, creation/expiration dates, name servers, registrant info, and DNSSEC status. "
                "Use when checking if a domain is newly registered (DGA/disposable), identifying ownership, "
                "or verifying legitimate domains."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Domain name to look up (e.g., 'example.com'). Use the registrable domain, not subdomains.",
                    }
                },
                "required": ["domain"],
            },
        },
        {
            "name": "dns_resolve",
            "description": (
                "Resolve DNS records for a domain name. "
                "Returns records by type (A, AAAA, CNAME, MX, NS, TXT). "
                "Use when checking what IPs a suspicious domain resolves to, finding mail servers, "
                "examining TXT records for SPF/DKIM, or investigating NS infrastructure."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "DNS name to resolve (e.g., 'example.com', 'mail.example.com')",
                    },
                    "record_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "DNS record types to query. Defaults to ['A', 'AAAA', 'CNAME', 'MX', 'NS', 'TXT'] if not specified.",
                    },
                },
                "required": ["name"],
            },
        },
        {
            "name": "ip_geolocation",
            "description": (
                "Get geolocation and network information for an IP address. "
                "Returns country, city, ISP, organization, AS number/name, and reverse DNS. "
                "Use when identifying where traffic originates, checking if IPs belong to known "
                "hosting providers or residential ranges, investigating C2 infrastructure by checking "
                "geolocation of IPs a suspicious domain resolves to (bulletproof hosting, fast-flux "
                "across many ASes, residential IPs used as proxies)."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "ip": {
                        "type": "string",
                        "description": "IPv4 or IPv6 address to look up",
                    }
                },
                "required": ["ip"],
            },
        },
        {
            "name": "reverse_dns_lookup",
            "description": (
                "Perform reverse DNS (PTR) lookup on an IP address with optional forward verification. "
                "Returns PTR records and whether forward DNS confirms the reverse mapping. "
                "Use when checking if an IP has legitimate reverse DNS or verifying server identity. "
                "Legitimate services have matching forward/reverse DNS; C2 infrastructure typically does not."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "ip": {
                        "type": "string",
                        "description": "IPv4 or IPv6 address for reverse DNS lookup",
                    },
                    "verify_forward": {
                        "type": "boolean",
                        "description": "If true, also perform a forward lookup on the PTR result to verify it points back to this IP. Defaults to true.",
                    },
                },
                "required": ["ip"],
            },
        },
    ]
