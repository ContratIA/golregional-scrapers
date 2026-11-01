from chain_check import should_continue


def test_sigue_solo_si_la_pasada_agoto_el_tope_y_termino_bien():
    assert should_continue({"ok": True, "requests": 1500})
    assert not should_continue({"ok": True, "requests": 900})
    assert not should_continue({"ok": False, "requests": 1500})  # fallo sin guardar nada: se detiene
    assert should_continue({"ok": False, "requests": 1500, "matches_upserted": 10251})  # anomalías sueltas, pero avanzó
    assert not should_continue({"ok": False, "requests": 900, "matches_upserted": 50})
    assert not should_continue(None)
