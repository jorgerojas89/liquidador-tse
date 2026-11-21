"""
Exportables del mes (paso 6): XLSX con las variables intermedias y PDF resumen
por autoridad.

Se exporta **lo guardado** (la última versión de cada central, salida de
`datos.liquidar_mes.liquidaciones_guardadas`), no un cálculo al vuelo: el
archivo que sale es el que queda con trazabilidad en la base.

No escribe en disco ni en la base: devuelve los bytes del archivo.
"""
from __future__ import annotations

import io
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from xml.sax.saxutils import escape

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from motor.delimitacion import SIN_ASIGNAR, texto_colombiano
from motor.liquidacion import CITA_BOLSA, CITA_FORMULA, CITA_TSE, CITA_UMBRAL, CITA_VENTAS
from motor.parametros import PARAMETROS_VIGENTES, ParametrosDecreto800
from motor.tse_calculator import centavos

MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre")
CARACTER = {"oficial": "Oficial (versión firme de XM)", "preliminar": "Preliminar (de referencia)"}
NOMBRE_RETENIDO = "Autoridad ambiental urbana o área en litigio (retenido sin asignar)"
CERO = Decimal("0.00")
SIN_GENERACION = "Sin generación"   # = datos.liquidar_mes.SIN_GENERACION (este módulo no lo importa)


def nombre_mes(periodo: str) -> str:
    return f"{MESES[int(periodo[5:7]) - 1]} de {periodo[:4]}"


def caracter_del_mes(guardadas: list[dict]) -> str | None:
    cs = {l["caracter"] for l in guardadas}
    return None if not cs else "oficial" if cs == {"oficial"} else "preliminar"


def resumen_por_autoridad(guardadas: list[dict]) -> list[dict]:
    """Una fila por autoridad: monto total y número de centrales. La parte
    retenida (`SIN_ASIGNAR`) va al final."""
    acum: dict[str, dict] = {}
    for l in guardadas:
        for f in l["filas"]:
            a = acum.setdefault(f["autoridad_sigla"], {"autoridad": f["autoridad_sigla"], "monto": CERO, "centrales": 0})
            a["monto"] += Decimal(f["monto"])
            a["centrales"] += 1 if Fraction(f["razon"]) > 0 else 0
    return sorted(acum.values(), key=lambda a: (a["autoridad"] == SIN_ASIGNAR, a["autoridad"].casefold()))


def totales(guardadas: list[dict]) -> dict[str, Decimal]:
    filas = [f for l in guardadas for f in l["filas"]]
    return {
        "bolsa": sum((Decimal(l["bolsa_car_pnn"]) for l in guardadas), CERO),
        "a_transferir": sum((Decimal(f["monto"]) for f in filas if f["autoridad_sigla"] != SIN_ASIGNAR), CERO),
        "retenido": sum((Decimal(f["monto"]) for f in filas if f["autoridad_sigla"] == SIN_ASIGNAR), CERO),
    }


def _nombre(sigla: str, autoridades: dict[str, dict]) -> str:
    if sigla == SIN_ASIGNAR:
        return NOMBRE_RETENIDO
    a = autoridades.get(sigla) or {}
    nombre = a.get("nombre") or sigla
    # El maestro guarda los Parques solo con su nombre propio («Chingaza»).
    return f"Parque Nacional Natural {nombre}" if a.get("tipo") == "PNN" and "parque" not in nombre.lower() else nombre


def dias_liquidados(l: dict) -> str:
    """«Mes completo» o «25 al 30» (mes parcial: desde que rige la delimitación)."""
    return f"{l['dia_desde']} al {l['dia_hasta']}" if l.get("dia_desde") else "Mes completo"


def sin_generacion(l: dict) -> bool:
    """Liquidación guardada con 0 kWh: la transferencia es $ 0 porque XM no
    reporta generación en los días liquidados. Se rotula (Jorge, 2026-11-15)."""
    return Decimal(l["generacion_kwh"]) == 0


def _dec(texto) -> Decimal | None:
    return None if texto is None else Decimal(texto)


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------

PESOS, KWH, HA, PCT = '#,##0.00', '#,##0.00', '#,##0.00000', '0.00000'
_NEGRILLA = Font(bold=True)
_RELLENO = PatternFill("solid", fgColor="DDEBE6")


def _hoja(wb, titulo: str, encabezados: list[str], filas: list[list], formatos: dict[int, str] | None = None,
          nota: str | None = None):
    ws = wb.create_sheet(titulo)
    inicio = 1
    if nota:
        ws.cell(row=1, column=1, value=nota).font = Font(italic=True)
        inicio = 3
    for j, e in enumerate(encabezados, start=1):
        c = ws.cell(row=inicio, column=j, value=e)
        c.font, c.fill = _NEGRILLA, _RELLENO
        c.alignment = Alignment(wrap_text=True, vertical="top")
    for i, fila in enumerate(filas, start=inicio + 1):
        for j, v in enumerate(fila, start=1):
            c = ws.cell(row=i, column=j, value=v)
            if formatos and j in formatos and isinstance(v, (Decimal, int)):
                c.number_format = formatos[j]
    for j, e in enumerate(encabezados, start=1):
        largo = max([len(str(e))] + [len(str(f[j - 1])) for f in filas if j - 1 < len(f) and f[j - 1] is not None])
        ws.column_dimensions[get_column_letter(j)].width = min(max(largo + 2, 10), 60)
    ws.freeze_panes = ws.cell(row=inicio + 1, column=1)
    return ws


def xlsx_mes(periodo: str, guardadas: list[dict], no_liquidadas: list, autoridades: dict[str, dict],
             parametros: ParametrosDecreto800 = PARAMETROS_VIGENTES, avisos: Sequence[str] = ()) -> bytes:
    """Libro con: resumen por autoridad, detalle con variables intermedias,
    trazabilidad de cada liquidación, no liquidados y parámetros.
    `no_liquidadas`: centrales SIN liquidación guardada (objetos con `nombre`,
    `codigo`, `motivo` y `detalle`; ver `liquidar_mes.no_liquidadas_de_lo_guardado`).
    `avisos`: en qué difiere lo guardado del cálculo de hoy; van en la primera hoja."""
    wb = Workbook()
    wb.remove(wb.active)
    caracter = caracter_del_mes(guardadas)
    titulo = (f"Liquidación TSE de {nombre_mes(periodo)} — {CARACTER.get(caracter, 'sin liquidaciones')} — "
              f"generado el {datetime.now():%d/%m/%Y %H:%M}")
    if avisos:
        titulo += " — AVISO: " + " ".join(avisos)
    t = totales(guardadas)

    # 1. Resumen por autoridad
    filas = [[a["autoridad"], _nombre(a["autoridad"], autoridades),
              "Retenido" if a["autoridad"] == SIN_ASIGNAR else (autoridades.get(a["autoridad"]) or {}).get("tipo", ""),
              a["centrales"], a["monto"]] for a in resumen_por_autoridad(guardadas)]
    filas += [[], ["", "Total a transferir", "", "", t["a_transferir"]],
              ["", "Retenido sin asignar", "", "", t["retenido"]],
              ["", "Bolsa del 3 % (CAR y Parques)", "", "", t["bolsa"]]]
    _hoja(wb, "Por autoridad", ["Sigla", "Autoridad ambiental", "Tipo", "Centrales", "Monto ($)"], filas,
          {5: PESOS}, nota=titulo)

    # 2. Detalle: una fila por central y autoridad, con toda la cadena
    filas = []
    for l in guardadas:
        ventas = Decimal(l["ventas_brutas"])
        seis = centavos(ventas * parametros.porcentaje_tse_total)
        for f in l["filas"]:
            razon = Fraction(f["razon"])
            filas.append([
                periodo, l["codigo_submercado"], " + ".join(l["proyectos"]), l["empresa"], l["caracter"],
                l["version_xm"], Decimal(l["generacion_kwh"]), l["tvb_anio"], Decimal(l["tvb_pesos_kwh"]),
                ventas, seis, Decimal(l["bolsa_car_pnn"]), l["formula"] if l["formula"] else "%",
                f["autoridad_sigla"], _nombre(f["autoridad_sigla"], autoridades),
                _dec(f["s1_ha"]), _dec(f["s2_ha"]), f"{razon.numerator}/{razon.denominator}",
                Decimal(f["porcentaje_mostrado"]), Decimal(f["monto"]),
                "sí" if f["ajuste_residuo"] else "", "sí" if f["autoridad_sigla"] == SIN_ASIGNAR else "",
                dias_liquidados(l), SIN_GENERACION if sin_generacion(l) else "",
            ])
    _hoja(wb, "Detalle", [
        "Periodo", "Central XM", "Proyecto(s) IGAC", "Empresa", "Carácter", "Versión XM", "Generación (kWh)",
        "Año TVB", "TVB ($/kWh)", "Ventas brutas ($)", "TSE 6 % ($)", "Bolsa 3 % ($)", "Fórmula", "Sigla",
        "Autoridad ambiental", "S1i (ha)", "S2i (ha)", "Razón exacta", "Participación en el 3 % (%)", "Monto ($)",
        "Recibió el residuo de centavos", "Retenido sin asignar", "Días liquidados", "Observación",
    ], filas, {7: KWH, 9: '0.00000', 10: PESOS, 11: PESOS, 12: PESOS, 16: HA, 17: HA, 19: PCT, 20: PESOS},
          nota="monto = bolsa del 3 % × razón exacta. Los 5 decimales del porcentaje son solo de presentación. "
               "«Sin generación»: XM no reporta generación en los días liquidados; por eso el monto es 0.")

    # 3. Trazabilidad
    filas = [[
        l["codigo_submercado"], " + ".join(l["proyectos"]), l["version_numero"], l["creada_en"], l["caracter"],
        l["version_xm"], l["archivo_xm"], l["carga_xm_sha256"],
        l["dias_reportados"], l["dias_archivo"], l["empresa"], _dec(l["capacidad_empresa_kw"]), l["capacidad_anio"],
        l["tvb_fuente"], "; ".join(d["radicado"] for d in l["delimitaciones"]),
        "; ".join(d["pdf_sha256"] or "—" for d in l["delimitaciones"]), l["parametros_version"],
        dias_liquidados(l),
    ] for l in guardadas]
    _hoja(wb, "Trazabilidad", [
        "Central XM", "Proyecto(s) IGAC", "Versión de la liquidación", "Guardada el", "Carácter", "Versión XM",
        "Archivo de XM", "SHA-256 del archivo de XM", "Días de la central en el archivo", "Días que cubre el archivo",
        "Empresa (umbral)", "Capacidad de la empresa (kW)", "Año de la capacidad", "Fuente de la TVB",
        "Radicado(s) de la delimitación", "SHA-256 del PDF de la delimitación", "Versión de parámetros",
        "Días liquidados",
    ], filas, {12: '#,##0'})

    # 4. No liquidados
    _hoja(wb, "No liquidados", ["Proyecto(s) IGAC", "Central XM", "Motivo", "Detalle"],
          [[c.nombre, c.codigo or "—", c.motivo, c.detalle] for c in no_liquidadas],
          nota="Centrales sin liquidación guardada en el mes. Sin monto: no se estima ni se reparte por partes "
               "iguales. El motivo es el de los datos al momento de exportar.")

    # 5. Parámetros y normas
    _hoja(wb, "Parámetros y normas", ["Parámetro", "Valor", "Norma"], [
        ["Versión de parámetros", parametros.version, ""],
        ["TSE total", parametros.porcentaje_tse_total, CITA_TSE],
        ["Bolsa CAR y Parques", parametros.porcentaje_car_pnn, CITA_BOLSA],
        ["α (ponderación de páramos)", parametros.alpha, CITA_FORMULA[2]],
        ["Umbral de sujeción (kW, por empresa)", parametros.capacidad_minima_kw, CITA_UMBRAL],
        ["Ventas brutas", "generación × TVB", CITA_VENTAS],
        ["Fórmula 1", "S1i / S1_T", CITA_FORMULA[1]],
        ["Redondeo", "Razones exactas (fracciones); monto a centavos, mitad hacia arriba",
         "El residuo de centavos va al beneficiario de mayor participación; nunca a la parte retenida."],
    ])

    salida = io.BytesIO()
    wb.save(salida)
    return salida.getvalue()


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def _pesos(v: Decimal) -> str:
    return "$ " + texto_colombiano(v, 2)


def pdf_resumen(periodo: str, guardadas: list[dict], no_liquidadas: list, autoridades: dict[str, dict],
                parametros: ParametrosDecreto800 = PARAMETROS_VIGENTES, avisos: Sequence[str] = ()) -> bytes:
    """PDF resumen: total por autoridad y, para cada una, de qué centrales sale.
    `no_liquidadas` y `avisos`: como en `xlsx_mes`."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    base = getSampleStyleSheet()
    normal = ParagraphStyle("n", parent=base["Normal"], fontSize=8.5, leading=11)
    der = ParagraphStyle("d", parent=normal, alignment=TA_RIGHT)
    nota = ParagraphStyle("nota", parent=normal, fontSize=7.5, leading=9.5, textColor=colors.HexColor("#444444"))
    h1 = ParagraphStyle("h1", parent=base["Title"], fontSize=15, leading=18, spaceAfter=4)
    h2 = ParagraphStyle("h2", parent=base["Heading2"], fontSize=11, leading=14, spaceBefore=10, spaceAfter=4)
    verde = colors.HexColor("#1f6f5c")

    def P(texto, estilo=normal):
        return Paragraph(escape(str(texto)), estilo)

    def tabla(datos, anchos, total=False):
        t = Table(datos, colWidths=anchos, repeatRows=1)
        estilo = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#DDEBE6")),
            ("LINEBELOW", (0, 0), (-1, 0), 0.6, verde),
            ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#CCCCCC")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        if total:
            estilo.append(("LINEABOVE", (0, -1), (-1, -1), 0.6, verde))
        t.setStyle(TableStyle(estilo))
        return t

    def B(texto, estilo=normal):
        return Paragraph(f"<b>{escape(str(texto))}</b>", estilo)

    caracter = caracter_del_mes(guardadas)
    t = totales(guardadas)
    ancho = letter[0] - 3.6 * cm
    partes = [
        Paragraph("Transferencias del sector eléctrico", h1),
        Paragraph(f"Liquidación de {escape(nombre_mes(periodo))} · 3 % para corporaciones autónomas regionales "
                  f"y Parques Nacionales Naturales", base["Heading3"]),
        P(f"Carácter: {CARACTER.get(caracter, 'sin liquidaciones guardadas')}. "
          f"Centrales liquidadas: {len(guardadas)}. Generado el {datetime.now():%d/%m/%Y %H:%M}."),
    ]
    parciales = [l for l in guardadas if l.get("dia_desde")]
    if parciales:
        partes.append(P(f"Mes parcial: {len(parciales)} de {len(guardadas)} centrales se liquidan solo por los días "
                        f"desde que rige su delimitación (se indica en cada una), con la generación de XM de esos días."))
    sin_gen = [l for l in guardadas if sin_generacion(l)]
    if sin_gen:
        partes.append(P(f"{SIN_GENERACION}: {', '.join(' + '.join(l['proyectos']) for l in sin_gen)}. XM no reporta "
                        f"generación en los días liquidados; por eso su transferencia es $ 0,00."))
    if caracter == "preliminar":
        partes.append(Paragraph(
            "<b>PRELIMINAR.</b> Al menos una central se liquidó con una versión de XM que no es la firme (TXF). "
            "Este documento es de referencia y no constituye liquidación oficial.", normal))
    for aviso in avisos:
        partes.append(Paragraph(f"<b>Aviso.</b> {escape(aviso)}", normal))
    partes += [Spacer(1, 6), tabla([
        [B("Bolsa del 3 %"), B("Total a transferir"), B("Retenido sin asignar")],
        [P(_pesos(t["bolsa"])), P(_pesos(t["a_transferir"])), P(_pesos(t["retenido"]))],
    ], [ancho / 3] * 3)]

    # Resumen por autoridad
    partes.append(Paragraph("Resumen por autoridad ambiental", h2))
    datos = [[B("Autoridad ambiental"), B("Centrales", der), B("Monto", der)]]
    for a in resumen_por_autoridad(guardadas):
        if a["autoridad"] == SIN_ASIGNAR or a["centrales"] == 0:
            continue  # lo retenido va aparte; las que figuran con N/A (0 %) no reciben nada
        datos.append([P(_nombre(a["autoridad"], autoridades)), P(a["centrales"], der), P(_pesos(a["monto"]), der)])
    datos.append([B("Total a transferir"), P(""), B(_pesos(t["a_transferir"]), der)])
    partes.append(tabla(datos, [ancho * 0.62, ancho * 0.13, ancho * 0.25], total=True))
    if t["retenido"] > 0:
        partes.append(Spacer(1, 4))
        partes.append(P(f"Además, {_pesos(t['retenido'])} corresponden a «autoridad ambiental urbana o área en "
                        f"litigio»: se calculan y quedan retenidos sin asignar; no se reparten entre las demás.", nota))

    # Detalle por autoridad
    partes.append(Paragraph("Detalle por autoridad ambiental", h2))
    anchos = [ancho * 0.30, ancho * 0.19, ancho * 0.19, ancho * 0.13, ancho * 0.19]
    for a in resumen_por_autoridad(guardadas):
        datos = [[B("Proyecto (central de XM)"), B("Generación (kWh)", der), B("Bolsa del 3 %", der),
                  B("Participación", der), B("Monto", der)]]
        for l in guardadas:
            for f in l["filas"]:
                if f["autoridad_sigla"] != a["autoridad"] or Fraction(f["razon"]) == 0:
                    continue
                datos.append([
                    P(f"{' + '.join(l['proyectos'])} ({l['codigo_submercado']})"
                      + ("" if l["caracter"] == "oficial" else " · preliminar")
                      + (f" · días {dias_liquidados(l)}" if l.get("dia_desde") else "")
                      + (f" · {SIN_GENERACION.lower()}" if sin_generacion(l) else "")),
                    P(texto_colombiano(Decimal(l["generacion_kwh"]), 2), der),
                    P(_pesos(Decimal(l["bolsa_car_pnn"])), der),
                    P(texto_colombiano(Decimal(f["porcentaje_mostrado"]), 5) + " %", der),
                    P(_pesos(Decimal(f["monto"])), der),
                ])
        if len(datos) == 1:
            continue
        datos.append([B("Total"), P(""), P(""), P(""), B(_pesos(a["monto"]), der)])
        partes.append(KeepTogether([
            Paragraph(f"<b>{escape(_nombre(a['autoridad'], autoridades))}</b>", normal), Spacer(1, 2),
            tabla(datos, anchos, total=True), Spacer(1, 8)]))

    # No liquidados
    if no_liquidadas:
        partes.append(Paragraph("Proyectos sin liquidar en el mes", h2))
        datos = [[B("Proyecto"), B("Central"), B("Motivo")]]
        datos += [[P(c.nombre), P(c.codigo or "—"), P(f"{c.motivo}. {c.detalle}")] for c in no_liquidadas]
        partes.append(tabla(datos, [ancho * 0.30, ancho * 0.10, ancho * 0.60]))
        partes.append(P("Sin monto: no se estima ni se reparte por partes iguales.", nota))

    # Cómo se calculó
    tvbs = sorted({(l["tvb_anio"], l["tvb_pesos_kwh"]) for l in guardadas})
    versiones = sorted({l["version_xm"] for l in guardadas})
    partes.append(Paragraph("Cómo se calculó", h2))
    for linea in (
        "ventas brutas = generación (kWh, XM) × tarifa de venta en bloque (CREG, año del periodo). " + CITA_VENTAS,
        "bolsa = 3 % de las ventas brutas. " + CITA_BOLSA,
        f"monto de cada autoridad = bolsa × su participación según las hectáreas de la delimitación del IGAC "
        f"(Fórmulas 1 y 2 del Decreto 800 de 2026, α = {texto_colombiano(parametros.alpha, 4)}). Las participaciones "
        f"se calculan como fracciones exactas; los 5 decimales son de presentación. Cada monto se redondea una vez a "
        f"centavos y el residuo de centavos va al beneficiario de mayor participación.",
        CITA_UMBRAL,
        "TVB aplicada: " + "; ".join(f"{a}: {texto_colombiano(Decimal(v), 5)} $/kWh" for a, v in tvbs)
        + f". Versiones de XM: {', '.join(versiones) or '—'}. Parámetros: {parametros.version}.",
        "El archivo XLSX que acompaña este resumen trae las variables intermedias y la trazabilidad "
        "(archivo de XM y su SHA-256, radicado y SHA-256 del PDF de cada delimitación).",
    ):
        partes.append(P(linea, nota))
        partes.append(Spacer(1, 2))

    def pie(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#555555"))
        marca = "PRELIMINAR · " if caracter == "preliminar" else ""
        canvas.drawString(1.8 * cm, 1.1 * cm, f"{marca}Liquidador TSE · ONVS, Minambiente · {nombre_mes(periodo)}")
        canvas.drawRightString(letter[0] - 1.8 * cm, 1.1 * cm, f"Página {doc.page}")
        canvas.restoreState()

    salida = io.BytesIO()
    SimpleDocTemplate(salida, pagesize=letter, leftMargin=1.8 * cm, rightMargin=1.8 * cm, topMargin=1.6 * cm,
                      bottomMargin=1.8 * cm, title=f"Liquidación TSE {periodo}",
                      author="Liquidador TSE — ONVS").build(partes, onFirstPage=pie, onLaterPages=pie)
    return salida.getvalue()
