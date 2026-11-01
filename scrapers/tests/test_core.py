import httpx
import pytest

from core.http import PoliteClient, RobotsDisallowed
from core.models import TeamRef
from core.normalize import display_player_name, display_team_name, normalize_for_match
from core.resolver import KnownTeam, TeamResolver

ROBOTS = "User-agent: *\nAllow: /\nDisallow: /acta-partido/\n"


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make_client(handler, **kwargs):
    fake = FakeClock()
    client = PoliteClient(transport=httpx.MockTransport(handler), sleep=fake.sleep, clock=fake.clock, **kwargs)
    return client, fake


def test_rate_limit_three_seconds_per_domain():
    client, fake = make_client(lambda r: httpx.Response(200, text=ROBOTS if r.url.path == "/robots.txt" else "ok"))
    client.get("https://www.rffm.es/a")
    client.get("https://www.rffm.es/b")
    assert client.requests_made == 3  # robots + 2
    assert fake.sleeps == [3.0, 3.0]


def test_robots_disallow_is_respected():
    client, _ = make_client(lambda r: httpx.Response(200, text=ROBOTS))
    with pytest.raises(RobotsDisallowed):
        client.get("https://www.rffm.es/acta-partido/123")


RFFM_ROBOTS = """# *
User-agent: *
Allow: /

Disallow: /fichaequipo/
Disallow: /fichajugador/
Disallow: /acta-partido/

# Host
Host: https://rffm.es
Sitemap: https://rffm.es/sitemap.xml
"""


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/competicion/resultados-y-jornadas?temporada=22", True),
        ("/api/groups?temporada=22", True),
        ("/acta-partido/5589120?temporada=22", False),
        ("/fichajugador/673985", False),
        ("/fichaequipo/1311", False),
    ],
)
def test_real_rffm_robots_longest_match(path, allowed):
    from core.http import Robots

    assert Robots(RFFM_ROBOTS).can_fetch(f"https://www.rffm.es{path}") is allowed


def test_robots_wildcards_and_specific_agent():
    from core.http import Robots

    robots = Robots("User-agent: *\nDisallow: /\n\nUser-agent: FutbolDirectoHoyBot\nDisallow: /*.pdf$\n")
    assert robots.can_fetch("https://x.es/competicion") is True
    assert robots.can_fetch("https://x.es/doc/bases.pdf") is False


def test_retries_with_backoff_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 3 else httpx.Response(200, text="ok")

    client, fake = make_client(handler, min_interval=0)
    assert client.get("https://x.es/p").text == "ok"
    assert calls["n"] == 3
    assert len([s for s in fake.sleeps if s >= 2]) == 2


def test_content_hash_cache_detects_unchanged(tmp_path):
    cache = tmp_path / "hashes.json"
    handler = lambda r: httpx.Response(200, text=ROBOTS if r.url.path == "/robots.txt" else "same")
    client, _ = make_client(handler, cache_path=cache, min_interval=0)
    assert client.get("https://x.es/p").changed is True
    client.close()
    client2, _ = make_client(handler, cache_path=cache, min_interval=0)
    assert client2.get("https://x.es/p").changed is False


def test_display_names():
    assert display_team_name("C.D. EJEMPLO 'B'") == "CD Ejemplo B"
    assert display_team_name("S.A.D. MUESTRA 'A'") == "SAD Muestra A"
    assert display_team_name("ATLETICO CHOPERA ALCOBENDAS 04 'A'") == "Atletico Chopera Alcobendas 04 A"
    assert display_team_name("C.D. ELECTROCOR LAS ROZAS C.F. 'A'") == "CD Electrocor Las Rozas CF A"
    assert display_team_name("A.D. PARLA DE LA SIERRA") == "AD Parla de La Sierra"
    assert display_player_name("APELLIDO01 PRUEBA, JUGADOR01") == "Jugador01 Apellido01 Prueba"
    assert display_player_name("GARCIA DE LA TORRE, JUAN") == "Juan Garcia de La Torre"


def test_normalize_ignores_legal_form_and_accents():
    assert normalize_for_match("C.D. Ejemplo 'B'") == normalize_for_match("Club Deportivo EJEMPLO B")


class MemoryStore:
    def __init__(self):
        self.created, self.aliases, self.reviews, self.links = [], [], [], []
        self.next_id = 100

    def create_team(self, ref):
        self.next_id += 1
        self.created.append((self.next_id, ref.name))
        return self.next_id

    def link_source_code(self, team_id, source_code):
        self.links.append((team_id, source_code))

    def add_alias(self, team_id, alias, normalized, source, confidence):
        self.aliases.append((team_id, normalized))

    def enqueue_review(self, raw_name, source, context, candidates):
        self.reviews.append(raw_name)


def resolver(store, **kw):
    teams = [KnownTeam(1, "C.D. EJEMPLO 'B'", "439"), KnownTeam(2, "Club Ficticio B", None)]
    return TeamResolver(store=store, teams=teams, aliases={}, source="test", **kw)


def test_resolve_by_source_code():
    store = MemoryStore()
    assert resolver(store).resolve(TeamRef(source_code="439", name="lo que sea")).team_id == 1


def test_resolve_fuzzy_links_uncoded_team():
    store = MemoryStore()
    res = resolver(store).resolve(TeamRef(source_code="900", name="C.F. CLUB FICTICIO 'B'"))
    assert res.method == "fuzzy" and res.team_id == 2
    assert store.links == [(2, "900")]


def test_unknown_coded_team_is_created():
    store = MemoryStore()
    res = resolver(store).resolve(TeamRef(source_code="777", name="UNION ZONA NORTE 'A'"))
    assert res.method == "created" and store.created


def test_uncoded_doubtful_team_goes_to_review():
    store = MemoryStore()
    res = resolver(store, auto_threshold=99).resolve(TeamRef(source_code="", name="Ejemplo"))
    assert res.team_id is None and res.method == "review"
    assert store.reviews == ["Ejemplo"]
