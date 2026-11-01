"""Colores dominantes de un escudo, para pintar la camiseta del club en la web."""

from __future__ import annotations

import io
from collections import defaultdict
from math import dist

from PIL import Image, UnidentifiedImageError

ANALYSIS_SIZE = 64  # basta para el reparto de colores y mantiene el cálculo en milisegundos
MIN_ALPHA = 128  # por debajo es fondo transparente o borde suavizado
BLACK_CEIL = 45  # los tres canales por debajo: negro de contorno
# "Blanco" = muy claro y casi sin saturación: incluye el blanco roto, el azul/rosa/crema pálidos que
# salen del brillo y del antialias del borde, que no son un color del club.
WHITE_VALUE = 0.75
WHITE_SATURATION = 0.25
BUCKET = 32  # 8 niveles por canal: agrupa tonos casi iguales (degradados, antialias)
NEUTRAL_SATURATION = 0.15  # por debajo es gris/plata (contornos, brillos): solo cuenta si no hay otro color
MIN_SHARE = 0.03  # un segundo color con menos de este reparto es ruido, no un color del escudo
MIN_DISTANCE = 80  # distancia RGB mínima entre primario y secundario


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(round(c) for c in rgb))


def _saturation(rgb: tuple[float, float, float]) -> float:
    top = max(rgb)
    return (top - min(rgb)) / top if top else 0.0


def _is_background(r: int, g: int, b: int) -> bool:
    top, bottom = max(r, g, b), min(r, g, b)
    if top <= BLACK_CEIL:
        return True
    return top / 255 >= WHITE_VALUE and (top - bottom) / top <= WHITE_SATURATION


def dominant_colors(content: bytes) -> tuple[str, str] | None:
    """(primary, secondary) como "#rrggbb" en minúsculas, o None si el escudo no tiene color propio
    (solo transparente, blanco o negro, o no es una imagen). Si solo hay un color, secondary = primary."""
    try:
        with Image.open(io.BytesIO(content)) as image:
            image = image.convert("RGBA")
            image.thumbnail((ANALYSIS_SIZE, ANALYSIS_SIZE))
            pixels = list(image.getdata())
    except (UnidentifiedImageError, OSError):
        return None

    buckets: dict[tuple[int, int, int], list[float]] = defaultdict(lambda: [0, 0.0, 0.0, 0.0])
    counted = 0
    for r, g, b, a in pixels:
        if a < MIN_ALPHA:
            continue
        if _is_background(r, g, b):
            continue
        slot = buckets[(r // BUCKET, g // BUCKET, b // BUCKET)]
        slot[0] += 1
        slot[1] += r
        slot[2] += g
        slot[3] += b
        counted += 1
    if not counted:
        return None

    ranked = sorted(
        ((n, (sr / n, sg / n, sb / n)) for n, sr, sg, sb in buckets.values()),
        key=lambda item: item[0],
        reverse=True,
    )
    # Un escudo plateado con un color real: el color real manda. Gris solo si no hay ninguno.
    chromatic = [item for item in ranked if _saturation(item[1]) >= NEUTRAL_SATURATION]
    if sum(n for n, _ in chromatic) / counted >= MIN_SHARE:
        ranked = chromatic
    primary = ranked[0][1]
    secondary = next(
        (color for n, color in ranked[1:] if n / counted >= MIN_SHARE and dist(color, primary) >= MIN_DISTANCE),
        primary,
    )
    return _hex(primary), _hex(secondary)
