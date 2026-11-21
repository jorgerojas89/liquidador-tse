"""
Casos de prueba del motor de cálculo TSE (Decreto 800 de 2026).

Todos los casos de este archivo son SINTÉTICOS — construidos a partir del
texto del Decreto para ejercitar la lógica, NO son liquidaciones reales.

Hasta el 2026-11-15 estos casos corrían contra `liquidar_proyecto`, el
orquestador heredado del SISTSE, que la aplicación no usaba (y que aplicaba el
umbral por central). Al retirarlo, los casos se pasaron, con las mismas cifras,
al motor que sí liquida: `motor.liquidacion.liquidar_central` y
`motor.tse_calculator.calcular_razones_de_reparto`. Se dejaron de probar los
conceptos que ese motor ya no tiene: umbral por central (el umbral es por
empresa, `test_sujecion_por_empresa.py`), estimación y páramo provisional.
"""
from decimal import Decimal
from fractions import Fraction

import pytest

from motor.delimitacion import Delimitacion, FilaDelimitacion
from motor.liquidacion import liquidar_central
from motor.parametros import ParametrosDecreto800
from motor.tse_calculator import (
    AreaAutoridad,
    TseCalculatorError,
    a_fraccion,
    calcular_razones_de_reparto,
    centavos,
)

D = Decimal
TVB = D("192.23886")      # TVB 2026 de la serie CREG
TRES = Fraction(3, 100)


def _delim(*areas, con_s2=False) -> Delimitacion:
    """areas: (sigla, S1) o (sigla, S1, S2)."""
    filas = tuple(FilaDelimitacion(a[0], a[1], a[2] if len(a) > 2 else None) for a in areas)
    return Delimitacion(modo="hectareas", filas=filas, trae_s2=con_s2)


def _por_autoridad(k):
    return {f.autoridad: f for f in k.filas}


def _exacto(k, f) -> Fraction:
    """Monto exacto, sin redondear: razón × 3 % × ventas brutas."""
    return f.razon * TRES * Fraction(k.ventas_brutas)


# ---------------------------------------------------------------------------
# Fundamentos: ventas brutas / TSE total / bolsa del 3 %
# ---------------------------------------------------------------------------

def test_ventas_brutas_tse_y_bolsa():
    k = liquidar_central(D("1000000"), TVB, _delim(("CAR-A", D("1"))))
    assert k.ventas_brutas == D("192238860")       # generación × TVB, exacto
    assert k.tse_total == D("11534331.60")          # 6 %
    assert k.bolsa_car_pnn == D("5767165.80")       # 3 %


def test_area_negativa_lanza_error():
    with pytest.raises(TseCalculatorError):
        AreaAutoridad(autoridad_id="X", s1_i=D("-1"))


def test_exactitud_rechaza_float():
    with pytest.raises(TseCalculatorError):
        AreaAutoridad("CAR-A", 2900.92344)


def test_exactitud_acepta_fraccion_a_b():
    assert a_fraccion("3/7") == Fraction(3, 7)
    assert a_fraccion("43,21875") == Fraction("43.21875")


# ---------------------------------------------------------------------------
# Fórmula 1 — sin S2
# ---------------------------------------------------------------------------

def test_f1_reparto_proporcional_a_s1():
    # 50.000.000 kWh × 192,23886 = 9.611.943.000; 3 % = 288.358.290; 60 % y 40 %
    k = liquidar_central(D("50000000"), TVB, _delim(("CAR-A", D("6000")), ("CAR-B", D("4000"))))
    a, b = _por_autoridad(k)["CAR-A"], _por_autoridad(k)["CAR-B"]
    assert k.formula == 1
    assert (a.razon, b.razon) == (Fraction(3, 5), Fraction(2, 5))
    assert (a.porcentaje, b.porcentaje) == (D("60.00000"), D("40.00000"))
    assert (a.monto, b.monto) == (D("173014974.00"), D("115343316.00"))
    assert a.monto + b.monto == k.bolsa_car_pnn


def test_f1_un_solo_beneficiario_recibe_la_bolsa_completa():
    k = liquidar_central(D("50000000"), TVB, _delim(("CAR-UNICA", D("8000"))))
    assert k.filas[0].razon == 1 and k.filas[0].monto == k.bolsa_car_pnn


def test_f1_tres_autoridades_reparto_proporcional():
    k = liquidar_central(D("50000000"), TVB, _delim(("CAR-A", D("5000")), ("CAR-B", D("3000")), ("PNN-C", D("2000"))))
    assert [f.razon for f in k.filas] == [Fraction(1, 2), Fraction(3, 10), Fraction(1, 5)]
    assert sum(f.monto for f in k.filas) == k.bolsa_car_pnn


def test_f1_s1_t_cero_lanza_error():
    with pytest.raises(TseCalculatorError, match="S1_T = 0"):
        liquidar_central(D("50000000"), TVB, _delim(("CAR-A", None)))


# ---------------------------------------------------------------------------
# Fórmula 2 — con S2
# ---------------------------------------------------------------------------

def _con_s2():
    return _delim(("CAR-A", D("6000"), D("1500")), ("CAR-B", D("4000"), D("2500")), con_s2=True)


def test_f2_mezcla_convexa_con_alpha_del_decreto():
    # S1_T = 10.000, S2_T = 4.000: (1 − 0,5228)·0,6 + 0,5228·0,375 = 0,48237 exacto
    k = liquidar_central(D("80000000"), TVB, _con_s2())
    a, b = _por_autoridad(k)["CAR-A"], _por_autoridad(k)["CAR-B"]
    assert k.formula == 2 and k.alpha == D("0.5228")
    assert (a.razon, b.razon) == (Fraction("0.48237"), Fraction("0.51763"))
    assert a.monto + b.monto == k.bolsa_car_pnn


def test_f2_un_solo_beneficiario_recibe_la_bolsa_completa():
    k = liquidar_central(D("80000000"), TVB, _delim(("PNN-UNICO", D("9000"), D("3000")), con_s2=True))
    assert k.filas[0].razon == 1 and k.filas[0].monto == k.bolsa_car_pnn


def test_f2_s2_t_cero_con_paramo_usa_formula_1():
    """El Decreto no define este caso; se usa la Fórmula 1 (supuesto operativo,
    `ParametrosDecreto800.s2_t_cero_con_paramo`)."""
    razones = calcular_razones_de_reparto(
        [AreaAutoridad("CAR-A", D("6000"), D("0")), AreaAutoridad("CAR-B", D("4000"), D("0"))], hay_paramo=True)
    assert {r.formula_aplicada for r in razones} == {1}
    assert [r.razon for r in razones] == [Fraction(3, 5), Fraction(2, 5)]


def test_f2_alpha_es_parametro_configurable_no_hardcodeado():
    """Regla 6: α vive en `motor/parametros.py`. Cambiarlo cambia el resultado."""
    oficial = liquidar_central(D("80000000"), TVB, _con_s2())
    otro = liquidar_central(D("80000000"), TVB, _con_s2(), parametros=ParametrosDecreto800(alpha=D("0.7")))
    assert _por_autoridad(oficial)["CAR-A"].razon != _por_autoridad(otro)["CAR-A"].razon
    assert otro.alpha == D("0.7")


# ---------------------------------------------------------------------------
# Casos límite
# ---------------------------------------------------------------------------

def test_sin_autoridades_lanza_error():
    with pytest.raises(TseCalculatorError, match="ninguna autoridad"):
        liquidar_central(D("50000000"), TVB, _delim())


def test_residuo_de_centavos_va_al_mayor_beneficiario():
    k = liquidar_central(D("33333333"), TVB,
                         _delim(("CAR-A", D("3333")), ("CAR-B", D("3333")), ("CAR-C", D("3334"))))
    assert sum(f.monto for f in k.filas) == k.bolsa_car_pnn
    assert [f.autoridad for f in k.filas if f.ajuste_residuo] in ([], ["CAR-C"])


# ---------------------------------------------------------------------------
# Precisión con hectáreas irregulares (auditoría 2026-08-26, hallazgos A1/D1)
#
# Con hectáreas redondas cualquier error de redondeo vale cero. Las
# delimitaciones reales traen 5 decimales: estos casos usan cifras así.
# Con razones exactas (decisión 2026-11-11) la única desviación posible es la
# cuantización a centavos: medio centavo por autoridad, más el residuo que
# recibe una sola (a lo sumo medio centavo por cada autoridad).
# ---------------------------------------------------------------------------

IRREGULARES = (("CAR-A", D("12345.67891")), ("CAR-B", D("9876.54321")), ("PNN-C", D("7654.32109")))


def _tope(n_autoridades: int) -> Fraction:
    return Fraction(1, 200) * (n_autoridades + 1)


def test_precision_f1_ninguna_autoridad_se_desvia_mas_que_la_cuantizacion():
    k = liquidar_central(D("250000000"), TVB, _delim(*IRREGULARES))
    for f in k.filas:
        assert abs(Fraction(f.monto) - _exacto(k, f)) <= _tope(len(k.filas)), f.autoridad
    assert sum(f.monto for f in k.filas) == k.bolsa_car_pnn


def test_precision_f2_con_hectareas_irregulares():
    d = _delim(("CAR-A", D("8234.11117"), D("1204.55501")), ("CAR-B", D("5117.90003"), D("2911.44409")), con_s2=True)
    k = liquidar_central(D("250000000"), TVB, d)
    assert k.formula == 2
    for f in k.filas:
        assert abs(Fraction(f.monto) - _exacto(k, f)) <= _tope(len(k.filas)), f.autoridad
    assert sum(f.monto for f in k.filas) == k.bolsa_car_pnn


def test_precision_la_razon_exacta_reconstruye_el_monto_al_centavo():
    """Explicabilidad (regla 8): con la razón exacta a/b una CAR rehace su
    monto a mano y le da el mismo centavo."""
    k = liquidar_central(D("250000000"), TVB, _delim(*IRREGULARES))
    for f in k.filas:
        if not f.ajuste_residuo:   # a esa fila se le sumó el residuo de centavos a propósito
            assert f.monto == centavos(_exacto(k, f))


def test_exactitud_las_razones_suman_uno_sin_ajuste():
    """Decisión de Jorge (2026-11-11): Σ razón = 1 sin residuo, también con
    hectáreas irregulares, con Fórmula 2 y con un área diminuta."""
    for d in (
        _delim(*IRREGULARES),
        _delim(("CAR-A", D("8234.11117"), D("1204.55501")), ("CAR-B", D("5117.90003"), D("2911.44409")),
               ("PNN-C", D("0.00001"), D("0.00003")), con_s2=True),
    ):
        k = liquidar_central(D("250000000"), TVB, d)
        assert sum((f.razon for f in k.filas), Fraction(0)) == 1


def test_precision_muchas_autoridades_no_acumula_error():
    """Ocho autoridades con superficies irregulares."""
    areas = tuple((f"CAR-{i}", D(f"{1000 + i * 137}.{i}7913")) for i in range(1, 9))
    k = liquidar_central(D("250000000"), TVB, _delim(*areas))
    for f in k.filas:
        assert abs(Fraction(f.monto) - _exacto(k, f)) <= _tope(len(k.filas)), f.autoridad
    assert sum(f.monto for f in k.filas) == k.bolsa_car_pnn
