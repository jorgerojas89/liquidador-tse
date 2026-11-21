"""Pantalla 2 — Generación de XM.

Cargar el Excel de XM (formato mensual 2018-2025 o diario-horario 2026), ver
qué centrales reconoció, cuáles cruzan con un proyecto, cuáles no y cuáles
quedan bajo el umbral del art. 45 (CLAUDE.md, «Pantallas», 2).
"""
from __future__ import annotations

import hashlib
import unicodedata
from decimal import Decimal

import pandas as pd
import streamlit as st

from paginas.comun import conexion, repo
from datos import cargue_xm as cx
from datos.rutas import dir_xm
from motor.delimitacion import texto_colombiano

con = conexion()
st.title("Generación de XM")
st.caption(
    "Se guardan todos los recursos y todas las versiones de XM. Solo la versión firme (**TXF**) produce una "
    "liquidación oficial; con TX1…TX5 es preliminar. Un mes que el archivo no cubre completo no se liquida."
)

versiones = {r["codigo"]: r for r in con.execute("SELECT * FROM versiones_xm ORDER BY orden")}


def _nfc(texto: str) -> str:
    """macOS entrega los nombres de archivo en NFD («ó» = «o» + tilde): se normalizan."""
    return unicodedata.normalize("NFC", texto)


def _kwh(v: Decimal | None) -> str:
    return "—" if v is None else texto_colombiano(v, 2)


def _kw(v: Decimal) -> str:
    return texto_colombiano(v, 0)


def _caracter(codigos) -> str:
    if not codigos:
        return "—"
    return "Oficial (firme)" if all(versiones[c]["es_firme"] for c in codigos if c in versiones) else "Preliminar"


def _umbral(c: cx.CentralEnCarga) -> str:
    s = c.sujecion
    if s is None:
        return "Sin determinar: no hay capacidad cargada"
    estado = "Sujeta" if s.sujeta else "Bajo el umbral"
    return f"{estado} · {_kw(s.capacidad_total_kw)} kW ({s.anio_capacidad})"


# ---------------------------------------------------------------------------
# Cargar un archivo
# ---------------------------------------------------------------------------

with st.container(border=True):
    st.markdown("**Cargar un archivo de XM**")
    origen = st.segmented_control("Origen", ["Subir archivo", "Carpeta de insumos"], default="Subir archivo",
                                  key="xm_origen")
    contenido = nombre = None
    if origen == "Carpeta de insumos":
        carpeta = dir_xm()
        archivos = sorted(p for p in carpeta.glob("*.xlsx") if not p.name.startswith("~$")) if carpeta else []
        elegido = st.selectbox("Archivo de insumos/xm-generacion", archivos, format_func=lambda p: _nfc(p.name),
                               index=None, placeholder="Elija un archivo", key="xm_archivo")
        if elegido is not None:
            contenido, nombre = elegido.read_bytes(), _nfc(elegido.name)
    else:
        subido = st.file_uploader("Excel de XM", type=["xlsx"], key="xm_subido")
        if subido is not None:
            contenido, nombre = subido.getvalue(), _nfc(subido.name)

    lectura = None
    if contenido:
        sha = hashlib.sha256(contenido).hexdigest()
        # (lectura, error) por archivo: también se recuerda el error, para no
        # volver a leer un archivo dañado en cada clic.
        lecturas = st.session_state.setdefault("_lecturas_xm_v2", {})
        if sha not in lecturas:
            lecturas.clear()  # se guarda solo la última, para no llenar la memoria
            with st.spinner("Leyendo el archivo de XM… (el de 2026, unos 15 segundos)"):
                lecturas[sha] = cx.leer_archivo(contenido, nombre)
        lectura, error = lecturas[sha]
        if error:
            st.error(error, title="No se pudo leer el archivo", icon=":material/error:")

    if lectura is not None:
        codigos = {f.codigo_submercado for f in lectura.filas}
        a, b, c, d = st.columns(4)
        a.metric("Formato", "Diario-horario" if lectura.formato == "diario" else "Mensual")
        b.metric("Año", lectura.anio)
        c.metric("Recursos", len(codigos))
        d.metric("Meses", len(lectura.periodos()))

        por_mes = lectura.versiones_por_mes()
        meses = []
        for p in lectura.periodos():
            dias_mes = next(f.dias_mes for f in lectura.filas if f.periodo == p)
            dias = lectura.dias_en_archivo.get(p)
            meses.append({
                "Mes": p,
                "Versión": ", ".join(sorted(por_mes.get(p, ()), key=lambda v: versiones.get(v, {"orden": 0})["orden"]))
                           or "— se declara —",
                "Carácter": _caracter(por_mes.get(p)) if lectura.trae_version else "Según la versión declarada",
                "Días en el archivo": "Total mensual" if dias is None else f"{dias} de {dias_mes}",
                "Estado": "Completo" if dias is None or dias == dias_mes else "Incompleto: no se liquida",
            })
        st.dataframe(pd.DataFrame(meses), hide_index=True, width="stretch")
        for aviso in lectura.avisos:
            st.warning(aviso, icon=":material/info:")

        previa = cx.carga_existente(con, lectura.archivo_sha256)
        raras = cx.versiones_desconocidas(con, lectura)
        version = None
        if previa is not None:
            st.info(f"Este archivo ya se cargó el {previa['cargada_en'][:10]} (cargue {previa['id']}). "
                    "Véalo abajo, en «Cargues guardados».", icon=":material/check_circle:")
        elif raras:
            st.error(f"El archivo trae versiones de XM que no están en el catálogo: {', '.join(sorted(raras))}. "
                     "Hay que agregarlas antes de cargar.", icon=":material/error:")
        else:
            if not lectura.trae_version:
                version = st.selectbox(
                    "Versión de XM de este archivo (no la trae; se declara)", list(versiones), index=None,
                    placeholder="Elija la versión", key="xm_version",
                    help="TXF = liquidación firme de XM. Con TX1…TX5 la liquidación será preliminar.",
                )
            observaciones = st.text_input("Observaciones (opcional)", key="xm_obs")
            listo = lectura.trae_version or version is not None
            if st.button("Guardar cargue", type="primary", disabled=not listo, icon=":material/upload:",
                         key="xm_guardar"):
                try:
                    carga_id = cx.guardar_lectura(con, lectura, version_declarada=version,
                                                  observaciones=observaciones.strip() or None)
                    st.session_state["xm_carga"] = carga_id
                    st.toast(f"Cargue {carga_id} guardado.")
                    st.rerun()
                except repo.ErrorRepositorio as e:
                    st.error(str(e), icon=":material/error:")


# ---------------------------------------------------------------------------
# Cargues guardados
# ---------------------------------------------------------------------------

st.subheader("Cargues guardados")
cargas = {c["id"]: c for c in cx.listar_cargas(con)}
if not cargas:
    st.info("Aún no hay archivos de XM cargados.")
    st.stop()
if st.session_state.get("xm_carga") not in cargas:
    st.session_state["xm_carga"] = next(iter(cargas))
carga_id = st.selectbox(
    "Cargue", list(cargas), key="xm_carga",
    format_func=lambda i: f"{i} · {cargas[i]['archivo_nombre']} · {cargas[i]['desde']} a {cargas[i]['hasta']} "
                          f"· cargado el {cargas[i]['cargada_en'][:10]}",
)
carga = cargas[carga_id]
st.caption(
    f"SHA-256 {carga['archivo_sha256']} · formato {carga['formato'] or '—'} · "
    + (f"versión declarada {carga['version_declarada']}" if carga["version_declarada"] else "versión tomada del archivo")
    + (f" · {carga['observaciones']}" if carga["observaciones"] else "")
)

r = cx.resumen_carga(con, carga_id)
a, b, c, d = st.columns(4)
a.metric("Centrales con proyecto", len(r.con_proyecto),
         help="Una central de XM puede corresponder a varios proyectos del IGAC (GUATRON, PAJARITO).")
b.metric("Proyectos sin generación", len(r.proyectos_sin_generacion) + len(r.proyectos_sin_central))
c.metric("Hidráulicas sin proyecto", len(r.hidraulicas_sin_proyecto))
d.metric("Bajo el umbral o sin determinar", len(r.bajo_umbral))

vista = st.segmented_control(
    "Ver", ["Proyectos", "Meses", "Sin cruce", "Hidráulicas sin proyecto", "Umbral"], default="Proyectos",
    key="xm_vista",
)

periodos = [p["periodo"] for p in r.periodos]
incompletos = {p["periodo"] for p in r.periodos if p["dias_archivo"] is not None and p["dias_archivo"] < p["dias_mes"]}

if vista == "Proyectos":
    st.caption("Generación del mes en kWh. Un asterisco (*) marca un mes en que la central no trae todos los días: "
               "XM omite los días sin generación. Los meses incompletos no se liquidan.")
    filas = []
    for central in r.con_proyecto:
        fila = {"Central": central.codigo, "Proyectos": "; ".join(central.proyectos), "Recurso XM": central.recurso,
                "Empresa": central.sujecion.agente if central.sujecion else central.agente,
                "Umbral": _umbral(central)}
        for p in periodos:
            dato = central.por_periodo.get(p)
            etiqueta = f"{p} (incompleto)" if p in incompletos else p
            if dato is None:
                fila[etiqueta] = "—"
            else:
                falta = (dato["dias_archivo"] or 0) - (dato["dias_reportados"] or 0)
                fila[etiqueta] = _kwh(dato["generacion_kwh"]) + (" *" if falta > 0 else "")
        filas.append(fila)
    st.dataframe(pd.DataFrame(filas), hide_index=True, width="stretch", height="content")

elif vista == "Meses":
    st.dataframe(pd.DataFrame([{
        "Mes": p["periodo"],
        "Versión": ", ".join(sorted(p["versiones"], key=lambda v: versiones[v]["orden"])),
        "Carácter": _caracter(p["versiones"]),
        "Recursos": p["recursos"],
        "Días en el archivo": "Total mensual" if p["dias_archivo"] is None else f"{p['dias_archivo']} de {p['dias_mes']}",
        "Estado": "Incompleto: no se liquida" if p["periodo"] in incompletos else "Completo",
    } for p in r.periodos]), hide_index=True, width="stretch")

elif vista == "Sin cruce":
    if r.proyectos_sin_generacion:
        st.markdown("**Proyectos cuya central no aparece en este archivo**")
        st.dataframe(pd.DataFrame([{"Proyecto": x["proyecto"], "Central XM": x["codigo"]}
                                   for x in r.proyectos_sin_generacion]), hide_index=True, width="stretch")
    if r.proyectos_sin_central:
        st.markdown("**Proyectos sin central de XM enlazada**")
        st.caption("El enlace se hace en la pantalla Delimitaciones.")
        st.dataframe(pd.DataFrame({"Proyecto": r.proyectos_sin_central}), hide_index=True, width="stretch")
    if not (r.proyectos_sin_generacion or r.proyectos_sin_central):
        st.success("Todos los proyectos tienen central de XM y todas aparecen en este archivo.",
                   icon=":material/check_circle:")

elif vista == "Hidráulicas sin proyecto":
    st.caption("Centrales hidráulicas del archivo que no corresponden a ninguna delimitación del IGAC. "
               "No se liquidan; se listan para detectar enlaces faltantes.")
    st.dataframe(pd.DataFrame([{
        "Central": x.codigo, "Recurso XM": x.recurso,
        "Empresa": x.sujecion.agente if x.sujecion else x.agente, "Umbral": _umbral(x),
        "Generación del cargue (kWh)": _kwh(sum((v["generacion_kwh"] or Decimal(0) for v in x.por_periodo.values()),
                                                Decimal(0))),
    } for x in r.hidraulicas_sin_proyecto]), hide_index=True, width="stretch")

elif vista == "Umbral":
    st.caption(
        "Art. 45 de la Ley 99 de 1993: tributan las empresas cuya potencia instalada total supera 10.000 kW "
        "(por empresa, no por central). Se usa la capacidad efectiva neta del archivo mensual de XM más reciente "
        "con año igual o anterior al del periodo; el archivo de 2026 no trae capacidad."
    )
    if r.bajo_umbral:
        st.dataframe(pd.DataFrame([{
            "Central": x.codigo, "Recurso XM": x.recurso, "Proyectos": "; ".join(x.proyectos) or "—",
            "Empresa": x.sujecion.agente if x.sujecion else x.agente, "Umbral": _umbral(x),
        } for x in r.bajo_umbral]), hide_index=True, width="stretch")
    if not any(x.estado_umbral != "sujeta" for x in r.con_proyecto):
        st.success("Todas las centrales con proyecto pertenecen a empresas sujetas.", icon=":material/check_circle:")
