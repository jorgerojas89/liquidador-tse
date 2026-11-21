"""Fixtures compartidas. Todos los datos de aquí son SINTÉTICOS salvo el
maestro de autoridades, que es el real (`datos/maestro_autoridades.csv`)."""
from decimal import Decimal

import pytest

from datos import repositorio as repo
from datos.maestro_autoridades import cargar_maestro

D = Decimal


@pytest.fixture
def con():
    c = repo.abrir(":memory:")
    repo.cargar_autoridades(c, cargar_maestro().values())
    yield c
    c.close()


@pytest.fixture
def proyecto(con):
    return repo.crear_proyecto(con, "PCH SINTÉTICA", codigo_submercado="SINT1")


def nueva_delimitacion(con, proyecto_id, vigente_desde="2025-01-01", radicado="RAD-SINT-1",
                       pdf_sha256="a" * 64, filas=None):
    filas = filas or [
        {"autoridad": "CVC", "s1_ha": D("2900.92344")},
        {"autoridad": "PNNLos Farallones de Cali", "s1_ha": D("889.88840")},
    ]
    return repo.crear_delimitacion(
        con, proyecto_id, radicado, fecha_documento=vigente_desde, vigente_desde=vigente_desde,
        modo="hectareas", filas=filas, pdf_nombre="delimitacion.pdf", pdf_sha256=pdf_sha256,
    )


def cargar_xm(con, version, periodo="2025-06", kwh=D("37123456"), sha="b" * 64):
    return repo.registrar_carga_xm(con, f"xm-{version}.xlsx", sha, [{
        "periodo": periodo, "version_xm": version, "codigo_submercado": "SINT1",
        "recurso": "PCH SINTÉTICA", "agente": "EMPRESA SINTÉTICA", "tipo_generacion": "HIDRAULICA",
        "generacion_kwh": kwh, "capacidad_kw": D("19900"),
    }])


def cabecera(proyecto_id, delimitacion_id, carga_id, version_xm="TXF", caracter="oficial"):
    return {
        "proyecto_id": proyecto_id, "periodo": "2025-06", "caracter": caracter,
        "version_xm": version_xm, "carga_xm_id": carga_id, "carga_xm_sha256": "b" * 64,
        "generacion_kwh": D("37123456"), "tvb_anio": 2025, "tvb_pesos_kwh": D("189.02930"),
        "ventas_brutas": D("7017420066.08"), "bolsa_car_pnn": D("210522601.98"),
        "formula": 1, "delimitacion_id": delimitacion_id, "delimitacion_sha256": "a" * 64,
        "parametros_version": "decreto-800-2026-v1",
    }
