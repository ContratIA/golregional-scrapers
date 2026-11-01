"""Normalización de nombres para mostrar y para comparar."""

from __future__ import annotations

import re
import unicodedata

from slugify import slugify

# Siglas societarias que aparecen con puntos en las federaciones: "C.D.", "S.A.D."…
_ACRONYM_DOTS = re.compile(r"\b((?:[A-ZÑ]\.){2,})")
_KEEP_UPPER = {"CD", "CF", "UD", "AD", "SD", "RC", "CP", "CDE", "SAD", "EF", "FC", "AC", "CDC", "ADC", "UE", "AF", "II", "III", "IV", "RSD", "CUC"}
# Solo conectores: "La/Las/Los/El" forman parte de topónimos ("Las Rozas", "El Escorial").
_LOWER_WORDS = {"de", "del", "y"}
# Palabras sin artículo/sigla que definen el "núcleo" del nombre para comparar.
_NOISE_TOKENS = {"cd", "cf", "ud", "ad", "sd", "rc", "cp", "cde", "sad", "ef", "fc", "ac", "club", "deportivo", "deportiva", "futbol", "de", "del", "la", "el", "y"}


def _collapse_acronyms(name: str) -> str:
    return _ACRONYM_DOTS.sub(lambda m: m.group(1).replace(".", "") + " ", name)


def display_team_name(raw: str) -> str:
    """"C.D. GALAPAGAR 'B'" → "CD Galapagar B"."""
    name = _collapse_acronyms(raw.strip())
    name = name.replace("'", " ").replace('"', " ").replace("�", "")
    words = []
    for i, word in enumerate(name.split()):
        bare = word.strip(".,")
        if bare.upper() in _KEEP_UPPER or (len(bare) == 1 and bare.isalpha()) or bare.isdigit():
            words.append(bare.upper())
        elif i > 0 and bare.lower() in _LOWER_WORDS:
            words.append(bare.lower())
        else:
            words.append(bare[:1].upper() + bare[1:].lower())
    return " ".join(w for w in words if w)


def display_player_name(raw: str) -> str:
    """"PEREZ LOPEZ, JUAN" → "Juan Perez Lopez"."""
    if "," in raw:
        surnames, given = (p.strip() for p in raw.split(",", 1))
        raw = f"{given} {surnames}"
    return " ".join(
        w.lower() if i > 0 and w.lower() in _LOWER_WORDS else w[:1].upper() + w[1:].lower()
        for i, w in enumerate(raw.replace("�", "").split())
    )


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalize_for_match(name: str) -> str:
    """Clave de comparación: minúsculas, sin acentos, sin siglas ni ruido, letra de equipo al final."""
    text = strip_accents(_collapse_acronyms(name.upper())).lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text.replace("�", ""))
    tokens = [t for t in text.split() if t not in _NOISE_TOKENS]
    return " ".join(tokens)


def slug(text: str) -> str:
    return slugify(strip_accents(text.replace("�", "")), lowercase=True)
