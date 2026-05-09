Resolve DNS records for a name. Returns records grouped by type (A, AAAA, CNAME, MX, NS, TXT by default; pass `record_types` to specify others) along with query timing.

**Use when** you need the *current* DNS state of a name — what IPs it resolves to, what mail servers it uses, who its authoritative name servers are, what TXT records (SPF, DKIM, verification tokens) it publishes. This is a live lookup against a resolver, not a query against the historical Statmon store.

**Distinguish from `statmon`:**
- `statmon` answers "what did clients ask for, and what did they get?" — historical, observed.
- `dns_resolve` answers "what does this name resolve to right now?" — live, authoritative.

If a question involves past traffic, use `statmon`. If it involves current state, use `dns_resolve`.

**Input notes:**
- `name` can be a full DNS name (`mail.example.com`) or an apex (`example.com`). It's not normalized to the registrable domain — pass exactly what you want resolved.
- `record_types` is optional. Defaults cover the common cases (A, AAAA, CNAME, MX, NS, TXT). Pass an explicit list to query others (PTR, SRV, CAA, SOA, DS, DNSKEY, TLSA, etc.).

**Output interpretation:**
- A and AAAA records → current hosting infrastructure. Multiple records spread across diverse ASes can indicate fast-flux; a single record in a residential ISP can indicate a compromised host used as a proxy.
- NS records → authoritative infrastructure. Compare to the WHOIS `name_servers` field; mismatches warrant investigation.
- MX records → mail infrastructure. Useful when investigating phishing or business-email-compromise patterns.
- TXT records → SPF, DKIM, DMARC, and ownership-verification tokens. The presence of `v=spf1 -all` on a domain that shouldn't be sending mail is suspicious.
- CNAME → indirection. A domain that CNAMEs to a CDN or third-party service is normal; a domain that CNAMEs to an unfamiliar hostname is worth following.

**Limitations:**
- Results reflect the resolver's view, which may be cached. For authoritative answers, the chat app's `dns_resolver` config can be set to a specific resolver.
- DNSSEC validation status is not surfaced in the result. If validation matters, follow up with a DS/DNSKEY query.
- Some authoritative servers rate-limit. Repeated queries against the same name in quick succession may return TIMEOUT.

This tool runs locally in the chat app — it does not target a specific DNS node.
