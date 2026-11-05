"""Orquestación de la ingesta: federación → temporada → competiciones → partidos,
clasificaciones y goleadores, con upsert idempotente y detección de anomalías."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import httpx
from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from adapters.novanet_rffm import (
    ADAPTER_NAME,
    ADULT_GAME_CODES,
    ADULT_SEGMENTS,
    BASE_GAME_CODES,
    BASE_SEGMENTS,
    GAME_TYPE_CODE,
    MADRID_TZ,
    MINOR_SEGMENTS,
    NovanetMapper,
    RffmTransport,
    SelectorEmpty,
)
from core.alerts import Anomaly, report, volume_drop
from core.colors import dominant_colors
from core.db import Supabase
from core.http import RobotsDisallowed
from core.models import CompetitionIn, MatchIn, TeamRef
from core.normalize import display_team_name, slug
from core.resolver import KnownTeam, TeamResolver

log = logging.getLogger(__name__)


def _sin_datos(exc: ValidationError) -> str:
    """Mensaje de validación sin los valores de entrada (pueden contener nombres)."""
    return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors(include_input=False, include_context=False))

FEDERATION = {
    "slug": "madrid",
    "name": "Real Federación de Fútbol de Madrid",
    "short_name": "RFFM",
    "platform": "novanet",
    "base_url": "https://www.rffm.es",
}


COMPETITION_COLUMNS = "id,source_code,group_source_code,category_slug,group_slug,total_rounds,total_teams,is_active,segment,has_scorers,game_type,tier"

# Equipos de estas competiciones se resuelven solo por código de federación (ver TeamResolver.resolve):
# comparten nombre con el equipo masculino o senior de su club y un alias/fuzzy los fundiría.
ISOLATED_SEGMENTS = ("femenino", *MINOR_SEGMENTS)

# Jobs de base: ventanas de datos en la BD (partidos sin cerrar) en vez de recorrer todos los grupos.
BASE_CLOSING_LOOKBACK = timedelta(hours=72)
BASE_CLOSING_SETTLE = timedelta(hours=2)
BASE_LIVE_BEFORE = timedelta(hours=3)
BASE_LIVE_AFTER = timedelta(minutes=10)


def _in_ids(competitions: list[dict]) -> str:
    """Filtro PostgREST `in.(…)` por competiciones; vacío -> ninguna (id imposible)."""
    return f"in.({','.join(str(c['id']) for c in competitions) or '0'})"


def game_code(competition: dict) -> str:
    """Código `tipojuego` de la RFFM de una fila de `competitions` (f11 por defecto)."""
    return GAME_TYPE_CODE.get(competition.get("game_type") or "f11", "1")
CREST_BUCKET = "crests"
CREST_SIZE = 128
_TEAM_LETTER = re.compile(r"\s+[A-Z]$")


class NotAnImage(Exception):
    """El origen devolvió algo que no es una imagen (WAF, redirección a HTML, aviso…)."""


def club_name(team_name: str) -> str:
    """'CD Leganes SAD B' -> 'CD Leganes SAD': los equipos A/B/C de un club comparten escudo."""
    return _TEAM_LETTER.sub("", team_name).strip()


def to_webp(content: bytes) -> bytes:
    """Escudo normalizado: máximo 128 px, WebP con transparencia (la tira de 18 escudos pesa poco)."""
    try:
        with Image.open(io.BytesIO(content)) as image:
            image = image.convert("RGBA")
            image.thumbnail((CREST_SIZE, CREST_SIZE))
            out = io.BytesIO()
            image.save(out, format="WEBP", quality=85, method=6)
            return out.getvalue()
    except UnidentifiedImageError as exc:
        raise NotAnImage(f"contenido de {len(content)} bytes que no se puede decodificar como imagen") from exc


def public_notes(notes: str | None) -> str | None:
    """En menores solo se conserva el aviso generado por nosotros, nunca el texto libre de la RFFM."""
    return "Equipo retirado" if notes and "Equipo retirado" in notes else None


def unique_slugs(rows: list[CompetitionIn]) -> list[CompetitionIn]:
    """Dos competiciones con el mismo nombre y grupos homónimos chocarían en `(temporada, categoría, grupo)`:
    la segunda lleva el código de la RFFM como sufijo. Estable entre pasadas porque el orden de la API lo es."""
    seen: set[tuple[str, str]] = set()
    out = []
    for row in rows:
        if (row.category_slug, row.group_slug) in seen:
            row = row.model_copy(update={"category_slug": f"{row.category_slug}-{row.source_code}"})
        seen.add((row.category_slug, row.group_slug))
        out.append(row)
    return out


def match_hash(match: MatchIn) -> str:
    return hashlib.sha256(match.model_dump_json().encode()).hexdigest()[:32]


class DbTeamStore:
    def __init__(self, db: Supabase, federation_id: int):
        self.db = db
        self.federation_id = federation_id

    def create_team(self, ref: TeamRef) -> int:
        name = display_team_name(ref.name)
        base = slug(name)
        for candidate in (base, f"{base}-{ref.source_code}"):
            try:
                row = self.db.insert(
                    "teams",
                    {"federation_id": self.federation_id, "source_code": ref.source_code or None, "slug": candidate, "name": name},
                    select="id",
                )
                return row[0]["id"]
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 409:
                    raise
        if ref.source_code:
            # El equipo ya existe con ese código (lo creó otra pasada tras cargar la lista de equipos): se reutiliza.
            existing = self.db.select("teams", select="id", federation_id=f"eq.{self.federation_id}", source_code=f"eq.{ref.source_code}")
            if existing:
                return existing[0]["id"]
        raise RuntimeError(f"slug de equipo imposible: {base}")

    def link_source_code(self, team_id: int, source_code: str) -> None:
        self.db.update("teams", {"source_code": source_code}, id=f"eq.{team_id}", source_code="is.null")

    def add_alias(self, team_id: int, alias: str, normalized: str, source: str, confidence: float) -> None:
        self.db.insert(
            "team_aliases",
            {"team_id": team_id, "alias": alias, "alias_normalized": normalized, "source": source, "confidence": confidence},
            select="id",
            ignore_duplicates=True,
        )

    def enqueue_review(self, raw_name: str, source: str, context: dict, candidates: list[dict]) -> None:
        self.db.insert(
            "entity_review_queue",
            {"entity_type": "team", "raw_name": raw_name, "source": source, "context": context, "candidates": candidates},
            select="id",
            ignore_duplicates=True,
        )


def is_placeholder_time(iso: str) -> bool:
    """La RFFM publica 00:00 cuando aún no hay hora: no es un saque real."""
    kickoff = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(MADRID_TZ)
    return kickoff.hour == 0 and kickoff.minute == 0


@dataclass
class RunStats:
    job: str
    requests_before: int
    matches: int = 0
    standings: int = 0
    crests: int = 0
    anomalies: list[Anomaly] = field(default_factory=list)


def run_job(pipeline: RffmPipeline, job: str, stats: RunStats) -> None:
    """Un fallo fuera de las competiciones (temporadas, lista de grupos, robots.txt) queda
    registrado como anomalía en vez de tumbar el proceso sin marcar el adaptador."""
    try:
        getattr(pipeline, f"job_{job}")(stats)
    except SelectorEmpty as exc:
        stats.anomalies.append(Anomaly("selector_empty", str(exc)[:300], {"job": job}))
    except NotAnImage as exc:
        stats.anomalies.append(Anomaly("not_an_image", str(exc)[:300], {"job": job}))
    except RobotsDisallowed as exc:
        stats.anomalies.append(Anomaly("robots", f"robots.txt prohíbe la ruta: {exc}"[:300], {"job": job}))
    except httpx.HTTPError as exc:
        stats.anomalies.append(Anomaly("http", f"{type(exc).__name__}: {exc}"[:300], {"job": job}))


@dataclass
class RffmPipeline:
    db: Supabase
    transport: RffmTransport
    today: date = field(default_factory=lambda: datetime.now(MADRID_TZ).date())
    # Tope de peticiones de esta ejecución (0 = sin tope). Los jobs largos de base lo respetan y terminan limpios.
    max_requests: int = 0
    # Tope de duración en segundos (0 = sin tope): la RFFM ha llegado a tardar ~18 s por página y el tope por peticiones no acota el tiempo.
    max_seconds: float = 0

    def now(self) -> datetime:
        return datetime.now(MADRID_TZ)

    def over_budget(self) -> bool:
        made = getattr(getattr(self.transport, "client", None), "requests_made", None)
        if self.max_requests and made is not None and made - self._requests_at_start >= self.max_requests:
            return True
        return bool(self.max_seconds) and time.monotonic() - self._started >= self.max_seconds

    def __post_init__(self) -> None:
        self.mapper = NovanetMapper(self.transport.base_url)
        self._requests_at_start = getattr(getattr(self.transport, "client", None), "requests_made", 0)
        self._started = time.monotonic()
        if self.max_requests and not hasattr(getattr(self.transport, "client", None), "requests_made"):
            raise ValueError("max_requests exige un transporte con contador de peticiones")
        self.federation_id: int | None = None
        self.season: dict | None = None
        self._resolver: TeamResolver | None = None

    # ───────────── estructura ─────────────

    def ensure_federation(self) -> int:
        if self.federation_id is None:
            self.federation_id = self.db.upsert("federations", FEDERATION, on_conflict="slug", select="id")[0]["id"]
        return self.federation_id

    def sync_season(self) -> dict:
        federation_id = self.ensure_federation()
        raw = [
            s for s in self.transport.seasons()
            if date.fromisoformat(s["fecha_inicio"]) <= self.today <= date.fromisoformat(s["fecha_fin"])
        ]
        if not raw:
            raise SelectorEmpty("no hay temporada vigente en /api/seasons")
        current = self.mapper.season(max(raw, key=lambda s: int(s["cod_temporada"])))
        self.db.update("seasons", {"is_current": False}, federation_id=f"eq.{federation_id}", source_code=f"neq.{current.source_code}")
        row = self.db.upsert(
            "seasons",
            {**current.model_dump(mode="json"), "federation_id": federation_id, "is_current": True},
            on_conflict="federation_id,source_code",
            select="id,source_code,slug",
        )[0]
        self.season = row
        return row

    def sync_competitions(self, scope: str = "adult") -> list[dict]:
        """`scope='adult'`: senior y femenino absoluto. `scope='base'`: menores y veteranos (solo equipos)."""
        season = self.season or self.sync_season()
        rows: list[CompetitionIn] = []
        for code in ADULT_GAME_CODES if scope == "adult" else BASE_GAME_CODES:
            for competition in self.transport.competitions(season["source_code"], game_type=code):
                if str(competition.get("codigo_tipo_juego") or code) != code:
                    continue  # la RFFM mezcló modalidades en este listado: no se guarda con un game_type que no se pidió
                category = self.mapper.category_for(competition, scope)
                if category is None:
                    continue
                groups = self.transport.groups(season["source_code"], competition["codigo"], game_type=code)
                if not groups:
                    if scope == "adult":
                        raise SelectorEmpty(f"competición {competition['codigo']} sin grupos")
                    continue  # una competición de base sin grupos aún (fase sin sorteo) no tumba la estructura
                for group in groups:
                    url = self.transport.page_url(
                        "clasificaciones",
                        game_type=code,
                        temporada=season["source_code"],
                        competicion=competition["codigo"],
                        grupo=group["codigo"],
                    )
                    rows.append(self.mapper.competition(competition, group, category, url))
        if not rows:
            raise SelectorEmpty(f"ninguna competición {scope} encontrada")
        return self.db.upsert(
            "competitions",
            [{**r.model_dump(mode="json"), "season_id": season["id"]} for r in unique_slugs(rows)],
            on_conflict="season_id,source_code,group_source_code",
            select=COMPETITION_COLUMNS,
        )

    def competitions(self, only_active: bool = True, segments: tuple[str, ...] = ADULT_SEGMENTS) -> list[dict]:
        """Por defecto solo senior y femenino absoluto: los jobs de siempre no tocan la base."""
        season = self.season or self.sync_season()
        params = {"season_id": f"eq.{season['id']}", "select": COMPETITION_COLUMNS, "segment": f"in.({','.join(segments)})"}
        if only_active:
            params["is_active"] = "eq.true"
        return self.db.select("competitions", **params)

    # ───────────── equipos ─────────────

    def resolver(self) -> TeamResolver:
        if self._resolver is None:
            federation_id = self.ensure_federation()
            teams = self.db.select("teams", federation_id=f"eq.{federation_id}", select="id,name,source_code")
            aliases = self.db.select("team_aliases", select="team_id,alias_normalized")
            self._resolver = TeamResolver(
                store=DbTeamStore(self.db, federation_id),
                teams=[KnownTeam(t["id"], t["name"], t["source_code"]) for t in teams],
                aliases={a["alias_normalized"]: a["team_id"] for a in aliases},
                source=ADAPTER_NAME,
            )
        return self._resolver

    def _team_id(self, ref: TeamRef, context: dict) -> int:
        resolution = self.resolver().resolve(ref, context, isolated=context.get("segment") in ISOLATED_SEGMENTS)
        if resolution.team_id is None:
            raise ValueError(f"equipo sin resolver: {ref.name}")
        return resolution.team_id

    # ───────────── partidos ─────────────

    def sync_round(self, competition: dict, round_number: int | None, stats: RunStats) -> int | None:
        season = self.season or self.sync_season()
        context = {"competition_id": competition["id"], "round": round_number, "segment": competition.get("segment")}
        results, _ = self.transport.results(
            season["source_code"], competition["source_code"], competition["group_source_code"], round_number, game_type=game_code(competition)
        )
        try:
            matches = self.mapper.matches(results, season["source_code"])
        except ValidationError as exc:
            stats.anomalies.append(Anomaly("validation", _sin_datos(exc)[:500], context))
            return None
        current_round = int(results["jornada"])
        context["round"] = current_round

        expected = (competition.get("total_teams") or 0) // 2
        if not matches and expected:
            stats.anomalies.append(Anomaly("selector_empty", "jornada sin partidos", context))
            return current_round
        previous = self.db.select(
            "matches", competition_id=f"eq.{competition['id']}", round=f"eq.{current_round - 1}", select="id"
        ) if current_round > 1 else []
        if anomaly := volume_drop(len(matches), len(previous) or None, context):
            stats.anomalies.append(anomaly)

        minor = competition.get("segment") in MINOR_SEGMENTS
        existing = {
            m["source_code"]: m
            for m in self.db.select(
                "matches",
                competition_id=f"eq.{competition['id']}",
                round=f"eq.{current_round}",
                select="id,source_code,source_hash,home_goals,away_goals,data_source,status",
            )
        }
        rows = []
        for match in matches:
            digest = match_hash(match)
            prior = existing.get(match.source_code)
            if prior and prior["source_hash"] == digest:
                continue
            home_id = self._team_id(match.home, context)
            away_id = self._team_id(match.away, context)
            row = {
                "competition_id": competition["id"],
                "round": match.round,
                "source_code": match.source_code,
                "slug": self._match_slug(match),
                "kickoff_at": match.kickoff_at.isoformat() if match.kickoff_at else None,
                "venue": match.venue,
                "home_team_id": home_id,
                "away_team_id": away_id,
                "home_goals": match.home_goals,
                "away_goals": match.away_goals,
                "status": match.status.value,
                "is_result_official": match.is_result_official,
                "data_source": "official",
                "source_hash": digest,
                # Menores: sin enlace al acta (en la RFFM lista a los jugadores) y sin texto libre de la RFFM.
                "official_url": None if minor else match.official_url,
                "notes": public_notes(match.notes) if minor else match.notes,
            }
            # El dato oficial siempre gana, pero un "sin marcador" oficial no borra un
            # resultado aportado por club/comunidad mientras el acta no esté cerrada.
            if prior and match.home_goals is None and prior["data_source"] != "official" and prior["home_goals"] is not None:
                row.update(home_goals=prior["home_goals"], away_goals=prior["away_goals"], data_source=prior["data_source"], status=prior.get("status") or "finished")
            rows.append(row)
        self._unique_match_slugs(rows)
        self.db.upsert("matches", rows, on_conflict="competition_id,source_code", select="id")
        stats.matches += len(rows)
        return current_round

    def _unique_match_slugs(self, rows: list[dict]) -> None:
        """`matches.slug` es único en toda la tabla. En base, el mismo "CD X A - CD Y B" se juega el mismo día en varias
        categorías y daba 409 Conflict (jornadas enteras sin guardar). El primero que llega conserva su slug; el resto
        lleva el id de su competición (y, si aún choca dentro de la misma tanda, el código de la fuente)."""
        if not rows:
            return
        owners: dict[str, tuple] = {}
        wanted = sorted({s for r in rows for s in (r["slug"], f"{r['slug']}-{r['competition_id']}")})
        for i in range(0, len(wanted), 50):
            chunk = ",".join(wanted[i : i + 50])
            for m in self.db.select("matches", slug=f"in.({chunk})", select="slug,competition_id,source_code"):
                owners[m["slug"]] = (m["competition_id"], m["source_code"])
        taken: set[str] = set()
        for row in rows:
            key = (row["competition_id"], row["source_code"])
            candidate = row["slug"]
            if owners.get(candidate, key) != key or candidate in taken:
                candidate = f"{row['slug']}-{row['competition_id']}"
                if owners.get(candidate, key) != key or candidate in taken:
                    candidate = f"{candidate}-{row['source_code']}"
            row["slug"] = candidate
            taken.add(candidate)

    def _match_slug(self, match: MatchIn) -> str:
        day = match.kickoff_at.date().isoformat() if match.kickoff_at else f"jornada-{match.round}"
        return f"{slug(display_team_name(match.home.name))}-{slug(display_team_name(match.away.name))}-{day}"

    def sync_standings(self, competition: dict, round_number: int | None, stats: RunStats) -> None:
        if competition.get("category_slug") == "copa-aficionados-rffm":
            return  # La Copa se disputa por eliminatorias y no tiene clasificación de liga.
        season = self.season or self.sync_season()
        context = {"competition_id": competition["id"], "round": round_number, "segment": competition.get("segment")}
        raw, _ = self.transport.standings(
            season["source_code"], competition["source_code"], competition["group_source_code"], round_number, game_type=game_code(competition)
        )
        try:
            table = self.mapper.standings(raw)
        except ValidationError as exc:
            stats.anomalies.append(Anomaly("validation", _sin_datos(exc)[:500], context))
            return
        if table.round == 0:
            return  # temporada sin empezar
        expected = competition.get("total_teams") or 0
        if anomaly := volume_drop(len(table.rows), expected or None, {**context, "what": "standings"}):
            stats.anomalies.append(anomaly)
        rows = [
            {
                "competition_id": competition["id"],
                "round": table.round,
                "team_id": self._team_id(r.team, context),
                **r.model_dump(exclude={"team"}),
                "captured_at": datetime.now(MADRID_TZ).isoformat(),
            }
            for r in table.rows
        ]
        self.db.upsert("standings", rows, on_conflict="competition_id,round,team_id", select="team_id")
        stats.standings += len(rows)

    def sync_scorers(self, competition: dict, stats: RunStats) -> None:
        if not competition.get("has_scorers", True) or competition.get("segment") in MINOR_SEGMENTS:
            return  # la federación no publica goleadores/as en este grupo, o es una competición de menores
        season = self.season or self.sync_season()
        federation_id = self.ensure_federation()
        context = {"competition_id": competition["id"], "what": "scorers", "segment": competition.get("segment")}
        raw, changed = self.transport.scorers(
            season["source_code"], competition["source_code"], competition["group_source_code"], game_type=game_code(competition)
        )
        if not changed:
            return
        scorers = self.mapper.scorers(raw)
        for scorer in scorers:
            team_id = self._team_id(scorer.team, context)
            player = self.db.upsert(
                "players",
                {
                    "federation_id": federation_id,
                    "source_code": scorer.player_source_code,
                    "slug": f"{slug(scorer.full_name)}-{scorer.player_source_code}",
                    "full_name": scorer.full_name,
                    "current_team_id": team_id,
                },
                on_conflict="federation_id,source_code",
                select="id",
            )[0]
            self.db.upsert(
                "player_competition_stats",
                {
                    "player_id": player["id"],
                    "competition_id": competition["id"],
                    "team_id": team_id,
                    "matches_played": scorer.matches_played,
                    "goals": scorer.goals,
                    "penalty_goals": scorer.penalty_goals,
                    "data_source": "official",
                    "updated_at": datetime.now(MADRID_TZ).isoformat(),
                },
                on_conflict="player_id,competition_id,team_id",
                select="player_id",
            )

    # ───────────── jobs ─────────────

    @contextmanager
    def _capture(self, stats: RunStats, **context):
        """Una competición rota no para la pasada: se registra la anomalía y se sigue."""
        try:
            yield
        except SelectorEmpty as exc:
            stats.anomalies.append(Anomaly("selector_empty", str(exc)[:300], context))
        except NotAnImage as exc:
            stats.anomalies.append(Anomaly("not_an_image", str(exc)[:300], context))
        except RobotsDisallowed as exc:
            stats.anomalies.append(Anomaly("robots", f"robots.txt prohíbe la ruta: {exc}"[:300], context))
        except httpx.HTTPError as exc:
            stats.anomalies.append(Anomaly("http", str(exc)[:300], context))

    def job_structure(self, stats: RunStats) -> None:
        self.sync_season()
        self.sync_competitions()

    def job_daily(self, stats: RunStats) -> None:
        """06:00 — estructura, clasificación, goleadores y jornada actual + 2 siguientes.
        Los goleadores iban solo en `closing` (lun/jue): la tabla se quedaba días sin actualizar."""
        self.job_structure(stats)
        for competition in self.competitions():
            with self._capture(stats, competition_id=competition["id"]):
                current = self.sync_round(competition, None, stats)
                for r in (current + 1, current + 2) if current else ():
                    if not competition.get("total_rounds") or r <= competition["total_rounds"]:
                        self.sync_round(competition, r, stats)
                self.sync_standings(competition, None, stats)
                self.sync_scorers(competition, stats)

    def job_live(self, stats: RunStats) -> None:
        """Directo: solo los grupos con partido hoy y solo sus jornadas de hoy (número explícito,
        sin depender de qué jornada muestre la RFFM por defecto), más su clasificación."""
        start = datetime.combine(self.today, datetime.min.time(), MADRID_TZ)
        rounds: dict[int, set[int]] = {}
        adult = self.competitions()
        for m in self.db.select_all(
            "matches",
            competition_id=_in_ids(adult),
            kickoff_at=f"gte.{start.isoformat()}",
            select="competition_id,round",
            **{"and": f"(kickoff_at.lt.{(start + timedelta(days=1)).isoformat()})"},
        ):
            rounds.setdefault(m["competition_id"], set()).add(m["round"])
        for competition in adult:
            if competition["id"] not in rounds:
                continue
            with self._capture(stats, competition_id=competition["id"]):
                for round_number in sorted(rounds[competition["id"]]):
                    self.sync_round(competition, round_number, stats)
                self.sync_standings(competition, None, stats)

    def job_hot(self, stats: RunStats) -> None:
        """Directo rápido: solo los grupos con algún partido en juego o dentro de la ventana
        [-3 h 45 min, +15 min] respecto a ahora, y solo su jornada. Los partidos sin hora real
        (00:00, p. ej. Segunda Regional) no cuentan: los cubre `live` (barrido del día).
        La clasificación se pide únicamente si la pasada cambió algún partido."""
        now = self.now()
        lo, hi = now - timedelta(minutes=225), now + timedelta(minutes=15)
        rounds: dict[int, set[int]] = {}
        adult = self.competitions()
        for m in self.db.select_all(
            "matches",
            competition_id=_in_ids(adult),
            select="competition_id,round,kickoff_at,status",
            status="in.(scheduled,live)",
            kickoff_at=f"gte.{lo.isoformat()}",
            **{"and": f"(kickoff_at.lt.{hi.isoformat()})"},
        ):
            if m["kickoff_at"] and not is_placeholder_time(m["kickoff_at"]):
                rounds.setdefault(m["competition_id"], set()).add(m["round"])
        for competition in self.competitions():
            if competition["id"] not in rounds:
                continue
            with self._capture(stats, competition_id=competition["id"]):
                before = stats.matches
                for round_number in sorted(rounds[competition["id"]]):
                    self.sync_round(competition, round_number, stats)
                if stats.matches != before:
                    self.sync_standings(competition, None, stats)

    def job_closing(self, stats: RunStats) -> None:
        """Lunes 08:00 — re-scrapea la jornada anterior hasta acta cerrada + goleadores.
        Sustituye al scraping de actas, que robots.txt prohíbe."""
        for competition in self.competitions():
            with self._capture(stats, competition_id=competition["id"]):
                current = self.sync_round(competition, None, stats)
                if current and current > 1:
                    self.sync_round(competition, current - 1, stats)
                self.sync_standings(competition, None, stats)
                self.sync_scorers(competition, stats)

    def job_calendar(self, stats: RunStats) -> None:
        """Semanal — calendario completo de la temporada (todas las jornadas)."""
        self.job_structure(stats)
        for competition in self.competitions(only_active=False):
            for r in range(1, (competition.get("total_rounds") or 0) + 1):
                with self._capture(stats, competition_id=competition["id"], round=r):
                    self.sync_round(competition, r, stats)

    # ───────────── jobs de base (menores y veteranos, solo equipos) ─────────────

    def base_competitions(self, only_active: bool = True) -> list[dict]:
        return self.competitions(only_active=only_active, segments=BASE_SEGMENTS)

    def job_base_structure(self, stats: RunStats) -> None:
        """Semanal: competiciones y grupos de base (puede haber grupos nuevos por sorteos de fase)."""
        self.sync_season()
        self.sync_competitions(scope="base")

    def job_base_daily(self, stats: RunStats) -> None:
        """Una pasada de calendario de lunes a viernes, un quinto de los grupos cada día (por id): cada grupo se refresca
        una vez por semana con la jornada actual y las dos siguientes (aplazamientos, jornadas nuevas). Los resultados
        y la clasificación los traen `base_live` (sábado y domingo) y `base_closing` (lunes); la clasificación solo se
        pide aquí si el grupo aún no tiene ninguna. Sin goleadores. Sábado y domingo no hace nada."""
        if self.today.weekday() >= 5:
            return
        for competition in self.base_competitions():
            if competition["id"] % 5 != self.today.weekday():
                continue
            if self.over_budget():
                break
            with self._capture(stats, competition_id=competition["id"]):
                current = self.sync_round(competition, None, stats)
                for r in (current + 1, current + 2) if current else ():
                    if not competition.get("total_rounds") or r <= competition["total_rounds"]:
                        self.sync_round(competition, r, stats)
                if not self.db.select("standings", competition_id=f"eq.{competition['id']}", select="team_id", limit="1"):
                    self.sync_standings(competition, None, stats)

    def _base_rounds(self, lo: datetime, hi: datetime, statuses: str, skip_placeholder: bool = True) -> dict[int, set[int]]:
        """(competición de base -> jornadas) con partidos en `statuses` y hora de inicio en [lo, hi)."""
        base = self.base_competitions(only_active=False)
        active = {c["id"] for c in base}
        rounds: dict[int, set[int]] = {}
        for m in self.db.select_all(
            "matches",
            competition_id=_in_ids(base),
            status=f"in.({statuses})",
            kickoff_at=f"gte.{lo.isoformat()}",
            select="competition_id,round,kickoff_at",
            **{"and": f"(kickoff_at.lt.{hi.isoformat()})"},
        ):
            if m["competition_id"] in active and not (skip_placeholder and m["kickoff_at"] and is_placeholder_time(m["kickoff_at"])):
                rounds.setdefault(m["competition_id"], set()).add(m["round"])
        return rounds

    def _refresh_base_rounds(self, rounds: dict[int, set[int]], stats: RunStats, only_if_changed: bool) -> None:
        by_id = {c["id"]: c for c in self.base_competitions(only_active=False)}
        # Primero las divisiones altas: si el tope de peticiones corta la pasada, lo que queda sin refrescar es lo menos visto.
        for competition_id in sorted(rounds, key=lambda cid: (by_id[cid].get("tier") or 99, cid)):
            if self.over_budget():
                return
            competition = by_id[competition_id]
            with self._capture(stats, competition_id=competition_id):
                before = stats.matches
                for round_number in sorted(rounds[competition_id]):
                    self.sync_round(competition, round_number, stats)
                if not only_if_changed or stats.matches != before:
                    self.sync_standings(competition, None, stats)

    def job_base_closing(self, stats: RunStats) -> None:
        """Cierre de jornada: solo los partidos de base de las últimas 72 h que siguen sin cerrar (la hora de inicio
        ya pasó hace más de 2 h). Coste proporcional a lo pendiente, no al número de grupos."""
        now = self.now()
        rounds = self._base_rounds(now - BASE_CLOSING_LOOKBACK, now - BASE_CLOSING_SETTLE, "scheduled,live", skip_placeholder=False)
        self._refresh_base_rounds(rounds, stats, only_if_changed=False)

    def job_base_live(self, stats: RunStats) -> None:
        """Directo de base: solo grupos con algún partido en [-3 h, +10 min]; clasificación solo si cambió algún partido."""
        now = self.now()
        rounds = self._base_rounds(now - BASE_LIVE_BEFORE, now + BASE_LIVE_AFTER, "scheduled,live")
        self._refresh_base_rounds(rounds, stats, only_if_changed=True)

    def job_base_calendar(self, stats: RunStats) -> None:
        """Calendario completo de base, solo las jornadas que aún no están en la BD. Se detiene al llegar a
        `max_requests` y la siguiente ejecución continúa donde quedó (no repite lo cargado)."""
        for competition in self.base_competitions(only_active=False):
            total = competition.get("total_rounds") or 0
            if not total:
                continue
            loaded = {m["round"] for m in self.db.select("matches", competition_id=f"eq.{competition['id']}", select="round")}
            for r in range(1, total + 1):
                if r in loaded:
                    continue
                if self.over_budget():
                    return
                with self._capture(stats, competition_id=competition["id"], round=r):
                    self.sync_round(competition, r, stats)

    def job_crests(self, stats: RunStats) -> None:
        """Escudos reales: URL desde la clasificación (ya pública) -> WebP en Storage -> clubs.crest_path
        y teams.club_id. Solo descarga si la URL de origen cambió.

        El binario llega por `www.rffm.es/_next/image` (ruta permitida por robots.txt): el host que
        aloja `/pnfg/pimg/` prohíbe todo el sitio y no se rastrea.
        """
        season = self.season or self.sync_season()
        federation_id = self.ensure_federation()
        self.db.ensure_bucket(CREST_BUCKET)

        crest_by_code: dict[str, str] = {}
        # La versión anterior solo recorría senior y femenino absoluto. Los escudos de la cantera
        # también están publicados en las clasificaciones oficiales de la RFFM.
        all_segments = (*ADULT_SEGMENTS, *BASE_SEGMENTS)
        for competition in self.competitions(segments=all_segments):
            with self._capture(stats, competition_id=competition["id"], what="crests"):
                raw, _ = self.transport.standings(season["source_code"], competition["source_code"], competition["group_source_code"])
                for row in raw.get("clasificacion") or []:
                    source_path = row.get("url_img")
                    url = self.mapper.crest(source_path)
                    # Evita guardar HTML de rutas vacías/rotas como si fuera un escudo real.
                    # El origen debe ser el directorio oficial de imágenes de equipos de RFFM.
                    is_official_image = bool(source_path and "pimg/" in source_path.lower())
                    if url and is_official_image and row.get("codequipo") and "default" not in url.lower():
                        # Se conserva el ?nova=1 del payload: sin él la RFFM responde HTML.
                        crest_by_code[row["codequipo"]] = url

        clubs: dict[str, dict] = {}
        for team in self.db.select("teams", federation_id=f"eq.{federation_id}", select="id,name,source_code"):
            name = club_name(team["name"])
            club = clubs.setdefault(slug(name), {"name": name, "urls": Counter(), "team_ids": []})
            club["team_ids"].append(team["id"])
            if url := crest_by_code.get(team["source_code"] or ""):
                club["urls"][url] += 1

        existing = {c["slug"]: c for c in self.db.select("clubs", select="id,slug,crest_path,crest_source_url,primary_color")}
        for key, club in clubs.items():
            url = club["urls"].most_common(1)[0][0] if club["urls"] else None
            prior = existing.get(key) or {}
            crest = {"crest_path": prior.get("crest_path"), "crest_source_url": prior.get("crest_source_url")}
            colors: dict[str, str] = {}
            if url and (url != prior.get("crest_source_url") or not prior.get("crest_path")):
                with self._capture(stats, club=key, what="crest"):
                    path = f"{key}.webp"
                    content = self.transport.crest_image(url)
                    self.db.upload(CREST_BUCKET, path, to_webp(content), "image/webp")
                    crest = {"crest_path": path, "crest_source_url": url}
                    stats.crests += 1
                    # Solo si el club aún no tiene colores: nunca se pisan los que haya puesto el propio club.
                    if not prior.get("primary_color") and (found := dominant_colors(content)):
                        colors = {"primary_color": found[0], "secondary_color": found[1]}
            row = self.db.upsert(
                "clubs",
                {"federation_id": federation_id, "slug": key, "name": club["name"], **{k: v for k, v in crest.items() if v}, **colors},
                on_conflict="slug",
                select="id",
            )[0]
            self.db.update("teams", {"club_id": row["id"]}, id=f"in.({','.join(str(i) for i in club['team_ids'])})")

    def finish(self, stats: RunStats, requests_after: int, started_at: datetime) -> bool:
        for anomaly in stats.anomalies:
            report(anomaly)
        ok = not stats.anomalies
        now = datetime.now(MADRID_TZ).isoformat()
        self.db.insert(
            "scrape_runs",
            {
                "adapter": ADAPTER_NAME,
                "job": stats.job,
                "started_at": started_at.isoformat(),
                "finished_at": now,
                "requests": requests_after - stats.requests_before,
                "matches_upserted": stats.matches,
                "standings_upserted": stats.standings,
                "anomalies": [a.as_dict() for a in stats.anomalies],
                "ok": ok,
            },
            select="id",
        )
        health = {
            "adapter": ADAPTER_NAME,
            "state": "healthy" if ok else "degraded",
            "last_run_at": now,
            "details": {"job": stats.job, "anomalies": len(stats.anomalies)},
            "updated_at": now,
        }
        if ok:
            health["last_success_at"] = now
        else:
            health["last_error"] = json.dumps(stats.anomalies[0].as_dict(), ensure_ascii=False)[:1000]
        self.db.upsert("adapter_health", health, on_conflict="adapter", select="adapter")
        return ok
