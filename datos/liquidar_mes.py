"""
Liquidación del mes (paso 5): une generación de XM, TVB, delimitación vigente,
umbral por empresa y motor.

    calcular_mes(con, "2026-12")  →  MesCalculado   (en memoria, no escribe)
    guardar_mes(con, mes)         →  versiones nuevas en `liquidaciones`

Se liquida **una vez por central de XM**. Una central con varios proyectos
(GUATRON, PAJARITO) se liquida solo si todas sus delimitaciones rigen el mes y
reparten igual, y cita todos los radicados.

Una central queda **sin monto** (nunca en cero ni estimada, regla 2) cuando:
  sin central · sin delimitación · mes parcial sin dato diario · reparto distinto ·
  sin generación · mes incompleto · sin TVB · bajo el umbral ·
  sujeción sin determinar.

**Mes parcial** (Jorge, 2026-11-14; reemplaza «solo meses completos»): si una
delimitación empieza a regir dentro del mes, ese mes se liquida solo desde ese
día (incluido) hasta el último, con la generación por día de XM. Los días
anteriores no se liquidan con ella.

Carácter: `oficial` si XM ya publicó la versión firme (TXF) del mes; si no,
`preliminar` con la versión más reciente (regla 13).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from datos import cargue_xm as cx
from datos import repositorio as repo
from motor import delimitacion as dm
from motor.liquidacion import LiquidacionCentral, liquidar_central
from motor.parametros import PARAMETROS_VIGENTES, ParametrosDecreto800

LIQUIDADA = "liquidada"
SIN_GENERACION = "Sin generación"   # rótulo de una central liquidada con 0 kWh (Jorge, 2026-11-15)
MOTIVOS = {
    "sin_central": "Sin central de XM",
    "sin_delimitacion": "Sin delimitación vigente",
    "sin_dato_diario": "Mes parcial sin generación por día",
    "reparto_distinto": "Reparto distinto en una central compartida",
    "sin_generacion": "Sin generación de XM",
    "mes_incompleto": "Mes incompleto en XM",
    "sin_tvb": "Sin TVB del año",
    "bajo_umbral": "Bajo el umbral del art. 45",
    "sujecion_sin_determinar": "Sujeción sin determinar",
}


@dataclass
class CentralMes:
    codigo: str | None                    # código de submercado de XM
    proyectos: list[str]                  # nombres de los proyectos del IGAC
    proyecto_id: int                      # proyecto bajo el que se guarda (el de menor id)
    estado: str = LIQUIDADA               # LIQUIDADA o una clave de MOTIVOS
    detalle: str = ""                     # por qué no se liquida
    caracter: str | None = None           # 'oficial' | 'preliminar'
    generacion: dict | None = None        # fila de `generacion` + hash del archivo
    sujecion: cx.Sujecion | None = None
    delimitaciones: list[dict] = field(default_factory=list)
    calculo: LiquidacionCentral | None = None
    alertas: list[str] = field(default_factory=list)
    dia_desde: int = 1                    # > 1 = mes parcial
    dia_hasta: int | None = None

    @property
    def parcial(self) -> bool:
        return self.dia_desde > 1

    @property
    def dias(self) -> str:
        return f"{self.dia_desde} al {self.dia_hasta}" if self.parcial else "Mes completo"

    @property
    def liquidada(self) -> bool:
        return self.estado == LIQUIDADA

    @property
    def motivo(self) -> str:
        return "" if self.liquidada else MOTIVOS[self.estado]

    @property
    def sin_generacion(self) -> bool:
        """Liquidada con 0 kWh: XM no reporta generación de la central en los
        días liquidados. La transferencia es $ 0 y se rotula «Sin generación»
        (Jorge, 2026-11-15): un cero a secas no dice por qué."""
        return self.liquidada and self.calculo is not None and self.calculo.generacion_kwh == 0

    @property
    def nombre(self) -> str:
        return " + ".join(self.proyectos)

    @property
    def radicados(self) -> str:
        return "; ".join(d["radicado"] for d in self.delimitaciones)

    @property
    def dias_ausentes(self) -> bool:
        """XM no trae todos los días de la central (se tomaron como sin
        generación). La pantalla lo marca con *; antes lo deducía buscando un
        texto en las alertas (2026-11-15)."""
        g = self.generacion or {}
        return (g.get("dias_reportados") is not None and g.get("dias_archivo") is not None
                and g["dias_reportados"] < g["dias_archivo"])


def ordenar_nombres(nombres) -> list[str]:
    """Orden de los proyectos de una central compartida, el mismo en
    Liquidación, Exportar y Proyecciones (antes uno ordenaba por id y otro por
    nombre, y «A + B» salía como «B + A»)."""
    return sorted(nombres, key=str.casefold)


@dataclass
class MesCalculado:
    periodo: str
    tvb: dict | None                      # fila de `tvb` del año, o None
    centrales: list[CentralMes]
    parametros: ParametrosDecreto800

    @property
    def liquidadas(self) -> list[CentralMes]:
        return [c for c in self.centrales if c.liquidada]

    @property
    def no_liquidadas(self) -> list[CentralMes]:
        return [c for c in self.centrales if not c.liquidada]

    @property
    def caracter(self) -> str | None:
        """'oficial' solo si todo lo liquidado es oficial."""
        cs = {c.caracter for c in self.liquidadas}
        return None if not cs else "oficial" if cs == {"oficial"} else "preliminar"

    def total(self, campo: str) -> Decimal:
        return sum((getattr(c.calculo, campo) for c in self.liquidadas), Decimal("0.00"))

    def por_autoridad(self) -> dict[str, Decimal]:
        salida: dict[str, Decimal] = {}
        for c in self.liquidadas:
            for f in c.calculo.filas:
                salida[f.autoridad] = salida.get(f.autoridad, Decimal("0.00")) + f.monto
        return dict(sorted(salida.items()))


def validar_periodo(periodo: str) -> str:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", periodo or ""):
        raise repo.ErrorRepositorio(f"Periodo inválido: «{periodo}» (use AAAA-MM).")
    return periodo


def _generacion(con, codigo: str, periodo: str, dia_desde: int = 1):
    """(fila, carácter). Oficial si hay versión firme; si no, preliminar.
    Con `dia_desde` > 1, la generación es solo la de ese día al último."""
    def leer(caracter):
        if dia_desde > 1:
            return repo.generacion_del_tramo(con, codigo, periodo, dia_desde, caracter)
        return dict(repo.generacion_para(con, codigo, periodo, caracter))
    try:
        return leer("oficial"), "oficial"
    except repo.MesIncompleto:
        raise
    except repo.SinGeneracion:
        return leer("preliminar"), "preliminar"


def calcular_mes(con, periodo: str, parametros: ParametrosDecreto800 = PARAMETROS_VIGENTES) -> MesCalculado:
    """Calcula el mes en memoria. No escribe en la base."""
    validar_periodo(periodo)
    anio = int(periodo[:4])
    fila_tvb = repo.tvb_de(con, anio)
    tvb = dict(fila_tvb) if fila_tvb is not None else None

    grupos: dict[object, list] = {}
    for p in con.execute("SELECT * FROM proyectos ORDER BY id"):
        grupos.setdefault(p["codigo_submercado"] or ("sin", p["id"]), []).append(p)

    centrales = []
    for clave, proyectos in grupos.items():
        codigo = clave if isinstance(clave, str) else None
        c = CentralMes(codigo=codigo, proyectos=ordenar_nombres(p["nombre"] for p in proyectos),
                       proyecto_id=proyectos[0]["id"])
        centrales.append(c)
        _calcular_central(con, c, proyectos, periodo, anio, tvb, parametros)
    centrales.sort(key=lambda c: c.nombre.casefold())
    return MesCalculado(periodo=periodo, tvb=tvb, centrales=centrales, parametros=parametros)


def _no(c: CentralMes, estado: str, detalle: str) -> None:
    c.estado, c.detalle, c.calculo, c.caracter = estado, detalle, None, None


def _calcular_central(con, c: CentralMes, proyectos, periodo, anio, tvb, parametros) -> None:
    if c.codigo is None:
        return _no(c, "sin_central", "El proyecto no está enlazado a una central de XM.")

    # 1. Delimitación que rige el mes, para CADA proyecto de la central. Si
    #    alguna empieza dentro del mes, la central se liquida desde ese día.
    anteriores = []
    for p in proyectos:
        try:
            d, dia, anterior = repo.delimitacion_del_tramo(con, p["id"], periodo)
        except repo.SinDelimitacionVigente:
            return _no(c, "sin_delimitacion",
                       f"{p['nombre']} no tiene una delimitación confirmada que rija {periodo}.")
        c.delimitaciones.append({**dict(d), "proyecto": p["nombre"]})
        c.dia_desde = max(c.dia_desde, dia)
        if anterior is not None:
            anteriores.append(anterior["radicado"])
    if c.parcial:
        c.dia_hasta = repo.ultimo_dia(periodo)
        c.alertas.append(
            f"Mes parcial: la delimitación rige desde el día {c.dia_desde}, así que se liquida del {c.dia_desde} "
            f"al {c.dia_hasta} con la generación de XM de esos días.")
        if anteriores:
            c.alertas.append(
                f"Los días 1 al {c.dia_desde - 1} los regía la delimitación anterior ({', '.join(anteriores)}) y "
                f"NO quedan liquidados aquí: la aplicación liquida un solo tramo por mes. Para no dejar días "
                f"sin liquidar, una delimitación que reemplaza a otra debería regir desde el día 1.")
    memorias = [repo.delimitacion_en_memoria(con, d["id"]) for d in c.delimitaciones]
    repartos = [{r.autoridad_id: r.razon for r in dm.razones(m) if r.razon} for m in memorias]
    if any(r != repartos[0] for r in repartos[1:]):
        return _no(c, "reparto_distinto",
                   "Las delimitaciones de la central reparten distinto: XM da una sola generación y no se "
                   "puede partir por proyecto.")

    # 2. Generación del mes completo.
    try:
        g, c.caracter = _generacion(con, c.codigo, periodo, c.dia_desde)
    except repo.MesIncompleto as e:
        return _no(c, "mes_incompleto", str(e))
    except repo.SinDatoDiario as e:
        return _no(c, "sin_dato_diario", str(e))
    except repo.SinGeneracion:
        return _no(c, "sin_generacion",
                   f"XM no reporta generación de {c.codigo} en {periodo} (o el mes no se ha cargado). "
                   f"Sin dato no se liquida: no se toma como cero.")
    c.generacion = g
    if c.dias_ausentes:
        c.alertas.append(
            f"XM trae {g['dias_reportados']} de {g['dias_archivo']} días de esta central: los días ausentes "
            f"se tomaron como sin generación. Conviene verificarlo con XM.")
    if c.caracter == "preliminar":
        c.alertas.append(f"Preliminar: XM no ha publicado la versión firme (TXF); se usó {g['version_xm']}.")

    # 3. Umbral del art. 45, por empresa.
    c.sujecion = cx.sujecion_central(con, c.codigo, anio)
    if c.sujecion is None:
        return _no(c, "sujecion_sin_determinar",
                   "No hay capacidad cargada para esta central (archivo mensual de XM con capacidad): "
                   "no se puede saber si su empresa supera los 10.000 kW.")
    if not c.sujecion.sujeta:
        return _no(c, "bajo_umbral",
                   f"{c.sujecion.agente} suma {c.sujecion.capacidad_total_kw} kW ({c.sujecion.anio_capacidad}): "
                   f"no supera los {parametros.capacidad_minima_kw} kW.")
    if c.sujecion.anio_capacidad != anio:
        c.alertas.append(f"El umbral se verificó con la capacidad de {c.sujecion.anio_capacidad} "
                         f"(el archivo de {anio} no trae capacidad).")

    # 4. TVB del año del periodo.
    if tvb is None:
        return _no(c, "sin_tvb", f"No hay TVB de {anio} en Parámetros. No se usa la de otro año.")

    # 5. Motor. El reparto es el mismo en todas las delimitaciones de la central.
    c.calculo = liquidar_central(Decimal(g["generacion_kwh"]), Decimal(tvb["pesos_kwh"]), memorias[0], parametros)
    if c.sin_generacion:
        dias = f"del {c.dia_desde} al {c.dia_hasta}" if c.parcial else "del mes"
        c.alertas.append(f"{SIN_GENERACION}: XM no reporta generación de {c.codigo} en los días {dias}. "
                         f"La transferencia es $ 0,00 por esa razón.")
    if len(c.delimitaciones) > 1:
        c.alertas.append(f"Central compartida: se liquida una vez con la generación de {c.codigo} y la "
                         f"respaldan {len(c.delimitaciones)} delimitaciones con el mismo reparto.")
    if c.calculo.retenido > 0:
        c.alertas.append("Parte de la bolsa corresponde a «autoridad ambiental urbana o área en litigio»: "
                         "se calcula y queda retenida sin asignar.")


# ---------------------------------------------------------------------------
# Guardar
# ---------------------------------------------------------------------------

def _huella(cabecera: dict, filas: list[dict], delimitaciones) -> tuple:
    """Lo que define una liquidación. Si no cambia, no se crea otra versión.
    Incluye la empresa y la capacidad con que se verificó el umbral: si llega
    otra capacidad, la versión guardada ya no la documenta (2026-11-15)."""
    return (
        cabecera["caracter"], cabecera["version_xm"], cabecera["carga_xm_sha256"], str(cabecera["generacion_kwh"]),
        cabecera["tvb_anio"], str(cabecera["tvb_pesos_kwh"]), str(cabecera["bolsa_car_pnn"]),
        cabecera["parametros_version"], tuple(sorted(delimitaciones)), cabecera.get("dia_desde") or 1,
        cabecera.get("empresa"), str(cabecera.get("capacidad_empresa_kw")), cabecera.get("capacidad_anio"),
        tuple(sorted((f["autoridad"], str(f["razon"]), str(f["monto"])) for f in filas)),
    )


def _para_guardar(mes: MesCalculado, c: CentralMes) -> tuple[dict, list[dict], list[int]]:
    g, k, principal = c.generacion, c.calculo, c.delimitaciones[0]
    cabecera = {
        "proyecto_id": c.proyecto_id, "periodo": mes.periodo, "caracter": c.caracter,
        "version_xm": g["version_xm"], "carga_xm_id": g["carga_id"], "carga_xm_sha256": g["archivo_sha256"],
        "generacion_kwh": k.generacion_kwh, "tvb_anio": mes.tvb["anio"], "tvb_pesos_kwh": k.tvb_pesos_kwh,
        "ventas_brutas": k.ventas_brutas, "bolsa_car_pnn": k.bolsa_car_pnn, "formula": k.formula,
        "delimitacion_id": principal["id"], "delimitacion_sha256": principal["pdf_sha256"],
        "parametros_version": k.parametros_version,
        "codigo_submercado": c.codigo, "empresa": c.sujecion.agente,
        "capacidad_empresa_kw": c.sujecion.capacidad_total_kw, "capacidad_anio": c.sujecion.anio_capacidad,
        "dias_reportados": g["dias_reportados"], "dias_archivo": g["dias_archivo"],
        "tvb_fuente": mes.tvb["fuente"],
        "dia_desde": c.dia_desde if c.parcial else None, "dia_hasta": c.dia_hasta if c.parcial else None,
    }
    filas = [{"autoridad": f.autoridad, "s1_ha": f.s1_ha, "s2_ha": f.s2_ha, "razon": f.razon,
              "porcentaje_mostrado": f.porcentaje, "monto": f.monto, "ajuste_residuo": f.ajuste_residuo}
             for f in k.filas]
    return cabecera, filas, [d["id"] for d in c.delimitaciones]


def _huella_guardada(con, liq) -> tuple:
    filas = [{"autoridad": f["autoridad_sigla"], "razon": f["razon"], "monto": f["monto"]}
             for f in repo.filas_liquidacion(con, liq["id"])]
    ids = [d["delimitacion_id"] for d in repo.delimitaciones_de_liquidacion(con, liq["id"])]
    return _huella(dict(liq), filas, ids)


def _ultima(con, proyecto_id: int, periodo: str):
    return con.execute(
        "SELECT * FROM liquidaciones WHERE proyecto_id = ? AND periodo = ? ORDER BY version_numero DESC LIMIT 1",
        (proyecto_id, periodo),
    ).fetchone()


def pendientes_de_guardar(con, mes: MesCalculado) -> list[CentralMes]:
    """Centrales liquidadas cuyo cálculo actual no coincide con lo último guardado."""
    salida = []
    for c in mes.liquidadas:
        cabecera, filas, ids = _para_guardar(mes, c)
        previa = _ultima(con, c.proyecto_id, mes.periodo)
        if previa is None or _huella_guardada(con, previa) != _huella(
                cabecera, [{**f, "razon": repo.a_texto(f["razon"])} for f in filas], ids):
            salida.append(c)
    return salida


def guardadas_que_ya_no_liquidan(con, mes: MesCalculado) -> list[dict]:
    """Liquidaciones guardadas de centrales que hoy no se liquidan (p. ej., se
    corrigió una delimitación o cambió la TVB). Se señalan; no se borran."""
    vivas = {c.proyecto_id for c in mes.liquidadas}
    return [dict(l) for l in repo.liquidaciones_del_periodo(con, mes.periodo) if l["proyecto_id"] not in vivas]


SIN_GUARDAR = "Calculada pero sin guardar"


@dataclass(frozen=True)
class SinLiquidacionGuardada:
    """Central del mes que no tiene liquidación guardada (hoja «No liquidados»)."""
    nombre: str
    codigo: str | None
    motivo: str
    detalle: str


def no_liquidadas_de_lo_guardado(mes: MesCalculado, guardadas: list[dict]) -> list[SinLiquidacionGuardada]:
    """Lo que acompaña a lo guardado en el XLSX y el PDF: las centrales del mes
    SIN liquidación guardada. Antes salía del cálculo de hoy y el archivo se
    contradecía: una central guardada que hoy ya no se liquida quedaba sumada en
    los totales y a la vez en «No liquidados», y una calculada sin guardar no
    aparecía en ninguna parte (corregido el 2026-11-15)."""
    con_guardada = {g["proyecto_id"] for g in guardadas}
    salida = []
    for c in mes.centrales:
        if c.proyecto_id in con_guardada:
            continue  # se exporta lo guardado; si hoy ya no se liquida, lo dice `avisos_de_lo_guardado`
        if c.liquidada:
            salida.append(SinLiquidacionGuardada(
                c.nombre, c.codigo, SIN_GUARDAR,
                "Con los datos de hoy se puede liquidar, pero no se ha guardado en «Liquidación»: no entra en "
                "los totales de este archivo."))
        else:
            salida.append(SinLiquidacionGuardada(c.nombre, c.codigo, c.motivo, c.detalle))
    return salida


def avisos_de_lo_guardado(con, mes: MesCalculado, guardadas: list[dict]) -> list[str]:
    """En qué difiere lo guardado (lo que se exporta) del cálculo de hoy. Van
    en la pantalla Exportar y dentro del XLSX y el PDF."""
    con_guardada = {g["proyecto_id"] for g in guardadas}
    avisos = []
    if huerfanas := guardadas_que_ya_no_liquidan(con, mes):
        avisos.append(f"{len(huerfanas)} centrales guardadas ya no se liquidarían con los datos de hoy "
                      f"({', '.join(sorted(h['proyecto'] for h in huerfanas))}): se exporta lo guardado.")
    if cambiadas := [c for c in pendientes_de_guardar(con, mes) if c.proyecto_id in con_guardada]:
        avisos.append(f"{len(cambiadas)} centrales tienen hoy un cálculo distinto del guardado "
                      f"({', '.join(c.nombre for c in cambiadas)}): se exporta lo guardado.")
    if sin_guardar := [c for c in mes.liquidadas if c.proyecto_id not in con_guardada]:
        avisos.append(f"{len(sin_guardar)} centrales se pueden liquidar hoy pero no se han guardado: figuran en "
                      f"«No liquidados» y no entran en los totales.")
    return avisos


def guardar_mes(con, mes: MesCalculado) -> dict[str, list[str]]:
    """Guarda como versión nueva cada central cuyo cálculo cambió. Lo que ya
    estaba guardado igual no se repite (no se crean versiones idénticas)."""
    pendientes = pendientes_de_guardar(con, mes)
    # Todo el mes en una transacción: o se guardan todas o ninguna.
    repo.guardar_liquidaciones(con, [_para_guardar(mes, c) for c in pendientes])
    return {"guardadas": [c.nombre for c in pendientes],
            "sin_cambios": [c.nombre for c in mes.liquidadas if c not in pendientes]}


# ---------------------------------------------------------------------------
# Lo guardado, para mostrar y exportar
# ---------------------------------------------------------------------------

def liquidaciones_guardadas(con, periodo: str) -> list[dict]:
    """Última versión guardada de cada central del mes, con sus filas, las
    delimitaciones que la respaldan y el nombre de sus proyectos."""
    salida = []
    for l in repo.liquidaciones_del_periodo(con, periodo):
        respaldo = [dict(d) for d in repo.delimitaciones_de_liquidacion(con, l["id"])]
        salida.append({
            **dict(l),
            "proyectos": ordenar_nombres(d["proyecto"] for d in respaldo) or [l["proyecto"]],
            "delimitaciones": respaldo,
            "filas": [dict(f) for f in repo.filas_liquidacion(con, l["id"])],
            "archivo_xm": con.execute("SELECT archivo_nombre FROM cargas_xm WHERE id = ?",
                                      (l["carga_xm_id"],)).fetchone()[0],
        })
    salida.sort(key=lambda x: " + ".join(x["proyectos"]).casefold())
    return salida
