"""Colores dominantes del escudo: 3 escudos reales (copias WebP del Storage de producción) y casos límite."""

import colorsys
import io
import re
from pathlib import Path

import pytest
from PIL import Image

from core.colors import dominant_colors

CRESTS = Path(__file__).parent / "fixtures" / "crests"
HEX = re.compile(r"^#[0-9a-f]{6}$")


def crest(name: str) -> bytes:
    return (CRESTS / f"{name}.webp").read_bytes()


def hue(color: str) -> float:
    r, g, b = (int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hsv(r, g, b)[0] * 360


def png(size, draw) -> bytes:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw(image)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def fill(image, box, rgba):
    image.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), rgba), box[:2])


def test_escudo_real_azul_marino_y_rojo():
    # CD Colonia Moscardó: franjas azul marino y rojo sobre fondo transparente y blanco.
    assert dominant_colors(crest("azul-y-rojo")) == ("#302d54", "#b51a16")


def test_escudo_real_ignora_el_gris_del_contorno():
    # CP Parla Escuela: contorno plateado con amarillo y verde; el gris no debe ganar a un color real.
    primary, secondary = dominant_colors(crest("amarillo-y-verde"))
    assert (primary, secondary) == ("#d0b530", "#0a730a")
    assert 40 < hue(primary) < 60  # amarillo
    assert 100 < hue(secondary) < 140  # verde


def test_escudo_real_solo_blanco_y_negro_no_tiene_color():
    # México FC: balón y escudo en blanco y negro -> sin color propio, la web no pinta camiseta.
    assert dominant_colors(crest("blanco-y-negro")) is None


def test_formato_hex_minusculas():
    for name in ("azul-y-rojo", "amarillo-y-verde"):
        assert all(HEX.match(c) for c in dominant_colors(crest(name)))


def test_un_solo_color_repite_el_primario():
    content = png((64, 64), lambda im: fill(im, (8, 8, 56, 56), (200, 20, 30, 255)))
    assert dominant_colors(content) == ("#c8141e", "#c8141e")


def test_dos_colores_primario_el_mas_frecuente():
    def draw(im):
        fill(im, (0, 0, 64, 40), (0, 60, 160, 255))  # azul, 62 %
        fill(im, (0, 40, 64, 64), (220, 30, 30, 255))  # rojo, 38 %

    assert dominant_colors(png((64, 64), draw)) == ("#003ca0", "#dc1e1e")


def test_ignora_transparente_blanco_y_casi_negro():
    def draw(im):
        fill(im, (0, 0, 64, 16), (255, 255, 255, 255))  # blanco
        fill(im, (0, 16, 64, 32), (10, 10, 10, 255))  # contorno casi negro
        fill(im, (0, 32, 64, 48), (0, 120, 60, 255))  # verde
        # el resto queda transparente

    assert dominant_colors(png((64, 64), draw)) == ("#00783c", "#00783c")


def test_pixeles_semitransparentes_no_cuentan():
    content = png((32, 32), lambda im: fill(im, (0, 0, 32, 32), (200, 0, 0, 60)))
    assert dominant_colors(content) is None


def test_tonos_casi_iguales_no_son_un_segundo_color():
    def draw(im):
        fill(im, (0, 0, 32, 64), (200, 20, 30, 255))
        fill(im, (32, 0, 64, 64), (205, 25, 35, 255))

    primary, secondary = dominant_colors(png((64, 64), draw))
    assert primary == secondary


def test_solo_gris_usa_el_gris():
    content = png((32, 32), lambda im: fill(im, (4, 4, 28, 28), (120, 120, 120, 255)))
    assert dominant_colors(content) == ("#787878", "#787878")


@pytest.mark.parametrize("content", [b"", b"<html>WAF</html>", b"\x00\x01\x02"])
def test_no_es_una_imagen(content):
    assert dominant_colors(content) is None
