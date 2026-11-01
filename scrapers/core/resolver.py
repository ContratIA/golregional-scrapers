"""Entity resolution de equipos: código de federación → alias exacto → fuzzy (rapidfuzz)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from rapidfuzz import fuzz, process

from .models import TeamRef
from .normalize import normalize_for_match


@dataclass(frozen=True)
class KnownTeam:
    id: int
    name: str
    source_code: str | None


@dataclass
class Resolution:
    team_id: int | None
    method: str  # 'source_code' | 'alias' | 'fuzzy' | 'created' | 'review'
    score: float = 100.0
    candidates: list[dict] = field(default_factory=list)


class TeamStore(Protocol):
    def create_team(self, ref: TeamRef) -> int: ...
    def link_source_code(self, team_id: int, source_code: str) -> None: ...
    def add_alias(self, team_id: int, alias: str, normalized: str, source: str, confidence: float) -> None: ...
    def enqueue_review(self, raw_name: str, source: str, context: dict, candidates: list[dict]) -> None: ...


@dataclass
class TeamResolver:
    store: TeamStore
    teams: list[KnownTeam]
    aliases: dict[str, int]  # alias_normalizado → team_id
    source: str
    auto_threshold: float = 92.0
    review_threshold: float = 80.0

    def __post_init__(self) -> None:
        self._by_code = {t.source_code: t.id for t in self.teams if t.source_code}
        self._names = {t.id: normalize_for_match(t.name) for t in self.teams}

    def _remember(self, team: KnownTeam) -> None:
        self.teams.append(team)
        if team.source_code:
            self._by_code[team.source_code] = team.id
        self._names[team.id] = normalize_for_match(team.name)

    def _fuzzy(self, normalized: str, exclude_coded: bool) -> list[dict]:
        choices = {
            tid: name
            for tid, name in self._names.items()
            if not (exclude_coded and any(t.id == tid and t.source_code for t in self.teams))
        }
        matches = process.extract(normalized, choices, scorer=fuzz.token_sort_ratio, limit=3)
        return [{"id": tid, "name": name, "score": round(score, 1)} for name, score, tid in matches]

    def resolve(self, ref: TeamRef, context: dict | None = None, isolated: bool = False) -> Resolution:
        """`isolated`: solo por código de federación; un equipo nuevo se crea sin alias ni fuzzy.
        El femenino comparte nombre con los equipos masculinos de su club ("CD X"): sin esto un
        alias/fuzzy lo fundiría con el masculino y sus resultados acabarían en el equipo equivocado."""
        context = context or {}
        if ref.source_code and ref.source_code in self._by_code:
            return Resolution(self._by_code[ref.source_code], "source_code")
        if isolated and ref.source_code:
            team_id = self.store.create_team(ref)
            self._remember(KnownTeam(team_id, ref.name, ref.source_code))
            return Resolution(team_id, "created")

        normalized = normalize_for_match(ref.name)
        if normalized in self.aliases:
            team_id = self.aliases[normalized]
            if ref.source_code:
                self.store.link_source_code(team_id, ref.source_code)
                self._by_code[ref.source_code] = team_id
            return Resolution(team_id, "alias")

        # Un código de federación es autoritativo: solo se fusiona con equipos que aún no
        # tienen código (creados por la comunidad/manualmente).
        candidates = self._fuzzy(normalized, exclude_coded=bool(ref.source_code))
        best = candidates[0] if candidates else None

        if best and best["score"] >= self.auto_threshold:
            team_id = best["id"]
            self.store.add_alias(team_id, ref.name, normalized, self.source, best["score"] / 100)
            self.aliases[normalized] = team_id
            if ref.source_code:
                self.store.link_source_code(team_id, ref.source_code)
                self._by_code[ref.source_code] = team_id
            return Resolution(team_id, "fuzzy", best["score"], candidates)

        doubtful = bool(best and best["score"] >= self.review_threshold)

        if not ref.source_code:
            # Sin código no se inventa un equipo: a revisión humana.
            self.store.enqueue_review(ref.name, self.source, context, candidates)
            return Resolution(None, "review", best["score"] if best else 0.0, candidates)

        team_id = self.store.create_team(ref)
        self.store.add_alias(team_id, ref.name, normalized, self.source, 1.0)
        self.aliases[normalized] = team_id
        self._remember(KnownTeam(team_id, ref.name, ref.source_code))
        if doubtful:
            self.store.enqueue_review(ref.name, self.source, {**context, "created_team_id": team_id}, candidates)
        return Resolution(team_id, "created", best["score"] if best else 0.0, candidates)
