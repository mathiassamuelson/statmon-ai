Perform a reverse DNS (PTR) lookup on an IP address, with optional forward verification. Returns any PTR records for the IP, and (when `verify_forward` is true, the default) whether the PTR's forward DNS confirms the reverse mapping.

**Use when** you need to know whether an IP claims a hostname, and whether that claim is consistent. Forward-confirmed reverse DNS (FCrDNS) is a low-but-real legitimacy signal: legitimate mail servers, public DNS resolvers, and most cloud-managed infrastructure publish matching forward and reverse records. C2 servers, residential proxies, and quickly-stood-up malicious infrastructure typically don't.

**The forward-verification step is what makes this useful.** A bare PTR record can claim anything — `ip-attacker-controlled.com` is a valid PTR. The verification step performs an A/AAAA lookup on the PTR result and checks whether the original IP appears in the answer. Only if forward and reverse agree does the rDNS claim hold up.

**Common patterns:**
- Triaging a top-talker client from Statmon. Matching FCrDNS → probably a real, managed host. No PTR → could be anything; not a strong signal alone but absent legitimacy. Mismatched forward/reverse → claim doesn't hold; treat with suspicion.
- Investigating IPs returned by `dns_resolve` for a suspicious domain. A domain whose A records all have legitimate FCrDNS pointing at the same hosting provider is probably benign infrastructure. A domain whose A records have no PTR or mismatched PTR is more suspicious.
- Mail-related investigations specifically — many mail systems require FCrDNS as a basic anti-spam check, so its presence/absence is meaningful.

**Input notes:**
- `ip` accepts IPv4 or IPv6.
- `verify_forward` defaults to true. Set to false when you only need the raw PTR string and don't want to incur the second lookup (e.g., bulk PTR collection).

**Output interpretation:**
- `ptr_records: []` → no PTR record exists. Common for residential and obscure hosting; not strongly malicious by itself.
- `ptr_records: [...]`, `forward_verified: true` → matching FCrDNS. Mild legitimacy signal.
- `ptr_records: [...]`, `forward_verified: false` → PTR exists but doesn't forward-resolve back to the IP. The hostname claim is unverifiable; treat the PTR string as untrusted.
- `ptr_records: [...]`, `forward_verified: null` → forward verification was skipped (you passed `verify_forward: false`).

**Limitations:**
- PTR records are controlled by the IP block holder (not the hostname owner), so they can lag reality and frequently don't exist at all.
- Forward verification depends on the resolver. The default uses the chat app's configured resolver.
- Not a security boundary on its own. FCrDNS is one signal; weight it accordingly and combine with `whois_lookup`, `ip_geolocation`, and traffic patterns.

This tool runs locally in the chat app — it does not target a specific DNS node.
