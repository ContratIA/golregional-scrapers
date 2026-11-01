"""Doble en memoria de `core.db.Supabase` para `--dry-run` y tests (subset de PostgREST)."""

from __future__ import annotations

import itertools
from collections import defaultdict
from typing import Any

import httpx


def _match(row: dict, key: str, expr: str) -> bool:
    if key in ("select", "and", "order", "limit", "offset"):
        return True
    op, _, value = expr.partition(".")
    current = row.get(key)
    if op == "eq":
        return str(current).lower() == value.lower() if isinstance(current, bool) else str(current) == value
    if op == "neq":
        return str(current) != value
    if op == "is":
        return current is None if value == "null" else True
    if op == "in":
        return str(current) in value.strip("()").split(",")
    if op == "gte":
        return current is not None and str(current) >= value
    if op == "lt":
        return current is not None and str(current) < value
    raise NotImplementedError(op)


class MemoryDb:
    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._ids = defaultdict(lambda: itertools.count(1))
        # Restricciones únicas relevantes para detectar conflictos (409) como PostgREST.
        self.unique = {"matches": [("slug",), ("competition_id", "source_code")], "teams": [("slug",), ("federation_id", "source_code")], "team_aliases": [("alias_normalized", "team_id")]}

        self.storage: dict[str, dict[str, tuple[bytes, str]]] = defaultdict(dict)

    def close(self) -> None:
        pass

    def ensure_bucket(self, bucket: str) -> None:
        self.storage.setdefault(bucket, {})

    def upload(self, bucket: str, path: str, content: bytes, content_type: str) -> None:
        self.storage[bucket][path] = (content, content_type)

    def download(self, bucket: str, path: str) -> bytes:
        return self.storage[bucket][path][0]

    def select_all(self, table: str, page_size: int = 1000, **params: str) -> list[dict[str, Any]]:
        return self.select(table, **params)

    def select(self, table: str, **params: str) -> list[dict[str, Any]]:
        rows = [r for r in self.tables[table] if all(_match(r, k, v) for k, v in params.items())]
        if "and" in params:
            inner = params["and"].strip("()")
            key, _, expr = inner.partition(".")
            rows = [r for r in rows if _match(r, key, expr)]
        return [dict(r) for r in rows]

    def _conflict(self, table: str, row: dict) -> bool:
        for cols in self.unique.get(table, []):
            if any(row.get(c) is None for c in cols):
                continue
            if any(all(r.get(c) == row.get(c) for c in cols) for r in self.tables[table]):
                return True
        return False

    def insert(self, table: str, rows: list[dict] | dict, select: str = "*", ignore_duplicates: bool = False) -> list[dict]:
        out = []
        for row in rows if isinstance(rows, list) else [rows]:
            if self._conflict(table, row):
                if ignore_duplicates:
                    continue
                raise httpx.HTTPStatusError("409", request=httpx.Request("POST", table), response=httpx.Response(409))
            stored = {"id": next(self._ids[table]), **row}
            self.tables[table].append(stored)
            out.append(dict(stored))
        return out

    def upsert(self, table: str, rows: list[dict] | dict, on_conflict: str, select: str = "*") -> list[dict]:
        keys = on_conflict.split(",")
        out = []
        for row in rows if isinstance(rows, list) else [rows]:
            existing = next((r for r in self.tables[table] if all(r.get(k) == row.get(k) for k in keys)), None)
            if existing:
                existing.update(row)
                out.append(dict(existing))
            else:
                out.extend(self.insert(table, row))
        return out

    def update(self, table: str, values: dict, **filters: str) -> None:
        for row in self.tables[table]:
            if all(_match(row, k, v) for k, v in filters.items()):
                row.update(values)
