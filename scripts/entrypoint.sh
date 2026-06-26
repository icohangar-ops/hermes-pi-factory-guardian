#!/usr/bin/env bash
# =============================================================================
# entrypoint.sh — Docker CMD wrapper for hermes-pi-factory-guardian
# -----------------------------------------------------------------------------
# Starts the Twingate event poller as a background sidecar process, then execs
# the Hermes agent with the original CMD. If Twingate env vars are missing or
# the poller fails to start, the wrapper logs a warning and continues — Hermes
# still runs, just without Twingate access-layer event ingestion.
#
# This script is the Dockerfile CMD. It is the process that gets PID 1 inside
# the container, so it MUST:
#   - Reap zombies (exec replaces the shell, so this is handled)
#   - Forward signals to children (the trap + kill does this)
#   - Exit cleanly on SIGTERM (so `docker stop` works in <10s)
# =============================================================================

set -euo pipefail

# --- Configuration -----------------------------------------------------------
CONFIG_PATH="${FACTORY_CONFIG_PATH:-/app/config/factory_config.yaml}"
POLL_LOG_PATH="${TWINGATE_POLL_LOG:-/app/data/twingate_poller.log}"
POLL_PIDFILE="/tmp/twingate_poll.pid"

log()  { printf '[entrypoint] %s\n' "$*"; }
warn() { printf '[entrypoint] [WARN] %s\n' "$*" >&2; }

mkdir -p "$(dirname "$POLL_LOG_PATH")" 2>/dev/null || true

# --- Decide whether to start the poller -------------------------------------
# Twingate is opt-in. We start the poller only if ALL of:
#   1. TWINGATE_NETWORK env var is set
#   2. TWINGATE_API_KEY env var is set
#   3. The config file has twingate.alert_routing.twingate_event_source.enabled=true
START_POLLER=0

if [[ -n "${TWINGATE_NETWORK:-}" && -n "${TWINGATE_API_KEY:-}" ]]; then
    if [[ -f "$CONFIG_PATH" ]]; then
        # Check the enabled flag without a full yaml parser — use grep.
        # The config block looks like:
        #   twingate:
        #     alert_routing:
        #       twingate_event_source:
        #         enabled: true
        # We do a conservative substring check; if it's not present we skip.
        # Fast-path: grep within a wide-enough window after the block header
        # so reordered YAML keys / interleaved comments don't fool the check.
        # `from_config()` in src/twingate/client.py is the authoritative parse;
        # this is only an early bail-out so we don't spawn the poller when it
        # would obviously refuse to start.
        if grep -q "twingate_event_source:" "$CONFIG_PATH" 2>/dev/null \
           && grep -A20 "twingate_event_source:" "$CONFIG_PATH" 2>/dev/null \
              | grep -E -q "^\s*enabled:\s*true\b"; then
            START_POLLER=1
        else
            warn "twingate_event_source.enabled is not true in $CONFIG_PATH — skipping poller"
        fi
    else
        warn "Config file $CONFIG_PATH not found — skipping Twingate poller"
    fi
else
    warn "TWINGATE_NETWORK / TWINGATE_API_KEY not set — skipping Twingate poller"
fi

# --- Start the poller --------------------------------------------------------
if [[ $START_POLLER -eq 1 ]]; then
    log "Starting Twingate poller in background (log: $POLL_LOG_PATH)"
    python -m src.twingate --watch --config "$CONFIG_PATH" -v \
        > "$POLL_LOG_PATH" 2>&1 &
    POLL_PID=$!
    echo "$POLL_PID" > "$POLL_PIDFILE"
    log "Twingate poller started (pid=$POLL_PID)"

    # Give it a moment to fail fast (missing deps, bad config, etc.)
    sleep 2
    if ! kill -0 "$POLL_PID" 2>/dev/null; then
        warn "Twingate poller exited immediately — check $POLL_LOG_PATH"
        warn "Continuing without Twingate. Hermes will still run."
    fi
else
    log "Twingate poller not started (see warnings above)"
fi

# --- Signal handling ---------------------------------------------------------
# We must NOT `exec` Hermes — `exec` replaces this shell, dropping every trap
# we register. After `exec`, signals would go straight to Hermes and the
# poller would never get its 5s SIGTERM window to flush state.
#
# Instead: start Hermes as a child, forward SIGTERM/SIGINT to both Hermes and
# the poller, then `wait` so this shell stays PID 1.
HERMES_PID=""

cleanup() {
    if [[ -f "$POLL_PIDFILE" ]]; then
        local pid
        pid=$(cat "$POLL_PIDFILE" 2>/dev/null || true)
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            log "Stopping Twingate poller (pid=$pid)"
            kill -TERM "$pid" 2>/dev/null || true
            # Give it up to 5s to flush state
            for _ in 1 2 3 4 5; do
                kill -0 "$pid" 2>/dev/null || break
                sleep 1
            done
            kill -KILL "$pid" 2>/dev/null || true
        fi
        rm -f "$POLL_PIDFILE"
    fi
}

forward_signal() {
    local sig="$1"
    if [[ -n "$HERMES_PID" ]] && kill -0 "$HERMES_PID" 2>/dev/null; then
        log "Forwarding SIG${sig} to Hermes (pid=$HERMES_PID)"
        kill -"$sig" "$HERMES_PID" 2>/dev/null || true
    fi
    cleanup
}

trap 'forward_signal TERM' TERM
trap 'forward_signal INT'  INT

# --- Run Hermes --------------------------------------------------------------
# Hermes runs as a child of this shell so the traps above stay installed and
# `cleanup` can stop the poller gracefully before the container tears down.
log "Starting Hermes agent: $*"
"$@" &
HERMES_PID=$!

# `wait` is interruptible by the trapped signals; once Hermes exits (either
# normally or via a forwarded SIGTERM), run cleanup one more time and exit
# with Hermes's status so `docker stop` returns the right code.
wait "$HERMES_PID"
HERMES_STATUS=$?
cleanup
exit "$HERMES_STATUS"
