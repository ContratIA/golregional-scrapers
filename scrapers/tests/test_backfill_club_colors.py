from pathlib import Path

import httpx

from backfill_club_colors import backfill
from core.memory_db import MemoryDb

CRESTS = Path(__file__).parent / "fixtures" / "crests"


def crest(name: str) -> bytes:
    return (CRESTS / f"{name}.webp").read_bytes()


def db_with_clubs():
    db = MemoryDb()
    db.ensure_bucket("crests")
    for slug in ("azul-y-rojo", "amarillo-y-verde", "blanco-y-negro"):
        db.upload("crests", f"{slug}.webp", crest(slug), "image/webp")
    db.tables["clubs"] = [
        {"id": 1, "slug": "azul-y-rojo", "crest_path": "azul-y-rojo.webp", "primary_color": None, "secondary_color": None},
        {"id": 2, "slug": "amarillo-y-verde", "crest_path": "amarillo-y-verde.webp", "primary_color": None, "secondary_color": None},
        {"id": 3, "slug": "blanco-y-negro", "crest_path": "blanco-y-negro.webp", "primary_color": None, "secondary_color": None},
        {"id": 4, "slug": "sin-escudo", "crest_path": None, "primary_color": None, "secondary_color": None},
        {"id": 5, "slug": "ya-coloreado", "crest_path": "azul-y-rojo.webp", "primary_color": "#010203", "secondary_color": "#040506"},
    ]
    return db


def test_backfill_colorea_solo_clubs_con_escudo_y_sin_color():
    db = db_with_clubs()
    stats = backfill(db)
    by_slug = {c["slug"]: c for c in db.tables["clubs"]}
    assert (by_slug["azul-y-rojo"]["primary_color"], by_slug["azul-y-rojo"]["secondary_color"]) == ("#302d54", "#b51a16")
    assert (by_slug["amarillo-y-verde"]["primary_color"], by_slug["amarillo-y-verde"]["secondary_color"]) == ("#d0b530", "#0a730a")
    assert by_slug["blanco-y-negro"]["primary_color"] is None  # blanco y negro: sin color propio
    assert by_slug["sin-escudo"]["primary_color"] is None
    assert (by_slug["ya-coloreado"]["primary_color"], by_slug["ya-coloreado"]["secondary_color"]) == ("#010203", "#040506")
    assert dict(stats) == {"candidatos": 3, "coloreados": 2, "sin_color": 1}


def test_backfill_es_idempotente():
    db = db_with_clubs()
    backfill(db)
    snapshot = [dict(c) for c in db.tables["clubs"]]
    again = backfill(db)
    assert db.tables["clubs"] == snapshot
    assert again["coloreados"] == 0 and again["candidatos"] == 1  # solo queda México FC, sigue sin color


def test_dry_run_no_escribe():
    db = db_with_clubs()
    stats = backfill(db, dry_run=True)
    assert stats["coloreados"] == 2
    assert all(c["primary_color"] in (None, "#010203") for c in db.tables["clubs"])


def test_un_escudo_que_no_se_descarga_no_para_el_resto():
    db = db_with_clubs()
    original = db.download

    def flaky(bucket, path):
        if path == "azul-y-rojo.webp" and not flaky.failed:
            flaky.failed = True
            raise httpx.ConnectError("caído")
        return original(bucket, path)

    flaky.failed = False
    db.download = flaky
    stats = backfill(db)
    assert stats["error_descarga"] == 1 and stats["coloreados"] >= 1
