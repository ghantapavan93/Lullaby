#!/usr/bin/env bash
# Install or update the Proof Layer on one Ubuntu 24.04 VM (aarch64 or x86_64).
#
#   sudo SLOTS=6 SITE=203-0-113-7.sslip.io CODE=/opt/proof-layer/app bash install.sh
#
# CODE is the unpacked public copy (release/build_public.py). The script is
# idempotent: run it again after unpacking a newer copy. It sets up:
#   - PostgreSQL 16, one app and one provider database per workspace slot,
#     reached over the local socket only (peer authentication, no passwords);
#   - a Python venv with the pinned runtime dependencies (PyAV bundles FFmpeg);
#   - systemd units: proof-layer-slot@N, proof-layer-worker@N, proof-layer-gateway,
#     and a nightly database backup; all restart on failure and start on boot;
#   - Caddy in front of the gateway: HTTPS for SITE when it is set, otherwise
#     plain HTTP on 127.0.0.1:8080 only (reachable through an SSH tunnel).
# It never opens the firewall: deploy/vm/open-web.sh does that, once approved.
set -euo pipefail

SLOTS="${SLOTS:-6}"
CODE="${CODE:-/opt/proof-layer/app}"
SITE="${SITE:-}"
DATA=/var/lib/proof-layer
VENV=/opt/proof-layer/venv
APP="$CODE/proof-layer"
ETC=/etc/proof-layer
SYSTEMD="${SYSTEMD:-1}"     # 0 inside a container without systemd (tests)

log() { printf '\n== %s\n' "$*"; }

log "packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -yq --no-install-recommends postgresql postgresql-contrib python3-venv python3-pip sqlite3 \
  ca-certificates curl gnupg debian-keyring debian-archive-keyring apt-transport-https sudo
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q && apt-get install -yq caddy
fi

log "service user and folders"
id prooflayer >/dev/null 2>&1 || useradd --system --home-dir /opt/proof-layer --shell /usr/sbin/nologin prooflayer
mkdir -p "$DATA/beats" "$DATA/backups" "$ETC"
for n in $(seq 1 "$SLOTS"); do mkdir -p "$DATA/slot$n"; done
chown -R prooflayer: "$DATA"
chmod 750 "$DATA"

log "python environment"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$CODE/deploy/vm/requirements.txt"

log "postgresql"
if [ "$SYSTEMD" = 1 ]; then systemctl enable --now postgresql; else service postgresql start; fi
sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='prooflayer'" | grep -q 1 || sudo -u postgres createuser prooflayer
SOCKET=/var/run/postgresql
for n in $(seq 1 "$SLOTS"); do
  for db in "proof_layer_s$n" "provider_sim_s$n"; do
    sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1 || sudo -u postgres createdb -O prooflayer "$db"
  done
  cat > "$ETC/slot-$n.env" <<EOF
DATABASE_URL=postgresql+psycopg://prooflayer@/proof_layer_s$n?host=$SOCKET
PROVIDER_DATABASE_URL=postgresql+psycopg://prooflayer@/provider_sim_s$n?host=$SOCKET
PROOF_LAYER_DEV=1
PROOF_LAYER_SAMPLE=staged
PROOF_LAYER_HTTPS=1
PROOF_LAYER_LEASE_SECONDS=3
PROOF_LAYER_MEDIA_ROOT=$DATA/slot$n
PROOF_LAYER_HEARTBEAT=$DATA/beats/worker-$n
PORT=$((9100 + n))
EOF
  log "slot $n: migrate"
  (cd "$APP" && sudo -u prooflayer env $(grep -v '^PORT=' "$ETC/slot-$n.env" | xargs) "$VENV/bin/python" -m alembic upgrade head)
  # Seed only an empty database: an update must not wipe a workspace someone is using.
  members=$(sudo -u prooflayer psql -h "$SOCKET" -d "proof_layer_s$n" -tAc "SELECT count(*) FROM members")
  if [ "$members" = 0 ]; then
    log "slot $n: seed"
    (cd "$APP" && sudo -u prooflayer env $(grep -v '^PORT=' "$ETC/slot-$n.env" | xargs) "$VENV/bin/python" -m app.seed >/dev/null)
  fi
done

SLOT_LIST=$(seq -s, 1 "$SLOTS")
cat > "$ETC/gateway.env" <<EOF
GATEWAY_SLOTS=$SLOT_LIST
GATEWAY_PORT_BASE=9100
GATEWAY_STATE=$DATA/gateway.sqlite3
GATEWAY_HEARTBEATS=$DATA/beats
GATEWAY_PG_DSN=postgresql://prooflayer@/postgres?host=$SOCKET
GATEWAY_SECURE_COOKIE=1
GATEWAY_STATIC=$APP/static
GATEWAY_LANDING=$CODE/deploy/landing/index.html
EOF
chmod 640 "$ETC"/*.env
chgrp prooflayer "$ETC"/*.env

log "caddy"
if [ -n "$SITE" ]; then
  sed "s/{SITE}/$SITE/" "$CODE/deploy/vm/Caddyfile" > /etc/caddy/Caddyfile
else
  cp "$CODE/deploy/vm/Caddyfile.local" /etc/caddy/Caddyfile
fi
mkdir -p /var/log/caddy && chown caddy: /var/log/caddy 2>/dev/null || true

if [ "$SYSTEMD" = 1 ]; then
  log "systemd"
  install -m 644 "$CODE"/deploy/vm/systemd/* /etc/systemd/system/
  install -d /etc/systemd/journald.conf.d
  printf '[Journal]\nSystemMaxUse=500M\n' > /etc/systemd/journald.conf.d/proof-layer.conf
  systemctl daemon-reload
  systemctl restart systemd-journald
  for n in $(seq 1 "$SLOTS"); do
    systemctl enable "proof-layer-slot@$n" "proof-layer-worker@$n"
    systemctl restart "proof-layer-slot@$n" "proof-layer-worker@$n"
  done
  systemctl enable proof-layer-gateway proof-layer-backup.timer caddy
  systemctl restart proof-layer-gateway caddy
  systemctl start proof-layer-backup.timer
  log "health"
  for i in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:8800/healthz >/dev/null 2>&1; then break; fi
    sleep 2
  done
  curl -sS http://127.0.0.1:8800/healthz; echo
fi
