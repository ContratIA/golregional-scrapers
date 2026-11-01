"""Modelo intermedio validado con pydantic, independiente de la federación de origen."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class MatchStatus(str, Enum):
    scheduled = "scheduled"
    live = "live"
    finished = "finished"
    postponed = "postponed"
    suspended = "suspended"
    cancelled = "cancelled"


class SeasonIn(BaseModel):
    source_code: str
    name: str
    slug: str
    starts_on: date
    ends_on: date


class CompetitionIn(BaseModel):
    source_code: str
    group_source_code: str
    name: str
    category_slug: str
    tier: int = Field(ge=1, le=10)
    group_name: str
    group_slug: str
    total_rounds: int | None = None
    total_teams: int | None = None
    is_active: bool = True
    starts_on: date | None = None
    source_url: str
    segment: Literal["senior", "femenino", "juvenil", "base", "femenino-base"] = "senior"
    # La BD exige is_senior = (segmento senior o femenino); su default es true, así que en base se envía false.
    is_senior: bool = True
    game_type: Literal["f11", "f7", "f5"] = "f11"
    age_group: Literal["juvenil", "cadete", "infantil", "alevin", "benjamin", "prebenjamin", "debutante", "veteranos"] | None = None
    # La federación a veces no publica tabla de goleadores/goleadoras (p. ej. Tercera Federación Femenino).
    has_scorers: bool = True


class RoundIn(BaseModel):
    number: int = Field(ge=1)
    date: date | None


class TeamRef(BaseModel):
    source_code: str
    name: str = Field(min_length=2)
    crest_url: str | None = None


class MatchIn(BaseModel):
    source_code: str
    round: int = Field(ge=1)
    kickoff_at: datetime | None
    venue: str | None
    home: TeamRef
    away: TeamRef
    home_goals: int | None = Field(default=None, ge=0, le=99)
    away_goals: int | None = Field(default=None, ge=0, le=99)
    status: MatchStatus
    is_result_official: bool
    official_url: str
    notes: str | None = None

    @model_validator(mode="after")
    def _coherent(self) -> MatchIn:
        if self.home.source_code == self.away.source_code:
            raise ValueError("local y visitante son el mismo equipo")
        if (self.home_goals is None) != (self.away_goals is None):
            raise ValueError("marcador incompleto")
        if self.status in (MatchStatus.finished, MatchStatus.live) and self.home_goals is None:
            raise ValueError(f"partido {self.status.value} sin marcador")
        return self


class StandingRowIn(BaseModel):
    team: TeamRef
    position: int = Field(ge=1)
    played: int = Field(ge=0)
    won: int = Field(ge=0)
    drawn: int = Field(ge=0)
    lost: int = Field(ge=0)
    goals_for: int = Field(ge=0)
    goals_against: int = Field(ge=0)
    points: int
    points_penalty: int = 0
    form: str | None = None

    @model_validator(mode="after")
    def _coherent(self) -> StandingRowIn:
        if self.won + self.drawn + self.lost != self.played:
            raise ValueError(f"{self.team.name}: G+E+P != PJ")
        return self


class StandingsIn(BaseModel):
    round: int = Field(ge=0)
    rows: list[StandingRowIn]


class ScorerIn(BaseModel):
    player_source_code: str
    full_name: str = Field(min_length=3)
    team: TeamRef
    matches_played: int = Field(ge=0)
    goals: int = Field(ge=0)
    penalty_goals: int = Field(ge=0)
