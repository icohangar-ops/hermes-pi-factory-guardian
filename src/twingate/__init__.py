"""Twingate integration package for hermes-pi-factory-guardian."""
from .client import (
    DEFAULT_SEVERITY_MAP,
    Severity,
    TwingateClient,
    TwingateEventSource,
    emit_to_dispatcher,
)
from .shift_report import build_twingate_summary

__all__ = [
    "DEFAULT_SEVERITY_MAP",
    "Severity",
    "TwingateClient",
    "TwingateEventSource",
    "build_twingate_summary",
    "emit_to_dispatcher",
]
