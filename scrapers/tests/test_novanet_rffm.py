from datetime import datetime

import json

import pytest

from adapters.novanet_rffm import MADRID_TZ, NovanetMapper, SelectorEmpty, extract_next_data
from core.models import MatchStatus

mapper = NovanetMapper("https://www.rffm.es")


def test_results_fixture_maps_all_matches(fixture_html):
    results = extract_next_data(fixture_html("results_pref_g1_j1.html"))["results"]
    matches = mapper.matches(results, "22", now=datetime(2026, 9, 15, tzinfo=MADRID_TZ))

    assert len(matches) == 9
    first = matches[0]
    assert first.source_code == "5589120"
    assert first.round == 1
    assert first.kickoff_at == datetime(2026, 9, 12, 18, 0, tzinfo=MADRID_TZ)
    assert (first.home.name, first.away.name) == ("C.D. EJEMPLO 'B'", "S.A.D. MUESTRA 'A'")
    assert (first.home_goals, first.away_goals) == (2, 1)
    assert first.status is MatchStatus.finished
    assert first.is_result_official
    assert first.notes == "MUTUO ACUERDO"
    assert first.official_url == "https://www.rffm.es/acta-partido/5589120?temporada=22&competicion=26737701&grupo=26737702"
    assert first.home.crest_url.startswith("https://appweb.rffm.es/pnfg/pimg/")


def test_crest_url_host_reescrito_y_parametro_conservado():
    # Forma real del payload de la RFFM.
    assert mapper.crest("/pnfg/pimg/Clubes/00100_x_Escudo.png?nova=1") == "https://appweb.rffm.es/pnfg/pimg/Clubes/00100_x_Escudo.png?nova=1"
    assert mapper.crest("pimg/Clubes/00100_x_Escudo.png?nova=1") == "https://appweb.rffm.es/pnfg/pimg/Clubes/00100_x_Escudo.png?nova=1"
    # Un payload con URL absoluta del host www también se reescribe: ahí ya no hay imagen.
    assert mapper.crest("https://www.rffm.es/pnfg/pimg/Clubes/00100_x_Escudo.jpg?nova=1") == (
        "https://appweb.rffm.es/pnfg/pimg/Clubes/00100_x_Escudo.jpg?nova=1"
    )
    assert mapper.crest(None) is None


def test_escudo_por_la_ruta_que_permite_robots(fixture_html):
    """`appweb.rffm.es` es `Disallow: /`: el binario se pide al optimizador del host permitido."""
    from adapters.novanet_rffm import RffmTransport, crest_fetch_url
    from core.http import Robots

    source = "https://appweb.rffm.es/pnfg/pimg/Clubes/00100_x_Escudo Con Ñ.png?nova=1"
    fetch = crest_fetch_url(source)
    assert fetch == (
        "https://www.rffm.es/_next/image?url=https%3A%2F%2Fappweb.rffm.es%2Fpnfg%2Fpimg%2FClubes"
        "%2F00100_x_Escudo%20Con%20%C3%91.png%3Fnova%3D1&w=256&q=75"
    )
    # La ruta pedida pasa el robots.txt real de la RFFM (guardado como fixture el 2026-09-19).
    assert Robots(fixture_html("robots_www.rffm.es_20260919.txt")).can_fetch(fetch)
    # Un host distinto no se reescribe.
    assert crest_fetch_url("https://example.test/escudo.png") == "https://example.test/escudo.png"

    class Client:
        def __init__(self):
            self.urls = []

        def get_bytes(self, url):
            self.urls.append(url)
            return b"\x89PNG\r\n\x1a\n", "image/png"

    client = Client()
    assert RffmTransport(client).crest_image(source) == b"\x89PNG\r\n\x1a\n"
    assert client.urls == [fetch]


def test_rounds_listing(fixture_html):
    results = extract_next_data(fixture_html("results_pref_g1_j1.html"))["results"]
    rounds = mapper.rounds(results)
    assert len(rounds) == 34
    assert rounds[0].number == 1 and rounds[0].date.isoformat() == "2026-09-13"


def test_standings_fixture(fixture_html):
    standings = mapper.standings(extract_next_data(fixture_html("standings_pref_g1_j1.html"))["standings"])
    assert standings.round == 1
    assert len(standings.rows) == 18
    leader = standings.rows[0]
    assert leader.position == 1 and leader.team.source_code == "1311" and leader.points == 3
    assert leader.form == "G"
    assert [r.position for r in standings.rows] == list(range(1, 19))


def test_scorers_fixture(fixture_html):
    scorers = mapper.scorers(extract_next_data(fixture_html("scorers_pref_g1.html"))["scorers"])
    assert len(scorers) == 21
    top = scorers[0]
    assert top.full_name == "Jugador01 Apellido01 Prueba"
    assert top.goals == 2 and top.team.source_code == "1074"


def test_only_senior_competitions_selected(fixture_html):
    competitions = extract_next_data(fixture_html("standings_pref_g1_j1.html"))["competitions"]
    selected = {c["codigo"]: mapper.senior_category(c).category_slug for c in competitions if mapper.senior_category(c)}
    assert selected == {
        "26738226": "tercera-federacion",
        "26737698": "primera-autonomica",
        "26737701": "preferente",
        "26737706": "primera-regional",
        "26738300": "segunda-regional",
        "27786100": "tercera-federacion-femenino",
        "26738252": "primera-autonomica-femenino",
        "26738262": "preferente-femenino",
        "26738233": "campeonato-universitario-masculino",
        "26738236": "campeonato-universitario-femenino",
    }


def test_femenino_absoluto_segment_y_goleadoras(fixture_html):
    competitions = extract_next_data(fixture_html("standings_pref_g1_j1.html"))["competitions"]
    by_code = {c["codigo"]: c for c in competitions}
    groups = {
        "27786100": json.loads(fixture_html("groups_27786100.json")),
        "26738252": json.loads(fixture_html("groups_26738252.json")),
        "26738262": json.loads(fixture_html("groups_26738262.json")),
    }
    rows = {}
    for code, glist in groups.items():
        category = mapper.senior_category(by_code[code])
        for g in glist:
            rows[g["codigo"]] = mapper.competition(by_code[code], g, category, "https://www.rffm.es/x")
    assert {r.segment for r in rows.values()} == {"femenino"}
    assert len(rows) == 4
    assert rows["27786685"].has_scorers is False  # Tercera Fed. Femenino: la RFFM no publica goleadoras
    assert rows["26738253"].has_scorers is True
    assert rows["26738253"].group_slug == "grupo-unico"
    assert rows["26738263"].category_slug == "preferente-femenino"


def test_senior_segment_por_defecto(fixture_html):
    competitions = extract_next_data(fixture_html("standings_pref_g1_j1.html"))["competitions"]
    pref = next(c for c in competitions if c["codigo"] == "26737701")
    group = json.loads(fixture_html("groups_26737698.json"))[0]
    row = mapper.competition(pref, group, mapper.senior_category(pref), "https://www.rffm.es/x")
    assert row.segment == "senior" and row.has_scorers is True


def test_femenino_real_fixture_se_mapea(fixture_html):
    pack = json.loads(fixture_html("femenino_tercera_g7_j3.json"))
    matches = mapper.matches(pack["results"], "22")
    assert len(matches) == 8 and all(m.round == 3 for m in matches)
    table = mapper.standings(pack["standings"])
    assert len(table.rows) == 15 and table.round == 3
    scorers = mapper.scorers(pack["scorers"])
    assert scorers and all(s.goals >= 1 for s in scorers)


@pytest.mark.parametrize(
    "competition",
    [
        {"tipo_competicion": "2", "nombre": "PREFERENTE JUVENIL", "NombreCategoria": "PREFERENTE AFICIONADO"},
        {"tipo_competicion": "2", "nombre": "PRIMERA CADETE", "NombreCategoria": "PRIMERA AFICIONADO"},
        {"tipo_competicion": "1", "nombre": "COPA", "NombreCategoria": "TERCERA FEDERACION"},
        # Femenino de base y competiciones femeninas no incluidas
        {"tipo_competicion": "2", "nombre": "PRIMERA FEMENINO JUVENIL", "NombreCategoria": "PRIMERA FEMENINO JUVENIL"},
        {"tipo_competicion": "2", "nombre": "PREFERENTE FEMENINO CADETE", "NombreCategoria": "PREFERENTE FEMENINO CADETE"},
    ],
)
def test_youth_and_unrecognized_cups_rejected_even_if_misconfigured(competition):
    assert mapper.senior_category(competition) is None


def test_live_and_pending_status():
    base = {
        "codacta": "1", "fecha": "20/09/2026", "hora": "11:00", "campojuego": "X",
        "CodEquipo_local": "1", "Nombre_equipo_local": "AA", "CodEquipo_visitante": "2", "Nombre_equipo_visitante": "BB",
    }
    results = {"jornada": "2", "codigo_competicion": "c", "codigo_grupo": "g"}
    now = datetime(2026, 9, 20, 11, 30, tzinfo=MADRID_TZ)

    live = mapper.matches({**results, "partidos": [{**base, "partido_en_juego": "1", "Goles_casa": "1", "Goles_visitante": "0"}]}, "22", now)[0]
    assert live.status is MatchStatus.live and not live.is_result_official

    pending = mapper.matches({**results, "partidos": [{**base, "Goles_casa": "", "Goles_visitante": ""}]}, "22", now)[0]
    assert pending.status is MatchStatus.scheduled and pending.home_goals is None

    postponed = mapper.matches({**results, "partidos": [{**base, "observaciones": "APLAZADO"}]}, "22", now)[0]
    assert postponed.status is MatchStatus.postponed


class _SequenceClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = 0

    def get(self, url):
        from core.http import FetchResult

        self.calls += 1
        return FetchResult(url=url, status=200, text=self.pages.pop(0), content_hash="h", changed=True)


def _next_data_html(page_props):
    import json

    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps({"props": {"pageProps": page_props}})}</script>'


def test_transient_null_results_retried_once():
    from adapters.novanet_rffm import RffmTransport

    good = {"jornada": "3", "partidos": []}
    client = _SequenceClient([_next_data_html({"results": None}), _next_data_html({"results": good})])
    data, _ = RffmTransport(client).results("22", "c", "g", 3)
    assert data == good and client.calls == 2


def test_persistent_null_results_raise():
    from adapters.novanet_rffm import RffmTransport

    client = _SequenceClient([_next_data_html({"results": None})] * 2)
    with pytest.raises(SelectorEmpty):
        RffmTransport(client).results("22", "c", "g", 3)


def test_missing_next_data_raises():
    with pytest.raises(SelectorEmpty):
        extract_next_data("<html><body>mantenimiento</body></html>")


def test_copa_aficionados_reconocida_solo_como_categoria_propia(fixture_html):
    competitions = json.loads(fixture_html("competitions_f11_2026-10-31.json"))
    cup = next(c for c in competitions if c["codigo"] == "28733581")
    category = mapper.senior_category(cup)
    assert category is not None
    assert (category.name, category.category_slug, category.segment) == ("Copa de Aficionados RFFM", "copa-aficionados-rffm", "senior")
    assert mapper.senior_category({"tipo_competicion": "1", "nombre": "COPA", "NombreCategoria": "TERCERA FEDERACION"}) is None
