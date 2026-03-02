#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <count>" >&2
    echo "Example: $0 100" >&2
    exit 1
fi

count=$1

consonants="bcdfghjklmnpqrstvwxyz"
vowels="aeiou"
tlds=("com" "net" "org" "io" "dev" "co" "info" "biz" "xyz" "app")

gen_label() {
    local min_len=$1
    local max_len=$2
    local len=$((RANDOM % (max_len - min_len + 1) + min_len))
    local label=""
    for ((c = 0; c < len; c++)); do
        if ((c % 2 == 0)); then
            label+="${consonants:RANDOM % ${#consonants}:1}"
        else
            label+="${vowels:RANDOM % ${#vowels}:1}"
        fi
    done
    echo "$label"
}

for ((i = 0; i < count; i++)); do
    # 2-4 labels (including TLD)
    num_labels=$((RANDOM % 3 + 2))
    tld=${tlds[RANDOM % ${#tlds[@]}]}
    tld_len=${#tld}

    # Budget remaining chars for non-TLD labels (dots count toward total)
    # Total length target: 8-20 chars including dots and TLD
    target_len=$((RANDOM % 13 + 8))
    available=$((target_len - tld_len - 1)) # subtract TLD and its dot
    non_tld=$((num_labels - 1))

    domain=""
    for ((l = 0; l < non_tld; l++)); do
        if ((l == non_tld - 1)); then
            # Last non-TLD label gets remaining budget
            dot_cost=$((l > 0 ? 0 : 0))
            lbl_len=$((available > 0 ? available : 2))
            if ((lbl_len < 2)); then lbl_len=2; fi
            if ((lbl_len > 12)); then lbl_len=12; fi
        else
            # Allocate 2-5 chars, leave room for remaining labels
            remaining_labels=$((non_tld - l - 1))
            max_here=$((available - remaining_labels * 3)) # reserve 2 + dot per remaining
            if ((max_here > 7)); then max_here=7; fi
            if ((max_here < 2)); then max_here=2; fi
            lbl_len=$((RANDOM % (max_here - 1) + 2))
        fi
        label=$(gen_label "$lbl_len" "$lbl_len")
        if [ -n "$domain" ]; then
            domain="${domain}.${label}"
            available=$((available - lbl_len - 1))
        else
            domain="${label}"
            available=$((available - lbl_len))
        fi
    done

    echo "${domain}.${tld}"
done
