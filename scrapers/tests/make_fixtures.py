"""Genera las fixtures SINTÉTICAS de los tests (`python tests/make_fixtures.py`).

Todo es inventado: clubes, jugadores, marcadores y fechas. Solo reproduce la FORMA de los datos de la RFFM (nombres de
campos, tipos y códigos de competición/categoría, que son etiquetas públicas) para ejercitar el adaptador sin copiar
ninguna página ni dato real.
"""

import json
from pathlib import Path

OUT = Path(__file__).parent / "fixtures" / "rffm"
CRESTS = Path(__file__).parent / "fixtures" / "crests"

# (modalidad, código, NombreCategoria, nombre de la competición o None si coincide, tipo, Activa, inicio, goleadores)
F11 = [
    ("26738289", "TERCERA FEDERACION", "COPA RFEF FASE AUTONOMICA", "2", "0", "2026-08-12", "1"),
    ("26738226", "TERCERA FEDERACION", "TERCERA FEDERACION RFEF", "2", "1", "2026-09-05", "1"),
    ("28733581", "PRIMERA DIVISION AUTONOMICA AFICIONADO", "COPA DE AFICIONADOS 2026/2027", "1", "0", "2026-11-03", "1"),
    ("26737698", "PRIMERA DIVISION AUTONOMICA AFICIONADO", None, "2", "1", "2026-09-13", "1"),
    ("26737701", "PREFERENTE AFICIONADO", None, "2", "1", "2026-09-12", "1"),
    ("26737706", "PRIMERA AFICIONADO", None, "2", "1", "2026-09-13", "1"),
    ("26738300", "SEGUNDA AFICIONADO", None, "2", "1", "2026-09-27", "1"),
    ("26738130", "LIGA NACIONAL JUVENIL", "NACIONAL JUVENIL", "2", "1", "2026-09-12", "1"),
    ("26737715", "PRIMERA DIVISION AUTONOMICA JUVENIL", None, "2", "1", "2026-09-12", "1"),
    ("26737718", "PREFERENTE JUVENIL", None, "2", "1", "2026-09-12", "1"),
    ("26737724", "PRIMERA JUVENIL", None, "2", "1", "2026-09-12", "1"),
    ("26738323", "SEGUNDA JUVENIL", None, "2", "1", "2026-10-10", "1"),
    ("26738047", "SUPERLIGA CADETE", None, "2", "1", "2026-09-26", "1"),
    ("26738054", "DIVISION DE HONOR CADETE", None, "2", "1", "2026-09-26", "1"),
    ("26737737", "PRIMERA DIVISION AUTONOMICA CADETE", None, "2", "1", "2026-09-26", "1"),
    ("26737742", "PREFERENTE CADETE", None, "2", "1", "2026-09-25", "1"),
    ("26737751", "PRIMERA CADETE", None, "2", "1", "2026-09-26", "1"),
    ("26737768", "SEGUNDA CADETE", None, "2", "1", "2026-10-10", "1"),
    ("26738049", "SUPERLIGA INFANTIL", None, "2", "1", "2026-09-26", "1"),
    ("26737811", "DIVISION DE HONOR INFANTIL", None, "2", "1", "2026-09-26", "1"),
    ("26737814", "PRIMERA DIVISION AUTONOMICA INFANTIL", None, "2", "1", "2026-09-26", "1"),
    ("26737819", "PREFERENTE INFANTIL", None, "2", "1", "2026-09-26", "1"),
    ("26737828", "PRIMERA INFANTIL", None, "2", "1", "2026-09-26", "1"),
    ("26738324", "SEGUNDA INFANTIL", None, "2", "1", "2026-10-10", "1"),
    ("26738128", "SUPERLIGA ALEVIN", None, "2", "1", "2026-09-26", "1"),
    ("26738051", "DIVISION DE HONOR ALEVIN", None, "2", "1", "2026-09-26", "1"),
    ("26737845", "PRIMERA DIVISION AUTONOMICA ALEVIN", None, "2", "1", "2026-10-10", "1"),
    ("26737850", "PREFERENTE ALEVIN", None, "2", "1", "2026-10-10", "1"),
    ("26737859", "PRIMERA ALEVIN", None, "2", "1", "2026-10-24", "1"),
    ("26738269", "VETERANOS F11", "VETERANOS MASCULINO F11 - MOVEMBER", "2", "1", "2026-10-24", "1"),
    ("27786100", "TERCERA FEDERACION DE FÚTBOL FEMENINO", "TERCERA FEDERACIÓN DE FÚTBOL FEMENINO", "2", "1", "2026-09-12", "0"),
    ("26738252", "PRIMERA DIVISION AUTONÓMICA FEMENINO", "PRIMERA DIVISIÓN AUTONÓMICA FEMENINO", "2", "1", "2026-09-26", "1"),
    ("26738262", "PREFERENTE FUTBOL FEMENINO", "PREFERENTE FÚTBOL FEMENINO", "2", "1", "2026-09-26", "1"),
    ("26737874", "PRIMERA FUTBOL FEMENINO", "PRIMERA FÚTBOL FEMENINO", "2", "1", "2026-10-24", "1"),
    ("26738254", "PRIMERA DIVISION AUTONOMICA FEMENINO JUVENIL", None, "2", "1", "2026-09-26", "1"),
    ("26737878", "PREFERENTE FEMENINO JUVENIL", None, "2", "1", "2026-09-26", "1"),
    ("26737881", "PRIMERA FEMENINO JUVENIL", None, "2", "1", "2026-10-24", "1"),
    ("26738256", "PRIMERA DIVISION AUTONOMICA FEMENINO CADETE", None, "2", "1", "2026-09-26", "1"),
    ("26737887", "PREFERENTE FEMENINO CADETE", None, "2", "1", "2026-09-26", "1"),
    ("26737890", "PRIMERA FEMENINO CADETE", None, "2", "1", "2026-10-24", "1"),
    ("26748876", "INFANTIL FEMENINO F11", None, "2", "1", "2026-10-10", "1"),
    ("26738236", "CAMPEONATO UNIVERSITARIO FEMENINO", None, "2", "1", "2026-10-13", "1"),
    ("26738233", "CAMPEONATO UNIVERSITARIO MASCULINO", None, "2", "1", "2026-10-12", "1"),
]
F7 = [
    ("26737923", "DIVISION DE HONOR ALEV-F7", None, "2", "1", "2026-10-07", "1"),
    ("26738066", "PRIMERA DIVISION AUTONOMICA ALEV.F-7", None, "2", "1", "2026-10-10", "1"),
    ("26738132", "PREFERENTE ALEVIN F-7", None, "2", "1", "2026-10-10", "1"),
    ("26738141", "PRIMERA ALEVIN F-7", None, "2", "1", "2026-10-10", "1"),
    ("26738071", "SEGUNDA ALEVIN F-7", None, "2", "1", "2026-10-17", "1"),
    ("26738273", "DIVISIÓN DE HONOR BENJAMÍN F7", None, "2", "1", "2026-10-03", "1"),
    ("26738158", "PRIMERA DIVISION AUTONOMICA BENJAMIN F7", None, "2", "1", "2026-10-03", "1"),
    ("26737926", "PREFERENTE BENJAMIN F7", None, "2", "1", "2026-10-24", "1"),
    ("26737943", "PRIMERA BENJAMIN F7", None, "2", "1", "2026-10-17", "1"),
    ("26738167", "PREBENJAMIN", "PREBENJAMÍN F-7 DESARROLLO", "2", "1", "2026-10-17", "1"),
    ("26797573", "PREBENJAMIN", "PREBENJAMIN F7 INICIACION", "2", "1", "2026-10-16", "1"),
    ("26738230", "PRIMERA FEMENINO SENIOR F-7", None, "2", "1", "2026-10-10", "1"),
    ("26738374", "JUVENIL F-7 FEMENINO", None, "2", "1", "2026-10-24", "1"),
    ("26738265", "CADETE F-7 FEMENINO", None, "2", "1", "2026-10-24", "1"),
    ("26748601", "DIVISION DE HONOR FEMENINO INFANTIL F7", None, "2", "1", "2026-10-10", "1"),
    ("26738258", "PRIMERA DIVISION AUTONOMICA FEMENINO INFANTIL F-7", None, "2", "1", "2026-10-24", "1"),
    ("26737899", "PREFERENTE FEMENINO INFANTIL F-7", None, "2", "1", "2026-10-24", "1"),
    ("26738260", "PRIMERA DIVISION AUTONOMICA FEMENINO ALEVIN F-7", None, "2", "1", "2026-10-10", "1"),
    ("26737919", "PREFERENTE FEMENINO ALEVIN F-7", None, "2", "1", "2026-10-24", "1"),
    ("26738057", "PRIMERA FEMENINO ALEVIN F-7", None, "2", "1", "2026-10-24", "1"),
    ("26738213", "PRIMERA BENJAMIN FEMENINO F-7", None, "2", "1", "2026-10-24", "1"),
    ("26738376", "FUTBOL ANDANDO F-7", None, "2", "0", "2026-11-17", "1"),
    ("27957934", "FUTBOL INCLUSIVO", None, "2", "1", "2026-10-24", "1"),
    ("28777685", "FUTBOL INCLUSIVO", "FUTBOL INCLUSIVO JORNADA INICIAL", "2", "0", "2026-10-25", "1"),
]
F5 = [("26738029", "DEBUTANTE", None, "2", "0", "2026-11-21", "1")]


def competition(game_code, row):
    code, category, name, kind, active, start, scorers = row
    return {
        "codigo": code, "nombre": name or category, "tipo_competicion": kind, "NombreCategoria": category,
        "codigo_tipo_juego": game_code, "Activa": active, "FechaInicio": start, "goleadores": scorers,
    }


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def page(page_props):
    data = {"props": {"pageProps": page_props}}
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        + "</script></body></html>"
    )


def group(code, name, rounds=30, teams=16, scorers="1", extra=None):
    g = {"codigo": code, "nombre": name, "total_jornadas": str(rounds), "total_equipos": str(teams), "clasificacion_porteros": "0",
         "ver_clasificacion": "1", "clasificacion_goleadores": scorers, "orden": "10", "nombre_delegacion": "DELEGACION DE PRUEBA"}
    return g | (extra or {})


# ───────────── equipos y jugadores inventados ─────────────
TEAM_CODES = ["439", "10656492", "1311", "1074"] + [str(1100 + i) for i in range(14)]
TEAM_NAMES = {
    "439": "C.D. EJEMPLO 'B'", "10656492": "S.A.D. MUESTRA 'A'", "1311": "A.D. MODELO 'A'", "1074": "U.D. PRUEBA 'A'",
}
for i, c in enumerate(TEAM_CODES[4:], 1):
    TEAM_NAMES[c] = f"CLUB DEPORTIVO SINTETICO {i:02d}"
CREST = "/pnfg/pimg/Clubes/{code}_Escudo.png?nova=1"


def match(i, home, away, date, hour, goals, closed="1", note=""):
    played = goals is not None
    return {
        "CodEquipo_local": home, "CodEquipo_visitante": away, "Goles_casa": str(goals[0]) if played else "", "Goles_visitante": str(goals[1]) if played else "",
        "Nombre_equipo_local": TEAM_NAMES[home], "Nombre_equipo_visitante": TEAM_NAMES[away], "ResultadoProvisional": "0",
        "Retirado_local": "0", "Retirado_visitante": "0", "acta_cerrada": closed if played else "0", "arbitro": "", "campojuego": "CAMPO DE PRUEBA",
        "codacta": str(5589120 + i), "codacta_origen": "", "codigo_campo": "1", "equipo_ganador_et": "", "estado": "1", "fecha": date, "ganado_et": "",
        "hay_actas": "1", "hora": hour, "motivo_estado": "", "observaciones": note, "partido_en_juego": "0", "penaltis": "0",
        "penaltis_casa": "", "penaltis_visitante": "", "situacion_juego": "", "url_img_local": CREST.format(code=home),
        "url_img_visitante": CREST.format(code=away),
    }


def results(group_code="26737702", comp_code="26737701", name="PREFERENTE AFICIONADO", rounds=34, round_number="1", n=9):
    pairs = [(TEAM_CODES[2 * k], TEAM_CODES[2 * k + 1]) for k in range(9)]
    scores = [(2, 1), (0, 0), (3, 1), (1, 2), (4, 0), (1, 1), (0, 2), None, None]
    dates = ["12/09/2026"] * 5 + ["13/09/2026"] * 4
    hours = ["18:00", "16:30", "17:00", "19:00", "20:00", "12:00", "17:30", "12:00", "00:00"]
    matches = [match(i, pairs[i][0], pairs[i][1], dates[i], hours[i], scores[i], note="MUTUO ACUERDO" if i == 0 else "") for i in range(n)]
    return {
        "estado": "1", "sesion_ok": "1", "nombre_competicion": name, "codigo_competicion": comp_code, "nombre_grupo": "Grupo 1",
        "codigo_grupo": group_code, "clasificacion_porteros": "1", "visible_goleadores": "1", "visible_clasificacion": "1", "jornada": round_number,
        "nombre_jornada": round_number, "fecha_jornada": "13/09/2026", "grupos_competicion": [],
        "listado_jornadas": [{"jornadas": [{"codjornada": str(r), "fecha_jornada": f"{13 + 7 * (r - 1) - 30 * ((13 + 7 * (r - 1) - 1) // 30):02d}-09-2026" if False else _round_date(r)} for r in range(1, rounds + 1)]}],
        "partidos": matches,
    }


def _round_date(r):
    from datetime import date, timedelta

    return (date(2026, 9, 13) + timedelta(days=7 * (r - 1))).strftime("%d-%m-%Y")


def standings(group_code="26737702", comp_code="26737701", name="PREFERENTE AFICIONADO", round_number="1", rows=18):
    out = []
    for pos, code in enumerate(["1311"] + [c for c in TEAM_CODES if c != "1311"][: rows - 1], 1):
        out.append({
            "codequipo": code, "nombre": TEAM_NAMES[code], "posicion": str(pos), "jugados": "1" if pos <= 2 else "0", "ganados": "1" if pos == 1 else "0",
            "empatados": "0", "perdidos": "1" if pos == 2 else "0", "goles_a_favor": {1: "2", 2: "1"}.get(pos, "0"),
            "goles_en_contra": {1: "1", 2: "2"}.get(pos, "0"), "puntos": "3" if pos == 1 else "0",
            "puntos_sancion": "0", "racha_partidos": [{"tipo": "G"}] if pos == 1 else [], "url_img": CREST.format(code=code),
        })
    return {"estado": "1", "sesion_ok": "1", "competicion": name, "codigo_competicion": comp_code, "grupo": "Grupo 1", "codigo_grupo": group_code,
            "tipo_competicion": "2", "jornada": round_number, "fecha_jornada": "13-09-2026", "clasificacion": out, "promociones": []}


def scorers(n=21, comp="PREFERENTE AFICIONADO", first_team="1074"):
    goles = []
    for i in range(1, n + 1):
        team = first_team if i == 1 else TEAM_CODES[(i * 3) % len(TEAM_CODES)]
        goles.append({
            "codigo_equipo": team, "codigo_jugador": str(900000 + i), "escudo_equipo": "", "foto": "", "goles": str(max(1, 3 - i // 7) if i > 1 else 2),
            "goles_penalti": "0", "goles_por_partidos": "1.0", "jugador": f"APELLIDO{i:02d} PRUEBA, JUGADOR{i:02d}", "nombre_equipo": TEAM_NAMES[team],
            "partidos_jugados": "1",
        })
    return {"estado": "1", "sesion_ok": "1", "competicion": comp, "grupo": "Grupo 1", "goles": goles}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    f11 = [competition("1", r) for r in F11]
    write(OUT / "competitions_f11_2026-10-31.json", f11)
    write(OUT / "competitions_f7_2026-10-31.json", [competition("2", r) for r in F7])
    write(OUT / "competitions_f5_2026-10-31.json", [competition("4", r) for r in F5])
    # El listado que acompaña a la página de clasificación: sin Primera Fútbol Femenino ni el cadete femenino F11 de otoño
    page_list = [c for c in f11 if c["codigo"] not in ("26737874",)]

    (OUT / "results_pref_g1_j1.html").write_text(page({"results": results()}), encoding="utf-8")
    (OUT / "standings_pref_g1_j1.html").write_text(page({"standings": standings(), "competitions": page_list}), encoding="utf-8")
    (OUT / "scorers_pref_g1.html").write_text(page({"scorers": scorers()}), encoding="utf-8")

    # Grupos por competición (los de Preferente los fabrica el test)
    write(OUT / "groups_26738226.json", [group("26738227", "GRUPO 7", 34, 18)])
    write(OUT / "groups_26737698.json", [group("26737699", "Grupo 2", 34, 18), group("26737700", "Grupo 1", 34, 18)])
    write(OUT / "groups_26737706.json", [group(str(26737707 + i), f"Grupo {i + 1}", 34, 18) for i in range(8)])
    seg_codes = [26738301, 26738302, 26738303, 26738311, 26738304, 26738305, 26738306, 26738307, 26738308, 26738309, 26738310, 26738312,
                 26738313, 26738315, 26738316, 26738314, 26738321, 26738317, 26738318, 26738319, 26738320, 26738322]
    write(OUT / "groups_26738300.json", [group(str(c), f"Grupo {i + 1}", 30, 14 + i % 3) for i, c in enumerate(seg_codes)])
    write(OUT / "groups_27786100.json", [group("27786685", "GRUPO 7", 30, 15, scorers="0")])
    write(OUT / "groups_26738252.json", [group("26738253", "GRUPO ÚNICO", 30, 16)])
    write(OUT / "groups_26738262.json", [group("26738263", "Grupo 1", 30, 16), group("26738264", "Grupo 2", 30, 16)])

    fem = [c for c in f11 if c["codigo"] in ("27786100", "26738252", "26738262")]
    write(OUT / "femenino_tercera_g7_j3.json", {
        "competitions": fem,
        "results": results("27786685", "27786100", "TERCERA FEDERACION DE FÚTBOL FEMENINO", 30, "3", 8),
        "standings": standings("27786685", "27786100", "TERCERA FEDERACION DE FÚTBOL FEMENINO", "3", 15),
        "scorers": scorers(80, "PRIMERA DIVISION AUTONOMICA FEMENINO"),
    })
    (OUT / "robots_www.rffm.es_20260919.txt").write_text(
        "# robots.txt de ejemplo con la misma forma que el de la RFFM\nUser-agent: *\nAllow: /\n\nDisallow: /fichaequipo/\nDisallow: /fichajugador/\nDisallow: /acta-partido/\n",
        encoding="utf-8",
    )

    # Escudos sintéticos
    from PIL import Image, ImageDraw

    CRESTS.mkdir(parents=True, exist_ok=True)
    pts = [(14, 10), (114, 10), (114, 70), (64, 118), (14, 70)]

    def shield(name, left, right, outline, split=64):
        img = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        mask = Image.new("L", (128, 128), 0)
        ImageDraw.Draw(mask).polygon(pts, fill=255)
        body = Image.new("RGBA", (128, 128), left)
        ImageDraw.Draw(body).rectangle([split, 0, 128, 128], fill=right)
        img.paste(body, (0, 0), mask)
        ImageDraw.Draw(img).line(pts + [pts[0]], fill=outline, width=4)
        img.save(CRESTS / f"{name}.webp", format="WEBP", lossless=True)

    shield("azul-y-rojo", (48, 45, 84, 255), (182, 27, 23, 255), (255, 255, 255, 255), split=80)
    shield("amarillo-y-verde", (208, 181, 48, 255), (10, 115, 10, 255), (176, 176, 176, 255), split=80)
    bw = Image.new("RGBA", (128, 128), (255, 255, 255, 255))
    ImageDraw.Draw(bw).rectangle([32, 32, 95, 95], fill=(0, 0, 0, 255))
    bw.save(CRESTS / "blanco-y-negro.webp", format="WEBP", lossless=True)
    print("fixtures sintéticas generadas")


if __name__ == "__main__":
    main()
