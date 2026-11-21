"""
Proyección de ingresos de un año futuro por autoridad ambiental (Jorge,
2026-11-14). Capacidad nueva: **solo lee** la base; no guarda nada.

    proyectar_anio(con, 2027, tvb)  →  Proyeccion   (en memoria)
    xlsx_proyeccion(proyeccion, …)  →  bytes

Para cada central y mes del año:
  generación estimada (bajo / medio / alto, `motor/proyeccion.py`)
  × TVB estimada × 3 % × participación de la delimitación  (`motor/liquidacion.py`,
  el mismo motor de la liquidación real).

Reglas (decisiones de Jorge, 2026-11-14):
  * **Solo delimitaciones vigentes.** Un proyecto sin delimitación confirmada
    que rija el mes sale sin proyección ese mes, igual que en la liquidación.
  * Umbral del art. 45 por empresa, con la capacidad más reciente cargada.
  * El histórico es la generación cargada en «Generación XM»: solo meses que el
    archivo cubre completos, con la versión más avanzada disponible.
  * La TVB estimada **no se guarda** en la tabla `tvb`, que es solo de valores
    reales de la CREG.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from datos import cargue_xm as cx
from datos import repositorio as repo
from datos.liquidar_mes import ordenar_nombres
from motor import delimitacion as dm
from motor.delimitacion import SIN_ASIGNAR, texto_colombiano
from motor.liquidacion import LiquidacionCentral, liquidar_central
from motor.parametros import PARAMETROS_VIGENTES, ParametrosDecreto800
from motor.tse_calculator import presentar
from motor.proyeccion import ESCENARIOS, VENTANA, MesProyectado, SugerenciaTVB, proyectar_generacion, sugerir_tvb

CERO = Decimal("0.00")
MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
PROYECTADA = "proyectada"
MOTIVOS = {
    "sin_central": "Sin central de XM",
    "sin_historico": "Sin histórico de generación",
    "bajo_umbral": "Bajo el umbral del art. 45",
    "sujecion_sin_determinar": "Sujeción sin determinar",
    "sin_delimitacion": "Sin delimitación vigente en ningún mes del año",
}


@dataclass
class MesCentral:
    mes: int
    generacion: MesProyectado
    calculo: dict[str, LiquidacionCentral] = field(default_factory=dict)   # por escenario
    nota: str = ""                                                         # por qué no hay proyección ese mes
    radicados: str = ""

    @property
    def proyectado(self) -> bool:
        return bool(self.calculo)


@dataclass
class CentralProyectada:
    codigo: str | None
    proyectos: list[str]
    estado: str = PROYECTADA
    detalle: str = ""
    sujecion: cx.Sujecion | None = None
    meses: list[MesCentral] = field(default_factory=list)

    @property
    def nombre(self) -> str:
        return " + ".join(self.proyectos)

    @property
    def proyectada(self) -> bool:
        return self.estado == PROYECTADA

    @property
    def meses_proyectados(self) -> int:
        return sum(m.proyectado for m in self.meses)

    def anual(self, escenario: str, campo: str = "bolsa_car_pnn") -> Decimal:
        return sum((getattr(m.calculo[escenario], campo) for m in self.meses if m.proyectado), CERO)

    def generacion_anual(self, escenario: str) -> Decimal:
        return sum((m.generacion.de(escenario) for m in self.meses if m.proyectado), Decimal(0))


@dataclass
class AporteCentral:
    """Lo que una central le aporta a UNA autoridad en el año proyectado."""
    central: CentralProyectada
    montos: dict[str, list[Decimal]]                      # escenario → 12 montos mensuales
    razones: list = field(default_factory=list)           # participaciones distintas usadas en el año
    meses: set[int] = field(default_factory=set)          # meses en que la central le aporta

    def anual(self, escenario: str) -> Decimal:
        return sum(self.montos[escenario], CERO)

    @property
    def participacion(self) -> str:
        return " / ".join(porcentaje(r) for r in self.razones)


@dataclass
class Proyeccion:
    anio: int
    tvb: Decimal
    tvb_es_real: bool
    ventana: int
    centrales: list[CentralProyectada]
    anios_con_generacion: list[int]        # años de XM cargados en la base
    parametros: ParametrosDecreto800

    @property
    def proyectadas(self) -> list[CentralProyectada]:
        return [c for c in self.centrales if c.proyectada]

    @property
    def sin_proyeccion(self) -> list[CentralProyectada]:
        return [c for c in self.centrales if not c.proyectada]

    def por_autoridad_y_central(self) -> dict[str, list[AporteCentral]]:
        """sigla → aportes de cada central que le reparte (en el orden de las
        centrales). `SIN_ASIGNAR` va al final."""
        salida: dict[str, dict[int, AporteCentral]] = {}
        for c in self.proyectadas:
            for m in c.meses:
                for esc, k in m.calculo.items():
                    for f in k.filas:
                        if f.razon == 0:
                            continue
                        a = salida.setdefault(f.autoridad, {}).setdefault(
                            id(c), AporteCentral(central=c, montos={e: [CERO] * 12 for e in ESCENARIOS}))
                        a.montos[esc][m.mes - 1] += f.monto
                        a.meses.add(m.mes)
                        if f.razon not in a.razones:
                            a.razones.append(f.razon)
        return {s: list(v.values())
                for s, v in sorted(salida.items(), key=lambda kv: (kv[0] == SIN_ASIGNAR, kv[0].casefold()))}

    def por_autoridad(self) -> dict[str, dict[str, list[Decimal]]]:
        """sigla → escenario → 12 montos mensuales. `SIN_ASIGNAR` va al final."""
        return {s: {e: [sum((a.montos[e][i] for a in aportes), CERO) for i in range(12)] for e in ESCENARIOS}
                for s, aportes in self.por_autoridad_y_central().items()}

    def mensual(self, escenario: str, campo: str = "a_transferir") -> list[Decimal]:
        """Total del mes en todas las centrales (campo de `LiquidacionCentral`)."""
        return [sum((getattr(m.calculo[escenario], campo) for c in self.proyectadas for m in c.meses
                     if m.mes == mes and m.proyectado), CERO) for mes in range(1, 13)]

    def anual(self, escenario: str, campo: str = "a_transferir") -> Decimal:
        return sum(self.mensual(escenario, campo), CERO)


# ---------------------------------------------------------------------------
# Insumos desde la base (solo lectura)
# ---------------------------------------------------------------------------

def serie_tvb(con) -> dict[int, Decimal]:
    return {t["anio"]: Decimal(t["pesos_kwh"]) for t in repo.listar_tvb(con)}


def tvb_sugerida(con, anio: int, ventana: int = VENTANA) -> SugerenciaTVB | None:
    return sugerir_tvb(serie_tvb(con), anio, ventana)


def historia_generacion(con, codigos: set[str]) -> dict[str, dict[str, Decimal]]:
    """central → periodo → kWh, con la mejor fila de cada mes: solo meses que
    el archivo cubre completos; entre ellos, la versión más avanzada y el
    cargue más reciente (el mismo criterio de la liquidación)."""
    mejor: dict[tuple[str, str], tuple] = {}
    for g in con.execute(
        "SELECT g.codigo_submercado, g.periodo, g.generacion_kwh, g.carga_id, v.orden FROM generacion g "
        "JOIN versiones_xm v ON v.codigo = g.version_xm WHERE g.generacion_kwh IS NOT NULL "
        "AND (g.dias_archivo IS NULL OR g.dias_mes IS NULL OR g.dias_archivo >= g.dias_mes)"
    ):
        if g["codigo_submercado"] not in codigos:
            continue
        clave, rango = (g["codigo_submercado"], g["periodo"]), (g["orden"], g["carga_id"])
        if clave not in mejor or rango > mejor[clave][0]:
            mejor[clave] = (rango, Decimal(g["generacion_kwh"]))
    salida: dict[str, dict[str, Decimal]] = {}
    for (codigo, periodo), (_, kwh) in mejor.items():
        salida.setdefault(codigo, {})[periodo] = kwh
    return salida


def _reparto_del_mes(con, proyectos, periodo: str, cache: dict):
    """(delimitación en memoria, radicados) que rige el mes para la central, o
    (None, motivo). Igual que la liquidación: todas vigentes y con el mismo reparto."""
    ids, radicados = [], []
    for p in proyectos:
        try:
            d = repo.exigir_delimitacion(con, p["id"], periodo)
        except repo.MesPartido:
            return None, (f"Una delimitación de {p['nombre']} empieza a regir dentro del mes: "
                          f"la proyección solo estima meses completos.")
        except repo.SinDelimitacionVigente:
            return None, f"{p['nombre']} no tiene delimitación confirmada que rija el mes."
        ids.append(d["id"])
        radicados.append(d["radicado"])
    clave = tuple(ids)
    if clave not in cache:
        memorias = [repo.delimitacion_en_memoria(con, i) for i in ids]
        repartos = [{r.autoridad_id: r.razon for r in dm.razones(m) if r.razon} for m in memorias]
        cache[clave] = None if any(r != repartos[0] for r in repartos[1:]) else memorias[0]
    if cache[clave] is None:
        return None, "Las delimitaciones de la central reparten distinto."
    return cache[clave], "; ".join(radicados)


# ---------------------------------------------------------------------------
# Proyección
# ---------------------------------------------------------------------------

def proyectar_anio(con, anio: int, tvb: Decimal, tvb_es_real: bool = False, ventana: int = VENTANA,
                   parametros: ParametrosDecreto800 = PARAMETROS_VIGENTES) -> Proyeccion:
    """Proyecta los 12 meses de `anio` con la TVB indicada. No escribe en la base."""
    if not isinstance(tvb, Decimal) or tvb <= 0:
        raise repo.ErrorRepositorio("La TVB de la proyección debe ser un número exacto mayor que cero.")
    grupos: dict[object, list] = {}
    for p in con.execute("SELECT * FROM proyectos ORDER BY id"):
        grupos.setdefault(p["codigo_submercado"] or ("sin", p["id"]), []).append(p)
    historia = historia_generacion(con, {k for k in grupos if isinstance(k, str)})
    cache: dict = {}

    centrales = []
    for clave, proyectos in grupos.items():
        codigo = clave if isinstance(clave, str) else None
        c = CentralProyectada(codigo=codigo, proyectos=ordenar_nombres(p["nombre"] for p in proyectos))
        centrales.append(c)
        if codigo is None:
            c.estado, c.detalle = "sin_central", "El proyecto no está enlazado a una central de XM."
            continue
        meses = proyectar_generacion(historia.get(codigo, {}), anio, ventana)
        if all(m.medio is None for m in meses):
            c.estado, c.detalle = "sin_historico", (
                f"No hay generación de {codigo} cargada de años anteriores a {anio}: sin dato no se estima.")
            continue
        c.sujecion = cx.sujecion_central(con, codigo, anio)
        if c.sujecion is None:
            c.estado, c.detalle = "sujecion_sin_determinar", (
                "No hay capacidad cargada para esta central (archivo mensual de XM con capacidad).")
            continue
        if not c.sujecion.sujeta:
            c.estado, c.detalle = "bajo_umbral", (
                f"{c.sujecion.agente} suma {c.sujecion.capacidad_total_kw} kW ({c.sujecion.anio_capacidad}): "
                f"no supera los {parametros.capacidad_minima_kw} kW.")
            continue
        for g in meses:
            m = MesCentral(mes=g.mes, generacion=g)
            c.meses.append(m)
            if g.medio is None:
                m.nota = "Sin histórico de ese mes."
                continue
            delimitacion, texto = _reparto_del_mes(con, proyectos, f"{anio}-{g.mes:02d}", cache)
            if delimitacion is None:
                m.nota = texto
                continue
            m.radicados = texto
            m.calculo = {e: liquidar_central(g.de(e), tvb, delimitacion, parametros) for e in ESCENARIOS}
        if not c.meses_proyectados:
            c.estado, c.detalle = "sin_delimitacion", next(m.nota for m in c.meses if m.nota)
    centrales.sort(key=lambda c: c.nombre.casefold())
    anios = sorted({int(p[:4]) for p in repo.periodos_con_generacion(con)})
    return Proyeccion(anio=anio, tvb=tvb, tvb_es_real=tvb_es_real, ventana=ventana, centrales=centrales,
                      anios_con_generacion=anios, parametros=parametros)


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------

def porcentaje(razon) -> str:
    """Fracción → «9,87 %» (sin pasar por float)."""
    return texto_colombiano(presentar(razon * 100, 2), 2) + " %"


_NEGRILLA, _RELLENO = Font(bold=True), PatternFill("solid", fgColor="FBE9C6")
PESOS, KWH = '#,##0.00', '#,##0.00'
AVISO = "PROYECCIÓN — ESTIMACIÓN. No es una liquidación ni un valor a transferir."


def _hoja(wb, titulo, encabezados, filas, formato=None, desde=1):
    ws = wb.create_sheet(titulo)
    ws.cell(row=1, column=1, value=AVISO).font = Font(bold=True, color="9C5700")
    for j, e in enumerate(encabezados, start=1):
        c = ws.cell(row=3, column=j, value=e)
        c.font, c.fill, c.alignment = _NEGRILLA, _RELLENO, Alignment(wrap_text=True, vertical="top")
    for i, fila in enumerate(filas, start=4):
        for j, v in enumerate(fila, start=1):
            c = ws.cell(row=i, column=j, value=v)
            if formato and j >= desde and isinstance(v, Decimal):
                c.number_format = formato
    for j, e in enumerate(encabezados, start=1):
        largo = max([len(str(e))] + [len(str(f[j - 1])) for f in filas if j - 1 < len(f) and f[j - 1] is not None])
        ws.column_dimensions[get_column_letter(j)].width = min(max(largo + 2, 11), 60)
    ws.freeze_panes = "A4"
    return ws


def xlsx_proyeccion(p: Proyeccion, autoridades: dict[str, dict], sugerencia: SugerenciaTVB | None = None) -> bytes:
    def nombre(sigla):
        if sigla == SIN_ASIGNAR:
            return "Autoridad ambiental urbana o área en litigio (retenido sin asignar)"
        a = autoridades.get(sigla) or {}
        n = a.get("nombre") or sigla
        return f"Parque Nacional Natural {n}" if a.get("tipo") == "PNN" and "parque" not in n.lower() else n

    wb = Workbook()
    wb.remove(wb.active)
    por = p.por_autoridad()

    supuestos = [
        ["Año proyectado", p.anio],
        ["Generado el", f"{datetime.now():%d/%m/%Y %H:%M}"],
        ["TVB usada ($/kWh)", p.tvb],
        ["Origen de la TVB", "Real, de la CREG" if p.tvb_es_real else "Estimada (no publicada por la CREG)"],
    ]
    if sugerencia is not None and not p.tvb_es_real:
        supuestos += [
            ["TVB sugerida por la aplicación ($/kWh)", sugerencia.valor],
            ["Base de la sugerencia", f"TVB {sugerencia.base_anio} = {sugerencia.base_valor} × (1 + variación promedio)"
                                       f"^{sugerencia.anios_de_distancia}"],
            ["Variación promedio anual usada", porcentaje(sugerencia.variacion_promedio)],
            ["Variaciones anuales consideradas", "; ".join(
                f"{a}: {porcentaje(v)}" for a, v in sugerencia.variaciones)],
        ]
    supuestos += [
        ["Generación — escenario medio", f"Promedio del mismo mes en los últimos {p.ventana} años con dato"],
        ["Generación — escenario bajo", "El menor valor del mismo mes en esos años"],
        ["Generación — escenario alto", "El mayor valor del mismo mes en esos años"],
        ["Rango anual", "Suma de los extremos de cada mes: es un rango amplio, no una probabilidad"],
        ["Años de XM cargados en la base", ", ".join(map(str, p.anios_con_generacion)) or "ninguno"],
        ["Reparto", "Delimitaciones vigentes del IGAC (Decreto 800 de 2026); sin delimitación vigente no se proyecta"],
        ["Bolsa", f"{p.parametros.porcentaje_car_pnn * 100} % de las ventas brutas (generación × TVB)"],
        ["Umbral", f"Empresas con más de {p.parametros.capacidad_minima_kw} kW (art. 45 Ley 99 de 1993)"],
        ["Versión de parámetros", p.parametros.version],
        ["Centrales proyectadas", f"{len(p.proyectadas)} de {len(p.centrales)}"],
    ]
    _hoja(wb, "Supuestos", ["Supuesto", "Valor"], supuestos)

    filas = [[s, nombre(s), sum(e["bajo"], CERO), sum(e["medio"], CERO), sum(e["alto"], CERO)] for s, e in por.items()]
    filas += [[], ["", "Total a transferir (sin lo retenido)", *(p.anual(e) for e in ESCENARIOS)],
              ["", "Retenido sin asignar", *(p.anual(e, "retenido") for e in ESCENARIOS)],
              ["", "Bolsa del 3 %", *(p.anual(e, "bolsa_car_pnn") for e in ESCENARIOS)]]
    _hoja(wb, "Anual por autoridad", ["Sigla", "Autoridad ambiental", "Bajo ($)", "Medio ($)", "Alto ($)"],
          filas, PESOS, 3)

    for esc in ESCENARIOS:
        filas = [[s, nombre(s), *e[esc], sum(e[esc], CERO)] for s, e in por.items()]
        filas += [[], ["", "Total a transferir (sin lo retenido)", *p.mensual(esc), p.anual(esc)]]
        _hoja(wb, f"Mensual {esc}", ["Sigla", "Autoridad ambiental", *(f"{m} {p.anio}" for m in MESES), "Total año"],
              filas, PESOS, 3)

    filas = [[s, nombre(s), a.central.codigo, a.central.nombre, a.participacion, f"{len(a.meses)} de 12",
              *(a.anual(e) for e in ESCENARIOS), *a.montos["medio"]]
             for s, aportes in p.por_autoridad_y_central().items() for a in aportes]
    _hoja(wb, "Autoridad por central", [
        "Sigla", "Autoridad ambiental", "Central XM", "Proyecto(s) IGAC", "Participación en el 3 %", "Meses",
        "Año bajo ($)", "Año medio ($)", "Año alto ($)", *(f"{m} {p.anio} medio ($)" for m in MESES),
    ], filas, PESOS, 7)

    filas = []
    for c in p.proyectadas:
        for m in c.meses:
            g = m.generacion
            filas.append([
                c.codigo, c.nombre, f"{p.anio}-{m.mes:02d}", ", ".join(map(str, g.anios)) or "—", len(g.anios),
                g.bajo, g.medio, g.alto,
                *((m.calculo[e].bolsa_car_pnn for e in ESCENARIOS) if m.proyectado else (None, None, None)),
                m.radicados or m.nota,
            ])
    _hoja(wb, "Por central y mes", [
        "Central XM", "Proyecto(s) IGAC", "Mes", "Años del histórico usados", "N.º de años",
        "Generación baja (kWh)", "Generación media (kWh)", "Generación alta (kWh)",
        "Bolsa 3 % baja ($)", "Bolsa 3 % media ($)", "Bolsa 3 % alta ($)", "Delimitación o motivo",
    ], filas, KWH, 6)

    _hoja(wb, "Sin proyección", ["Proyecto(s) IGAC", "Central XM", "Motivo", "Detalle"],
          [[c.nombre, c.codigo or "—", MOTIVOS[c.estado], c.detalle] for c in p.sin_proyeccion]
          + [[c.nombre, c.codigo, f"Solo {c.meses_proyectados} de 12 meses",
              "; ".join(sorted({m.nota for m in c.meses if m.nota}))]
             for c in p.proyectadas if c.meses_proyectados < 12])

    salida = io.BytesIO()
    wb.save(salida)
    return salida.getvalue()
