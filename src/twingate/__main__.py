"""
Long-running entrypoint for the Twingate event poller.

Run as:
    python -m src.twingate                 # one-shot poll, then exit
    python -m src.twingate --watch         # loop forever (default interval 30s)
    python -m src.twingate --watch --interval 60

In --watch mode the process:
  1. Loads config/factory_config.yaml
  2. Instantiates an AlertDispatcher (same class the Hermes on_alert.sh hook uses)
  3. Starts TwingateEventSource in a background thread that polls every N seconds
  4. Blocks on SIGINT/SIGTERM, then shuts down cleanly

This is the process that scripts/entrypoint.sh launches as a sidecar
before exec'ing the Hermes agent binary.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Optional


def _load_config(path: str) -> dict:
    import yaml
    with open(path) as f:
        return yaml.safe_load(f)


def _build_dispatcher(factory_config: dict):
    """Instantiate the project's AlertDispatcher with the alert_routing config."""
    from src.alert.dispatcher import AlertDispatcher
    return AlertDispatcher(config=factory_config.get("alert_routing", {}))


def main() -> int:
    p = argparse.ArgumentParser(
        prog="python -m src.twingate",
        description="Twingate event poller for hermes-pi-factory-guardian",
    )
    p.add_argument(
        "--config", default=os.environ.get("FACTORY_CONFIG_PATH", "config/factory_config.yaml"),
        help="Path to factory_config.yaml (default: config/factory_config.yaml)",
    )
    p.add_argument(
        "--watch", action="store_true",
        help="Run in watch mode: poll indefinitely every --interval seconds",
    )
    p.add_argument(
        "--interval", type=int, default=None,
        help="Override poll interval (seconds). Defaults to config value or 30.",
    )
    p.add_argument(
        "--once", action="store_true",
        help="Run a single poll and exit (overrides --watch)",
    )
    p.add_argument(
        "-v", "--verbose", action="count", default=0,
        help="-v=INFO, -vv=DEBUG",
    )
    args = p.parse_args()

    # --- Logging ---
    level = logging.WARNING
    if args.verbose == 1:
        level = logging.INFO
    elif args.verbose >= 2:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("twingate.main")

    # --- Preflight ---
    config_path = Path(args.config)
    if not config_path.is_file():
        log.error("Config file not found: %s", config_path)
        return 2

    # Check required env vars early so we fail fast.
    network = os.environ.get("TWINGATE_NETWORK")
    api_key = os.environ.get("TWINGATE_API_KEY")
    if not network or not api_key:
        log.error(
            "TWINGATE_NETWORK and TWINGATE_API_KEY env vars are required. "
            "Set them in .env (or pass via docker-compose environment:)."
        )
        return 3

    log.info("Loading config from %s", config_path)
    factory_config = _load_config(str(config_path))

    # Check the feature flag in config
    src_cfg = (
        factory_config.get("twingate", {})
        .get("alert_routing", {})
        .get("twingate_event_source", {})
    )
    if not src_cfg.get("enabled", False):
        log.error(
            "Twingate event source is disabled in config "
            "(twingate.alert_routing.twingate_event_source.enabled=false). "
            "Nothing to do."
        )
        return 4

    # --- Build dispatcher + source ---
    try:
        dispatcher = _build_dispatcher(factory_config)
        dispatcher.initialize()
    except Exception as e:
        log.error("Failed to initialize AlertDispatcher: %s", e)
        return 5

    try:
        from src.twingate import TwingateEventSource, emit_to_dispatcher
    except Exception as e:
        log.error("Failed to import src.twingate: %s", e)
        return 6

    try:
        source = TwingateEventSource.from_config(factory_config)
    except Exception as e:
        log.error("Failed to initialize TwingateEventSource: %s", e)
        return 7

    if args.interval:
        source.poll_interval = max(5, args.interval)

    # --- Wire poller -> dispatcher ---
    def _on_event(event_kwargs):
        emit_to_dispatcher(dispatcher, event_kwargs)

    # --- Modes ---
    if args.once or not args.watch:
        log.info("Running one-shot poll")
        events = source.poll_once()
        log.info("Polled: %d event(s)", len(events))
        for e in events:
            _on_event(e)
        return 0

    # Watch mode: run until SIGINT/SIGTERM
    log.info(
        "Starting Twingate poller in watch mode (interval=%ds, network=%s)",
        source.poll_interval, network,
    )
    source.start(_on_event)

    stop = signal.Event() if hasattr(signal, "Event") else None
    # signal.Event() doesn't exist; use a simple flag + signal handler
    _stop_flag = {"stop": False}

    def _handle_signal(signum, _frame):
        log.info("Received signal %d, shutting down", signum)
        _stop_flag["stop"] = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        while not _stop_flag["stop"]:
            time.sleep(1)
    finally:
        log.info("Stopping Twingate poller")
        source.stop(timeout=5.0)
        log.info("Stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
