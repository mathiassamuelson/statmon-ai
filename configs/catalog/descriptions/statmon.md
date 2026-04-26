Execute a read-only Statmon querystore command on this DNS node. Returns JSON
output from the Statmon log collector.

Use for query activity analysis, traffic metrics, bandwidth statistics, and
forensic replay of DNS queries. Commands use S-expression filter syntax for
targeted searches.

Examples:
  querystore.top-clients duration=3600 max-results=10
  querystore.count duration=300 filter="((result-code (true (nxdomain))))"
  auth-querystore.group-count group-by=(result-code) duration=600

> **Note:** the full Statmon Querystore CLI reference is not included in this
> public repository. See README.md for instructions on providing your own
> reference. The reference should document the `querystore.*` and
> `auth-querystore.*` command families: argument syntax (key=value),
> S-expression filter syntax, and group-by attributes.
