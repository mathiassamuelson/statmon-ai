# Domain Investigation Tools — Requirements

## Context

The Statmon AI Aggregator is a natural-language chatbot for carrier engineers to query across multiple CacheServe DNS servers and Statmon log collectors. See `CLAUDE.md` and `docs/design.md` for full project context.

A key use case is SecOps investigation: when Statmon reveals suspicious domains or client IPs (e.g., DGA patterns, NXDOMAIN floods, amplification sources), the engineer currently has to leave the chatbot to manually research those indicators. We want the investigation to stay within the conversation.

## Goal

Add domain and IP investigation tools directly to the chat app so that Claude can perform structured lookups (WHOIS, DNS resolution, IP geolocation) and open-ended web research within the same conversation used for Statmon queries. This enables end-to-end SecOps workflows like:

1. Statmon reveals a client generating thousands of NXDOMAIN queries
2. Claude identifies the top queried domains (DGA-like patterns)
3. Claude checks WHOIS on suspicious domains — recently registered? privacy-protected?
4. Claude resolves the domains to see what IPs they point to
5. Claude checks IP geolocation and reverse DNS
6. Claude searches the web for threat intel on the domains/IPs
7. Claude synthesizes a report with findings and recommended actions

## Architecture Decision

These tools are **built directly into the chat app** (`copilot`), not as a separate MCP server. Rationale:

- Domain/IP lookups are HTTP API calls — they don't need to run on any specific node
- No extra container to deploy, monitor, or configure
- The tools appear identically to Claude alongside the node-prefixed Statmon tools
- Future threat intel API integrations (VirusTotal, AbuseIPDB, etc.) can be added incrementally to the same module

Web search is enabled via the Anthropic API's built-in web search tool for open-ended research.

## Implementation

### New Module: `copilot/copilot/security_tools.py`

This module implements the tool execution functions. Each function takes structured input and returns a JSON string, consistent with how MCP tool results are returned.

#### Tool 1: `whois_lookup`

Performs a WHOIS query on a domain name.

**Input schema:**
```json
{
  "type": "object",
  "properties": {
    "domain": {
      "type": "string",
      "description": "Domain name to look up (e.g., 'example.com'). Do not include subdomains — use the registrable domain."
    }
  },
  "required": ["domain"]
}
```

**Implementation notes:**
- Use the `python-whois` library (PyPI: `python-whois`) for WHOIS queries
- Extract and return structured fields: registrar, creation date, expiration date, updated date, name servers, registrant organization, registrant country, DNSSEC status, and status codes
- Dates should be formatted as ISO 8601 strings
- Include the raw WHOIS text in a `raw` field (truncated to 4KB) for cases where structured parsing misses something
- Handle errors gracefully: domain not found, WHOIS server unreachable, rate limiting, invalid domain input
- Normalize the domain input: strip whitespace, lowercase, remove trailing dots, reject obviously invalid input (IP addresses, URLs with schemes)

**Response format:**
```json
{
  "tool": "whois_lookup",
  "domain": "example.com",
  "status": "success",
  "result": {
    "registrar": "Example Registrar, Inc.",
    "creation_date": "1995-08-14T00:00:00",
    "expiration_date": "2026-08-13T00:00:00",
    "updated_date": "2024-01-15T00:00:00",
    "name_servers": ["ns1.example.com", "ns2.example.com"],
    "registrant_organization": "Example Inc.",
    "registrant_country": "US",
    "dnssec": "unsigned",
    "status": ["clientDeleteProhibited", "clientTransferProhibited"],
    "raw": "Domain Name: EXAMPLE.COM\n..."
  }
}
```

#### Tool 2: `dns_resolve`

Performs DNS resolution for a domain, querying multiple record types.

**Input schema:**
```json
{
  "type": "object",
  "properties": {
    "name": {
      "type": "string",
      "description": "DNS name to resolve (e.g., 'example.com', 'mail.example.com')"
    },
    "record_types": {
      "type": "array",
      "items": { "type": "string" },
      "description": "DNS record types to query. Defaults to ['A', 'AAAA', 'CNAME', 'MX', 'NS', 'TXT'] if not specified."
    }
  },
  "required": ["name"]
}
```

**Implementation notes:**
- Use the `dnspython` library (PyPI: `dnspython`) for DNS resolution
- Query against a reliable public resolver (default: system resolver; configurable to use 8.8.8.8 or 1.1.1.1)
- Query each requested record type independently so partial failures (e.g., no MX record) don't prevent other results from being returned
- For each record type, return the records found or note that none exist
- Handle errors: NXDOMAIN (domain doesn't exist), SERVFAIL, timeout, invalid input
- Include query time in milliseconds

**Response format:**
```json
{
  "tool": "dns_resolve",
  "name": "example.com",
  "status": "success",
  "result": {
    "A": ["93.184.216.34"],
    "AAAA": ["2606:2800:220:1:248:1893:25c8:1946"],
    "CNAME": [],
    "MX": ["10 mail.example.com"],
    "NS": ["ns1.example.com", "ns2.example.com"],
    "TXT": ["v=spf1 -all"]
  },
  "query_time_ms": 45
}
```

#### Tool 3: `ip_geolocation`

Returns geolocation and network information for an IP address.

**Input schema:**
```json
{
  "type": "object",
  "properties": {
    "ip": {
      "type": "string",
      "description": "IPv4 or IPv6 address to look up"
    }
  },
  "required": ["ip"]
}
```

**Implementation notes:**
- Use a free geolocation API that doesn't require an API key for the initial implementation. Good options: `ip-api.com` (free for non-commercial, 45 req/min) or the MaxMind GeoLite2 database (free with registration, local lookups)
- Recommended: start with `ip-api.com` for simplicity, but use an async HTTP client (`httpx`) so it doesn't block
- Return: country, region, city, ISP/organization, AS number, AS name, reverse DNS (PTR record via `dnspython`)
- Validate input is a valid IP address (not a domain name, not a private/reserved range — though note the warning if it IS private, since that's useful context)
- Handle errors: invalid IP, API rate limiting, API unreachable

**Response format:**
```json
{
  "tool": "ip_geolocation",
  "ip": "93.184.216.34",
  "status": "success",
  "result": {
    "country": "United States",
    "country_code": "US",
    "region": "California",
    "city": "Los Angeles",
    "isp": "Edgecast Inc.",
    "org": "Verizon Digital Media Services",
    "as_number": 15133,
    "as_name": "Edgecast Inc.",
    "reverse_dns": "93.184.216.34.example.com",
    "is_private": false
  }
}
```

#### Tool 4: `reverse_dns_lookup`

Performs reverse DNS (PTR) lookup on an IP address and optionally resolves forward to verify.

**Input schema:**
```json
{
  "type": "object",
  "properties": {
    "ip": {
      "type": "string",
      "description": "IPv4 or IPv6 address for reverse DNS lookup"
    },
    "verify_forward": {
      "type": "boolean",
      "description": "If true, also perform a forward lookup on the PTR result to verify it points back to this IP. Defaults to true."
    }
  },
  "required": ["ip"]
}
```

**Implementation notes:**
- Use `dnspython` for the PTR query
- Construct the reverse lookup name (e.g., `34.216.184.93.in-addr.arpa` for IPv4, nibble format for IPv6)
- If `verify_forward` is true, resolve the PTR result back to an A/AAAA record and check if it matches the original IP (forward-confirmed reverse DNS)
- This is particularly useful for SecOps: legitimate mail servers and CDNs typically have matching forward/reverse DNS; suspicious hosts often don't

**Response format:**
```json
{
  "tool": "reverse_dns_lookup",
  "ip": "93.184.216.34",
  "status": "success",
  "result": {
    "ptr_records": ["example.com"],
    "forward_verified": true,
    "forward_addresses": ["93.184.216.34"]
  }
}
```

### Future Tool (not for initial implementation): `threat_intel_check`

Placeholder for when threat intel API access becomes available. Document the intended interface now so the architecture supports it.

**Intended input schema:**
```json
{
  "type": "object",
  "properties": {
    "indicator": {
      "type": "string",
      "description": "Domain name or IP address to check"
    },
    "indicator_type": {
      "type": "string",
      "enum": ["domain", "ip"],
      "description": "Type of indicator"
    }
  },
  "required": ["indicator", "indicator_type"]
}
```

**Intended services (when API keys are available):**
- VirusTotal: domain/IP reputation, detection ratios, associated samples
- AbuseIPDB: IP abuse reports, confidence score
- URLhaus: known malware distribution URLs
- Shodan: open ports, services, banners (for IP investigation)

**For now:** Add a commented-out tool definition and a stub function that returns a message indicating which services will be available in the future. This way the system prompt can mention the capability is coming.

### Web Search Integration

Enable the Anthropic API's built-in web search tool alongside the custom tools.

**Implementation notes:**
- In `anthropic_client.py`, when building the API request, include the web search tool in the tools list:
  ```python
  {"type": "web_search_20250305", "name": "web_search"}
  ```
- Web search does NOT route through MCP — it's handled by the Anthropic API itself
- No tool result routing needed; the API handles it transparently
- The web search tool should be included alongside the existing MCP-discovered tools and the new security tools

**Important:** Web search tool results come back as part of the normal response content blocks. The existing conversation loop in `anthropic_client.py` should already handle this correctly since it processes `tool_use` blocks generically and the web search results appear as text content.

### Tool Registration in the Chat App

Modify `copilot/copilot/app.py` to register the security tools alongside the MCP-discovered tools.

**Approach:**
- Create a function in `security_tools.py` that returns the Anthropic-format tool definitions (list of dicts with `name`, `description`, `input_schema`)
- Create a dispatch function that takes a tool name and arguments, calls the right function, and returns the result string
- In `app.py` during lifespan startup, combine the MCP tools with the security tools into a single `_tools` list
- In `anthropic_client.py`, add routing logic: if a tool call name matches a security tool, dispatch locally instead of routing to MCP

**Tool routing logic (in `anthropic_client.py` or a new routing module):**
```
if tool_name starts with a node prefix (contains "__"):
    → route to MCP pool
elif tool_name is a known security tool:
    → dispatch locally via security_tools module
else:
    → error: unknown tool
```

### System Prompt Updates

Add a new section to `copilot/copilot/prompt.txt` documenting the security investigation tools.

**Add after the "Investigation Patterns" section:**

```
## Domain & IP Investigation Tools

You have direct access to the following investigation tools (these do not target a specific node):

### whois_lookup
  Look up WHOIS registration data for a domain.
  Input: domain (registrable domain, not a subdomain)
  Returns: registrar, creation/expiration dates, name servers, registrant info, DNSSEC status
  Use when: Checking if a domain is newly registered (DGA/disposable), identifying ownership,
  verifying legitimate domains

### dns_resolve
  Resolve DNS records for a name.
  Input: name, optional record_types (defaults to A, AAAA, CNAME, MX, NS, TXT)
  Returns: DNS records by type, query time
  Use when: Checking what IPs a suspicious domain resolves to, finding mail servers,
  examining TXT records for SPF/DKIM

### ip_geolocation
  Get geolocation and network info for an IP address.
  Input: ip (IPv4 or IPv6)
  Returns: country, city, ISP, organization, AS number/name, reverse DNS
  Use when: Identifying where traffic originates, checking if IPs belong to known
  hosting providers or residential ranges, investigating C2 infrastructure by
  checking geolocation of IPs that a suspicious domain resolves to (bulletproof
  hosting, fast-flux across many ASes, residential IPs used as proxies)

### reverse_dns_lookup
  Perform reverse DNS (PTR) lookup with optional forward verification.
  Input: ip, optional verify_forward (default true)
  Returns: PTR records, forward verification result
  Use when: Checking if an IP has legitimate reverse DNS, verifying server identity

### Web Search
  You can also search the web for threat intelligence, domain reputation reports,
  and security advisories. Use this for open-ended research after structured lookups.

## SecOps Investigation Patterns

### Suspicious Domain Investigation
1. Identify suspicious domains from Statmon (NXDOMAIN floods, unusual query patterns)
2. whois_lookup on the core domain — check registration date, registrar, registrant
3. dns_resolve to see current infrastructure (A records, name servers)
4. ip_geolocation on EACH resolved IP — hosting provider, country, AS number
   (C2 servers often sit in bulletproof hosting or unexpected jurisdictions;
   multiple A records resolving to diverse geolocations is a fast-flux indicator)
5. reverse_dns_lookup on resolved IPs — legitimate services have matching rDNS,
   C2 infrastructure typically does not
6. Web search for threat reports mentioning the domain, IP ranges, or AS numbers
7. Correlate back to Statmon: which clients are querying this domain?

### Suspicious Client Investigation
1. Identify client IP from Statmon top-clients or anomaly detection
2. reverse_dns_lookup — does it have legitimate rDNS?
3. ip_geolocation — residential ISP? hosting provider? expected geography?
4. Statmon replay of the client's queries — what domains are they hitting?
5. whois_lookup on unusual domains from the replay
6. Web search for the IP or AS in threat feeds

### DGA / PRSD Analysis
1. Statmon group-count of NXDOMAIN by domain — identify the targeted zone
2. whois_lookup on the core domain — is it a legitimate domain under attack, or attacker-controlled?
3. dns_resolve on the core domain — what's the authoritative infrastructure?
4. auth-querystore to check upstream impact (timeouts, NXDOMAIN rates)
5. Web search for known DGA families targeting this domain pattern

### C2 / Botnet Infrastructure Investigation
1. Statmon identifies clients querying a suspicious domain that resolves successfully (NOERROR)
2. dns_resolve on the domain — collect all A and AAAA records
3. ip_geolocation on EACH resolved IP:
   - Are they in the same AS or spread across many? (fast-flux uses many ASes)
   - Are they in known bulletproof hosting providers?
   - Are they in residential IP ranges? (compromised hosts used as proxies)
4. reverse_dns_lookup on each resolved IP — C2 servers rarely have legitimate rDNS
5. whois_lookup on the domain — recently registered? privacy-shielded? free registrar?
6. Check if the domain's name servers are also suspicious (dns_resolve NS records,
   then ip_geolocation on NS IPs)
7. Statmon replay of client queries to this domain — how frequent? regular intervals
   suggest beaconing behavior
8. Web search for the domain, resolved IPs, and AS numbers in threat feeds
9. Summarize: domain age, hosting infrastructure geography, rDNS status, query
   pattern (beaconing interval), and number of affected clients across all nodes
```

### Dependencies

Add to `copilot/pyproject.toml`:
```
"python-whois",
"dnspython",
"httpx",
```

### Configuration

Add an optional `security_tools` section to the chat app config:

```yaml
# In /etc/copilot/config.yaml
security_tools:
  dns_resolver: "system"          # "system", "8.8.8.8", "1.1.1.1"
  whois_timeout_seconds: 10
  geolocation_provider: "ip-api"  # "ip-api" (free, no key needed)
  # Future: API keys for threat intel services
  # virustotal_api_key: ""
  # abuseipdb_api_key: ""
```

If the `security_tools` section is absent, use sensible defaults (system resolver, 10s WHOIS timeout, ip-api for geolocation). The tools should work with zero configuration.

### Tests

Create `tests/test_security_tools.py`:

- Test WHOIS parsing with mocked responses (don't make real WHOIS queries in tests)
- Test DNS resolution with mocked `dnspython` responses
- Test IP geolocation with mocked HTTP responses
- Test reverse DNS with mocked responses, including forward verification logic
- Test input validation: invalid domains, invalid IPs, private IP ranges, URLs instead of domains
- Test error handling: timeouts, unreachable services, unexpected response formats
- Test the tool dispatch function: correct routing by tool name, unknown tool error

### Tracing

The existing `TraceCollector` should capture timing for security tool calls just as it does for MCP tool calls. The tool call span should include:
- `tool_name`: e.g., `whois_lookup`
- `query`: the domain or IP being investigated
- `response_bytes`: size of the result
- `duration_ms`: execution time

This requires the same `trace.span()` wrapping used for MCP tool calls in `anthropic_client.py`.

## Example End-to-End Interaction

**User:** "We're seeing a lot of NXDOMAIN from client 10.0.5.42. Can you investigate?"

**Claude's workflow:**

Round 1 (parallel, Statmon):
- `dns_node_a__statmon(command="querystore.top-domains duration=3600 max-results=20 filter=\"((result-code (true (nxdomain)))(client-address (true (10.0.5.42))))\"")`
- `dns_node_b__statmon(command="querystore.top-domains duration=3600 max-results=20 filter=\"((result-code (true (nxdomain)))(client-address (true (10.0.5.42))))\"")`

Round 2 (parallel, security tools — investigate domain and client):
- `whois_lookup(domain="suspiciousdomain.xyz")`
- `dns_resolve(name="suspiciousdomain.xyz")`
- `reverse_dns_lookup(ip="10.0.5.42")`
- `ip_geolocation(ip="10.0.5.42")`

Round 3 (parallel, security tools — investigate resolved IPs):
After dns_resolve returns A records (e.g., 185.234.72.11, 91.215.85.33):
- `ip_geolocation(ip="185.234.72.11")`
- `ip_geolocation(ip="91.215.85.33")`
- `reverse_dns_lookup(ip="185.234.72.11")`
- `reverse_dns_lookup(ip="91.215.85.33")`

Round 4 (web search):
- Web search for "suspiciousdomain.xyz malware" or the AS numbers / IP ranges found

Round 5 (synthesis):
Claude presents a unified report combining Statmon findings, domain registration data, DNS infrastructure, client identification, and any web-sourced threat intel.

## Out of Scope (for this iteration)

- Threat intel API integrations (VirusTotal, AbuseIPDB, etc.) — deferred until API access is available
- Bulk lookups (e.g., "check all 50 NXDOMAIN domains") — Claude can iterate, but we're not building batch tooling
- Caching of lookup results — can be added later if rate limiting becomes an issue
- Domain age calculation or risk scoring — let Claude reason about the raw data for now