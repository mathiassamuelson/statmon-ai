Look up WHOIS registration data for a domain. Returns the registrar, creation/expiration/updated dates, name servers, registrant organization and country, DNSSEC status, and the raw WHOIS response (truncated).

**Use when** you need to assess the legitimacy of a domain that's surfaced in DNS traffic — recently registered domains, privacy-shielded registrations, free-tier registrars, and unusual registrant geography are all signals worth investigating. Particularly useful as a follow-up to Statmon findings: a domain receiving heavy NXDOMAIN traffic that was registered three days ago tells a different story from a domain registered fifteen years ago.

**Input notes:**
- Pass the **registrable domain only**. For `mail.api.example.com`, look up `example.com`. WHOIS records exist at the apex; subdomain queries either fail or fall back to the apex anyway.
- Don't include a URL scheme. `https://example.com` is wrong; `example.com` is right.
- Don't pass an IP address — use `ip_geolocation` or `reverse_dns_lookup` for IPs.

**Output highlights to look for:**
- `creation_date` within the last 30 days → strong signal for DGA, phishing, or disposable infrastructure.
- Privacy-shield registrant (`REDACTED FOR PRIVACY`, `WHOISGUARD`, etc.) → not inherently malicious, but combined with recent registration it's more suspicious.
- Free or bulletproof registrars (Freenom, NameSilo, etc.) → frequently used for malicious infrastructure.
- Status codes like `clientHold` or `pendingDelete` → domain in a non-functional state.

**Limitations:**
- WHOIS data is registrar-dependent and the format varies. The `raw` field captures the original response when structured parsing misses something.
- Some TLDs (most notably `.io`, `.ai`, certain ccTLDs) return sparse or inconsistent data.
- Rate limits exist on the registrar side. Don't loop over hundreds of domains.

This tool runs locally in the chat app — it does not target a specific DNS node.
