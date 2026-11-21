"""El maestro real de autoridades carga sin errores."""
from datos.maestro_autoridades import cargar_maestro


def test_el_maestro_real_carga_y_es_coherente():
    maestro = cargar_maestro()
    assert len(maestro) == 49  # 23 CAR (con CVS, 2026-11-13) y 26 PNN
    assert {a.tipo for a in maestro.values()} == {"CAR", "PNN"}
    assert maestro["CORPONARIÑO"].nombre.endswith("NARIÑO")
