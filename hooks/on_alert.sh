#!/usr/bin/env bash
# =============================================================================
# Hermes-Pi Factory Guardian — On-Alert Hook Script
# =============================================================================
# Triggered by Hermes Agent when an anomaly is detected.
# This script:
#   1. Captures a camera frame (if available)
#   2. Runs an immediate sensor check
#   3. Sends the alert through the alert router
#   4. Logs the event for the learning loop
#
# Usage: Called automatically by Hermes Agent hook system
#   hooks/on_alert.sh <alert_json>
#
# Environment:
#   HERMES_GUARDIAN_DIR - Project root directory
# =============================================================================

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────
PROJECT_DIR="${HERMES_GUARDIAN_DIR:-/opt/hermes-pi-factory-guardian}"
VENV_PYTHON="${PROJECT_DIR}/venv/bin/python3"
LOG_FILE="${PROJECT_DIR}/logs/hook_alerts.log"
DATA_DIR="${PROJECT_DIR}/data"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# ── Logging ────────────────────────────────────────────────────────────
mkdir -p "$(dirname "$LOG_FILE")"

log_hook() {
    echo "[$(date -Iseconds)] [ALERT_HOOK] $*" >> "$LOG_FILE"
}

# ── Parse input ───────────────────────────────────────────────────────
ALERT_JSON="${1:-}"

if [[ -z "$ALERT_JSON" ]]; then
    log_hook "ERROR: No alert JSON provided as argument"
    exit 1
fi

log_hook "Alert received: ${ALERT_JSON:0:200}..."

# Extract alert fields
SEVERITY=$(echo "$ALERT_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('severity','INFO'))" 2>/dev/null || echo "INFO")
MACHINE_ID=$(echo "$ALERT_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('machine_id','unknown'))" 2>/dev/null || echo "unknown")
ANOMALY_TYPE=$(echo "$ALERT_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('anomaly_type','unknown'))" 2>/dev/null || echo "unknown")

log_hook "Parsed: severity=${SEVERITY}, machine=${MACHINE_ID}, type=${ANOMALY_TYPE}"

# ── Step 1: Capture camera frame ─────────────────────────────────────
CAPTURE_PATH=""
if [[ -d "${DATA_DIR}/anomalies" ]]; then
    CAPTURE_PATH="${DATA_DIR}/anomalies/hook_capture_${TIMESTAMP}.jpg"
    log_hook "Attempting camera capture to: ${CAPTURE_PATH}"

    # Use libcamera for Pi Camera, or fswebcam for USB
    if command -v libcamera-jpeg &>/dev/null; then
        libcamera-jpeg -o "$CAPTURE_PATH" --width 1280 --height 720 --timeout 1 2>/dev/null && \
            log_hook "Camera capture successful (libcamera)" || \
            log_hook "Camera capture failed (libcamera)"
    elif command -v fswebcam &>/dev/null; then
        fswebcam -r 1280x720 --no-banner "$CAPTURE_PATH" 2>/dev/null && \
            log_hook "Camera capture successful (fswebcam)" || \
            log_hook "Camera capture failed (fswebcam)"
    else
        log_hook "No camera tool available (install libcamera-still or fswebcam)"
    fi
fi

# ── Step 2: Run immediate sensor check ────────────────────────────────
log_hook "Running immediate sensor check..."
if [[ -x "$VENV_PYTHON" ]]; then
    SENSOR_READINGS=$("$VENV_PYTHON" -c "
import sys
sys.path.insert(0, '${PROJECT_DIR}')
try:
    from skills.sensor_polling.sensor_poller import SensorPoller
    poller = SensorPoller(mock_mode=True)
    readings = poller.poll_all()
    for r in readings:
        print(f'{r.machine_id}:{r.sensor_type}={r.value:.2f}')
except Exception as e:
    print(f'ERROR: {e}')
" 2>&1) || SENSOR_READINGS="Sensor check failed"
    log_hook "Sensor readings: ${SENSOR_READINGS}"
else
    log_hook "Python not available at ${VENV_PYTHON}"
fi

# ── Step 3: Route alert ──────────────────────────────────────────────
log_hook "Routing alert (severity=${SEVERITY})..."
if [[ -x "$VENV_PYTHON" ]]; then
    ROUTE_RESULT=$("$VENV_PYTHON" -c "
import sys, json
sys.path.insert(0, '${PROJECT_DIR}')
try:
    from skills.alert_router.alert_router import AlertRouter, Alert, Severity
    router = AlertRouter()
    severity = Severity('${SEVERITY}')
    alert = Alert(
        alert_id='hook_${TIMESTAMP}',
        severity=severity,
        machine_id='${MACHINE_ID}',
        anomaly_type='${ANOMALY_TYPE}',
        anomaly_score=0.8,
        value=0.0,
        baseline_mean=0.0,
        baseline_std=0.0,
        description='Alert triggered by Hermes hook: ${ANOMALY_TYPE}',
        image_path='${CAPTURE_PATH}' if '${CAPTURE_PATH}' else None,
    )
    result = router.route_alert(alert)
    print(f'channels={[c.value for c in result.channels]}, dedup={result.deduplicated}')
except Exception as e:
    print(f'ERROR: {e}')
" 2>&1) || ROUTE_RESULT="Routing failed"
    log_hook "Route result: ${ROUTE_RESULT}"
fi

# ── Step 4: Log event for learning loop ──────────────────────────────
log_hook "Logging event for learning loop..."
if [[ -x "$VENV_PYTHON" ]]; then
    "$VENV_PYTHON" -c "
import sys
sys.path.insert(0, '${PROJECT_DIR}')
try:
    from skills.learning_loop.learning_manager import LearningManager
    lm = LearningManager()
    lm.record_event(
        event_type='hook_alert',
        machine_id='${MACHINE_ID}',
        data={
            'severity': '${SEVERITY}',
            'anomaly_type': '${ANOMALY_TYPE}',
            'sensor_readings': '''${SENSOR_READINGS}''',
            'image_captured': bool('${CAPTURE_PATH}'),
        },
        tags=['hermes_hook', 'alert', '${SEVERITY}'],
    )
except Exception as e:
    print(f'Learning log error: {e}')
" 2>&1 | while read -r line; do log_hook "$line"; done
fi

# ── Done ──────────────────────────────────────────────────────────────
log_hook "Alert hook completed: severity=${SEVERITY}, machine=${MACHINE_ID}"
echo "Alert processed: ${SEVERITY} — ${MACHINE_ID} — ${ANOMALY_TYPE}"
