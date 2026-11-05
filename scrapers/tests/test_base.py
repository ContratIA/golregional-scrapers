"""Madrid entera: competiciones de base y veteranos, solo a nivel de equipo.

La clasificación se prueba con las listas reales de competiciones de la RFFM (2026-10-31, Fútbol-11, 7 y 5);
el pipeline, con un transporte falso que clona los payloads reales de Preferente con códigos de equipo propios por grupo.
"""

import copy
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from types import SimpleNamespace

from adapters.novanet_rffm import NovanetMapper
from core.memory_db import MemoryDb
from core.models import CompetitionIn
from pipeline import RffmPipeline, RunStats, unique_slugs
from tests.conftest import FIXTURES
from tests.test_pipeline import FakeTransport, _next

mapper = NovanetMapper("https://www.rffm.es")
GAME_NAMES = {"1": "f11", "2": "f7", "4": "f5"}
BASE_CODES: set[str] = set()


def _base_codes() -> set[str]:
    return {c["codigo"] for code in GAME_NAMES for c in competitions_payload(code) if mapper.base_category(c)}


def competitions_payload(game_code: str) -> list[dict]:
    path = FIXTURES / f"competitions_{GAME_NAMES[game_code]}_2026-10-31.json"
    return json.loads(path.read_text(encoding="utf-8"))


def all_competitions() -> list[dict]:
    return [c for code in GAME_NAMES for c in competitions_payload(code)]


# ───────────── clasificación de categorías (datos reales) ─────────────


def test_clasifica_las_competiciones_reales_de_base():
    base = {c["codigo"]: mapper.base_category(c) for c in all_competitions()}
    base = {code: cat for code, cat in base.items() if cat}
    by_segment = Counter(cat.segment for cat in base.values())
    assert by_segment == {"base": 35, "femenino-base": 16}
    by_game = Counter(cat.game_type for cat in base.values())
    assert by_game == {"f11": 30, "f7": 20, "f5": 1}
    assert Counter(cat.age_group for cat in base.values()) == {
        "juvenil": 9, "cadete": 10, "infantil": 10, "alevin": 13, "benjamin": 5, "prebenjamin": 2, "debutante": 1, "veteranos": 1,
    }
    assert all(cat.segment in ("base", "femenino-base") and cat.age_group for cat in base.values())


def test_adultos_y_fuera_de_alcance_no_son_base():
    by_code = {c["codigo"]: c for c in all_competitions()}
    adult = ["26738226", "26737698", "26737701", "26737706", "26738300", "27786100", "26738252", "26738262", "26737874", "26738230"]
    for code in adult:
        assert mapper.base_category(by_code[code]) is None, code
        assert mapper.senior_category(by_code[code]) is not None, code
    # copas distintas de Aficionados, fútbol inclusivo y andando quedan fuera.
    for code in ("26738289", "27957934", "28777685", "26738376"):
        assert mapper.base_category(by_code[code]) is None, code
        assert mapper.senior_category(by_code[code]) is None, code


def test_los_menores_nunca_son_senior_ni_femenino_adulto():
    for c in all_competitions():
        if mapper.base_category(c):
            assert mapper.senior_category(c) is None, c["nombre"]


def test_dos_prebenjamin_son_fases_distintas_con_slug_propio():
    by_code = {c["codigo"]: c for c in all_competitions()}
    a = mapper.base_category(by_code["26738167"])
    b = mapper.base_category(by_code["26797573"])
    assert (a.category_slug, b.category_slug) == ("prebenjamin-f7-desarrollo", "prebenjamin-f7-iniciacion")
    assert a.game_type == b.game_type == "f7" and a.age_group == b.age_group == "prebenjamin"


def test_nombres_slugs_y_niveles():
    by_code = {c["codigo"]: c for c in all_competitions()}
    cases = {
        "26738130": ("Nacional Juvenil", "nacional-juvenil", "base", "f11", "juvenil", 1),
        "26738066": ("Primera División Autonómica Alevín F7", "primera-division-autonomica-alevin-f7", "base", "f7", "alevin", 3),
        "26738273": ("División de Honor Benjamín F7", "division-de-honor-benjamin-f7", "base", "f7", "benjamin", 2),
        "26737881": ("Primera Femenino Juvenil", "primera-femenino-juvenil", "femenino-base", "f11", "juvenil", 5),
        "26738029": ("Debutante", "debutante-f5", "base", "f5", "debutante", 7),
        "26738269": ("Veteranos Masculino F11 - Movember", "veteranos-masculino-f11-movember", "base", "f11", "veteranos", 7),
    }
    for code, (name, category_slug, segment, game, age, tier) in cases.items():
        cat = mapper.base_category(by_code[code])
        assert (cat.name, cat.category_slug, cat.segment, cat.game_type, cat.age_group, cat.tier) == (name, category_slug, segment, game, age, tier), code


def test_ninguna_competicion_de_base_se_confunde_con_la_division_de_honor_juvenil():
    # La DHJ es de la RFEF y el check `juvenil_scope` de la BD la reserva: aquí nunca aparece ese slug.
    for c in all_competitions():
        cat = mapper.base_category(c)
        if cat:
            assert not cat.category_slug.startswith("division-de-honor-juvenil"), cat.category_slug
            assert cat.segment != "juvenil"


def test_competicion_de_base_sin_goleadores_ni_senior():
    comp = next(c for c in competitions_payload("1") if c["codigo"] == "26737718")  # Preferente Juvenil
    group = {"codigo": "1", "nombre": "Grupo 1", "total_jornadas": "30", "total_equipos": "16", "clasificacion_goleadores": "1"}
    row = mapper.competition(comp, group, mapper.base_category(comp), "https://www.rffm.es/x")
    assert row.segment == "base" and row.is_senior is False and row.has_scorers is False  # aunque la RFFM los publique
    assert row.age_group == "juvenil" and row.game_type == "f11"


def test_slugs_duplicados_se_desambiguan_con_el_codigo():
    def row(code, group):
        return CompetitionIn(source_code=code, group_source_code=f"g{code}{group}", name="X", category_slug="x", tier=5, group_name=group, group_slug=group.lower(), source_url="u")

    out = unique_slugs([row("1", "G1"), row("2", "G1"), row("2", "G2")])
    assert [(r.category_slug, r.group_slug) for r in out] == [("x", "g1"), ("x-2", "g1"), ("x", "g2")]


# ───────────── pipeline ─────────────


@dataclass
class BaseTransport(FakeTransport):
    """Clona los payloads reales de Preferente con códigos propios por grupo (los equipos de base tienen otros códigos)."""

    requests: list = field(default_factory=list)
    client: SimpleNamespace = field(default_factory=lambda: SimpleNamespace(requests_made=0))

    def _tick(self, kind, group, round_number, game_type):
        self.client.requests_made += 1
        self.requests.append((kind, group, round_number, game_type))

    def page_url(self, page, game_type=None, **params):
        return f"{self.base_url}/competicion/{page}?tipojuego={game_type or '1'}"

    def competitions(self, season, game_type=None):
        return competitions_payload(game_type or "1")

    def groups(self, season, competition, game_type=None):
        return [
            {"codigo": f"{competition}1", "nombre": "Grupo 1", "total_jornadas": "6", "total_equipos": "16", "clasificacion_goleadores": "1"},
            {"codigo": f"{competition}2", "nombre": "Grupo 2", "total_jornadas": "6", "total_equipos": "16", "clasificacion_goleadores": "1"},
        ]

    def results(self, season, competition, group, round_number=None, game_type=None):
        self._tick("results", group, round_number, game_type)
        payload = copy.deepcopy(_next("results_pref_g1_j1.html", "results"))
        payload["jornada"] = str(round_number or 3)
        for m in payload["partidos"]:
            m["codacta"] = f"{group}-{round_number or 3}-{m['codacta']}"
            m["CodEquipo_local"] = f"{group}-{m['CodEquipo_local']}"
            m["CodEquipo_visitante"] = f"{group}-{m['CodEquipo_visitante']}"
        # Observaciones de texto libre y un equipo retirado en el primer partido
        payload["partidos"][0]["observaciones"] = "MUTUO ACUERDO con nombre de un jugador"
        payload["partidos"][1]["Retirado_local"] = "1"
        return payload, True

    def standings(self, season, competition, group, round_number=None, game_type=None):
        self._tick("standings", group, round_number, game_type)
        payload = copy.deepcopy(_next("standings_pref_g1_j1.html", "standings"))
        for r in payload["clasificacion"]:
            r["codequipo"] = f"{group}-{r['codequipo']}"
        return payload, True

    def scorers(self, season, competition, group, game_type=None):
        if competition in BASE_CODES:
            raise AssertionError("las competiciones de base no piden goleadores")
        return _next("scorers_pref_g1.html", "scorers"), True


BASE_CODES.update(_base_codes())


def base_pipeline(**kwargs):
    db = MemoryDb()
    transport = BaseTransport()
    p = RffmPipeline(db=db, transport=transport, today=date(2026, 9, 15), **kwargs)
    return p, db, transport


def load_base():
    p, db, transport = base_pipeline()
    p.job_base_structure(RunStats("base_structure", 0))
    transport.requests.clear()
    return p, db, transport


def base_rows(db):
    return [c for c in db.tables["competitions"] if c["segment"] in ("base", "femenino-base")]


def test_base_structure_guarda_grupos_de_menores_sin_datos_de_jugador():
    p, db, transport = base_pipeline()
    p.job_base_structure(RunStats("base_structure", 0))
    rows = db.tables["competitions"]
    assert len(rows) == 51 * 2 and all(c["segment"] in ("base", "femenino-base") for c in rows)
    assert all(c["is_senior"] is False and c["has_scorers"] is False and c["age_group"] for c in rows)
    assert len({(c["category_slug"], c["group_slug"]) for c in rows}) == len(rows)
    assert Counter(c["game_type"] for c in rows) == {"f11": 60, "f7": 40, "f5": 2}
    # la URL de origen lleva la modalidad de juego de cada competición
    f7 = next(c for c in rows if c["game_type"] == "f7")
    assert "tipojuego=2" in f7["source_url"]


def test_los_jobs_de_siempre_no_tocan_la_base():
    p, db, transport = base_pipeline()
    p.job_structure(RunStats("structure", 0))
    assert not base_rows(db)
    p.job_base_structure(RunStats("base_structure", 0))
    adult = p.competitions(only_active=False)
    assert adult and all(c["segment"] in ("senior", "femenino") for c in adult)
    assert {c["segment"] for c in p.base_competitions(only_active=False)} == {"base", "femenino-base"}
    transport.requests.clear()
    p.job_daily(RunStats("daily", 0))
    base_groups = {c["group_source_code"] for c in base_rows(db)}
    assert transport.requests and not any(r[1] in base_groups for r in transport.requests)


def test_cada_peticion_usa_la_modalidad_de_su_competicion():
    p, db, transport = load_base()
    f7 = next(c for c in p.base_competitions(only_active=False) if c["game_type"] == "f7")
    f5 = next(c for c in p.base_competitions(only_active=False) if c["game_type"] == "f5")
    f11 = next(c for c in p.base_competitions(only_active=False) if c["game_type"] == "f11")
    for comp in (f7, f5, f11):
        p.sync_round(comp, 2, RunStats("x", 0))
        p.sync_standings(comp, None, RunStats("x", 0))
    assert transport.requests == [
        ("results", f7["group_source_code"], 2, "2"), ("standings", f7["group_source_code"], None, "2"),
        ("results", f5["group_source_code"], 2, "4"), ("standings", f5["group_source_code"], None, "4"),
        ("results", f11["group_source_code"], 2, "1"), ("standings", f11["group_source_code"], None, "1"),
    ]


def test_equipos_de_base_son_nuevos_y_no_se_crea_ningun_jugador():
    p, db, transport = base_pipeline()
    # Senior ya cargado con los mismos nombres de equipo que usará la base (con otros códigos de federación).
    p.job_structure(RunStats("structure", 0))
    p.sync_round(p.competitions()[0], None, RunStats("daily", 0))
    senior = {t["source_code"]: t for t in db.tables["teams"]}
    p.job_base_structure(RunStats("base_structure", 0))
    comp = p.base_competitions(only_active=False)[0]
    stats = RunStats("base", 0)
    p.sync_round(comp, 1, stats)
    p.sync_standings(comp, None, stats)
    p.sync_scorers(comp, stats)  # aunque alguien lo llame: no hace nada en menores
    new = [t for t in db.tables["teams"] if t["source_code"] not in senior]
    assert len(new) >= 18 and all(t["source_code"].startswith(comp["group_source_code"]) for t in new)
    # el escenario existe de verdad (mismos nombres) y aun así no se funden
    assert {t["name"] for t in new} & {t["name"] for t in senior.values()}
    assert not {t["id"] for t in new} & {t["id"] for t in senior.values()}
    assert all(a["team_id"] not in {t["id"] for t in new} for a in db.tables.get("team_aliases", []))
    assert not db.tables.get("players") and not db.tables.get("player_competition_stats") and not stats.anomalies


def test_sync_scorers_ignora_menores_aunque_el_flag_estuviera_mal():
    p, db, transport = load_base()
    comp = {**p.base_competitions(only_active=False)[0], "has_scorers": True}
    p.sync_scorers(comp, RunStats("x", 0))  # BaseTransport.scorers lanza AssertionError si se pidiera
    assert not db.tables.get("players")


def test_base_daily_un_quinto_de_los_grupos_por_dia_laborable():
    p, db, transport = load_base()  # today = 2026-09-15 = martes (weekday 1)
    p.job_base_daily(RunStats("base_daily", 0))
    expected = {c["group_source_code"] for c in p.base_competitions() if c["id"] % 5 == 1}
    assert expected and {r[1] for r in transport.requests} == expected
    per_group = Counter(r[1] for r in transport.requests if r[0] == "results")
    assert set(per_group.values()) == {3}  # jornada actual y las dos siguientes
    assert Counter(r[0] for r in transport.requests)["standings"] == len(expected)  # primera vez: aún no hay clasificación


def test_base_daily_no_repite_clasificacion_y_no_trabaja_en_fin_de_semana():
    p, db, transport = load_base()
    p.job_base_daily(RunStats("base_daily", 0))
    transport.requests.clear()
    p.job_base_daily(RunStats("base_daily", 0))  # misma pasada otra vez: ya hay clasificación
    assert "standings" not in {r[0] for r in transport.requests}
    for weekend_day in (date(2026, 9, 19), date(2026, 9, 20)):  # sábado y domingo
        transport.requests.clear()
        p.today = weekend_day
        p.job_base_daily(RunStats("base_daily", 0))
        assert transport.requests == []


def test_base_daily_respeta_el_tope_de_peticiones():
    p, db, transport = base_pipeline(max_requests=8)
    p.job_base_structure(RunStats("base_structure", 0))
    transport.requests.clear()
    transport.client.requests_made = 0
    p._requests_at_start = 0
    p.job_base_daily(RunStats("base_daily", 0))
    assert 0 < len(transport.requests) <= 8 + 4  # termina el grupo en curso y no empieza otro


def _seed_match(db, comp, round_number, kickoff, status="scheduled", code=None):
    n = len(db.tables["matches"]) + 1
    db.tables["matches"].append(
        {"id": 9000 + n, "competition_id": comp["id"], "round": round_number, "source_code": code or f"seed-{n}", "kickoff_at": kickoff.isoformat(), "status": status}
    )


def test_base_closing_solo_vuelve_a_los_partidos_de_base_sin_cerrar():
    p, db, transport = load_base()
    comps = p.base_competitions(only_active=False)
    a, b, c, d, e = comps[:5]
    now = p.now()
    _seed_match(db, a, 2, now - timedelta(hours=5))                      # sin cerrar y pasado: sí
    _seed_match(db, b, 2, now - timedelta(hours=5), status="finished")  # cerrado: no
    _seed_match(db, c, 2, now + timedelta(hours=20))                    # futuro: no
    _seed_match(db, d, 2, now - timedelta(minutes=30))                  # acaba de empezar (<2 h): no
    _seed_match(db, e, 2, now - timedelta(days=5))                      # más de 72 h: no
    p.job_base_closing(RunStats("base_closing", 0))
    assert transport.requests == [("results", a["group_source_code"], 2, GAME_CODE[a["game_type"]]), ("standings", a["group_source_code"], None, GAME_CODE[a["game_type"]])]


GAME_CODE = {"f11": "1", "f7": "2", "f5": "4"}


def test_base_live_solo_grupos_con_partido_en_ventana_y_clasificacion_si_cambia():
    p, db, transport = load_base()
    comps = p.base_competitions(only_active=False)
    a, b, c = comps[:3]
    now = p.now()
    _seed_match(db, a, 4, now - timedelta(hours=1))        # en ventana
    _seed_match(db, b, 4, now + timedelta(hours=2))        # aún lejos
    _seed_match(db, c, 4, now - timedelta(hours=1), status="finished")
    stats = RunStats("base_live", 0)
    p.job_base_live(stats)
    assert [r[:3] for r in transport.requests] == [("results", a["group_source_code"], 4), ("standings", a["group_source_code"], None)]
    assert stats.matches > 0


def test_base_live_ignora_partidos_a_las_0000_sin_hora_real():
    p, db, transport = load_base()
    a = p.base_competitions(only_active=False)[0]
    # Hora local 00:00 = "hora sin fijar" en la RFFM: no hay partido real que vigilar
    local_midnight = p.now().replace(hour=0, minute=0, second=0, microsecond=0)
    _seed_match(db, a, 4, local_midnight)
    p.job_base_live(RunStats("base_live", 0))
    assert transport.requests == []


def test_base_calendar_carga_solo_jornadas_que_faltan_y_continua_donde_quedo():
    p, db, transport = base_pipeline(max_requests=7)
    p.job_base_structure(RunStats("base_structure", 0))
    transport.requests.clear()
    transport.client.requests_made = 0
    p._requests_at_start = 0
    first = p.base_competitions(only_active=False)[0]
    # La jornada 2 ya estaba cargada: no se vuelve a pedir
    _seed_match(db, first, 2, p.now() + timedelta(days=3))
    p.job_base_calendar(RunStats("base_calendar", 0))
    assert [r[2] for r in transport.requests[:5]] == [1, 3, 4, 5, 6]
    assert len(transport.requests) == 7 and all(r[0] == "results" for r in transport.requests)

    # Segunda ejecución (otra instancia del pipeline, mismo tope): continúa sin repetir lo ya cargado
    loaded_before = {(m["competition_id"], m["round"]) for m in db.tables["matches"]}
    transport.requests.clear()
    p2 = RffmPipeline(db=db, transport=transport, today=p.today, max_requests=7)
    p2.job_base_calendar(RunStats("base_calendar", 0))
    assert transport.requests
    asked = {(next(c["id"] for c in p2.base_competitions(only_active=False) if c["group_source_code"] == r[1]), r[2]) for r in transport.requests}
    assert not asked & loaded_before  # nada de lo que ya estaba cargado se vuelve a pedir
    assert (first["id"], 6) in {(m["competition_id"], m["round"]) for m in db.tables["matches"]}


def test_base_calendar_sin_tope_completa_un_grupo():
    p, db, transport = load_base()
    first = p.base_competitions(only_active=False)[0]
    p.job_base_calendar(RunStats("base_calendar", 0))
    rounds = {m["round"] for m in db.tables["matches"] if m["competition_id"] == first["id"]}
    assert rounds == {1, 2, 3, 4, 5, 6}


def test_en_menores_no_hay_enlace_al_acta_ni_texto_libre_de_la_rfmm():
    p, db, transport = load_base()
    comp = p.base_competitions(only_active=False)[0]
    p.sync_round(comp, 3, RunStats("x", 0))
    rows = [m for m in db.tables["matches"] if m["competition_id"] == comp["id"]]
    assert len(rows) == 9
    assert all(m["official_url"] is None for m in rows)
    assert {m["notes"] for m in rows} == {None, "Equipo retirado"}
    assert not any("MUTUO" in (m["notes"] or "") for m in rows)
    # Control: en senior se conserva el enlace al acta (que solo se enlaza, nunca se rastrea) y las notas
    p.job_structure(RunStats("structure", 0))
    adult = p.competitions()[0]
    p.sync_round(adult, 3, RunStats("x", 0))
    senior = [m for m in db.tables["matches"] if m["competition_id"] == adult["id"]]
    assert senior and all(m["official_url"] for m in senior) and any("MUTUO" in (m["notes"] or "") for m in senior)


def test_base_live_con_tope_cubre_antes_las_divisiones_altas():
    p, db, transport = load_base()
    comps = p.base_competitions(only_active=False)
    low = max(comps, key=lambda c: (c["tier"], c["id"]))
    high = min(comps, key=lambda c: (c["tier"], -c["id"]))
    assert high["tier"] < low["tier"]
    now = p.now()
    _seed_match(db, low, 4, now - timedelta(hours=1))
    _seed_match(db, high, 4, now - timedelta(hours=1))
    transport.client.requests_made = 0
    p._requests_at_start = 0
    p.max_requests = 2  # solo cabe un grupo (resultados + clasificación)
    p.job_base_live(RunStats("base_live", 0))
    assert {r[1] for r in transport.requests} == {high["group_source_code"]}


def test_base_closing_si_cierra_partidos_sin_hora_real_pero_base_live_no():
    p, db, transport = load_base()
    a = p.base_competitions(only_active=False)[0]
    midnight_yesterday = (p.now() - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    _seed_match(db, a, 5, midnight_yesterday)
    p.job_base_live(RunStats("base_live", 0))
    assert transport.requests == []
    p.job_base_closing(RunStats("base_closing", 0))
    assert [r[:3] for r in transport.requests] == [("results", a["group_source_code"], 5), ("standings", a["group_source_code"], None)]


def test_tope_de_tiempo_y_contador_obligatorio():
    p, db, transport = load_base()
    p.max_seconds = 0.001
    p._started -= 5
    assert p.over_budget() is True
    import pytest

    with pytest.raises(ValueError):
        RffmPipeline(db=MemoryDb(), transport=FakeTransport(), max_requests=5)  # sin contador de peticiones


def test_competicion_con_otra_modalidad_en_el_listado_no_se_guarda():
    class Mixed(BaseTransport):
        def competitions(self, season, game_type=None):
            return competitions_payload("1") if game_type == "2" else super().competitions(season, game_type)

    p = RffmPipeline(db=MemoryDb(), transport=Mixed(), today=date(2026, 9, 15))
    p.job_base_structure(RunStats("base_structure", 0))
    f11_names = {c["codigo"] for c in competitions_payload("1")}
    assert all(c["game_type"] == "f11" for c in p.db.tables["competitions"] if c["source_code"] in f11_names)
    assert not [c for c in p.db.tables["competitions"] if c["game_type"] == "f7" and c["source_code"] in f11_names]
