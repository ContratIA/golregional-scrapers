"""Acceso a Supabase vía PostgREST con la service_role key (solo desde GitHub Actions)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class Supabase:
    url: str
    service_key: str
    transport: httpx.BaseTransport | None = None

    def __post_init__(self) -> None:
        self._client = httpx.Client(
            base_url=f"{self.url.rstrip('/')}/rest/v1",
            headers={
                "apikey": self.service_key,
                "Authorization": f"Bearer {self.service_key}",
                "Content-Type": "application/json",
            },
            timeout=60,
            transport=self.transport,
        )

        self._storage = httpx.Client(
            base_url=f"{self.url.rstrip('/')}/storage/v1",
            headers={"apikey": self.service_key, "Authorization": f"Bearer {self.service_key}"},
            timeout=60,
            transport=self.transport,
        )

    @classmethod
    def from_env(cls) -> Supabase:
        return cls(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])

    def close(self) -> None:
        self._client.close()
        self._storage.close()

    def ensure_bucket(self, bucket: str) -> None:
        """Bucket público (idempotente: si ya existe no hace nada)."""
        resp = self._storage.post("/bucket", json={"id": bucket, "name": bucket, "public": True})
        if resp.status_code >= 400 and "already exists" not in resp.text.lower() and "duplicate" not in resp.text.lower():
            resp.raise_for_status()

    def upload(self, bucket: str, path: str, content: bytes, content_type: str) -> None:
        resp = self._storage.post(
            f"/object/{bucket}/{path}",
            content=content,
            headers={"Content-Type": content_type, "x-upsert": "true", "Cache-Control": "max-age=604800"},
        )
        resp.raise_for_status()

    def download(self, bucket: str, path: str) -> bytes:
        """Objeto de un bucket público."""
        resp = self._storage.get(f"/object/public/{bucket}/{path}")
        resp.raise_for_status()
        return resp.content

    def select(self, table: str, **params: str) -> list[dict[str, Any]]:
        resp = self._client.get(f"/{table}", params=params)
        resp.raise_for_status()
        return resp.json()

    def select_all(self, table: str, page_size: int = 1000, **params: str) -> list[dict[str, Any]]:
        """Como `select`, pero pagina con limit/offset y orden estable: PostgREST devuelve como mucho 1000 filas."""
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self.select(table, **{"order": "id.asc", **params}, limit=str(page_size), offset=str(offset))
            out += page
            if len(page) < page_size:
                return out
            offset += page_size

    def upsert(self, table: str, rows: list[dict] | dict, on_conflict: str, select: str = "*") -> list[dict[str, Any]]:
        """Idempotente: misma fila dos veces = mismo resultado (merge por clave natural)."""
        if isinstance(rows, list) and not rows:
            return []
        resp = self._client.post(
            f"/{table}",
            params={"on_conflict": on_conflict, "select": select},
            json=rows,
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        )
        resp.raise_for_status()
        return resp.json()

    def insert(self, table: str, rows: list[dict] | dict, select: str = "*", ignore_duplicates: bool = False) -> list[dict[str, Any]]:
        prefer = "return=representation" + (",resolution=ignore-duplicates" if ignore_duplicates else "")
        resp = self._client.post(f"/{table}", params={"select": select}, json=rows, headers={"Prefer": prefer})
        resp.raise_for_status()
        return resp.json()

    def update(self, table: str, values: dict, **filters: str) -> None:
        resp = self._client.patch(f"/{table}", params=filters, json=values)
        resp.raise_for_status()
