"""Backfill dirigido: completa las jornadas que faltan del calendario sin re-fetchear
las ya cargadas. Útil cuando el job `calendar` se ha interrumpido (p. ej. por un
timeout de GitHub Actions) y solo quedan huecos puntuales por grupo.

Uso (igual que run.py, escribe en Supabase real):
    cd scrapers
    .venv/Scripts/python backfill_missing.py
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from adapters.novanet_rffm import MADRID_TZ, RffmTransport
from core.db import Supabase
from core.http import PoliteClient
from pipeline import RffmPipeline, RunStats

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

MIN_INTERVAL = float(os.environ.get("SCRAPER_MIN_INTERVAL", "2"))


def main() -> int:
    db = Supabase.from_env()
    cache = Path(os.environ.get("SCRAPER_CACHE_DIR", ".cache")) / "novanet_rffm-hashes.json"
    client = PoliteClient(min_interval=MIN_INTERVAL, timeout=60, cache_path=cache)
    pipeline = RffmPipeline(db=db, transport=RffmTransport(client))

    pipeline.sync_season()
    pipeline.sync_competitions()

    stats = RunStats(job="backfill_missing", requests_before=client.requests_made)
    total_missing = 0
    for competition in pipeline.competitions(only_active=False):
        total = competition.get("total_rounds") or 0
        loaded = db.select("matches", competition_id=f"eq.{competition['id']}", select="round")
        loaded_rounds = {m["round"] for m in loaded}
        missing = [r for r in range(1, total + 1) if r not in loaded_rounds]
        if not missing:
            continue
        total_missing += len(missing)
        logging.info(
            "comp %s (%s %s): faltan %s jornadas -> %s",
            competition["id"], competition["category_slug"], competition["group_slug"], len(missing), missing,
        )
        for r in missing:
            with pipeline._capture(stats, competition_id=competition["id"], round=r):
                pipeline.sync_round(competition, r, stats)

    ok = pipeline.finish(stats, client.requests_made, datetime.now(MADRID_TZ))
    client.close()
    logging.info(
        "DONE: %s jornadas pendientes, %s partidos escritos, %s anomalías",
        total_missing, stats.matches, len(stats.anomalies),
    )
    return 0 if not stats.anomalies else 1


if __name__ == "__main__":
    raise SystemExit(main())
