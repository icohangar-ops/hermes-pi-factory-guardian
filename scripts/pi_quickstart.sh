#!/usr/bin/env bash
# =============================================================================
# pi_quickstart.sh — hermes-pi-factory-guardian + Twingate overlay deployment
# -----------------------------------------------------------------------------
# Runbook-style script: clone the repo, drop in the three overlay files,
# fill in .env, bring up the stack, and verify the Connector registers.
#
# Tested on:
#   - Raspberry Pi 5, Raspberry Pi OS Lite (64-bit, Bookworm)
#   - Ubuntu 24.04 LTS (aarch64) — for non-Pi dev/test
#
# Usage:
#   bash pi_quickstart.sh              # interactive: prompts for tokens
#   bash pi_quickstart.sh --non-interactive \\
#       --network acme --access-token AT --refresh-token RT \\
#       --factory factory-a --line line-1
# =============================================================================
set -euo pipefail

# --- Defaults -----------------------------------------------------------------
REPO="https://github.com/icohangar-ops/hermes-pi-factory-guardian.git"
CLONE_DIR="${CLONE_DIR:-/home/pi/hermes-pi-factory-guardian}"
OVERLAY_DIR="${OVERLAY_DIR:-/home/z/my-project/download}"

TWINGATE_NETWORK=""
TWINGATE_ACCESS_TOKEN=""
TWINGATE_REFRESH_TOKEN=""
FACTORY_NAME="factory-a"
FACTORY_LINE="line-1"
INTERACTIVE=1

# --- Parse args ---------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --non-interactive) INTERACTIVE=0; shift ;;
    --network)         TWINGATE_NETWORK="$2"; shift 2 ;;
    --access-token)    TWINGATE_ACCESS_TOKEN="$2"; shift 2 ;;
    --refresh-token)   TWINGATE_REFRESH_TOKEN="$2"; shift 2 ;;
    --factory)         FACTORY_NAME="$2"; shift 2 ;;
    --line)            FACTORY_LINE="$2"; shift 2 ;;
    --clone-dir)       CLONE_DIR="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# --- Helpers ------------------------------------------------------------------
log()  { printf '\033[1;34m[quickstart]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[ok]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[err]\033[0m %s\n' "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

# --- Preflight ----------------------------------------------------------------
log "Preflight checks"
require_cmd git
require_cmd docker
# docker compose v2 is a plugin; check via `docker compose version`
docker compose version >/dev/null 2>&1 || die "docker compose v2 not installed (apt install docker-compose-plugin)"

if [[ $EUID -eq 0 ]]; then
  warn "running as root — ok on Pi OS, but prefer a non-root docker group user"
fi

# --- Interactive prompts ------------------------------------------------------
if [[ $INTERACTIVE -eq 1 ]]; then
  [[ -z "$TWINGATE_NETWORK" ]]      && read -rp "Twingate network slug (e.g. acme for acme.twingate.com): " TWINGATE_NETWORK
  [[ -z "$TWINGATE_ACCESS_TOKEN" ]] && read -rp "Twingate access token (generate in Admin Console): " TWINGATE_ACCESS_TOKEN
  [[ -z "$TWINGATE_REFRESH_TOKEN" ]] && read -rp "Twingate refresh token: " TWINGATE_REFRESH_TOKEN
  read -rp "Factory name [${FACTORY_NAME}]: " i; FACTORY_NAME="${i:-$FACTORY_NAME}"
  read -rp "Line name    [${FACTORY_LINE}]: " i; FACTORY_LINE="${i:-$FACTORY_LINE}"
fi

[[ -n "$TWINGATE_NETWORK" ]]       || die "TWINGATE_NETWORK is required"
[[ -n "$TWINGATE_ACCESS_TOKEN" ]]  || die "TWINGATE_ACCESS_TOKEN is required"
[[ -n "$TWINGATE_REFRESH_TOKEN" ]] || die "TWINGATE_REFRESH_TOKEN is required"

# --- Clone --------------------------------------------------------------------
if [[ -d "$CLONE_DIR/.git" ]]; then
  log "Existing clone at $CLONE_DIR — pulling latest"
  git -C "$CLONE_DIR" pull --ff-only
else
  log "Cloning repo to $CLONE_DIR"
  git clone --depth 1 "$REPO" "$CLONE_DIR"
fi
cd "$CLONE_DIR"
ok "Repo ready at $(pwd)"

# --- Drop in overlay files ----------------------------------------------------
log "Installing Twingate overlay files"

# 1) docker-compose overlay
cp -v "$OVERLAY_DIR/docker-compose.twingate.yml" ./docker-compose.twingate.yml

# 2) extended config (replaces the base config — base is preserved as .orig)
if [[ ! -f config/factory_config.yaml.orig ]]; then
  cp config/factory_config.yaml config/factory_config.yaml.orig
fi
cp -v "$OVERLAY_DIR/factory_config.twingate.yaml" config/factory_config.yaml

# 3) .env extension
if [[ ! -f .env ]]; then
  cp .env.example .env
  warn "Created .env from .env.example — edit it to fill in Telegram/Slack/SMTP/Hermes vars"
fi

# Append Twingate vars if not already present
append_env() {
  local key="$1" val="$2"
  if grep -q "^${key}=" .env; then
    sed -i "s|^${key}=.*|${key}=${val}|" .env
  else
    echo "${key}=${val}" >> .env
  fi
}
append_env FACTORY_NAME           "$FACTORY_NAME"
append_env FACTORY_LINE           "$FACTORY_LINE"
append_env TWINGATE_NETWORK       "$TWINGATE_NETWORK"
append_env TWINGATE_ACCESS_TOKEN  "$TWINGATE_ACCESS_TOKEN"
append_env TWINGATE_REFRESH_TOKEN "$TWINGATE_REFRESH_TOKEN"
ok ".env updated with Twingate vars (FACTORY_NAME, FACTORY_LINE, TWINGATE_*)"

# --- Validate the merged compose config --------------------------------------
log "Validating merged docker-compose config"
if docker compose -f docker-compose.yml -f docker-compose.twingate.yml config >/dev/null; then
  ok "Merged compose config is valid"
else
  die "Merged compose config invalid — check .env for missing vars"
fi

# --- Bring up the stack -------------------------------------------------------
log "Bringing up the stack (this pulls twingate/connector:latest on first run)"
docker compose \
  -f docker-compose.yml \
  -f docker-compose.twingate.yml \
  up -d
ok "Stack is up"

# --- Verify -------------------------------------------------------------------
log "Verifying containers"
sleep 5
docker compose -f docker-compose.yml -f docker-compose.twingate.yml ps

log "Checking Connector logs (last 20 lines)"
docker logs --tail 20 twingate-connector-${FACTORY_NAME}-${FACTORY_LINE} 2>&1 || \
  warn "Connector container not found — check container name in 'docker ps'"

log "Checking guardian health"
sleep 2
if docker exec factory-guardian python -c "import src.sensors.gpio_reader; print('GPIO OK')" 2>/dev/null; then
  ok "Guardian is healthy"
else
  warn "Guardian GPIO check failed — this is expected on non-Pi hosts"
fi

# --- Next steps ---------------------------------------------------------------
cat <<EOF

=============================================================================
 Twingate overlay deployed. Next steps:
=============================================================================

1. In the Twingate Admin Console (https://${TWINGATE_NETWORK}.twingate.com):
   - Confirm the Connector "connector-${FACTORY_NAME}-${FACTORY_LINE}-a"
     shows as ALIVE under Connectors.
   - Add a second Connector "connector-${FACTORY_NAME}-${FACTORY_LINE}-b"
     for failover (see factory_config.yaml -> twingate.connectors[1]).

2. Register the Pi as a Resource:
   - Resources > Add Resource
   - Name:   cnc-mill-01-guardian.factory-a.local
   - Address: factory-guardian:8080   (or the Pi's LAN IP if outside Docker)
   - Remote Network: factory-${FACTORY_NAME}-line-${FACTORY_LINE}

3. Generate an Admin API key (Settings > API > Generate Token) and export
   it so the Python event poller can run:
     export TWINGATE_NETWORK="${TWINGATE_NETWORK}"
     export TWINGATE_API_KEY="<your-api-key>"
   Then test:
     docker exec -e TWINGATE_NETWORK -e TWINGATE_API_KEY factory-guardian \\
       python -m src.twingate.client

4. (Optional) Generate Terraform for the same Resources:
     python3 scripts/generate_terraform.py \\
       --config config/factory_config.yaml \\
       --out    terraform/

=============================================================================
EOF
ok "Done."
