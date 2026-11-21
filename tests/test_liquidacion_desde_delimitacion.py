"""
Liquidación a partir de las hectáreas digitadas de la delimitación del IGAC.

Adaptado el 2026-11-11 de SISTSE `tests/test_liquidacion_desde_oficio.py`
(«oficio» pasa a llamarse «delimitación»). De los 8 casos originales:

- 4 corren ya contra el motor, en memoria (CLAUDE.md, regla 10).
- 3 dependen del repositorio SQLite y viven en `test_repositorio.py`
  (paso 2): `test_sin_delimitacion_no_hay_aplicable`,
  `test_un_borrador_no_aplica` y `test_la_liquidacion_guarda_toda_la_trazabilidad`.
- 2 se retiraron porque comparaban contra la cartografía, que ya no existe en
  este proyecto (`test_manda_el_oficio_y_no_la_cartografia`,
  `test_la_ruta_historica_sigue_disponible_y_se_marca`).

Las cifras de hectáreas son las mismas que usaba la prueba del SISTSE; las
cifras de generación y TVB son SINTÉTICAS. Desde el 2026-11-15 los montos se
calculan con `motor.liquidacion.liquidar_central`, el motor que liquida (antes,
con el heredado `liquidar_proyecto`, ya retirado).
"""
from __future__ import annotations

from decimal import Decimal

from motor.delimitacion import Delimitacion, FilaDelimitacion
from motor.liquidacion import liquidar_central
from motor.parametros import PARAMETROS_VIGENTES
from motor.tse_calculator import AreaAutoridad, calcular_razones_de_reparto, centavos

D = Decimal
GENERACION, TVB = D("37123456"), D("189.02930")   # sintética; TVB 2025 de la serie CREG


def _liquidar(*filas):
    """filas: (sigla, S1); S1 = None es «N/A» en la delimitación."""
    return liquidar_central(GENERACION, TVB, Delimitacion(
        modo="hectareas", filas=tuple(FilaDelimitacion(s, s1) for s, s1 in filas)))


# --- Corren ya: motor en memoria ------------------------------------------

def test_sin_s2_aplica_la_formula_1():
    razones = calcular_razones_de_reparto(
        [AreaAutoridad("CAR-A", D("2900.92344")), AreaAutoridad("CAR-B", D("889.88840"))],
        hay_paramo=False,
    )
    assert {r.formula_aplicada for r in razones} == {1}
    assert sum(r.razon for r in razones) == 1  # exacto, sin ajuste


def test_con_s2_declarado_aplica_la_formula_2():
    """La fórmula la decide lo que se digitó: si trae S2, hay mezcla con α."""
    razones = calcular_razones_de_reparto(
        [
            AreaAutoridad("CAR-A", D("2900.92344"), D("6920.10189")),
            AreaAutoridad("CAR-B", D("889.88840"), D("2000.00000")),
        ],
        hay_paramo=True,
    )
    assert {r.formula_aplicada for r in razones} == {2}
    assert PARAMETROS_VIGENTES.alpha == D("0.5228")
    assert sum(r.razon for r in razones) == 1  # exacto, sin ajuste


def test_la_suma_de_los_montos_da_el_tres_por_ciento_exacto():
    """Áreas que no dividen redondo: el residuo queda asignado, no perdido."""
    k = _liquidar(("CAR-A", D("45393.38087")), ("CAR-B", D("889.88840")), ("PNN-X", D("6225.87737")))
    assert k.bolsa_car_pnn == centavos(GENERACION * TVB * D("0.03"))
    assert sum(f.monto for f in k.filas) == k.bolsa_car_pnn
    assert sum(1 for f in k.filas if f.ajuste_residuo) <= 1


def test_una_autoridad_con_s1_na_queda_registrada_sin_monto():
    """Caso de El Edén: el parque figura en la delimitación sin área de
    intersección («N/A»). Debe aparecer con cero, no desaparecer."""
    k = _liquidar(("CAR-A", D("2900.92344")), ("PNN-X", None))
    por_autoridad = {f.autoridad: f for f in k.filas}
    assert "PNN-X" in por_autoridad
    assert por_autoridad["PNN-X"].monto == D("0.00") and por_autoridad["PNN-X"].razon == 0
