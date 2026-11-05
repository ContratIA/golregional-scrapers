"""Adaptador Novanet para la RFFM.

Dos capas:
- `NovanetMapper`: JSON Novanet (partidos, clasificación, goleadores) → modelos propios.
  Es el mismo formato en todas las federaciones con backend Novanet NFG.
- `RffmTransport`: cómo obtener ese JSON en rffm.es (HTML SSR con `__NEXT_DATA__`).
  Otra federación Novanet solo necesita su propio transport + config de categorías.

"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import quote, urlencode, urlsplit
from zoneinfo import ZoneInfo

from selectolax.parser import HTMLParser

from core.http import PoliteClient
from core.models import (
    CompetitionIn,
    MatchIn,
    MatchStatus,
    RoundIn,
    ScorerIn,
    SeasonIn,
    StandingRowIn,
    StandingsIn,
    TeamRef,
)
from core.normalize import display_player_name, slug

MADRID_TZ = ZoneInfo("Europe/Madrid")
ADAPTER_NAME = "novanet_rffm"

# Host que sirve de verdad los binarios de /pnfg/pimg/ (escudos). Su robots.txt es
# `Disallow: /`, así que NO se rastrea: el propio portal público los sirve ya optimizados
# en `/_next/image`, ruta que su robots.txt sí permite.
CREST_BASE_URL = "https://appweb.rffm.es"
CREST_OPTIMIZER_URL = "https://www.rffm.es/_next/image"
CREST_OPTIMIZER_WIDTH = 256


def crest_fetch_url(source_url: str) -> str:
    """URL permitida por robots.txt para el binario del escudo.

    `appweb.rffm.es` prohíbe todo el sitio, así que un escudo alojado allí se pide al
    optimizador de imágenes de `www.rffm.es` (mismo club, misma imagen, ruta permitida y ya
    redimensionada: `w=256` deja margen al WebP propio de 128 px). Una URL de otro host se
    devuelve tal cual.
    """
    if urlsplit(source_url).netloc != urlsplit(CREST_BASE_URL).netloc:
        return source_url
    # `quote_via=quote`: los espacios van como %20, no como `+` (el escudo puede tener espacios).
    params = urlencode({"url": source_url, "w": CREST_OPTIMIZER_WIDTH, "q": 75}, quote_via=quote)
    return f"{CREST_OPTIMIZER_URL}?{params}"


class SelectorEmpty(Exception):
    """La estructura esperada no aparece: la web ha cambiado."""


@dataclass(frozen=True)
class CategoryConfig:
    category_name: str  # NombreCategoria en Novanet
    name: str
    category_slug: str
    tier: int
    segment: str = "senior"
    game_type: str = "f11"
    age_group: str | None = None


# Competiciones senior con calendario propio. Se incluyen la Copa de Aficionados y los
# campeonatos universitarios publicados por la RFFM.
RFFM_SENIOR_CATEGORIES = (
    CategoryConfig("TERCERA FEDERACION", "Tercera Federación", "tercera-federacion", 5),
    CategoryConfig("PRIMERA DIVISION AUTONOMICA AFICIONADO", "Primera Autonómica", "primera-autonomica", 6),
    CategoryConfig("PREFERENTE AFICIONADO", "Preferente", "preferente", 7),
    CategoryConfig("PRIMERA AFICIONADO", "Primera Regional", "primera-regional", 8),
    CategoryConfig("SEGUNDA AFICIONADO", "Segunda Regional", "segunda-regional", 9),
    CategoryConfig("CAMPEONATO UNIVERSITARIO MASCULINO", "Campeonato Universitario Masculino", "campeonato-universitario-masculino", 10),
    CategoryConfig("CAMPEONATO UNIVERSITARIO FEMENINO", "Campeonato Universitario Femenino", "campeonato-universitario-femenino", 10, "femenino"),
)

RFFM_CUP_CATEGORY = CategoryConfig(
    "COPA DE AFICIONADOS RFFM 2026/2027",
    "Copa de Aficionados RFFM",
    "copa-aficionados-rffm",
    10,
)

# Fútbol femenino absoluto (decisión 2026-10-31). Las categorías femeninas de base (cadete, juvenil,
# infantil…) siguen fuera: no están aquí y además las rechaza `_YOUTH_MARKERS`.
RFFM_FEMENINO_CATEGORIES = (
    CategoryConfig("TERCERA FEDERACION DE FUTBOL FEMENINO", "Tercera Federación Femenino", "tercera-federacion-femenino", 5, "femenino"),
    CategoryConfig("PRIMERA DIVISION AUTONOMICA FEMENINO", "Primera Autonómica Femenino", "primera-autonomica-femenino", 6, "femenino"),
    CategoryConfig("PREFERENTE FUTBOL FEMENINO", "Preferente Femenino", "preferente-femenino", 7, "femenino"),
    CategoryConfig("PRIMERA FUTBOL FEMENINO", "Primera Femenino", "primera-femenino", 8, "femenino"),
    CategoryConfig("PRIMERA FEMENINO SENIOR F-7", "Primera Femenino Senior F7", "primera-femenino-senior-f7", 8, "femenino", "f7"),
)

# Modalidad de juego de la RFFM (`tipojuego`). El fútbol sala (3) no entra: es otro deporte.
GAME_TYPE_CODE = {"f11": "1", "f7": "2", "f5": "4"}
GAME_TYPE_BY_CODE = {code: game for game, code in GAME_TYPE_CODE.items()}
# Orden de consulta de `/api/competitions` para las competiciones adultas y para las de base.
ADULT_GAME_CODES = ("1", "2")
BASE_GAME_CODES = ("1", "2", "4")

# Segmentos de menores y de adultos: deben coincidir con `is_minor_segment` en la base de datos.
MINOR_SEGMENTS = ("juvenil", "base", "femenino-base")
ADULT_SEGMENTS = ("senior", "femenino")
BASE_SEGMENTS = ("base", "femenino-base")

# Grupo de edad según el nombre de la competición. PREBENJAM antes que BENJAM (lo contiene).
_BASE_AGES = (
    ("PREBENJAM", "prebenjamin"),
    ("BENJAM", "benjamin"),
    ("ALEV", "alevin"),
    ("INFANTIL", "infantil"),
    ("CADETE", "cadete"),
    ("JUVENIL", "juvenil"),
    ("DEBUTANTE", "debutante"),
    ("VETERANO", "veteranos"),
)
# Fuera de alcance: copas y eliminatorias, fútbol inclusivo y andando, campeonatos universitarios.
_BASE_EXCLUDE = re.compile(r"COPA|INCLUSIV|ANDANDO|UNIVERSITARI|SALA|FUTSAL")
# Nivel de la liga (menor = más alto), de la palabra más específica a la más genérica.
_BASE_TIERS = (
    ("SUPERLIGA", 1),
    ("LIGA NACIONAL", 1),
    ("NACIONAL", 1),
    ("DIVISION DE HONOR", 2),
    ("PRIMERA DIVISION AUTONOMICA", 3),
    ("PREFERENTE", 4),
    ("SEGUNDA", 6),
    ("PRIMERA", 5),
)
# Palabras de la RFFM sin tilde (la API mezcla ambas formas) y su forma correcta para mostrar.
_ACCENTS = {
    "DIVISION": "División", "AUTONOMICA": "Autonómica", "ALEVIN": "Alevín", "BENJAMIN": "Benjamín",
    "PREBENJAMIN": "Prebenjamín", "FEDERACION": "Federación", "FUTBOL": "Fútbol", "INICIACION": "Iniciación",
    "SENIOR": "Sénior", "ESCOLAR": "Escolar",
}
_LOWER_WORDS = {"de", "del", "la", "el", "y"}

# Segunda red de seguridad: aunque una categoría de base se colara en la config, se rechaza.
_YOUTH_MARKERS = re.compile(r"JUVENIL|CADETE|INFANTIL|ALEV|BENJAM|PREBENJAM|BASE|SUB[- ]?\d", re.I)


def _fold(text: str) -> str:
    """Mayúsculas sin acentos: la RFFM mezcla 'FÚTBOL'/'FUTBOL' y 'AUTONÓMICA'/'AUTONOMICA' entre campos."""
    return "".join(c for c in unicodedata.normalize("NFD", text.upper()) if unicodedata.category(c) != "Mn")


def _pretty_name(text: str) -> str:
    """'PRIMERA DIVISION AUTONOMICA ALEV.F-7' -> 'Primera División Autonómica Alevín F7'."""
    text = re.sub(r"ALEV\.?-?\s*F-?7", "ALEVIN F7", text, flags=re.I)
    text = re.sub(r"\bF[-.]?(\d)\b", r"F\1", text, flags=re.I)
    words = []
    for word in re.sub(r"\s+", " ", text).strip().split(" "):
        low = word.lower()
        if low in _LOWER_WORDS:
            words.append(low)
        elif re.fullmatch(r"f\d", low):
            words.append(low.upper())
        else:
            words.append(_ACCENTS.get(_fold(word)) or word.capitalize())
    if words:
        words[0] = words[0][:1].upper() + words[0][1:]
    return " ".join(words)


def is_youth(text: str) -> bool:
    return bool(_YOUTH_MARKERS.search(text or ""))


def _int(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(value) if value.lstrip("-").isdigit() else None


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def _flag(value: str | None) -> bool:
    return (value or "").strip() == "1"


class NovanetMapper:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def crest(self, path: str | None) -> str | None:
        """URL absoluta del escudo.

        Cualquier forma del payload (`/pnfg/pimg/...`, `pimg/...` o una URL absoluta en
        `www.rffm.es`) se normaliza al host que sirve de verdad el binario. Desde 2026-09
        `www.rffm.es` responde a esa ruta con la app Next.js en HTML, mientras que
        `appweb.rffm.es` devuelve la imagen; el `?nova=1` que trae el payload se conserva.
        """
        if not path:
            return None
        if "pimg/" in path:
            return f"{CREST_BASE_URL}/pnfg/pimg/{path.split('pimg/', 1)[1]}"
        return path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"

    def season(self, raw: dict) -> SeasonIn:
        name = raw["nombre"]
        start, end = name.split("-")
        return SeasonIn(
            source_code=raw["cod_temporada"],
            name=name,
            slug=f"{start}-{end[-2:]}",
            starts_on=date.fromisoformat(raw["fecha_inicio"]),
            ends_on=date.fromisoformat(raw["fecha_fin"]),
        )

    def senior_category(self, competition: dict) -> CategoryConfig | None:
        label = _fold(f"{competition.get('nombre', '')} {competition.get('NombreCategoria', '')}")
        kind = str(competition.get("tipo_competicion") or "")
        # La RFFM publica la Copa de Aficionados como tipo 1; no abrir el resto de eliminatorias.
        if kind == "1":
            if "COPA DE AFICIONADOS RFFM" in label:
                return RFFM_CUP_CATEGORY
            return None
        if kind != "2":
            return None
        # La Copa RFEF fase autonómica viene como tipo 2 bajo la categoría Tercera Federación.
        if is_youth(label) or "COPA" in label:
            return None
        category = _fold((competition.get("NombreCategoria") or "").strip())
        return next((c for c in (*RFFM_SENIOR_CATEGORIES, *RFFM_FEMENINO_CATEGORIES) if _fold(c.category_name) == category), None)

    def base_category(self, competition: dict) -> CategoryConfig | None:
        """Competiciones de base (prebenjamín a juvenil, masculino y femenino) y veteranos, solo a nivel de equipo.

        Se clasifica por el texto `nombre + NombreCategoria`; el nombre y el slug salen de `nombre`, que distingue
        fases (p. ej. los dos PREBENJAMIN: Desarrollo e Iniciación) que comparten categoría.
        """
        if competition.get("tipo_competicion") != "2":  # copas y eliminatorias fuera
            return None
        label = _fold(f"{competition.get('nombre', '')} {competition.get('NombreCategoria', '')}")
        if _BASE_EXCLUDE.search(label):
            return None
        age = next((group for token, group in _BASE_AGES if token in label), None)
        game_type = GAME_TYPE_BY_CODE.get(str(competition.get("codigo_tipo_juego")))
        if age is None or game_type is None:
            return None
        name = _pretty_name((competition.get("nombre") or competition.get("NombreCategoria") or "").strip())
        category_slug = slug(name)
        if game_type != "f11" and game_type not in category_slug:
            category_slug = f"{category_slug}-{game_type}"
        tier = next((tier for token, tier in _BASE_TIERS if token in label), 7)
        segment = "femenino-base" if "FEMENIN" in label else "base"
        return CategoryConfig(name, name, category_slug, tier, segment, game_type, age)

    def category_for(self, competition: dict, scope: str) -> CategoryConfig | None:
        """`scope='adult'`: senior y femenino absoluto. `scope='base'`: menores y veteranos."""
        return self.senior_category(competition) if scope == "adult" else self.base_category(competition)

    def competition(self, competition: dict, group: dict, category: CategoryConfig, source_url: str) -> CompetitionIn:
        group_name = re.sub(r"\s+", " ", group["nombre"]).strip().title()
        starts_on = _date(competition.get("FechaInicio"))
        # La RFFM deja `Activa=0` en categorías (p. ej. Segunda Aficionado) aunque su calendario ya
        # esté en marcha: una competición cuya fecha de inicio ya pasó cuenta como activa, si no
        # `live`/`daily` nunca la actualizan y se quedan sin resultados ni clasificación.
        started = starts_on is not None and starts_on <= datetime.now(MADRID_TZ).date()
        return CompetitionIn(
            source_code=competition["codigo"],
            group_source_code=group["codigo"],
            name=category.name,
            category_slug=category.category_slug,
            tier=category.tier,
            group_name=group_name,
            group_slug=slug(group_name),
            total_rounds=_int(group.get("total_jornadas")),
            total_teams=_int(group.get("total_equipos")),
            is_active=_flag(competition.get("Activa")) or started,
            starts_on=starts_on,
            source_url=source_url,
            segment=category.segment,
            is_senior=category.segment in ADULT_SEGMENTS,
            game_type=category.game_type,
            age_group=category.age_group,
            # Menores: nunca goleadores, aunque la RFFM los publique.
            has_scorers=category.segment not in MINOR_SEGMENTS
            and (competition.get("goleadores") or "1").strip() != "0"
            and (group.get("clasificacion_goleadores") or "1").strip() != "0",
        )

    def rounds(self, results: dict) -> list[RoundIn]:
        listing = results.get("listado_jornadas") or []
        jornadas = listing[0].get("jornadas", []) if listing else []
        out = []
        for j in jornadas:
            raw_date = (j.get("fecha_jornada") or "").strip()
            parsed = datetime.strptime(raw_date, "%d-%m-%Y").date() if raw_date else None
            out.append(RoundIn(number=int(j["codjornada"]), date=parsed))
        return out

    def _status(self, p: dict, has_score: bool, kickoff: datetime | None, now: datetime) -> MatchStatus:
        notes = (p.get("observaciones") or "").upper() + " " + (p.get("motivo_estado") or "").upper()
        if _flag(p.get("partido_en_juego")):
            return MatchStatus.live
        if "SUSPENDID" in notes:
            return MatchStatus.suspended
        if "APLAZAD" in notes:
            return MatchStatus.postponed
        if "ANULAD" in notes or "CANCELAD" in notes:
            return MatchStatus.cancelled
        if has_score and (_flag(p.get("acta_cerrada")) or (kickoff and kickoff <= now)):
            return MatchStatus.finished
        return MatchStatus.scheduled

    def matches(self, results: dict, season_code: str, now: datetime | None = None) -> list[MatchIn]:
        now = now or datetime.now(MADRID_TZ)
        round_number = int(results["jornada"])
        out = []
        for p in results.get("partidos") or []:
            kickoff = None
            if p.get("fecha"):
                hour = (p.get("hora") or "").strip() or "00:00"
                kickoff = datetime.strptime(f"{p['fecha']} {hour}", "%d/%m/%Y %H:%M").replace(tzinfo=MADRID_TZ)
            home_goals, away_goals = _int(p.get("Goles_casa")), _int(p.get("Goles_visitante"))
            has_score = home_goals is not None and away_goals is not None
            status = self._status(p, has_score, kickoff, now)
            if not has_score and status in (MatchStatus.finished, MatchStatus.live):
                status = MatchStatus.scheduled
            notes = [n for n in ((p.get("observaciones") or "").strip(),) if n]
            if _flag(p.get("Retirado_local")) or _flag(p.get("Retirado_visitante")):
                notes.append("Equipo retirado")
            query = urlencode({"temporada": season_code, "competicion": results["codigo_competicion"], "grupo": results["codigo_grupo"]})
            out.append(
                MatchIn(
                    source_code=p["codacta"],
                    round=round_number,
                    kickoff_at=kickoff,
                    venue=(p.get("campojuego") or "").strip() or None,
                    home=TeamRef(source_code=p["CodEquipo_local"], name=p["Nombre_equipo_local"].strip(), crest_url=self.crest(p.get("url_img_local"))),
                    away=TeamRef(source_code=p["CodEquipo_visitante"], name=p["Nombre_equipo_visitante"].strip(), crest_url=self.crest(p.get("url_img_visitante"))),
                    home_goals=home_goals if has_score else None,
                    away_goals=away_goals if has_score else None,
                    status=status,
                    is_result_official=has_score and _flag(p.get("acta_cerrada")) and not _flag(p.get("ResultadoProvisional")),
                    official_url=f"{self.base_url}/acta-partido/{p['codacta']}?{query}",
                    notes="; ".join(notes) or None,
                )
            )
        return out

    def standings(self, standings: dict) -> StandingsIn:
        rows = []
        for r in standings.get("clasificacion") or []:
            form = "".join((x.get("tipo") or "")[:1] for x in (r.get("racha_partidos") or [])) or None
            rows.append(
                StandingRowIn(
                    team=TeamRef(source_code=r["codequipo"], name=r["nombre"].strip(), crest_url=self.crest(r.get("url_img"))),
                    position=int(r["posicion"]),
                    played=int(r["jugados"]),
                    won=int(r["ganados"]),
                    drawn=int(r["empatados"]),
                    lost=int(r["perdidos"]),
                    goals_for=int(r["goles_a_favor"]),
                    goals_against=int(r["goles_en_contra"]),
                    points=int(r["puntos"]),
                    points_penalty=_int(r.get("puntos_sancion")) or 0,
                    form=form,
                )
            )
        return StandingsIn(round=_int(standings.get("jornada")) or 0, rows=rows)

    def scorers(self, scorers: dict) -> list[ScorerIn]:
        return [
            ScorerIn(
                player_source_code=s["codigo_jugador"],
                full_name=display_player_name(s["jugador"]),
                team=TeamRef(source_code=s["codigo_equipo"], name=s["nombre_equipo"].strip()),
                matches_played=_int(s.get("partidos_jugados")) or 0,
                goals=_int(s.get("goles")) or 0,
                penalty_goals=_int(s.get("goles_penalti")) or 0,
            )
            for s in scorers.get("goles") or []
        ]


def extract_next_data(html: str) -> dict:
    node = HTMLParser(html).css_first("script#__NEXT_DATA__")
    if node is None:
        raise SelectorEmpty("script#__NEXT_DATA__ no encontrado")
    try:
        return json.loads(node.text())["props"]["pageProps"]
    except (json.JSONDecodeError, KeyError) as exc:
        raise SelectorEmpty(f"__NEXT_DATA__ ilegible: {exc}") from exc


@dataclass
class RffmTransport:
    client: PoliteClient
    base_url: str = "https://www.rffm.es"
    game_type: str = "1"  # Fútbol-11

    def _page(self, page: str, key: str, game_type: str | None = None, **params: str) -> tuple[dict, bool]:
        url = f"{self.base_url}/competicion/{page}?{urlencode({**params, 'tipojuego': game_type or self.game_type})}"
        # El backend Novanet devuelve a veces `null` puntual para una página que sí existe
        # (visto 2026-09-15 en Preferente G1 J3): un reintento evita falsas alertas.
        for attempt in range(2):
            fetched = self.client.get(url)
            data = extract_next_data(fetched.text).get(key)
            if isinstance(data, dict):
                return data, fetched.changed
        raise SelectorEmpty(f"{page}: pageProps.{key} vacío tras reintento ({url})")

    def page_url(self, page: str, game_type: str | None = None, **params: str) -> str:
        return f"{self.base_url}/competicion/{page}?{urlencode({**params, 'tipojuego': game_type or self.game_type})}"

    def crest_image(self, url: str) -> bytes:
        """Binario del escudo por la única ruta que permite robots.txt.

        `/pnfg/pimg/` solo existe en `appweb.rffm.es`, con `Disallow: /`: no se rastrea. Se pide
        al optimizador de `www.rffm.es` (permitido). Si responde HTML (WAF/aviso), `to_webp` lo
        detecta y ese escudo se salta sin tumbar la pasada.
        """
        content, _ = self.client.get_bytes(crest_fetch_url(url))
        return content

    def seasons(self) -> list[dict]:
        return self.client.get_json(f"{self.base_url}/api/seasons")

    def competitions(self, season: str, game_type: str | None = None) -> list[dict]:
        return self.client.get_json(
            f"{self.base_url}/api/competitions?{urlencode({'temporada': season, 'tipojuego': game_type or self.game_type})}"
        )

    def groups(self, season: str, competition: str, game_type: str | None = None) -> list[dict]:
        return self.client.get_json(
            f"{self.base_url}/api/groups?{urlencode({'temporada': season, 'competicion': competition, 'tipojuego': game_type or self.game_type})}"
        )

    def results(self, season: str, competition: str, group: str, round_number: int | None = None, game_type: str | None = None) -> tuple[dict, bool]:
        params = {"temporada": season, "competicion": competition, "grupo": group}
        if round_number is not None:
            params["jornada"] = str(round_number)
        data, changed = self._page("resultados-y-jornadas", "results", game_type, **params)
        if "partidos" not in data:
            raise SelectorEmpty("results.partidos ausente")
        return data, changed

    def standings(self, season: str, competition: str, group: str, round_number: int | None = None, game_type: str | None = None) -> tuple[dict, bool]:
        params = {"temporada": season, "competicion": competition, "grupo": group}
        if round_number is not None:
            params["jornada"] = str(round_number)
        data, changed = self._page("clasificaciones", "standings", game_type, **params)
        if "clasificacion" not in data:
            raise SelectorEmpty("standings.clasificacion ausente")
        return data, changed

    def scorers(self, season: str, competition: str, group: str, game_type: str | None = None) -> tuple[dict, bool]:
        data, changed = self._page("goleadores", "scorers", game_type, temporada=season, competicion=competition, grupo=group)
        if "goles" not in data:
            raise SelectorEmpty("scorers.goles ausente")
        return data, changed
