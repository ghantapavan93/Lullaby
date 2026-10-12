#!/usr/bin/env bash
# Open ports 80 and 443 on the VM's own firewall. Run only after the owner
# approves public access; the cloud security list must allow them too.
set -euo pipefail
for port in 80 443; do
  iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport "$port" -m state --state NEW -j ACCEPT
done
command -v netfilter-persistent >/dev/null && netfilter-persistent save
