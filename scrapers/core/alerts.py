"""Detección de roturas del scraper y aviso a Sentry."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

VOLUME_DROP_RATIO = 0.5


def init_sentry(adapter: str) -> None:
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        log.info("SENTRY_DSN no definido: alertas solo en log")
        return
    import sentry_sdk

    sentry_sdk.init(dsn=dsn, environment=os.environ.get("SENTRY_ENVIRONMENT", "scrapers"), traces_sample_rate=0)
    sentry_sdk.set_tag("adapter", adapter)


@dataclass
class Anomaly:
    kind: str  # 'selector_empty' | 'volume_drop' | 'validation' | 'http' | 'robots' | 'not_an_image'
    message: str
    context: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"kind": self.kind, "message": self.message, "context": self.context}


def volume_drop(current: int, previous: int | None, context: dict) -> Anomaly | None:
    """Anomalía si el volumen cae más de un 50% frente a la jornada/pasada anterior."""
    if not previous or current >= previous * VOLUME_DROP_RATIO:
        return None
    return Anomaly("volume_drop", f"volumen {current} < 50% de {previous}", context)


def report(anomaly: Anomaly) -> None:
    log.error("[%s] %s %s", anomaly.kind, anomaly.message, anomaly.context)
    if not os.environ.get("SENTRY_DSN"):
        return
    import sentry_sdk

    with sentry_sdk.new_scope() as scope:
        scope.set_tag("anomaly", anomaly.kind)
        scope.set_context("anomaly", anomaly.context)
        sentry_sdk.capture_message(anomaly.message, level="error")
