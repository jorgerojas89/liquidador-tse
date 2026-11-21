"""
Sujeción a la TSE por empresa (art. 45 Ley 99/1993).

"Las empresas generadoras de energía hidroeléctrica cuya potencia nominal
instalada **total** supere los 10.000 kilovatios..." — el sujeto de la norma
es la empresa. Una generadora que supera el umbral hace tributar todas sus
centrales hidroeléctricas, incluidas las de pocos megavatios.

Referencia de XM: la hoja **"Empresas Sujetos activos"** del reporte
`GeneraciónReal_Y_CapacidadEfectiva_kWh_por_Departamento_Municipio_Agente_2025.xlsx`
es una tabla dinámica de "Suma de Capacidad Efectiva Neta" por agente. Se
reprodujo exactamente (68 de 68 empresas con valor) sumando la capacidad de
**enero** sobre los tipos HIDRAULICA + TERMICA + COGENERADOR (la hoja deja
fuera SOLAR). Con EOLICA también cuadra 68 de 68, porque en enero de 2025 las
eólicas no traen capacidad; el parámetro la incluye (ver la nota en
`motor/parametros.py`). Los tipos están en
`ParametrosDecreto800.tipos_generacion_del_umbral`.

Este módulo no usa el mes de enero para decidir: toma el **máximo mensual**
de la suma por agente (ver `capacidades_por_empresa`); enero solo sirve para
reproducir la hoja de XM (`reproducir_hoja_xm`).

Salvedad pendiente (TAREAS-JORGE.md, Bloque D): XM reporta *capacidad efectiva
neta* y el art. 45 habla de *potencia nominal instalada*, que es mayor o igual.
Se usa el máximo mensual reportado en el año como la mejor aproximación
disponible por debajo de la nominal.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from motor.parametros import PARAMETROS_VIGENTES, ParametrosDecreto800
from motor.tse_calculator import es_empresa_sujeta_a_tse

MES_DE_REFERENCIA_XM = 1  # la hoja de XM está armada con enero


@dataclass(frozen=True)
class CapacidadEmpresa:
    nombre: str
    capacidad_total_kw: Decimal
    capacidad_hidraulica_kw: Decimal
    anio: int
    tipos_incluidos: tuple[str, ...]
    archivo_fuente: str
    es_sujeto_activo: bool


def capacidades_por_empresa(
    registros,
    anio: int,
    archivo_fuente: str,
    parametros: ParametrosDecreto800 = PARAMETROS_VIGENTES,
    mes_referencia: int = MES_DE_REFERENCIA_XM,
) -> dict[str, CapacidadEmpresa]:
    """Capacidad por agente generador a partir de los registros mensuales de XM.

    Se toma el **máximo mensual** de la suma por agente, no el valor de un mes
    fijo: la capacidad efectiva fluctúa y quedarse con un mes flojo puede dejar
    por debajo del umbral a una empresa que sí lo supera. `mes_referencia` se
    conserva para poder reproducir la hoja de XM tal cual."""
    tipos = {t.upper() for t in parametros.tipos_generacion_del_umbral}
    total_mes: dict[str, dict[int, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    hidraulica_mes: dict[str, dict[int, Decimal]] = defaultdict(lambda: defaultdict(Decimal))

    for r in registros:
        if not r.agente or r.capacidad_efectiva_kw is None:
            continue
        tipo = (r.tipo_generacion or "").upper()
        agente = r.agente.strip()
        if tipo in tipos:
            total_mes[agente][r.mes] += r.capacidad_efectiva_kw
        if tipo == "HIDRAULICA":
            hidraulica_mes[agente][r.mes] += r.capacidad_efectiva_kw

    salida: dict[str, CapacidadEmpresa] = {}
    for agente, por_mes in total_mes.items():
        total = max(por_mes.values()) if por_mes else Decimal("0")
        hidro = max(hidraulica_mes.get(agente, {}).values(), default=Decimal("0"))
        if hidro <= 0:
            continue  # no es generadora hidroeléctrica: no es sujeto de la TSE
        base = total if parametros.sujecion_por == "EMPRESA" else hidro
        salida[agente] = CapacidadEmpresa(
            nombre=agente,
            capacidad_total_kw=total,
            capacidad_hidraulica_kw=hidro,
            anio=anio,
            tipos_incluidos=tuple(sorted(tipos)),
            archivo_fuente=archivo_fuente,
            es_sujeto_activo=es_empresa_sujeta_a_tse(base, parametros),
        )
    return salida


def reproducir_hoja_xm(registros, mes: int = MES_DE_REFERENCIA_XM) -> dict[str, Decimal]:
    """Suma por agente tal como la calcula la hoja "Empresas Sujetos activos":
    un mes concreto, sobre los tipos del umbral. Sirve para verificar contra el
    archivo original."""
    tipos = {t.upper() for t in PARAMETROS_VIGENTES.tipos_generacion_del_umbral}
    suma: dict[str, Decimal] = defaultdict(Decimal)
    for r in registros:
        if r.mes != mes or r.capacidad_efectiva_kw is None or not r.agente:
            continue
        if (r.tipo_generacion or "").upper() not in tipos:
            continue
        suma[r.agente.strip()] += r.capacidad_efectiva_kw
    return dict(suma)


def leer_hoja_sujetos_activos(ruta_xlsx: str | Path) -> dict[str, Decimal]:
    """Lee la pestaña "Empresas Sujetos activos" del reporte de XM, si existe."""
    import openpyxl

    wb = openpyxl.load_workbook(str(ruta_xlsx), data_only=True)
    hoja = next(
        (n for n in wb.sheetnames if "sujeto" in n.lower() and "empresa" in n.lower()), None
    )
    if hoja is None:
        return {}
    ws = wb[hoja]
    salida: dict[str, Decimal] = {}
    for fila in ws.iter_rows(min_row=2, values_only=True):
        nombre, valor = (fila + (None, None))[:2]
        if not isinstance(nombre, str) or valor is None:
            continue
        if nombre.strip().lower() in ("etiquetas de fila", "total general"):
            continue
        salida[nombre.strip()] = Decimal(str(valor))
    return salida
