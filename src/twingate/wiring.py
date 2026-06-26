"""
Drop-in wiring snippet for the project's main entrypoint.

The repo's Dockerfile CMD is:
    hermes --skills ./hermes_skills/ --config ./config/factory_config.yaml

To start the Twingate poller alongside the Hermes agent, add a 3-line
import + call to whatever bootstrap file the project uses (or to the
Hermes `--skills` loader). This file shows the minimal integration.

This file is NOT auto-imported; it's a reference you can copy from.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def start_twingate_event_source(
    factory_config: Dict[str, Any],
    dispatcher,
) -> "TwingateEventSource | None":
    """Start the Twingate poller if it's enabled in config.

    Returns the started source (so the caller can `.stop()` it on shutdown),
    or None if disabled / misconfigured.
    """
    # Mirror the lookup TwingateEventSource.from_config() does: try the
    # nested twingate.alert_routing.* path first, then fall back to the
    # top-level alert_routing.twingate_event_source path that the shipped
    # factory_config.twingate.yaml actually uses. Without this fallback the
    # helper logs "disabled" and skips an enabled poller.
    src_cfg = (
        factory_config.get("twingate", {})
        .get("alert_routing", {})
        .get("twingate_event_source", {})
    )
    if not src_cfg:
        src_cfg = (
            factory_config.get("alert_routing", {})
            .get("twingate_event_source", {})
        )
    if not src_cfg.get("enabled", False):
        logger.info("Twingate event source disabled; skipping")
        return None

    try:
        from src.twingate import TwingateEventSource, emit_to_dispatcher
    except Exception as e:  # pragma: no cover - import guard
        logger.error("Failed to import src.twingate: %s", e)
        return None

    try:
        source = TwingateEventSource.from_config(factory_config)
    except Exception as e:
        logger.error("Twingate event source failed to initialize: %s", e)
        return None

    def _adapter(event_kwargs):
        emit_to_dispatcher(dispatcher, event_kwargs)

    source.start(_adapter)
    return source
