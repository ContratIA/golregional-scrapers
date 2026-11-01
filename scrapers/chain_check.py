"""Decide si la cadena del calendario de base debe lanzar otra pasada.

Imprime `continuar=true|false` (formato de $GITHUB_OUTPUT). Sigue solo si la última pasada `base_calendar` terminó bien
y agotó el tope de peticiones: una pasada por debajo del tope ya no tiene nada más que pedir.
"""

import os
import sys

import httpx

CAP_THRESHOLD = 1400


def should_continue(row: dict | None) -> bool:
    return bool(row) and bool(row.get("ok")) and int(row.get("requests") or 0) >= CAP_THRESHOLD


def main() -> int:
    url, key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    resp = httpx.get(
        f"{url}/rest/v1/scrape_runs",
        params={"job": "eq.base_calendar", "order": "id.desc", "limit": "1", "select": "ok,requests"},
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json()
    print(f"continuar={'true' if should_continue(rows[0] if rows else None) else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
