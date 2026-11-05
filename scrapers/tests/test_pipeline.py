"""Pipeline completo contra fixtures reales y BD en memoria: idempotencia, alertas y reglas de fuente."""

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from adapters.novanet_rffm import SelectorEmpty, extract_next_data
from core.memory_db import MemoryDb
from pipeline import RffmPipeline, RunStats
from tests.conftest import FIXTURES


def _next(name, key):
    return extract_next_data((FIXTURES / name).read_text(encoding="utf-8"))[key]


@dataclass
class FakeTransport:
    base_url: str = "https://www.rffm.es"
    results_payload: dict = field(default_factory=lambda: _next("results_pref_g1_j1.html", "results"))
    broken: bool = False
    calls: list = field(default_factory=list)

    def page_url(self, page, game_type=None, **params):
        return f"{self.base_url}/competicion/{page}"

    def seasons(self):
        return [{"cod_temporada": "22", "nombre": "2026-2027", "fecha_inicio": "2026-07-01", "fecha_fin": "2027-09-01"}]

    def competitions(self, season, game_type=None):
        if game_type not in (None, "1"):
            return []  # la RFFM devuelve las competiciones de cada modalidad por separado
        # Integración senior/femenino histórica; el mapeo de las nuevas categorías
        # se cubre con pruebas específicas de RFFM.
        return [c for c in _next("standings_pref_g1_j1.html", "competitions") if c["codigo"] not in ("28733581", "26738233", "26738236")]

    def groups(self, season, competition, game_type=None):
        return json.loads((FIXTURES / f"groups_{competition}.json").read_text(encoding="utf-8")) if competition != "26737701" else [
            {"codigo": "26737702", "nombre": "Grupo 1", "total_jornadas": "34", "total_equipos": "18"}
        ]

    def results(self, season, competition, group, round_number=None, game_type=None):
        self.calls.append(("results", group, round_number))
        if self.broken:
            raise SelectorEmpty("results.partidos ausente")
        return self.results_payload, True

    def standings(self, season, competition, group, round_number=None, game_type=None):
        return _next("standings_pref_g1_j1.html", "standings"), True

    def scorers(self, season, competition, group, game_type=None):
        return _next("scorers_pref_g1.html", "scorers"), True

    def crest_image(self, url):
        import io

        from PIL import Image

        self.calls.append(("crest", url))
        buf = io.BytesIO()
        Image.new("RGB", (300, 360), (200, 16, 46)).save(buf, format="PNG")
        return buf.getvalue()


def pipeline(transport=None):
    db = MemoryDb()
    return RffmPipeline(db=db, transport=transport or FakeTransport(), today=date(2026, 9, 15)), db


def preferente_g1(p):
    return next(c for c in p.competitions() if c["group_source_code"] == "26737702")


def test_structure_loads_only_senior_groups():
    p, db = pipeline()
    p.job_structure(RunStats("structure", 0))
    slugs = {(c["category_slug"], c["group_slug"]) for c in db.tables["competitions"]}
    assert ("tercera-federacion", "grupo-7") in slugs
    assert len(db.tables["competitions"]) == 1 + 2 + 1 + 8 + 22 + 4
    segments = Counter(c["segment"] for c in db.tables["competitions"])
    assert segments == {"senior": 34, "femenino": 4}
    assert all(c["is_senior"] if "is_senior" in c else True for c in db.tables["competitions"])
    femenino = {c["group_source_code"]: c for c in db.tables["competitions"] if c["segment"] == "femenino"}
    assert femenino["27786685"]["has_scorers"] is False and femenino["26738253"]["has_scorers"] is True
    assert db.tables["seasons"][0]["is_current"] is True
    tercera = next(c for c in db.tables["competitions"] if c["category_slug"] == "tercera-federacion")
    assert tercera["starts_on"] == "2026-09-05"


def test_round_upsert_is_idempotent():
    p, db = pipeline()
    p.job_structure(RunStats("structure", 0))
    comp = preferente_g1(p)
    stats = RunStats("daily", 0)
    p.sync_round(comp, None, stats)
    assert stats.matches == 9
    teams = len(db.tables["teams"])
    assert teams == 18

    again = RunStats("daily", 0)
    p.sync_round(comp, None, again)
    assert again.matches == 0  # source_hash sin cambios → no se reescribe
    assert len(db.tables["matches"]) == 9 and len(db.tables["teams"]) == teams

    match = next(m for m in db.tables["matches"] if m["source_code"] == "5589120")
    assert match["slug"] == "cd-ejemplo-b-sad-muestra-a-2026-09-12"
    assert match["official_url"].startswith("https://www.rffm.es/acta-partido/5589120")


def test_standings_and_scorers():
    p, db = pipeline()
    p.job_structure(RunStats("structure", 0))
    comp = preferente_g1(p)
    stats = RunStats("closing", 0)
    p.sync_standings(comp, None, stats)
    p.sync_scorers(comp, stats)
    assert stats.standings == 18
    assert len(db.tables["players"]) == 21
    assert db.tables["players"][0]["slug"] == "jugador01-apellido01-prueba-900001"
    assert not stats.anomalies


def test_community_score_survives_empty_official_result():
    transport = FakeTransport()
    p, db = pipeline(transport)
    p.job_structure(RunStats("structure", 0))
    comp = preferente_g1(p)
    p.sync_round(comp, None, RunStats("daily", 0))

    match = next(m for m in db.tables["matches"] if m["source_code"] == "5589120")
    match.update(home_goals=4, away_goals=0, data_source="community", source_hash="x")

    payload = json.loads(json.dumps(transport.results_payload))
    for partido in payload["partidos"]:
        partido.update(Goles_casa="", Goles_visitante="", acta_cerrada="0")
    transport.results_payload = payload
    p.sync_round(comp, None, RunStats("daily", 0))
    match = next(m for m in db.tables["matches"] if m["source_code"] == "5589120")
    assert (match["home_goals"], match["data_source"]) == (4, "community")

    # Cuando el acta oficial trae marcador, sobrescribe siempre.
    payload = json.loads(json.dumps(payload))
    payload["partidos"][0].update(Goles_casa="2", Goles_visitante="1", acta_cerrada="1")
    transport.results_payload = payload
    p.sync_round(comp, None, RunStats("daily", 0))
    match = next(m for m in db.tables["matches"] if m["source_code"] == "5589120")
    assert (match["home_goals"], match["data_source"], match["is_result_official"]) == (2, "official", True)


def test_volume_drop_marks_adapter_degraded():
    transport = FakeTransport()
    p, db = pipeline(transport)
    p.job_structure(RunStats("structure", 0))
    comp = preferente_g1(p)
    p.sync_round(comp, None, RunStats("daily", 0))

    payload = json.loads(json.dumps(transport.results_payload))
    payload["jornada"] = "2"
    payload["partidos"] = [dict(x, codacta=f"9{x['codacta']}") for x in payload["partidos"][:3]]
    transport.results_payload = payload
    stats = RunStats("daily", 0)
    p.sync_round(comp, None, stats)
    assert [a.kind for a in stats.anomalies] == ["volume_drop"]

    assert p.finish(stats, 0, __import__("datetime").datetime.now()) is False
    assert db.tables["adapter_health"][0]["state"] == "degraded"
    assert db.tables["scrape_runs"][0]["ok"] is False


def test_network_failure_in_structure_is_recorded_not_raised():
    import httpx

    from pipeline import run_job

    class TimeoutTransport(FakeTransport):
        def seasons(self):
            raise httpx.ReadTimeout("The read operation timed out")

    p, db = pipeline(TimeoutTransport())
    stats = RunStats("daily", 0)
    run_job(p, "daily", stats)
    assert [a.kind for a in stats.anomalies] == ["http"]
    assert "ReadTimeout" in stats.anomalies[0].message
    assert p.finish(stats, 0, __import__("datetime").datetime.now()) is False
    assert db.tables["adapter_health"][0]["state"] == "degraded"


def test_broken_selector_does_not_stop_other_groups():
    transport = FakeTransport(broken=True)
    p, db = pipeline(transport)
    stats = RunStats("closing", 0)
    p.job_closing(stats)
    assert len(stats.anomalies) == len(p.competitions())
    assert all(a.kind == "selector_empty" for a in stats.anomalies)


def test_crests_create_clubs_link_teams_and_skip_unchanged():
    import io

    from PIL import Image

    transport = FakeTransport()
    p, db = pipeline(transport)
    p.job_structure(RunStats("structure", 0))
    p.sync_standings(preferente_g1(p), None, RunStats("daily", 0))

    stats = RunStats("crests", 0)
    p.job_crests(stats)
    assert not stats.anomalies
    downloads = [c for c in transport.calls if c[0] == "crest"]
    assert stats.crests == len(downloads) == 18
    assert all(url.startswith("https://appweb.rffm.es/pnfg/pimg/") for _, url in downloads)

    ejemplo = next(c for c in db.tables["clubs"] if c["slug"] == "cd-ejemplo")
    assert ejemplo["crest_path"] == "cd-ejemplo.webp" and ejemplo["name"] == "CD Ejemplo"
    content, content_type = db.storage["crests"]["cd-ejemplo.webp"]
    assert content_type == "image/webp" and content[:4] == b"RIFF"
    assert max(Image.open(io.BytesIO(content)).size) <= 128
    assert all(t.get("club_id") for t in db.tables["teams"])

    again = RunStats("crests", 0)
    p.job_crests(again)
    assert again.crests == 0  # misma URL de origen: no se vuelve a descargar
    assert len([c for c in transport.calls if c[0] == "crest"]) == 18


def test_crest_que_no_es_imagen_no_tumba_la_pasada():
    """La RFFM puede responder HTML (WAF/redirección): se registra la anomalía y se sigue."""
    transport = FakeTransport()
    p, db = pipeline(transport)
    p.job_structure(RunStats("structure", 0))
    p.sync_standings(preferente_g1(p), None, RunStats("daily", 0))

    calls = {"n": 0}

    def flaky(url):
        calls["n"] += 1
        if calls["n"] == 1:
            return b"<!DOCTYPE html><html><body>aviso</body></html>"
        return FakeTransport.crest_image(transport, url)

    transport.crest_image = flaky
    stats = RunStats("crests", 0)
    p.job_crests(stats)

    assert [a.kind for a in stats.anomalies] == ["not_an_image"]
    assert stats.crests == 17
    assert len(db.storage["crests"]) == 17
    # El club cuyo escudo falló sigue existiendo y con sus equipos enlazados.
    assert all(t.get("club_id") for t in db.tables["teams"])
    assert len([c for c in db.tables["clubs"] if not c.get("crest_path")]) == 1


def test_robots_prohibido_no_tumba_la_pasada_de_escudos():
    """Si robots.txt cambia y prohíbe la ruta, los escudos que ya había se conservan y la
    pasada sigue: queda una anomalía `robots`."""
    from core.http import RobotsDisallowed

    transport = FakeTransport()
    p, db = pipeline(transport)
    p.job_structure(RunStats("structure", 0))
    p.sync_standings(preferente_g1(p), None, RunStats("daily", 0))

    def blocked(url):
        raise RobotsDisallowed(url)

    transport.crest_image = blocked
    stats = RunStats("crests", 0)
    p.job_crests(stats)

    assert stats.crests == 0
    assert len(stats.anomalies) == 18 and {a.kind for a in stats.anomalies} == {"robots"}
    # Los clubes existen y los equipos quedan enlazados aunque no haya escudo.
    assert all(t.get("club_id") for t in db.tables["teams"])
    assert not db.storage["crests"]


def test_robots_en_la_primera_llamada_se_registra_como_anomalia():
    from core.http import RobotsDisallowed
    from pipeline import run_job

    p, _ = pipeline()

    def blocked(*args, **kwargs):
        raise RobotsDisallowed("https://www.rffm.es/competicion/clasificaciones")

    p.transport.seasons = blocked
    stats = RunStats("structure", 0)
    run_job(p, "structure", stats)

    assert [a.kind for a in stats.anomalies] == ["robots"]
    assert stats.anomalies[0].context == {"job": "structure"}


def test_club_name_strips_team_letter_only():
    from pipeline import club_name

    assert club_name("CD Leganes SAD C") == "CD Leganes SAD"
    assert club_name("Rayo Ciudad Alcobendas CF") == "Rayo Ciudad Alcobendas CF"
    assert club_name("Club Polid Parla Escuela - Fair Play A") == "Club Polid Parla Escuela - Fair Play"


def test_live_requests_only_todays_groups_with_explicit_round():
    transport = FakeTransport()
    p, db = pipeline(transport)
    p.job_structure(RunStats("structure", 0))
    p.sync_round(preferente_g1(p), None, RunStats("daily", 0))  # partidos del 12 y 13-09, jornada 1
    transport.calls.clear()

    p.today = date(2026, 9, 13)
    p.job_live(RunStats("live", 0))
    assert [c for c in transport.calls if c[0] == "results"] == [("results", "26737702", 1)]

    transport.calls.clear()
    p.today = date(2026, 9, 16)  # día sin partidos: no se pide ninguna página
    p.job_live(RunStats("live", 0))
    assert transport.calls == []


def test_hot_only_requests_groups_with_match_in_window():
    from datetime import datetime, timedelta

    from pipeline import is_placeholder_time

    assert is_placeholder_time("2026-09-26T22:00:00+00:00")  # 00:00 Madrid (CEST)
    assert not is_placeholder_time("2026-09-27T09:00:00+00:00")

    transport = FakeTransport()
    p, db = pipeline(transport)
    p.job_structure(RunStats("structure", 0))
    p.sync_round(preferente_g1(p), None, RunStats("daily", 0))
    for row in db.tables["matches"]:
        row["status"] = "scheduled"  # el fixture trae partidos ya jugados: se ponen como pendientes
    kickoffs = [datetime.fromisoformat(m["kickoff_at"]) for m in db.select("matches", select="kickoff_at") if m["kickoff_at"]]
    kickoffs = [k for k in kickoffs if not (k.astimezone(p.now().tzinfo).hour == 0)]
    assert kickoffs
    first = min(kickoffs)

    transport.calls.clear()
    p.now = lambda: first + timedelta(minutes=30)  # partido en curso
    p.job_hot(RunStats("hot", 0))
    assert any(c[0] == "results" for c in transport.calls)

    transport.calls.clear()
    p.now = lambda: first - timedelta(days=3)  # nada en la ventana
    p.job_hot(RunStats("hot", 0))
    assert transport.calls == []


def _femenino(p):
    return next(c for c in p.competitions() if c["group_source_code"] == "27786685")


def test_femenino_sin_goleadoras_no_pide_la_pagina():
    class NoScorers(FakeTransport):
        def scorers(self, season, competition, group, game_type=None):
            raise AssertionError("no debe pedirse la tabla de goleadoras")

    p, db = pipeline(NoScorers())
    p.job_structure(RunStats("structure", 0))
    stats = RunStats("daily", 0)
    p.sync_scorers(_femenino(p), stats)
    assert not stats.anomalies and not db.tables.get("players")


def test_equipo_femenino_no_se_funde_con_el_masculino_del_mismo_nombre():
    men = FakeTransport()
    p, db = pipeline(men)
    p.job_structure(RunStats("structure", 0))
    p.sync_round(preferente_g1(p), None, RunStats("daily", 0))
    first = men.results_payload["partidos"][0]
    male_code, male_name = first["CodEquipo_local"], first["Nombre_equipo_local"]
    male_id = next(t["id"] for t in db.tables["teams"] if t["source_code"] == male_code)
    teams_before = {t["source_code"] for t in db.tables["teams"]}

    # Equipo femenino con exactamente el mismo nombre que uno masculino ya cargado, otro código.
    fem = json.loads(json.dumps(men.results_payload))
    fem["partidos"] = [dict(first, codacta="FEM-1", CodEquipo_local="FEM-HOME", Nombre_equipo_local=male_name, CodEquipo_visitante="FEM-AWAY", Nombre_equipo_visitante="Otro Club Femenino")]
    men.results_payload = fem
    p.sync_round(_femenino(p), None, RunStats("daily", 0))

    created = {t["source_code"]: t for t in db.tables["teams"] if t["source_code"] not in teams_before}
    assert set(created) == {"FEM-HOME", "FEM-AWAY"}
    assert created["FEM-HOME"]["id"] != male_id  # no se fundió con el masculino
    assert created["FEM-HOME"]["slug"] != next(t["slug"] for t in db.tables["teams"] if t["id"] == male_id)
    new_ids = {t["id"] for t in created.values()}
    assert not any(a["team_id"] in new_ids for a in db.tables.get("team_aliases", []))  # sin alias que contaminen
    match = next(m for m in db.tables["matches"] if m["source_code"] == "FEM-1")
    assert match["home_team_id"] == created["FEM-HOME"]["id"]


def test_crests_calculan_colores_del_club_y_no_pisan_los_existentes():
    transport = FakeTransport()
    p, db = pipeline(transport)
    p.job_structure(RunStats("structure", 0))
    p.sync_standings(preferente_g1(p), None, RunStats("daily", 0))
    # Un club que ya tiene colores propios antes de que llegue su escudo.
    db.tables["clubs"].append({"id": 900, "federation_id": 1, "slug": "cd-ejemplo", "name": "CD Ejemplo", "primary_color": "#112233", "secondary_color": "#445566"})

    p.job_crests(RunStats("crests", 0))

    # El escudo falso es un cuadrado rojo (200, 16, 46): un solo color -> secondary = primary.
    otros = [c for c in db.tables["clubs"] if c["slug"] != "cd-ejemplo"]
    assert otros and all(c["primary_color"] == c["secondary_color"] == "#c8102e" for c in otros)
    ejemplo = next(c for c in db.tables["clubs"] if c["slug"] == "cd-ejemplo")
    assert (ejemplo["primary_color"], ejemplo["secondary_color"]) == ("#112233", "#445566")
    assert ejemplo["crest_path"] == "cd-ejemplo.webp"  # el escudo sí se sube


def test_crest_blanco_y_negro_deja_los_colores_en_null():
    import io

    from PIL import Image

    class BlackWhite(FakeTransport):
        def crest_image(self, url):
            buf = io.BytesIO()
            Image.new("RGB", (64, 64), (255, 255, 255)).save(buf, format="PNG")
            return buf.getvalue()

    p, db = pipeline(BlackWhite())
    p.job_structure(RunStats("structure", 0))
    p.sync_standings(preferente_g1(p), None, RunStats("daily", 0))
    stats = RunStats("crests", 0)
    p.job_crests(stats)
    assert not stats.anomalies and stats.crests == 18
    assert all(c.get("primary_color") is None for c in db.tables["clubs"])


def test_slug_de_partido_repetido_en_otra_categoria_no_da_409():
    """Mismo "CD X A - CD Y B" el mismo día en dos categorías: antes 409 Conflict y la jornada entera sin guardar."""
    from core.memory_db import MemoryDb
    from pipeline import RffmPipeline

    pipe = RffmPipeline.__new__(RffmPipeline)
    pipe.db = MemoryDb()
    row = lambda comp, code: {"competition_id": comp, "source_code": code, "slug": "cd-x-a-cd-y-b-2026-11-01"}  # noqa: E731
    first = [row(1, "a")]
    pipe._unique_match_slugs(first)
    pipe.db.upsert("matches", first, on_conflict="competition_id,source_code")
    second = [row(2, "b"), row(2, "c")]  # otra categoría, y dos veces en la misma tanda
    pipe._unique_match_slugs(second)
    pipe.db.upsert("matches", second, on_conflict="competition_id,source_code")  # no debe lanzar 409
    assert first[0]["slug"] == "cd-x-a-cd-y-b-2026-11-01"
    assert len({r["slug"] for r in first + second}) == 3
    again = [row(1, "a")]  # la misma fila otra vez conserva su slug
    pipe._unique_match_slugs(again)
    assert again[0]["slug"] == "cd-x-a-cd-y-b-2026-11-01"


def test_copa_no_consulta_clasificacion():
    p, _ = pipeline()
    stats = RunStats("daily", 0)
    p.sync_standings({"category_slug": "copa-aficionados-rffm"}, None, stats)
    assert stats.standings == 0
    assert not stats.anomalies
