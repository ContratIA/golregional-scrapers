"""Backfill único: calcula clubs.primary_color / secondary_color de los escudos ya subidos.

Solo toca clubs con escudo y sin color primario, así que es idempotente y nunca pisa colores
puestos por el propio club. Los escudos sin color propio (solo blanco/negro/transparente) se
quedan en NULL y la web no les pinta camiseta.

Uso (escribe en Supabase real; necesita SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY):
    cd scrapers
    .venv/Scripts/python backfill_club_colors.py            # aplica
    .venv/Scripts/python backfill_club_colors.py --dry-run  # solo informa
"""

from __future__ import annotations

import argparse
import logging
from collections import Counter

import httpx

from core.colors import dominant_colors
from pipeline import CREST_BUCKET

log = logging.getLogger(__name__)


def backfill(db, dry_run: bool = False) -> Counter:
    stats: Counter = Counter()
    for club in db.select("clubs", primary_color="is.null", select="id,slug,crest_path"):
        if not club.get("crest_path"):
            continue
        stats["candidatos"] += 1
        try:
            colors = dominant_colors(db.download(CREST_BUCKET, club["crest_path"]))
        except httpx.HTTPError as exc:
            log.warning("%s: no se pudo descargar el escudo (%s)", club["slug"], exc)
            stats["error_descarga"] += 1
            continue
        if colors is None:
            stats["sin_color"] += 1
            continue
        if not dry_run:
            db.update("clubs", {"primary_color": colors[0], "secondary_color": colors[1]}, id=f"eq.{club['id']}")
        stats["coloreados"] += 1
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from core.db import Supabase

    db = Supabase.from_env()
    try:
        stats = backfill(db, dry_run=args.dry_run)
    finally:
        db.close()
    log.info("backfill de colores%s: %s", " (dry-run)" if args.dry_run else "", dict(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
