"""
Twingate access-layer event source for hermes-pi-factory-guardian.

Polls the Twingate Admin GraphQL API for network events, device posture
failures, and Connector status, then translates them into the project's
existing AlertEvent shape so they flow through the same AlertDispatcher
as sensor anomalies (see src/alert/dispatcher.py).

Wiring
------
factory_config.twingate.alert_routing.twingate_event_source:
    enabled: true
    poll_interval_seconds: 30
    ingest_network_events: true
    ingest_posture_failures: true
    severity_map:
        access_denied: "warning"
        posture_check_failed: "warning"
        jit_request_denied: "info"
        connector_offline: "critical"

Environment
-----------
TWINGATE_NETWORK            Network slug (e.g. "acme" for acme.twingate.com)
TWINGATE_API_KEY            Admin API key (Settings > API > Generate Token)
TWINGATE_REMOTE_NETWORK_ID  Optional: filter events to one Remote Network

The Admin API is GraphQL at https://<network>.twingate.com/api/graphql/
authed via the X-API-KEY header. See:
    https://www.twingate.com/docs/api-overview
    https://www.twingate.com/docs/network-events
    https://www.twingate.com/docs/audit-logs
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# GraphQL endpoint shape: https://<network>.twingate.com/api/graphql/
GRAPHQL_URL_TEMPLATE = "https://{network}.twingate.com/api/graphql/"

# Twingate network slugs are DNS-label-shaped: lowercase alnum + dashes, no
# leading/trailing dash. Validate this before formatting into the URL so a
# value like `evil.example/path` cannot redirect the API call.
_NETWORK_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


# ---------------------------------------------------------------------------
# Severity mapping
# ---------------------------------------------------------------------------
class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# Default severity per Twingate event type. Overridable via
# alert_routing.twingate_event_source.severity_map.
DEFAULT_SEVERITY_MAP: Dict[str, Severity] = {
    "access_denied":           Severity.WARNING,
    "access_allowed":          Severity.INFO,
    "posture_check_failed":    Severity.WARNING,
    "posture_check_passed":    Severity.INFO,
    "jit_request_denied":      Severity.INFO,
    "jit_request_approved":    Severity.INFO,
    "jit_request_expired":     Severity.WARNING,
    "connector_offline":       Severity.CRITICAL,
    "connector_online":        Severity.INFO,
    "resource_added":          Severity.INFO,
    "resource_removed":        Severity.INFO,
    "policy_changed":          Severity.WARNING,
    "user_blocked":            Severity.CRITICAL,
    "service_account_rotated": Severity.WARNING,
}


# ---------------------------------------------------------------------------
# State persistence (so we don't re-alert after a restart)
# ---------------------------------------------------------------------------
@dataclass
class _PollState:
    cursor_iso: Optional[str] = None
    seen_event_ids: set = field(default_factory=set)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cursor_iso": self.cursor_iso,
            "seen_event_ids": sorted(self.seen_event_ids),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "_PollState":
        return cls(
            cursor_iso=d.get("cursor_iso"),
            seen_event_ids=set(d.get("seen_event_ids", [])),
        )


STATE_PATH = os.environ.get(
    "TWINGATE_STATE_PATH",
    os.path.join("data", "twingate_poll_state.json"),
)


def _load_state() -> _PollState:
    try:
        with open(STATE_PATH) as f:
            return _PollState.from_dict(json.load(f))
    except FileNotFoundError:
        return _PollState()
    except Exception as e:
        logger.warning("Twingate state load failed (%s); starting fresh", e)
        return _PollState()


def _save_state(state: _PollState) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_PATH) or ".", exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state.to_dict(), f, indent=2)
        os.replace(tmp, STATE_PATH)
    except Exception as e:
        logger.error("Twingate state save failed: %s", e)


# ---------------------------------------------------------------------------
# GraphQL client
# ---------------------------------------------------------------------------
class TwingateClient:
    """Thin GraphQL client for the Twingate Admin API.

    Uses urllib from the stdlib so we don't add a hard dependency just for
    one authenticated POST. The project already ships httpx for the
    notifiers; we keep this stdlib-only to minimize import-time blast
    radius on the Pi.
    """

    def __init__(self, network: str, api_key: str, timeout: int = 15):
        if not network or not api_key:
            raise ValueError("TWINGATE_NETWORK and TWINGATE_API_KEY are required")
        network_slug = network.strip().lower()
        if not _NETWORK_SLUG_RE.fullmatch(network_slug):
            raise ValueError(
                f"TWINGATE_NETWORK must be a Twingate network slug "
                f"(e.g. 'acme' for acme.twingate.com), got {network!r}"
            )
        self.url = GRAPHQL_URL_TEMPLATE.format(network=network_slug)
        self.api_key = api_key
        self.timeout = timeout

    def execute(self, query: str, variables: Optional[Dict] = None) -> Dict[str, Any]:
        """Execute a GraphQL query and return the `data` field.

        Raises RuntimeError on transport errors or GraphQL `errors`.
        """
        import urllib.request
        import urllib.error

        payload = json.dumps({"query": query, "variables": variables or {}}).encode()
        req = urllib.request.Request(self.url, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("X-API-KEY", self.api_key)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Twingate API HTTP {e.code}: {e.read()[:200]!r}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(f"Twingate API unreachable: {e}") from e

        if body.get("errors"):
            raise RuntimeError(f"Twingate GraphQL errors: {body['errors']}")
        return body.get("data", {})


# ---------------------------------------------------------------------------
# GraphQL queries
# ---------------------------------------------------------------------------
# Query the activity log via the `activities` root field. The schema is
# documented at https://www.twingate.com/docs/audit-logs and is exposed
# introspectively at the GraphQL endpoint. We request a minimal projection
# so the query is stable across schema versions.
ACTIVITIES_QUERY = """
query Activities($first: Int!, $after: String, $filter: ActivityFilterInput) {
  activities(first: $first, after: $after, filter: $filter) {
    edges {
      node {
        id
        activityType
        occurredAt
        actor { name }
        target { name type }
        details
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

CONNECTORS_QUERY = """
query Connectors {
  connectors(first: 100) {
    edges {
      node {
        id
        name
        state
        remoteNetwork { name }
        updatedAt
      }
    }
  }
}
"""


# ---------------------------------------------------------------------------
# Event classification + translation
# ---------------------------------------------------------------------------
def _parse_dt(s: str) -> datetime:
    """Parse an ISO-8601 timestamp from the API into a UTC datetime."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def _classify(activity_type: str) -> str:
    """Map a raw Twingate activityType string to one of our canonical
    severity_map keys (access_denied, posture_check_failed, etc.)."""
    t = (activity_type or "").lower()
    if "denied" in t or "blocked" in t:
        if "posture" in t:
            return "posture_check_failed"
        if "jit" in t or "access_request" in t:
            return "jit_request_denied"
        if "user" in t:
            return "user_blocked"
        return "access_denied"
    if "posture" in t:
        return "posture_check_passed" if "pass" in t or "allow" in t else "posture_check_failed"
    if "jit" in t or "access_request" in t:
        if "approv" in t:
            return "jit_request_approved"
        if "expire" in t:
            return "jit_request_expired"
        return "jit_request_denied"
    if "connector" in t:
        return "connector_offline" if "offline" in t or "fail" in t else "connector_online"
    if "resource" in t:
        return "resource_removed" if "remov" in t or "delet" in t else "resource_added"
    if "policy" in t:
        return "policy_changed"
    if "service_account" in t and "rotat" in t:
        return "service_account_rotated"
    if "access" in t and "allow" in t:
        return "access_allowed"
    return t or "unknown"


def _severity_for(key: str, override: Dict[str, str]) -> Severity:
    """Resolve a severity for a classified event key, honoring user overrides."""
    if key in override:
        try:
            return Severity(override[key].lower())
        except ValueError:
            logger.warning("Invalid severity override for %s: %s", key, override[key])
    return DEFAULT_SEVERITY_MAP.get(key, Severity.INFO)


def _build_alert_event(activity: Dict[str, Any], severity: Severity) -> Dict[str, Any]:
    """Translate a Twingate activity node into AlertEvent constructor kwargs.

    Returns a dict so callers can either:
        AlertEvent(**kwargs)            # in-process
    or for testing:
        assert kwargs["severity"] == "warning"
    """
    occurred = _parse_dt(activity["occurredAt"])
    actor_name = (activity.get("actor") or {}).get("name", "unknown")
    target = activity.get("target") or {}
    target_name = target.get("name", "unknown")
    target_type = target.get("type", "resource")
    details = activity.get("details") or {}

    # machine_id: prefer the resource/machine name; fall back to "twingate".
    machine_id = target_name if target_type in ("Resource", "resource") else "twingate"

    description = (
        f"Twingate {activity.get('activityType', 'event')}: "
        f"actor={actor_name} target={target_name} ({target_type})"
    )
    if isinstance(details, dict) and details:
        extra = details.get("reason") or details.get("message") or ""
        if extra:
            description += f" — {extra}"

    return {
        "alert_id": f"tg-{activity['id']}",
        "severity": severity.value,           # string; adapter maps to AlertSeverity
        "machine_id": machine_id,
        "description": description,
        "confidence": 1.0,
        "source": "twingate_event_source",
        "sensor_data": {
            "twingate_activity_id": activity["id"],
            "twingate_activity_type": activity.get("activityType"),
            "actor": actor_name,
            "target_name": target_name,
            "target_type": target_type,
            "occurred_at": occurred.isoformat(),
            "details": details if isinstance(details, dict) else {"raw": str(details)},
        },
        "timestamp": occurred,
    }


# ---------------------------------------------------------------------------
# Connector watchdog (synthetic events)
# ---------------------------------------------------------------------------
def _check_connectors(
    client: TwingateClient,
    severity_map_override: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Query connector state and emit synthetic events for any that are
    offline. Fallback for when the activity log doesn't capture a connector
    failure fast enough."""
    try:
        data = client.execute(CONNECTORS_QUERY)
    except Exception as e:
        logger.warning("Connector state query failed: %s", e)
        return []

    out: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for edge in data.get("connectors", {}).get("edges", []):
        node = edge.get("node", {})
        state = (node.get("state") or "").lower()
        if state in ("offline", "error", "disabled"):
            severity = _severity_for("connector_offline", severity_map_override)
            # Stable alert_id per connector — otherwise every poll while the
            # connector is offline emits a new alert and floods the dispatcher.
            # Downstream merge_window dedupe relies on this being constant.
            out.append({
                "alert_id": f"tg-conn-{node['id']}-offline",
                "severity": severity.value,
                "machine_id": "twingate",
                "description": (
                    f"Twingate Connector offline: {node.get('name')} "
                    f"on RemoteNetwork {(node.get('remoteNetwork') or {}).get('name','?')}"
                ),
                "confidence": 1.0,
                "source": "twingate_event_source",
                "sensor_data": {
                    "connector_id": node["id"],
                    "connector_name": node.get("name"),
                    "state": state,
                    "remote_network": (node.get("remoteNetwork") or {}).get("name"),
                    "updated_at": node.get("updatedAt"),
                },
                "timestamp": now,
            })
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
class TwingateEventSource:
    """Polls Twingate for access-layer events and emits AlertEvent-shaped
    dicts to a callback (typically AlertDispatcher.dispatch).

    Usage:
        source = TwingateEventSource.from_config(config)
        source.start(dispatcher.dispatch)  # background thread
    """

    def __init__(
        self,
        client: TwingateClient,
        poll_interval: int = 30,
        ingest_network_events: bool = True,
        ingest_posture_failures: bool = True,
        severity_map_override: Optional[Dict[str, str]] = None,
        remote_network_id: Optional[str] = None,
    ):
        self.client = client
        self.poll_interval = max(5, int(poll_interval))
        self.ingest_network_events = ingest_network_events
        self.ingest_posture_failures = ingest_posture_failures
        self.severity_map_override = severity_map_override or {}
        self.remote_network_id = remote_network_id
        self._state = _load_state()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @classmethod
    def from_config(cls, factory_config: Dict[str, Any]) -> "TwingateEventSource":
        tg = factory_config.get("twingate", {})
        src = tg.get("alert_routing", {}).get("twingate_event_source", {})
        # Also accept the top-level placement we used in factory_config.twingate.yaml
        if not src:
            src = factory_config.get("alert_routing", {}).get(
                "twingate_event_source", {}
            )
        if not src.get("enabled", False):
            raise RuntimeError(
                "Twingate event source is disabled in config "
                "(alert_routing.twingate_event_source.enabled=false)"
            )

        network = os.environ.get("TWINGATE_NETWORK") or tg.get("network")
        api_key = os.environ.get("TWINGATE_API_KEY")
        if not network:
            raise RuntimeError("TWINGATE_NETWORK env var (or twingate.network) is required")
        if not api_key:
            raise RuntimeError("TWINGATE_API_KEY env var is required")

        client = TwingateClient(network=network, api_key=api_key)
        return cls(
            client=client,
            poll_interval=src.get("poll_interval_seconds", 30),
            ingest_network_events=src.get("ingest_network_events", True),
            ingest_posture_failures=src.get("ingest_posture_failures", True),
            severity_map_override=src.get("severity_map", {}),
            remote_network_id=os.environ.get("TWINGATE_REMOTE_NETWORK_ID"),
        )

    # --- background loop ----------------------------------------------------
    def start(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """Start the poller in a background daemon thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("Twingate event source already running")
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(callback,), name="twingate-poller", daemon=True,
        )
        self._thread.start()
        logger.info("Twingate event source started (poll=%ds)", self.poll_interval)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _run(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        while not self._stop.is_set():
            try:
                for event in self.poll_once():
                    eid = (event.get("sensor_data") or {}).get(
                        "twingate_activity_id"
                    )
                    occurred = (event.get("sensor_data") or {}).get(
                        "occurred_at"
                    )
                    try:
                        callback(event)
                    except Exception as e:
                        # Do NOT mark this event as seen — leave it retryable
                        # on the next poll cycle.
                        logger.error(
                            "Callback failed for Twingate event %s: %s",
                            event.get("alert_id"), e,
                        )
                        continue
                    # Acknowledge only on successful dispatch.
                    if eid:
                        self._state.seen_event_ids.add(eid)
                    if occurred:
                        self._state.cursor_iso = occurred
                # Cap the seen set so it doesn't grow unboundedly across months.
                if len(self._state.seen_event_ids) > 10_000:
                    self._state.seen_event_ids = set(
                        list(self._state.seen_event_ids)[-5_000:]
                    )
                _save_state(self._state)
            except Exception as e:
                logger.error("Twingate poll cycle failed: %s", e)
            self._stop.wait(self.poll_interval)

    # --- one-shot poll ------------------------------------------------------
    def poll_once(self) -> List[Dict[str, Any]]:
        """Query Twingate for new events since the cursor; return a list of
        AlertEvent-shaped dicts. Caller is responsible for invoking the
        AlertDispatcher (the dict shape matches AlertEvent constructor kwargs
        except `severity` is a string — the dispatcher adapter maps it)."""
        events: List[Dict[str, Any]] = []

        # 1) Activity log
        if self.ingest_network_events or self.ingest_posture_failures:
            events.extend(self._poll_activities())

        # 2) Connector state watchdog
        events.extend(_check_connectors(self.client, self.severity_map_override))

        return events

    def _poll_activities(self) -> List[Dict[str, Any]]:
        # Page through the activity log until there are no more new pages.
        # Without this, > 50 events between polls silently drop on the floor.
        out: List[Dict[str, Any]] = []
        after: Optional[str] = None
        total_seen = 0
        # Bound the per-cycle work so a single bad polling window can't pin
        # the thread forever. 20 pages * 50 = 1000 events / cycle is plenty.
        max_pages = 20

        for _ in range(max_pages):
            variables: Dict[str, Any] = {"first": 50, "after": after}
            if self.remote_network_id:
                variables["filter"] = {"remoteNetworkId": self.remote_network_id}

            try:
                data = self.client.execute(ACTIVITIES_QUERY, variables)
            except Exception as e:
                logger.warning("Activities query failed: %s", e)
                break

            activity_data = data.get("activities", {})
            page_info = activity_data.get("pageInfo", {})
            edges = activity_data.get("edges", [])
            total_seen += len(edges)

            for edge in edges:
                node = edge.get("node") or {}
                eid = node.get("id")
                if not eid or eid in self._state.seen_event_ids:
                    continue

                key = _classify(node.get("activityType", ""))
                # Honor ingest flags
                if key.startswith("posture") and not self.ingest_posture_failures:
                    continue
                if not key.startswith("posture") and not self.ingest_network_events:
                    continue

                severity = _severity_for(key, self.severity_map_override)
                out.append(_build_alert_event(node, severity))
                # NOTE: ack happens in _run() AFTER successful dispatch.

            if not page_info.get("hasNextPage"):
                break
            next_cursor = page_info.get("endCursor")
            if not next_cursor or next_cursor == after:
                # Defensive: server says hasNextPage but didn't advance.
                break
            after = next_cursor

        logger.info(
            "Twingate poll: %d new events across %d activity edges",
            len(out), total_seen,
        )
        return out


# ---------------------------------------------------------------------------
# Dispatcher adapter
# ---------------------------------------------------------------------------
def emit_to_dispatcher(dispatcher, event_kwargs: Dict[str, Any]) -> None:
    """Adapt the dict shape produced by TwingateEventSource into the actual
    AlertEvent dataclass from src/alert/dispatcher.py and dispatch it.

    Kept as a separate function so the import of dispatcher types is lazy —
    this module can be imported on a dev box without gpiozero installed.
    """
    from src.alert.dispatcher import AlertEvent, AlertSeverity

    sev_str = event_kwargs.pop("severity")
    try:
        severity = AlertSeverity(sev_str)
    except ValueError:
        # Map our 3-level enum onto the dispatcher's 4-level enum.
        fallback = {
            "info":     AlertSeverity.LOW,
            "warning":  AlertSeverity.MEDIUM,
            "critical": AlertSeverity.CRITICAL,
        }
        severity = fallback.get(sev_str, AlertSeverity.LOW)

    # AlertEvent uses utcnow() by default; if we pass timestamp explicitly
    # we have to make sure tz-aware datetimes don't trip it.
    ts = event_kwargs.get("timestamp")
    if ts and ts.tzinfo is not None:
        event_kwargs["timestamp"] = ts.replace(tzinfo=None)

    event = AlertEvent(severity=severity, **event_kwargs)
    dispatcher.dispatch(event)


# ---------------------------------------------------------------------------
# CLI / self-test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    network = os.environ.get("TWINGATE_NETWORK")
    api_key = os.environ.get("TWINGATE_API_KEY")
    if not network or not api_key:
        print("Set TWINGATE_NETWORK and TWINGATE_API_KEY to run a self-test poll.")
        raise SystemExit(0)

    client = TwingateClient(network=network, api_key=api_key)
    source = TwingateEventSource(client=client, poll_interval=30)

    print(f"Polling Twingate network {network!r} ...")
    events = source.poll_once()
    if not events:
        print("No new events.")
    for e in events:
        print(json.dumps(e, indent=2, default=str))
