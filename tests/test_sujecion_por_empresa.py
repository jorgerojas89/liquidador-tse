"""
Sujeción a la TSE por empresa (art. 45 Ley 99/1993) — corrección del 2026-08-27.

El sistema aplicaba el umbral de 10.000 kW a cada central. La norma lo aplica a
la EMPRESA: "las empresas generadoras de energía hidroeléctrica cuya potencia
nominal instalada total supere los 10.000 kilovatios". La diferencia no es
teórica: RIO CALI (1.800 kW), NIMA (6.700 kW) y RIO FRIO I (1.670 kW) quedaban
fuera del sistema pese a que la ONVS ya levantó su cartografía, porque sus
empresas sí superan el umbral.
"""
from decimal import Decimal

import pytest

from motor.parametros import ParametrosDecreto800
from motor.tse_calculator import es_empresa_sujeta_a_tse

D = Decimal

# Hasta el 2026-11-15 aquí también se probaba `es_sujeta_a_tse`, la función
# heredada que caía al umbral por central si faltaba la capacidad de la
# empresa. Se retiró con el resto del orquestador del SISTSE: la liquidación
# solo decide con la capacidad de la empresa (`datos.cargue_xm.sujecion_central`)
# y, si no la tiene, la central queda «sujeción sin determinar».


def test_una_central_pequena_de_una_empresa_grande_si_tributa():
    """El caso que motivó la corrección: RIO CALI (1.800 kW) tributa porque su
    empresa suma 14.170 kW. Decide la empresa; la central no entra."""
    assert es_empresa_sujeta_a_tse(D("14170")) is True
    assert es_empresa_sujeta_a_tse(D("9500")) is False


def test_el_umbral_de_la_empresa_es_estrictamente_mayor():
    assert es_empresa_sujeta_a_tse(D("10000")) is False
    assert es_empresa_sujeta_a_tse(D("10000.01")) is True


def test_el_umbral_es_un_parametro():
    assert es_empresa_sujeta_a_tse(D("14170"), ParametrosDecreto800(capacidad_minima_kw=D("20000"))) is False


# ---------------------------------------------------------------------------
# Contraste contra la hoja "Empresas Sujetos activos" de XM
# ---------------------------------------------------------------------------

from datos import rutas  # noqa: E402
from datos.sujecion_empresas import (  # noqa: E402
    leer_hoja_sujetos_activos,
    reproducir_hoja_xm,
)


def _archivo_2025():
    dir_xm = rutas.dir_xm()
    if dir_xm is None:
        return None
    candidatos = sorted(dir_xm.glob("*2025*.xlsx"))
    return candidatos[0] if candidatos else None


@pytest.mark.skipif(_archivo_2025() is None, reason="sin el reporte de XM 2025")
def test_se_reproduce_exactamente_la_hoja_empresas_sujetos_activos():
    """Jorge señaló esa pestaña como la referencia. Reproducirla es la prueba
    de que el criterio implementado es el mismo que usa XM: suma de capacidad
    efectiva neta por agente, de enero, sobre los tipos del umbral."""
    from datos.parse_xm_generacion import parsear_xm_generacion_anual

    archivo = _archivo_2025()
    oficial = leer_hoja_sujetos_activos(archivo)
    assert oficial, "no se encontró la pestaña 'Empresas Sujetos activos'"
    calculado = reproducir_hoja_xm(parsear_xm_generacion_anual(archivo))
    diferencias = {
        nombre: (valor, calculado.get(nombre))
        for nombre, valor in oficial.items()
        if calculado.get(nombre) != valor
    }
    assert not diferencias, f"{len(diferencias)} empresas no coinciden: {list(diferencias)[:5]}"


@pytest.mark.skipif(_archivo_2025() is None, reason="sin el reporte de XM 2025")
def test_hay_centrales_pequenas_de_empresas_sujetas():
    """Si esto deja de pasar, el criterio volvió a ser por central."""
    from datos.parse_xm_generacion import parsear_xm_generacion_anual
    from datos.sujecion_empresas import capacidades_por_empresa

    archivo = _archivo_2025()
    registros = parsear_xm_generacion_anual(archivo)
    empresas = capacidades_por_empresa(registros, 2025, archivo.name)

    pequenas_sujetas = []
    for r in registros:
        if (r.tipo_generacion or "").upper() != "HIDRAULICA":
            continue
        if r.capacidad_efectiva_kw is None or r.capacidad_efectiva_kw > D("10000"):
            continue
        empresa = empresas.get((r.agente or "").strip())
        if empresa and empresa.es_sujeto_activo:
            pequenas_sujetas.append(r.recurso)
    assert pequenas_sujetas, (
        "no se encontró ninguna central de menos de 10.000 kW perteneciente a una "
        "empresa sujeto activo; el umbral se está aplicando por central"
    )
