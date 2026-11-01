"""CLI de ingesta. Se ejecuta en GitHub Actions, nunca en Vercel.

    python run.py --job daily|live|hot|closing|calendar|structure|crests [--dry-run] [--max-groups N]
    python run.py --job base_structure|base_daily|base_closing|base_live|base_calendar [--max-requests N]
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from adapters.novanet_rffm import ADAPTER_NAME, MADRID_TZ, RffmTransport
from core.alerts import init_sentry
from core.http import PoliteClient
from pipeline import RffmPipeline, RunStats, run_job

JOBS = (
    "structure",
    "daily",
    "live",
    "hot",
    "closing",
    "calendar",
    "crests",
    "base_structure",
    "base_daily",
    "base_closing",
    "base_live",
    "base_calendar",
)


def exit_code(results: list[tuple[bool, RunStats]]) -> int:
    """0 si ninguna pasada quedó sin trabajo hecho; 1 si alguna no hizo absolutamente nada.

    Degradado (anomalías pero datos/escudos escritos) no rompe el workflow: se alerta por Sentry.
    """
    return 0 if all(ok or s.matches or s.standings or s.crests for ok, s in results) else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True, help=f"uno o varios separados por comas: {', '.join(JOBS)}")
    parser.add_argument("--dry-run", action="store_true", help="BD en memoria; no escribe en Supabase")
    parser.add_argument("--max-groups", type=int, default=0, help="limita grupos (pruebas manuales)")
    parser.add_argument("--max-requests", type=int, default=0, help="tope de peticiones de la ejecución; los jobs de base lo respetan y terminan limpios")
    parser.add_argument("--max-minutes", type=float, default=0, help="tope de duración de la ejecución en minutos (jobs largos de base)")
    parser.add_argument("--loop-seconds", type=int, default=0, help="repite los jobs durante N segundos (directo continuo, sin reinstalar entre pasadas)")
    parser.add_argument("--every", type=int, default=45, help="con --loop-seconds: segundos mínimos entre inicios de pasada")
    parser.add_argument("--idle-passes", type=int, default=3, help="con --loop-seconds: termina tras N pasadas seguidas sin peticiones")
    parser.add_argument("--dump", type=Path, help="con --dry-run: vuelca la BD en memoria a JSON (snapshot de desarrollo web)")
    args = parser.parse_args()
    jobs = args.job.split(",")
    if unknown := set(jobs) - set(JOBS):
        parser.error(f"jobs desconocidos: {unknown}")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if os.environ.get("ADAPTER_DISABLED", "").lower() in ("1", "true", ADAPTER_NAME):
        logging.warning("adaptador %s desactivado por ADAPTER_DISABLED", ADAPTER_NAME)
        return 0

    init_sentry(ADAPTER_NAME)

    if args.dry_run:
        from core.memory_db import MemoryDb

        db = MemoryDb()
    else:
        from core.db import Supabase

        db = Supabase.from_env()

    cache = Path(os.environ.get("SCRAPER_CACHE_DIR", ".cache")) / f"{ADAPTER_NAME}-hashes.json"
    started = datetime.now(MADRID_TZ)
    # La RFFM ha llegado a tardar ~18 s en servir cada página SSR (2026-09-15, noche): 30 s se quedaba corto.
    timeout = float(os.environ.get("SCRAPER_TIMEOUT", "60"))
    with PoliteClient(min_interval=float(os.environ.get("SCRAPER_MIN_INTERVAL", "3")), timeout=timeout, cache_path=cache) as client:
        pipeline = RffmPipeline(db=db, transport=RffmTransport(client), max_requests=args.max_requests, max_seconds=args.max_minutes * 60)
        if args.max_groups:
            original = pipeline.competitions
            pipeline.competitions = lambda *a, **k: original(*a, **k)[: args.max_groups]
        results = []
        deadline = time.monotonic() + args.loop_seconds
        idle = 0
        while True:
            pass_started = time.monotonic()
            started = datetime.now(MADRID_TZ)
            requests_start = client.requests_made
            for job in jobs:
                stats = RunStats(job=job, requests_before=client.requests_made)
                run_job(pipeline, job, stats)
                made = client.requests_made - stats.requests_before
                # En bucle, una pasada sin peticiones (nada en juego) no deja fila en scrape_runs.
                ok = pipeline.finish(stats, client.requests_made, started) if made or not args.loop_seconds else True
                results.append((ok, stats))
                logging.info(
                    "job=%s ok=%s peticiones=%s partidos=%s clasificaciones=%s escudos=%s anomalías=%s",
                    job, ok, made, stats.matches, stats.standings, stats.crests, len(stats.anomalies),
                )
            if not args.loop_seconds:
                break
            idle = 0 if client.requests_made > requests_start else idle + 1
            wait = args.every - (time.monotonic() - pass_started)
            if idle >= args.idle_passes or time.monotonic() + max(wait, 0) >= deadline:
                break
            time.sleep(max(wait, 1))

    if args.dry_run:
        for table in ("competitions", "teams", "matches", "standings", "players"):
            logging.info("dry-run %s: %s filas", table, len(db.tables[table]))
        if args.dump:
            import json

            args.dump.parent.mkdir(parents=True, exist_ok=True)
            args.dump.write_text(json.dumps(db.tables, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            logging.info("snapshot escrito en %s", args.dump)
    # Degradado no rompe el workflow (se alerta por Sentry); solo un fallo total lo hace.
    # Los escudos cuentan como trabajo hecho: 220 de 222 con 2 anomalías es una pasada útil,
    # no un fallo (y si no se subió ninguno, `crests == 0` y sí devuelve 1).
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
