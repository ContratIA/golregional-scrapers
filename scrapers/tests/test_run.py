"""Código de salida del CLI: distingue degradado (útil) de fallo total (workflow en rojo)."""

from pipeline import RunStats
from run import exit_code


def stats(**kwargs) -> RunStats:
    return RunStats(job="crests", requests_before=0, **kwargs)


def test_pasada_completa_devuelve_cero():
    assert exit_code([(True, stats())]) == 0


def test_degradado_con_trabajo_hecho_no_rompe_el_workflow():
    # El caso real del 2026-09-19: 220 escudos subidos y 2 timeouts → anomalías, pero útil.
    partial = stats(crests=220)
    partial.anomalies.append(type("A", (), {"kind": "http", "message": "timeout", "context": {}})())
    assert exit_code([(False, partial)]) == 0
    assert exit_code([(False, stats(standings=18))]) == 0
    assert exit_code([(False, stats(matches=9))]) == 0


def test_fallo_total_devuelve_uno():
    assert exit_code([(False, stats())]) == 1
    assert exit_code([(True, stats()), (False, stats())]) == 1
