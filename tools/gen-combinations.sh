#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 3 ]; then
    echo "Usage: $0 <count> <ip-file> <domain-file>" >&2
    echo "Example: $0 100 ips.txt domains.txt" >&2
    exit 1
fi

count=$1
ip_file=$2
domain_file=$3

ips=()
while IFS= read -r line; do ips+=("$line"); done < "$ip_file"
domains=()
while IFS= read -r line; do domains+=("$line"); done < "$domain_file"

num_ips=${#ips[@]}
num_domains=${#domains[@]}

for ((i = 0; i < count; i++)); do
    ip=${ips[RANDOM % num_ips]}
    domain=${domains[RANDOM % num_domains]}
    echo "$domain $ip"
done
