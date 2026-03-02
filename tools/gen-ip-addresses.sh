#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <count> [prefix]" >&2
    echo "Example: $0 50 10.0." >&2
    exit 1
fi

count=$1
prefix=${2:-""}

# Ensure prefix ends with a dot if non-empty
if [ -n "$prefix" ] && [[ "$prefix" != *. ]]; then
    prefix="${prefix}."
fi

# Count octets in prefix to know how many to generate
dots="${prefix//[^.]}"
prefix_octets=${#dots}
remaining=$((4 - prefix_octets))

for ((i = 0; i < count; i++)); do
    suffix=""
    for ((j = 0; j < remaining; j++)); do
        octet=$((RANDOM % 254 + 1))
        if [ -n "$suffix" ]; then
            suffix="${suffix}.${octet}"
        else
            suffix="${octet}"
        fi
    done
    echo "${prefix}${suffix}"
done
