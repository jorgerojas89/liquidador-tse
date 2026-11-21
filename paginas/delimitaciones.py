"""Pantalla 1 — Delimitaciones del IGAC.

Lista por proyecto con estado; captura y corrección de borradores con el PDF
al lado; tabla de porcentajes resultante; confirmación explícita contra el PDF
(reglas 3 y 4).

La validación y la confirmación trabajan sobre lo GUARDADO. Por eso la
pantalla detecta los cambios sin guardar y no deja confirmar mientras existan.
"""
from __future__ import annotations

import hashlib
import math
import sqlite3
from datetime import date
from decimal import Decimal
from fractions import Fraction

import pandas as pd
import streamlit as st

from paginas.comun import ESTADOS, RAIZ, conexion, repo
from motor import delimitacion as dm
from motor.delimitacion import NA, SIN_ASIGNAR, numero_desde_texto, texto_colombiano
from motor.parametros import PARAMETROS_VIGENTES, VIGENCIA_DELIMITACIONES_POR_DEFECTO

ALMACEN_PDF = RAIZ / "almacen" / "delimitaciones"
PROYECTO_NUEVO = "Proyecto nuevo…"
COLOR_ESTADO = {"borrador": "orange", "vigente": "green", "reemplazada": "gray"}
MODOS = {"hectareas": "Hectáreas S1/S2", "porcentaje": "Porcentajes"}


# ---------------------------------------------------------------------------
# Utilidades de presentación
# ---------------------------------------------------------------------------

def _fecha(texto: str | None) -> date | None:
    return date.fromisoformat(texto) if texto else None


def _fecha_txt(texto: str | None) -> str:
    return _fecha(texto).strftime("%d/%m/%Y") if texto else "—"


def _exacto(v: Decimal | None) -> str:
    """Cifra guardada → texto para editar, SIN redondear, de modo que leerla de
    vuelta dé exactamente lo guardado. None → «N/A». Los puntos de miles solo
    se ponen si hay coma decimal: «1.312» a secas se leería como 1,312."""
    if v is None:
        return NA
    signo, _, resto = f"{v:f}".rpartition("-")
    entero, _, frac = resto.partition(".")
    if not frac:
        return signo + entero
    return signo + f"{int(entero):,}".replace(",", ".") + "," + frac


def _decimal(fr: Fraction) -> Decimal:
    return Decimal(fr.numerator) / Decimal(fr.denominator)


def _pct(fr: Fraction) -> str:
    return texto_colombiano(_decimal(fr * 100), 5) + " %"


def _celda(v):
    """Celda del editor → texto o None (las filas nuevas llegan con None o NaN)."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return v


def _lista(mensajes: list[str]) -> str:
    return "\n".join(f"- {m}" for m in mensajes)


def _refrescar(abrir: int | None = None) -> None:
    """Tras escribir en la base: la lista se vuelve a dibujar con su selección
    y, si se pide, se abre otra delimitación."""
    if abrir is not None:
        st.session_state["delim_id"] = abrir
    st.session_state["lista_n"] = st.session_state.get("lista_n", 0) + 1


def _guardar_pdf(archivo) -> tuple[str, str, str]:
    contenido = archivo.getvalue()
    sha = hashlib.sha256(contenido).hexdigest()
    ALMACEN_PDF.mkdir(parents=True, exist_ok=True)
    destino = ALMACEN_PDF / archivo.name
    if destino.exists() and hashlib.sha256(destino.read_bytes()).hexdigest() != sha:
        destino = ALMACEN_PDF / f"{destino.stem}-{sha[:8]}{destino.suffix}"
    destino.write_bytes(contenido)
    return archivo.name, str(destino.relative_to(RAIZ)), sha


def _tabla_porcentajes(mem: dm.Delimitacion, nombres: dict[str, str]) -> pd.DataFrame | None:
    """Participación de cada autoridad en el 3 %, o None si aún no cuadra."""
    try:
        razones = dm.razones(mem)
    except Exception:
        return None
    if not razones:
        return None
    s1_t = sum((f.s1_ha or 0 for f in mem.filas), Decimal(0))
    s2_t = sum((f.s2_ha or 0 for f in mem.filas), Decimal(0))
    filas = []
    for f, r in zip(mem.filas, razones):
        fila = {"Autoridad": f.autoridad, "Nombre": nombres.get(f.autoridad, "¿?")}
        if mem.modo == "hectareas":
            fila["S1ᵢ / S1_T"] = _pct(Fraction(f.s1_ha or 0) / Fraction(s1_t)) if s1_t else "—"
            if mem.trae_s2:
                fila["S2ᵢ / S2_T"] = _pct(Fraction(f.s2_ha or 0) / Fraction(s2_t)) if s2_t else "—"
        fila["Participación en el 3 %"] = _pct(r.razon)
        filas.append(fila)
    return pd.DataFrame(filas)


def _texto_formula(mem: dm.Delimitacion) -> str:
    return {
        1: "Fórmula 1 del Decreto 800 (sin S2): S1ᵢ / S1_T.",
        2: f"Fórmula 2 del Decreto 800: (1 − α)·S1ᵢ/S1_T + α·S2ᵢ/S2_T, con α = "
           f"{texto_colombiano(PARAMETROS_VIGENTES.alpha, 4)}.",
        None: "Porcentajes digitados tal como vienen en la delimitación.",
    }[dm.formula(mem)]


# ---------------------------------------------------------------------------
# Diálogos
# ---------------------------------------------------------------------------

@st.dialog("Registrar una delimitación", width="medium", icon=":material/note_add:")
def _registrar() -> None:
    con = conexion()
    proyectos = [r["nombre"] for r in con.execute("SELECT nombre FROM proyectos ORDER BY nombre")]
    proyecto = st.selectbox("Proyecto", [*proyectos, PROYECTO_NUEVO], index=None,
                            placeholder="Elija el proyecto")
    nombre_nuevo = ""
    if proyecto == PROYECTO_NUEVO:
        nombre_nuevo = st.text_input("Nombre del proyecto nuevo (como lo escribe el IGAC)")
    a, b = st.columns(2)
    radicado = a.text_input("Radicado del IGAC").strip()
    fecha_doc = b.date_input("Fecha del documento", value=None, format="DD/MM/YYYY")
    vigencia = st.date_input("Rige desde", value=_fecha(VIGENCIA_DELIMITACIONES_POR_DEFECTO), format="DD/MM/YYYY",
                             help="Por defecto, el 25/11/2026 (ese día incluido). Cámbiela si esta delimitación "
                                  "rige desde otra fecha; también se puede ajustar después.")
    modo = st.segmented_control("La delimitación trae", list(MODOS), default="hectareas",
                                format_func=MODOS.get, required=True)
    pdf = st.file_uploader("PDF de la delimitación", type=["pdf"])
    st.caption("Las cifras por autoridad se digitan después, en el borrador.")
    if not st.button("Crear borrador", type="primary"):
        return
    nombre = nombre_nuevo.strip() if proyecto == PROYECTO_NUEVO else proyecto
    faltan = [n for n, v in (("proyecto", nombre), ("radicado", radicado),
                             ("fecha del documento", fecha_doc), ("PDF", pdf)) if not v]
    if faltan:
        st.error("Falta: " + ", ".join(faltan) + ".")
        return
    # Un radicado repetido solo se acepta para CORREGIR una ya confirmada del
    # mismo proyecto (misma fecha de vigencia: la sustituye, con su rastro).
    choque = con.execute(
        "SELECT d.estado, p.nombre FROM delimitaciones d JOIN proyectos p ON p.id = d.proyecto_id "
        "WHERE d.radicado = ? AND (d.estado = 'borrador' OR p.nombre <> ?)", (radicado, nombre)).fetchone()
    if choque:
        st.error(f"Ya existe una delimitación con el radicado {radicado} ({choque['nombre']}, "
                 f"{ESTADOS[choque['estado']].lower()}). Solo se repite un radicado para corregir una "
                 "delimitación ya confirmada del mismo proyecto.")
        return
    if proyecto == PROYECTO_NUEVO and nombre.casefold() in {p.casefold() for p in proyectos}:
        st.error(f"Ya existe el proyecto «{nombre}»: elíjalo en la lista en vez de crearlo de nuevo.")
        return
    fila = con.execute("SELECT id FROM proyectos WHERE nombre = ?", (nombre,)).fetchone()
    pid = fila["id"] if fila else repo.crear_proyecto(con, nombre)
    pdf_nombre, pdf_ruta, sha = _guardar_pdf(pdf)
    nuevo = repo.crear_delimitacion(
        con, pid, radicado, fecha_doc.isoformat(), vigencia.isoformat() if vigencia else None, modo, [],
        pdf_nombre=pdf_nombre, pdf_ruta=pdf_ruta, pdf_sha256=sha,
    )
    _refrescar(abrir=nuevo)
    st.session_state["filtro_estado"] = "Todas"
    st.toast("Borrador creado. Ahora digite las filas por autoridad.")
    st.rerun()


@st.dialog("Confirmar delimitación", width="large", icon=":material/verified:")
def _confirmar(did: int) -> None:
    con = conexion()
    d = con.execute(
        "SELECT d.*, p.nombre AS proyecto, p.codigo_submercado FROM delimitaciones d "
        "JOIN proyectos p ON p.id = d.proyecto_id WHERE d.id = ?", (did,)
    ).fetchone()
    mem = repo.delimitacion_en_memoria(con, did)
    nombres = {r["sigla"]: r["nombre"] for r in con.execute("SELECT sigla, nombre FROM autoridades")}
    st.table(
        {
            "Proyecto": d["proyecto"],
            "Central de XM": d["codigo_submercado"] or "—",
            "Radicado": d["radicado"],
            "Fecha del documento": _fecha_txt(d["fecha_documento"]),
            "Rige desde": f"**{_fecha_txt(d['vigente_desde'])}**",
            "PDF": d["pdf_nombre"] or "—",
        },
        border="horizontal",
    )
    pct = _tabla_porcentajes(mem, nombres)
    if pct is not None:
        st.caption(_texto_formula(mem))
        st.dataframe(pct, hide_index=True, alt="Participación de cada autoridad en el 3 %")
    st.warning(
        "Una delimitación confirmada no se edita ni se borra. Si hay un error, habrá que registrar otra "
        "que la reemplace desde su fecha de vigencia.",
        title="Esta acción no se deshace", icon=":material/lock:",
    )
    a, b = st.columns(2)
    if b.button("Volver a revisar", width="stretch"):
        st.rerun()
    if a.button("Confirmar delimitación", type="primary", width="stretch"):
        try:
            r = repo.confirmar_delimitacion(con, did)
        except repo.ErrorRepositorio as e:
            st.error(str(e))
            return
        if not r.ok:
            st.error(_lista(r.errores), title="No se pudo confirmar")
            return
        # Lo siguiente por hacer: el próximo borrador, en el orden de la lista.
        todas = repo.listar_delimitaciones(con)
        pos = next(i for i, x in enumerate(todas) if x["id"] == did)
        siguiente = next((x["id"] for x in todas[pos + 1:] + todas[:pos] if x["estado"] == "borrador"), did)
        _refrescar(abrir=siguiente)
        st.toast(f"{d['proyecto']}: delimitación vigente desde el {_fecha_txt(d['vigente_desde'])}.")
        st.rerun()


# ---------------------------------------------------------------------------
# Resumen y lista
# ---------------------------------------------------------------------------

con = conexion()
st.title("Delimitaciones del IGAC")
st.caption(
    "Las cifras se digitan desde la delimitación y el PDF queda adjunto como respaldo. "
    "Una delimitación solo liquida cuando está **vigente**, es decir, después de confirmarla contra el PDF."
)

delims = repo.listar_delimitaciones(con)
autoridades = {r["sigla"]: r["nombre"] for r in con.execute("SELECT sigla, nombre FROM autoridades ORDER BY tipo, sigla")}
validaciones = {d["id"]: repo.validar_delimitacion(con, d["id"]) for d in delims if d["estado"] == "borrador"}


def _otros_errores(d) -> int:
    """Errores del borrador sin contar la fecha de vigencia que falta."""
    return len(validaciones[d["id"]].errores) - (0 if d["vigente_desde"] else 1)


def _pendiente(d) -> str:
    if d["estado"] != "borrador":
        return "—"
    partes = []
    if not d["vigente_desde"]:
        partes.append("Sin fecha de vigencia")
    if n := _otros_errores(d):
        partes.append(f"{n} por corregir")
    return " · ".join(partes) or "Lista para confirmar"


borradores = [d for d in delims if d["estado"] == "borrador"]
confirmadas = len(delims) - len(borradores)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Confirmadas", f"{confirmadas} de {len(delims)}")
c2.metric("Borradores", len(borradores))
c3.metric("Sin fecha de vigencia", sum(not d["vigente_desde"] for d in borradores))
c4.metric("Con cifras por corregir", sum(_otros_errores(d) > 0 for d in borradores))
if delims:
    st.progress(confirmadas / len(delims))

izq, der = st.columns([3, 1], vertical_alignment="bottom")
filtro = izq.segmented_control("Estado", ["Todas", "Borrador", "Vigente", "Reemplazada"], default="Todas",
                               required=True, key="filtro_estado")
if der.button("Registrar delimitación", icon=":material/note_add:", width="stretch"):
    _registrar()

visibles = [d for d in delims if filtro == "Todas" or ESTADOS[d["estado"]] == filtro]
ids = [d["id"] for d in visibles]
if st.session_state.get("delim_id") not in ids:
    # Por defecto, lo que falta por hacer: el primer borrador a la vista.
    st.session_state["delim_id"] = next(
        (x["id"] for x in visibles if x["estado"] == "borrador"), ids[0] if ids else None)
did = st.session_state["delim_id"]

if not visibles:
    st.info("No hay delimitaciones en ese estado." if delims else
            "Aún no hay delimitaciones. Use «Registrar delimitación» para crear la primera.")
    st.stop()

seleccion = st.dataframe(
    pd.DataFrame([{
        "Proyecto": d["proyecto"],
        "Central XM": d["codigo_submercado"] or "— sin enlazar —",
        "Radicado": d["radicado"],
        "Fecha": _fecha(d["fecha_documento"]),
        "Rige desde": _fecha(d["vigente_desde"]),
        "Estado": ESTADOS[d["estado"]],
        "Fórmula": dm.formula(dm.Delimitacion(modo=d["modo"], filas=(), trae_s2=bool(d["trae_s2"]))) or "%",
        "Pendiente": _pendiente(d),
    } for d in visibles]),
    hide_index=True, height=320, placeholder="—",
    column_config={
        "Fecha": st.column_config.DateColumn(format="DD/MM/YYYY"),
        "Rige desde": st.column_config.DateColumn(format="DD/MM/YYYY"),
    },
    on_select="rerun", selection_mode="single-row",
    selection_default={"selection": {"rows": [ids.index(did)]}},
    key=f"lista_{filtro}_{st.session_state.get('lista_n', 0)}",
    alt="Lista de delimitaciones; seleccione una fila para abrirla",
)
if seleccion.selection.rows and ids[seleccion.selection.rows[0]] != did:
    did = st.session_state["delim_id"] = ids[seleccion.selection.rows[0]]
st.caption("Seleccione una fila para abrir la delimitación.")


# ---------------------------------------------------------------------------
# Detalle de una delimitación
# ---------------------------------------------------------------------------

d = next(x for x in delims if x["id"] == did)
es_borrador = d["estado"] == "borrador"
hectareas = d["modo"] == "hectareas"
mem = repo.delimitacion_en_memoria(con, did)

st.divider()
izq, der = st.columns([3, 1], vertical_alignment="center")
izq.subheader(f"{d['proyecto']} — {d['radicado']}")
insignias = [
    f":{COLOR_ESTADO[d['estado']]}-badge[{ESTADOS[d['estado']]}]",
    f":gray-badge[Documento del {_fecha_txt(d['fecha_documento'])}]",
    f":gray-badge[Rige desde: {_fecha_txt(d['vigente_desde'])}]",
    f":gray-badge[{'Fórmula ' + str(dm.formula(mem)) if hectareas else 'Porcentajes digitados'}]",
]
if hectareas and d["paramo_declarado"] is not None:
    insignias.append(f":gray-badge[Páramo: {'sí' if d['paramo_declarado'] else 'no'}]")
insignias.append(f":blue-badge[Central XM: {d['codigo_submercado']}]" if d["codigo_submercado"]
                 else ":red-badge[Sin central de XM]")
izq.markdown(" ".join(insignias))
if d["codigo_submercado"]:
    vecinos = [p["nombre"] for p in repo.proyectos_de_central(con, d["codigo_submercado"])
               if p["id"] != d["proyecto_id"]]
    if vecinos:
        izq.caption(f"Comparte la central {d['codigo_submercado']} con: {', '.join(vecinos)}. "
                    "Se liquidan juntas, una sola vez.")
if d["observaciones"]:
    izq.caption(d["observaciones"])

with der.popover("Cambiar central de XM", icon=":material/edit:", width="stretch"):
    codigo = st.text_input("Código de submercado", value=d["codigo_submercado"] or "",
                           key=f"xm_{d['proyecto_id']}")
    if d["proyecto_notas"]:
        st.caption(d["proyecto_notas"])
    if st.button("Guardar central", key=f"xm_btn_{d['proyecto_id']}"):
        try:
            repo.enlazar_proyecto(con, d["proyecto_id"], codigo)
            _refrescar()
            st.toast("Central guardada.")
            st.rerun()
        except sqlite3.Error as e:
            st.error(f"No se pudo guardar la central. Detalle técnico: {e}")

col_pdf, col_datos = st.columns(2, gap="medium")

# --- PDF al lado ----------------------------------------------------------------
with col_pdf:
    ruta_pdf = RAIZ / d["pdf_ruta"] if d["pdf_ruta"] else None
    if ruta_pdf and ruta_pdf.exists():
        try:
            st.pdf(ruta_pdf, height=900, alt=f"PDF de la delimitación {d['radicado']}")
        except Exception:
            st.warning("Para ver el PDF aquí mismo instale el visor: `pip install \"streamlit[pdf]\"`. "
                       "Mientras tanto, descárguelo con el botón.", title="Visor de PDF no instalado")
        a, b = st.columns([1, 2], vertical_alignment="center")
        a.download_button("Descargar el PDF", ruta_pdf.read_bytes(), file_name=d["pdf_nombre"],
                          mime="application/pdf", icon=":material/download:", key=f"pdf_{did}")
        b.caption(f"{d['pdf_nombre']} · SHA-256 {d['pdf_sha256'][:16]}…")
    else:
        st.warning("Esta delimitación no tiene PDF adjunto: no se puede cotejar ni confirmar.",
                   title="Sin PDF")

# --- cifras -----------------------------------------------------------------------
filas = con.execute("SELECT * FROM delimitacion_filas WHERE delimitacion_id = ? ORDER BY rowid", (did,)).fetchall()
if hectareas:
    tabla = pd.DataFrame(
        [{"Autoridad": f["autoridad_sigla"], "S1ᵢ (ha)": _exacto(repo.a_decimal(f["s1_ha"])),
          "S2ᵢ (ha)": _exacto(repo.a_decimal(f["s2_ha"]))} for f in filas],
        columns=["Autoridad", "S1ᵢ (ha)", "S2ᵢ (ha)"], dtype="object")
else:
    tabla = pd.DataFrame(
        [{"Autoridad": f["autoridad_sigla"],
          "Porcentaje": _exacto(None if f["porcentaje"] is None else _decimal(Fraction(f["porcentaje"])))}
         for f in filas],
        columns=["Autoridad", "Porcentaje"], dtype="object")

sucio = False
with col_datos:
    if es_borrador:
        v = st.session_state.get("form_n", 0)  # cambia al guardar o descartar: reinicia los campos
        with st.container(border=True):
            st.markdown("##### 1. Cifras de la delimitación")
            st.caption("Escriba las cifras como vienen en el PDF («86.537,28373»). «N/A» o vacío = el IGAC "
                       "declara que no hay área; un cero digitado no se acepta.")
            a, b = st.columns(2)
            radicado = a.text_input("Radicado", value=d["radicado"], key=f"rad_{did}_{v}").strip()
            fecha_doc = b.date_input("Fecha del documento", value=_fecha(d["fecha_documento"]),
                                     format="DD/MM/YYYY", key=f"fd_{did}_{v}")
            lectura: list[str] = []   # lo digitado que no se pudo leer

            def _numero(texto, donde: str) -> Decimal | None:
                try:
                    return numero_desde_texto(_celda(texto))
                except dm.NumeroInvalido as e:
                    lectura.append(f"{donde}: {e}")
                    return None

            guardado = {"radicado": d["radicado"], "fecha_documento": d["fecha_documento"]}
            actual = {"radicado": radicado,
                      "fecha_documento": fecha_doc.isoformat() if fecha_doc else d["fecha_documento"]}
            if hectareas:
                a, b = st.columns(2, vertical_alignment="bottom")
                paramo = a.segmented_control(
                    "¿Declara intersección con páramo?", ["sí", "no"], key=f"par_{did}_{v}",
                    default=None if d["paramo_declarado"] is None else "sí" if d["paramo_declarado"] else "no")
                trae_s2 = b.checkbox("La delimitación trae la columna S2", value=bool(d["trae_s2"]),
                                     key=f"s2_{did}_{v}")
                a, b = st.columns(2)
                s1_t = a.text_input("Total S1 (fila TOTAL)", value=_exacto(repo.a_decimal(d["s1_t_declarado"]))
                                    if d["s1_t_declarado"] else "", key=f"s1t_{did}_{v}")
                s2_t = b.text_input("Total S2 (fila TOTAL)", value=_exacto(repo.a_decimal(d["s2_t_declarado"]))
                                    if d["s2_t_declarado"] else "", key=f"s2t_{did}_{v}", disabled=not trae_s2)
                guardado.update(
                    paramo_declarado=None if d["paramo_declarado"] is None else bool(d["paramo_declarado"]),
                    trae_s2=bool(d["trae_s2"]),
                    s1_t_declarado=repo.a_decimal(d["s1_t_declarado"]),
                    s2_t_declarado=repo.a_decimal(d["s2_t_declarado"]))
                actual.update(
                    paramo_declarado=None if paramo is None else paramo == "sí", trae_s2=trae_s2,
                    s1_t_declarado=_numero(s1_t, "Total S1"),
                    s2_t_declarado=_numero(s2_t, "Total S2") if trae_s2 else None)

            columnas_cifras = [c for c in tabla.columns if c != "Autoridad"]
            editada = st.data_editor(
                tabla, num_rows="dynamic", hide_index=True, key=f"ed_{did}_{v}",
                column_config={
                    "Autoridad": st.column_config.SelectboxColumn(
                        options=list(autoridades), required=True, width="large",
                        format_func=lambda s: f"{s} — {autoridades.get(s, '¿?')}",
                        help="SIN_ASIGNAR = «autoridad ambiental urbana o área en litigio»: su parte se retiene."),
                    **{c: st.column_config.TextColumn() for c in columnas_cifras},
                },
                alt="Cifras por autoridad, editables",
            )

            if hectareas:
                filas_guardadas = [{"autoridad": f["autoridad_sigla"], "s1_ha": repo.a_decimal(f["s1_ha"]),
                                    "s2_ha": repo.a_decimal(f["s2_ha"])} for f in filas]
            else:
                filas_guardadas = [{"autoridad": f["autoridad_sigla"], "porcentaje": repo.a_fraccion(f["porcentaje"])}
                                   for f in filas]
            nuevas = []
            for r in editada.to_dict("records"):
                sigla = _celda(r["Autoridad"])
                if not isinstance(sigla, str) or not sigla:
                    continue
                if hectareas:
                    nuevas.append({"autoridad": sigla, "s1_ha": _numero(r["S1ᵢ (ha)"], f"{sigla}, S1"),
                                   "s2_ha": _numero(r["S2ᵢ (ha)"], f"{sigla}, S2")})
                else:
                    p = _numero(r["Porcentaje"], f"{sigla}, porcentaje")
                    nuevas.append({"autoridad": sigla, "porcentaje": None if p is None else Fraction(p)})

            # Suma de lo digitado contra el total declarado, antes de guardar.
            def _cuadre(nombre: str, suma: Decimal, declarado: Decimal | None, unidad: str) -> str:
                base = f"Σ {nombre} = **{texto_colombiano(suma, 5)}** {unidad}"
                if declarado is None:
                    return base + " · :gray[sin total declarado para cotejar]"
                if suma == declarado:
                    return base + " · :green[:material/check_circle: igual al total declarado]"
                return (base + f" · total declarado {texto_colombiano(declarado, 5)} · "
                        f":red[:material/error: diferencia {texto_colombiano(suma - declarado, 5)} {unidad}]")

            if not lectura and nuevas:
                if hectareas:
                    lineas = [_cuadre("S1ᵢ", sum((f["s1_ha"] or 0 for f in nuevas), Decimal(0)),
                                      actual["s1_t_declarado"], "ha")]
                    if trae_s2:
                        lineas.append(_cuadre("S2ᵢ", sum((f["s2_ha"] or 0 for f in nuevas), Decimal(0)),
                                              actual["s2_t_declarado"], "ha"))
                else:
                    lineas = [_cuadre("porcentajes", _decimal(sum((f["porcentaje"] or 0 for f in nuevas), Fraction(0))),
                                      Decimal(100), "%")]
                st.markdown("  \n".join(lineas))

            repetidas = sorted({f["autoridad"] for f in nuevas
                                if sum(x["autoridad"] == f["autoridad"] for x in nuevas) > 1})
            if repetidas:
                lectura.append(f"Autoridad repetida: {', '.join(repetidas)}. Cada autoridad va en una sola fila.")
            if not radicado:
                lectura.append("Falta el radicado.")
            elif radicado != d["radicado"] and con.execute(
                    "SELECT 1 FROM delimitaciones WHERE radicado = ? AND id <> ?", (radicado, did)).fetchone():
                lectura.append(f"Ya existe otra delimitación con el radicado {radicado}.")
            if lectura:
                st.error(_lista(lectura), title="No se puede guardar todavía", icon=":material/error:")

            sucio = bool(lectura) or actual != guardado or nuevas != filas_guardadas
            a, b = st.columns(2)
            if a.button("Guardar borrador", type="primary", disabled=not sucio or bool(lectura),
                        icon=":material/save:", width="stretch", key=f"guardar_{did}"):
                try:
                    repo.actualizar_borrador(con, did, filas=nuevas, **actual)
                except repo.ErrorRepositorio as e:
                    st.error(str(e))
                except sqlite3.IntegrityError as e:
                    st.error("No se pudo guardar: hay una autoridad repetida o que no existe en el maestro. "
                             f"Detalle técnico: {e}")
                else:
                    st.session_state["form_n"] = v + 1
                    _refrescar()
                    st.toast("Borrador guardado.")
                    st.rerun()
            if b.button("Descartar cambios", disabled=not sucio, icon=":material/undo:", width="stretch",
                        key=f"descartar_{did}"):
                st.session_state["form_n"] = v + 1
                st.rerun()
            if sucio:
                st.warning("La validación y los porcentajes de abajo corresponden a lo último que se guardó, "
                           "no a lo que está en pantalla. Guarde o descarte para poder confirmar.",
                           title="Hay cambios sin guardar", icon=":material/edit_note:")

        with st.container(border=True):
            st.markdown("##### 2. Fecha de vigencia")
            vig = st.date_input("Rige desde", value=_fecha(d["vigente_desde"]), format="DD/MM/YYYY",
                                key=f"vig_{did}")
            st.caption("El PDF no trae esta fecha: se digita aquí y se guarda al elegirla. Por defecto es el "
                       "25/11/2026. Si no es el día 1, ese mes se liquida solo desde ese día (incluido).")
            nueva_vig = vig.isoformat() if vig else None
            if nueva_vig != d["vigente_desde"]:
                repo.actualizar_borrador(con, did, vigente_desde=nueva_vig)
                _refrescar()
                st.rerun()
    else:
        with st.container(border=True):
            st.markdown("##### Cifras de la delimitación")
            st.dataframe(tabla, hide_index=True, alt="Cifras por autoridad")
            st.info("Las cifras de una delimitación confirmada no se editan. Si el IGAC emite otra, regístrela "
                    "como nueva: reemplaza a esta desde su fecha de vigencia y esta sigue rigiendo los meses anteriores.")
        if d["estado"] == "vigente":
            with st.container(border=True):
                st.markdown("##### Ajustar la fecha de vigencia")
                st.caption("Solo cambia desde cuándo rige; las cifras no se tocan y el cambio queda registrado. "
                           "Si no es el día 1, ese mes se liquida solo desde ese día (incluido). Si esta delimitación "
                           "corrigió a otra, la corregida se mueve con ella.")
                a, b = st.columns([1, 2])
                nueva = a.date_input("Rige desde", value=_fecha(d["vigente_desde"]), format="DD/MM/YYYY",
                                     key=f"aj_{did}_{d['vigente_desde']}")
                motivo = b.text_input("Motivo del cambio", key=f"ajm_{did}", placeholder="Por qué se ajusta la fecha")
                cambia = nueva is not None and nueva.isoformat() != d["vigente_desde"]
                usadas = con.execute("SELECT count(DISTINCT liquidacion_id) FROM liquidacion_delimitaciones "
                                     "WHERE delimitacion_id = ?", (did,)).fetchone()[0]
                if cambia and usadas:
                    st.warning(f"{usadas} liquidaciones guardadas usan esta delimitación con la fecha actual. Después "
                               "del cambio, vuelva a guardar esos meses en «Liquidación»: queda una versión nueva.",
                               icon=":material/warning:")
                if st.button("Guardar la nueva fecha", disabled=not cambia, icon=":material/event:", key=f"ajb_{did}"):
                    try:
                        movidas = repo.cambiar_vigencia(con, did, nueva.isoformat(), motivo)
                    except repo.ErrorRepositorio as e:
                        st.error(str(e), icon=":material/error:")
                    else:
                        _refrescar()
                        st.toast(f"Rige desde el {nueva.strftime('%d/%m/%Y')}."
                                 + (" La delimitación que esta corrigió se movió con ella, para que no vuelva a regir."
                                    if movidas else ""))
                        st.rerun()
                cambios = repo.cambios_de_delimitacion(con, did)
                if cambios:
                    st.caption("Cambios anteriores: " + " · ".join(
                        f"{_fecha_txt(c['antes'])} → {_fecha_txt(c['despues'])} el {c['cambiado_en'][:10]}"
                        + (f" ({c['motivo']})" if c["motivo"] else "") for c in cambios))

    # --- validación y porcentajes resultantes -------------------------------------
    resultado = validaciones.get(did) or repo.validar_delimitacion(con, did)
    with st.container(border=True):
        st.markdown(f"##### {'3. ' if es_borrador else ''}Validación y porcentajes")
        if resultado.errores:
            n = len(resultado.errores)
            st.error(_lista(resultado.errores), icon=":material/error:",
                     title="1 cosa por corregir" if n == 1 else f"{n} cosas por corregir")
        elif es_borrador:
            st.success("Lo guardado cumple todas las validaciones.", icon=":material/check_circle:")
        if resultado.avisos:
            st.warning(_lista(resultado.avisos), icon=":material/info:", title="Para tener en cuenta")
        pct = _tabla_porcentajes(mem, autoridades)
        if pct is not None:
            st.caption(_texto_formula(mem) + " Cálculo con fracciones exactas: la suma es 1 sin residuo. "
                       "Los 5 decimales son solo de presentación.")
            st.dataframe(pct, hide_index=True, alt="Participación de cada autoridad en el 3 %")
            if any(f.autoridad == SIN_ASIGNAR for f in mem.filas):
                st.caption("SIN_ASIGNAR: la parte de «autoridad ambiental urbana o área en litigio» se calcula "
                           "y queda retenida; no se transfiere hasta que la ONVS defina el beneficiario.")

    # --- confirmar ------------------------------------------------------------------
    if es_borrador:
        with st.container(border=True):
            st.markdown("##### 4. Confirmar")
            cotejo = st.checkbox("Cotejé cada cifra y la fecha de vigencia contra el PDF.", key=f"cot_{did}",
                                 disabled=sucio or not resultado.ok)
            if st.button("Revisar y confirmar…", type="primary", icon=":material/verified:",
                         disabled=not (cotejo and resultado.ok and not sucio), key=f"conf_{did}"):
                _confirmar(did)
            if sucio:
                st.caption("Guarde o descarte los cambios del paso 1 para poder confirmar.")
            elif not resultado.ok:
                st.caption("Corrija lo marcado en rojo para poder confirmar. La aplicación no ajusta cifras sola.")
