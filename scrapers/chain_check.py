"""Decide si la cadena del calendario de base debe lanzar otra pasada.

Imprime `continuar=true|false` (formato de $GITHUB_OUTPUT). Sigue solo si la última pasada `base_calendar` agotó el tope de
peticiones y avanzó: terminó bien, o con anomalías sueltas pero guardando partidos (una jornada vacía en la RFFM no debe
parar la carga de las demás). Una pasada por debajo del tope ya no tiene nada más que pedir; una que no guardó nada y
falló es un fallo real y la cadena se detiene.
"""

import os
import sys

import httpx

CAP_THRESHOLD = 1400


def should_continue(row: dict | None) -> bool:
    if not row or int(row.get("requests") or 0) < CAP_THRESHOLD:
        return False
    return bool(row.get("ok")) or int(row.get("matches_upserted") or 0) > 0


def main() -> int:
    url, key = os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    resp = httpx.get(
        f"{url}/rest/v1/scrape_runs",
        params={"job": "eq.base_calendar", "order": "id.desc", "limit": "1", "select": "ok,requests,matches_upserted"},
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json()
    print(f"continuar={'true' if should_continue(rows[0] if rows else None) else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
