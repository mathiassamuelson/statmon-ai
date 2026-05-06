Get geolocation and network metadata for an IP address. Returns country, city, ISP, organization, AS number and name, and reverse DNS (PTR) when available.

**Use when** you have an IP address and need to know *where it sits* in the network: what country it's in, what ASN it belongs to, what kind of organization owns it (residential ISP, hosting provider, cloud, enterprise). Geographic and AS-level context is one of the most useful disambiguating signals in DNS investigations.

**Common patterns:**
- After `dns_resolve` returns multiple A/AAAA records, geolocate each one. Diverse ASes across multiple countries → fast-flux indicator. Single bulletproof-hosting AS → likely malicious. Single major-cloud AS → probably benign infrastructure shared with many tenants.
- After identifying a top-talker client in `statmon`, geolocate it. Residential ISP → compromised consumer device. Hosting provider → likely a server, possibly a misconfigured one or a scanner. Unexpected country → policy violation or infrastructure outside the deployment footprint.
- When investigating a suspicious domain, geolocate its name-server IPs too — attackers sometimes leave sloppy infrastructure footprints in their NS records.

**Input notes:**
- `ip` accepts IPv4 or IPv6.
- Private and reserved IP ranges (RFC 1918, link-local, loopback, multicast, etc.) are flagged in the response with `is_private: true` and skip the external API call. Geolocation of internal addresses isn't meaningful.

**Output interpretation:**
- `org` and `isp` distinguish hosting from residential. "Comcast Cable Communications" vs. "DigitalOcean LLC" tell different stories about the same query rate.
- `as_number` is the most stable identifier — organizations rename, but ASNs are durable. Useful as a search term in threat-intel feeds.
- `country` matters more for compliance and policy questions ("why is this client in CN?") than for malice-scoring; malicious infrastructure exists everywhere.
- `reverse_dns` (when present) is a quick legitimacy signal. `ec2-x-y-z.compute.amazonaws.com` is normal for AWS. Bare IPs with no rDNS, especially on hosting providers, are a mild signal of carelessness or anonymity-seeking.

**Limitations:**
- Geolocation is best-effort. City-level data is approximate; country-level is reliable for most allocations but wrong for some VPN exits and anycast addresses.
- The default provider (ip-api.com free tier) is rate-limited. Don't loop over hundreds of IPs in a single investigation.
- ASN data lags — recent IP allocations and reassignments may not be reflected.

This tool runs locally in the chat app — it does not target a specific DNS node.
