# Twingate Integration — hermes-pi-factory-guardian

This branch adds a Twingate zero-trust access layer to the Factory Guardian
project. It is opt-in: the base `docker-compose.yml` and `config/factory_config.yaml`
are unchanged, and the Twingate pieces are added as separate files you can
ignore if you don't need them.

## What's in this branch

```
docker-compose.twingate.yml          # Overlay: adds twingate-connector service
config/factory_config.twingate.yaml  # Extended schema with twingate: blocks per machine/camera
.env.twingate.example                # Env vars the overlay references
src/twingate/                        # Python package: poller + dispatcher adapter
  __init__.py
  client.py                          # GraphQL client + TwingateEventSource
  shift_report.py                    # build_twingate_summary() for the shift handover
  wiring.py                          # Drop-in start helper for the app entrypoint
scripts/
  generate_terraform.py              # YAML config -> twingate_* Terraform resources
  pi_quickstart.sh                   # End-to-end deploy runbook for a Pi
  validate_overlay.py                # YAML + schema + compose-merge self-test
terraform/                           # Generated Terraform (run generate_terraform.py)
  main.tf
  variables.tf
  terraform.tfvars.example
docs/TWINGATE_INTEGRATION.md         # This file
```

## Quick start on a Raspberry Pi

```bash
bash scripts/pi_quickstart.sh
```

The script will:
1. Clone this repo to `/home/pi/hermes-pi-factory-guardian`
2. Drop in the three overlay files (`docker-compose.twingate.yml`,
   `config/factory_config.twingate.yaml`, `.env.twingate.example`)
3. Prompt you for `TWINGATE_NETWORK`, `TWINGATE_ACCESS_TOKEN`,
   `TWINGATE_REFRESH_TOKEN`, `FACTORY_NAME`, `FACTORY_LINE`
4. Validate the merged compose config
5. Run `docker compose -f docker-compose.yml -f docker-compose.twingate.yml up -d`
6. Verify the Connector registered with Twingate

## What the integration does

### 1. Network layer (docker-compose.twingate.yml)

Adds a `twingate-connector` service that:
- Joins the existing `factory-guardian` service on a new `ot_net` bridge
  network so the Connector can reach the guardian's dashboard by service
  name (`factory-guardian:8080`) without exposing port 8080 on the host LAN.
- Makes **outbound-only TLS** connections to the Twingate Controller — no
  inbound firewall holes, no port forwarding on the OT segment.
- Includes the `sysctls: net.ipv4.ping_group_range=0 2147483647` block
  per Twingate docs so the Connector can issue ICMP health checks.
- Has **no `ports:` block** (correct — Connectors are never directly
  reachable from outside).
- Ships an optional commented-out `twingate-client-headless` service for
  the Pi's own outbound sync (Service Account auth) — implements the
  README's "syncs skills and reports when connected" line as identity-bound
  traffic.

### 2. Schema layer (config/factory_config.twingate.yaml)

Adds a `twingate:` sub-block on every `machines[]` and `cameras[]` entry:

```yaml
machines:
  - id: "cnc_mill_01"
    # ... existing fields unchanged ...
    twingate:
      resource_name: "cnc-mill-01-guardian.factory-a.local"
      address: "factory-guardian:8080"
      protocol: "http"
      port: 8080
      tags:
        factory: "factory-a"
        line: "line-1"
        machine-id: "cnc_mill_01"
        machine-type: "cnc_mill"
        classification: "restricted"
      policy: "policy-machine-restricted"
      jit_access:
        allowed_groups: ["shift_leads", "maintenance"]
        allow_contractors: false
        windows: ["day_shift", "swing_shift"]
        max_session_minutes: 240
        require_mfa: false
      sub_resources:
        - name: "cnc-mill-01-gpio-write"
          path: "/api/gpio/write"
          policy: "policy-machine-gpio-write"
          require_mfa: true
```

Plus a top-level `twingate:` block for:
- `remote_network` — the Twingate Remote Network name for this site
- `connectors` — failover pair (Twingate recommends >= 2 per RN)
- `identity_provider` — Okta/Entra ID/Google Workspace/JumpCloud/Keycloak/OneLogin + SCIM groups
- `device_posture_checks` — CrowdStrike EDR, OS patch age, disk encryption, geo
- `jit_access` — windows that mirror `factory.shift_schedule`, with auto-lock
- `audit` — S3 export of Audit Logs + Network Events (365-day retention)
- `egress_allowlist` — pins the Pi's egress to Telegram/Slack/SMTP/Hermes/S3

### 3. Event ingestion (src/twingate/)

A background poller that calls the Twingate Admin GraphQL API every 30s for:
- Network events (access_allowed, access_denied, jit_request_*)
- Device posture check failures
- Connector state (synthetic offline events as a watchdog fallback)

Each Twingate event is translated into the project's existing `AlertEvent`
shape and dispatched through the same `AlertDispatcher` as sensor anomalies,
so a "bearing wear detected" alert and a "contractor denied access to
press_brake_01 outside JIT window" alert flow through the same
Telegram/Slack/email/buzzer channels.

Wiring (in the app entrypoint):
```python
from src.twingate import TwingateEventSource, emit_to_dispatcher
source = TwingateEventSource.from_config(factory_config)
source.start(lambda e: emit_to_dispatcher(dispatcher, e))
```

### 4. Shift report integration (src/twingate/shift_report.py)

The daily shift handover now includes a "Twingate access summary" section:
- Top 10 access events during the shift
- Posture failures during the shift
- Connector uptime % per connector
- Off-hours access attempts (events outside `jit_access.windows`)

### 5. Terraform generator (scripts/generate_terraform.py)

Reads `config/factory_config.twingate.yaml` and emits the full Twingate
infrastructure as code:

```bash
FACTORY_NAME=factory-a FACTORY_LINE=line-1 \
  python3 scripts/generate_terraform.py \
    --config config/factory_config.twingate.yaml \
    --out terraform/

cd terraform
cp terraform.tfvars.example terraform.tfvars  # fill in real values
terraform init
terraform plan
terraform apply
```

Generates: 1 `twingate_remote_network`, 2 `twingate_connector` (failover),
4 `twingate_group` (one per IdP SCIM role), 6 `twingate_resource` (4
machines + 2 cameras), 5 `twingate_security_policy` (one per distinct
policy name referenced in the config).

## Twingate docs referenced

- Infrastructure Access use case: https://www.twingate.com/docs/infra-access-use-case
- How Twingate Works:               https://www.twingate.com/docs/how-twingate-works
- Connectors (best practices):      https://www.twingate.com/docs/connector-best-practices
- Deploy with Docker Compose:       https://www.twingate.com/docs/deploy-connector-with-docker-compose
- Linux Headless Mode:              https://www.twingate.com/docs/linux-headless-mode
- JIT Access Requests:              https://www.twingate.com/docs/jit-access-requests
- Device Posture Checks:            https://www.twingate.com/docs/device-posture-checks
- Resource Policies:                https://www.twingate.com/docs/resource-policies
- Audit Logs:                       https://www.twingate.com/docs/audit-logs
- Network Events:                   https://www.twingate.com/docs/network-events
- Admin API (GraphQL):              https://www.twingate.com/docs/api-overview
- Terraform provider:               https://registry.terraform.io/providers/Twingate/twingate/latest/docs

## Validation

```bash
python3 scripts/validate_overlay.py
```

Checks:
1. All three YAML/env files parse
2. Every machine has `twingate.resource_name`, `twingate.policy`, and `tags.machine-id`.
   Every camera has `twingate.resource_name` (cameras do not carry `machine-id`).
3. The compose overlay merges cleanly with the base `docker-compose.yml`
   (factory-guardian joins `ot_net`, Connector has no `ports:` block, sysctls present)

## Security notes

- The Twingate Connector's `TWINGATE_ACCESS_TOKEN` and `TWINGATE_REFRESH_TOKEN`
  are one-time-use at creation. Rotate via the API if expired.
- The Admin API key (`TWINGATE_API_KEY`, used by the Python poller) is a
  long-lived credential. Store it in a secrets manager, not in `.env`
  committed to the repo.
- The `egress_allowlist` is enforced at the Twingate Internet Security
  layer (DNS Filtering + Exit Networks). It is not a host-level firewall
  on the Pi — pair it with `iptables`/`nftables` rules if you need
  defense-in-depth at the host layer too.
