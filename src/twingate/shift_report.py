"""
Shift-report integration: pulls Twingate access-layer events for the just-
ended shift and produces a structured summary the shift_report skill can
embed in its handover.

Returns a dict shaped like:
    {
        "access_log_top_10": [ {actor, target, activity_type, occurred_at}, ... ],
        "posture_failures":  [ {...}, ... ],
        "connector_uptime":  {connector_name: "99.2%", ...},
        "off_hours_access_attempts": [ {...}, ... ],
    }
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - python < 3.9 fallback
    ZoneInfo = None  # type: ignore[assignment]

from .client import TwingateClient, _parse_dt

logger = logging.getLogger(__name__)


def build_twingate_summary(
    client: TwingateClient,
    shift_start: datetime,
    shift_end: datetime,
    factory_config: Dict[str, Any],
    top_n: int = 10,
) -> Dict[str, Any]:
    """Query Twingate for everything that happened during this shift and
    bucket it into the four sections declared in
    `shift_report.twingate_summary.sections`.
    """
    sections_cfg = (
        factory_config.get("shift_report", {})
        .get("twingate_summary", {})
        .get("sections", ["access_log_top_10"])
    )

    # Pull a wide window (last 24h) and filter in-process so a single query
    # covers all three shift windows the daily summary might run for.
    since = (shift_end - timedelta(hours=24)).isoformat()
    query = """
    query Activities($first: Int!, $filter: ActivityFilterInput) {
      activities(first: $first, filter: $filter) {
        edges {
          node {
            id activityType occurredAt
            actor { name }
            target { name type }
            details
          }
        }
      }
    }
    """
    try:
        data = client.execute(query, {"first": 500, "filter": {"since": since}})
    except Exception as e:
        logger.warning("Shift-report Twingate query failed: %s", e)
        return {"error": str(e)}

    edges = data.get("activities", {}).get("edges", [])
    in_window: List[Dict[str, Any]] = []
    for edge in edges:
        node = edge.get("node") or {}
        try:
            ts = _parse_dt(node["occurredAt"])
        except Exception:
            continue
        if shift_start <= ts <= shift_end:
            in_window.append(node)

    out: Dict[str, Any] = {}
    if "access_log_top_10" in sections_cfg:
        out["access_log_top_10"] = [
            {
                "actor": (n.get("actor") or {}).get("name", "?"),
                "target": (n.get("target") or {}).get("name", "?"),
                "activity_type": n.get("activityType"),
                "occurred_at": n.get("occurredAt"),
            }
            for n in in_window[:top_n]
        ]

    if "posture_failures" in sections_cfg:
        out["posture_failures"] = [
            n for n in in_window
            if "posture" in (n.get("activityType") or "").lower()
            and ("fail" in (n.get("activityType") or "").lower()
                 or "denied" in (n.get("activityType") or "").lower())
        ]

    if "connector_uptime" in sections_cfg:
        # Connector uptime = 100% - (minutes_offline / shift_minutes)
        # We approximate by counting connector_offline events per connector.
        conn_off = {}
        for n in in_window:
            t = (n.get("activityType") or "").lower()
            if "connector" in t and ("offline" in t or "fail" in t):
                name = (n.get("target") or {}).get("name", "unknown")
                conn_off[name] = conn_off.get(name, 0) + 1
        shift_minutes = max(1, int((shift_end - shift_start).total_seconds() // 60))
        out["connector_uptime"] = {
            name: f"{max(0.0, 100.0 - (count * 5 / shift_minutes * 100)):.1f}%"
            for name, count in conn_off.items()
        }
        # Connectors with no offline events in window:
        if not conn_off:
            out["connector_uptime"] = {"all_connectors": "100.0%"}

    if "off_hours_access_attempts" in sections_cfg:
        # "Off hours" = access attempts outside the configured jit_access.windows
        jit = factory_config.get("twingate", {}).get("jit_access", {}).get("windows", {})
        out["off_hours_access_attempts"] = [
            n for n in in_window
            if "access" in (n.get("activityType") or "").lower()
            and not _in_any_jit_window(n.get("occurredAt"), jit,
                                       factory_config.get("twingate", {})
                                       .get("jit_access", {})
                                       .get("timezone", "UTC"))
        ]

    return out


def _in_any_jit_window(iso_ts: Optional[str], windows: Dict[str, str], tz: str) -> bool:
    """Return True if the timestamp falls inside any of the configured
    shift windows (e.g. {"day_shift": "06:00-14:00", ...}).

    The `tz` argument is the configured shift timezone (IANA name, e.g.
    "America/New_York"). Without converting, a 06:00-14:00 New York window
    would match 06:00 UTC = 02:00 NY — silently misclassifying every event
    on non-UTC sites.
    """
    if not iso_ts or not windows:
        return False
    try:
        ts = _parse_dt(iso_ts)
        if tz and tz.upper() != "UTC" and ZoneInfo is not None:
            try:
                ts = ts.astimezone(ZoneInfo(tz))
            except Exception:
                # Fall back to UTC if the tz string is unknown; logged once.
                logger.warning("Unknown JIT timezone %r; comparing in UTC", tz)
        hhmm = ts.strftime("%H:%M")
        for window in windows.values():
            if "-" in window:
                start, end = window.split("-")
                if start <= hhmm < end:
                    return True
                # wrap-around midnight (e.g. "22:00-06:00")
                if start > end and (hhmm >= start or hhmm < end):
                    return True
    except Exception:
        return False
    return False
