"""Pantalla de parámetros: serie de la TVB por año y parámetros del Decreto 800.

La TVB se guarda en la base (tabla `tvb`), una por año. α, 6 %, 3 % y el
umbral viven en `motor/parametros.py`, un único archivo versionado (regla 6):
aquí se muestran, no se editan.
"""
from __future__ import annotations

import hashlib
from datetime import date

import pandas as pd
import streamlit as st

from paginas.comun import conexion, repo
from datos.parse_creg_tvb import parsear_tvb_consolidado_rtf
from datos.rutas import dir_creg
from motor.delimitacion import NumeroInvalido, numero_desde_texto, texto_colombiano
from motor.parametros import PARAMETROS_VIGENTES as P

con = conexion()
st.title("Parámetros")

# ---------------------------------------------------------------------------
# TVB
# ---------------------------------------------------------------------------

st.subheader("Tarifa de venta en bloque (TVB)")
st.caption("La fija la CREG para cada año (Resolución CREG 010 de 2018) y rige del 1 de enero al 31 de diciembre. "
           "La liquidación de un mes usa la TVB de su año; si falta, el mes no se liquida.")

serie = repo.listar_tvb(con)
liquidados = {r[0]: r[1] for r in con.execute(
    "SELECT tvb_anio, count(*) FROM liquidaciones GROUP BY tvb_anio")}

if serie:
    st.dataframe(pd.DataFrame([{
        "Año": t["anio"], "TVB ($/kWh)": texto_colombiano(repo.a_decimal(t["pesos_kwh"]), 5),
        "Fuente": t["fuente"], "Liquidaciones que la usan": liquidados.get(t["anio"], 0),
    } for t in serie]), hide_index=True, width="stretch", height="content",
        column_config={"Año": st.column_config.NumberColumn(format="%d")},
        alt="Serie de la tarifa de venta en bloque por año")
else:
    st.info("Aún no hay TVB registrada. Cargue la serie de la CREG o registre un año.", icon=":material/info:")

sin_tvb = sorted({int(p[:4]) for p in repo.periodos_con_generacion(con)} - {t["anio"] for t in serie})
if sin_tvb:
    st.warning(f"Hay generación de XM cargada de {', '.join(map(str, sin_tvb))} y no hay TVB de ese año: "
               "esos meses no se pueden liquidar.", title="Faltan años", icon=":material/warning:")

izq, der = st.columns(2, gap="medium")

with izq.container(border=True):
    st.markdown("**Cargar la serie de la CREG**")
    carpeta = dir_creg()
    rtf = carpeta / "TVB Consolidado.rtf" if carpeta else None
    if rtf is None or not rtf.exists():
        st.caption("No se encontró `insumos/creg-tarifas/TVB Consolidado.rtf`.")
    else:
        try:
            tarifas = parsear_tvb_consolidado_rtf(rtf)
        except Exception as e:  # el archivo es un insumo externo: se informa, no se cae la pantalla
            tarifas = []
            st.error(f"No se pudo leer «{rtf.name}»: {e}", icon=":material/error:")
        actuales = {t["anio"]: repo.a_decimal(t["pesos_kwh"]) for t in serie}
        nuevas = [t for t in tarifas if t.anio not in actuales]
        distintas = [t for t in tarifas if t.anio in actuales and actuales[t.anio] != t.tvb_pesos_kwh]
        st.caption(f"`{rtf.name}` trae {len(tarifas)} años"
                   + (f" ({tarifas[0].anio}–{tarifas[-1].anio})" if tarifas else "")
                   + f": {len(nuevas)} sin registrar y {len(distintas)} con un valor distinto del registrado.")
        if distintas:
            st.caption("Con valor distinto (no se pisan desde aquí; corríjalos a mano si corresponde): "
                       + ", ".join(f"{t.anio}: {texto_colombiano(t.tvb_pesos_kwh, 5)}" for t in distintas))
        if st.button(f"Registrar {len(nuevas)} años", disabled=not nuevas, icon=":material/playlist_add:",
                     key="tvb_serie"):
            for t in nuevas:
                repo.guardar_tvb(con, t.anio, t.tvb_pesos_kwh, f"{t.fuente} · {t.resolucion}", t.hash_archivo_fuente)
            st.toast(f"{len(nuevas)} años registrados.")
            st.rerun()

with der.container(border=True):
    st.markdown("**Registrar o corregir un año**")
    with st.form("tvb_form", border=False, clear_on_submit=False):
        a, b = st.columns(2)
        anio = a.number_input("Año", min_value=2017, max_value=date.today().year + 1, value=date.today().year,
                              step=1, format="%d")
        valor = b.text_input("TVB ($/kWh)", placeholder="192,23886",
                             help="Como la publica la CREG, con coma decimal.")
        fuente = st.text_input("Fuente", placeholder="Resolución o archivo de la CREG / XM de donde sale el valor")
        respaldo = st.file_uploader("Archivo de respaldo (opcional)", type=["pdf", "xlsx", "xls", "rtf", "docx"])
        enviar = st.form_submit_button("Guardar TVB", type="primary", icon=":material/save:")
    if enviar:
        try:
            tvb = numero_desde_texto(valor)
        except NumeroInvalido as e:
            tvb = None
            st.error(str(e), icon=":material/error:")
        else:
            if tvb is None or tvb <= 0:
                st.error("Escriba la TVB, mayor que cero (p. ej. 192,23886).", icon=":material/error:")
            elif not fuente.strip():
                st.error("Indique la fuente del valor: sin fuente no se registra.", icon=":material/error:")
            else:
                previa = repo.tvb_de(con, int(anio))
                sha = hashlib.sha256(respaldo.getvalue()).hexdigest() if respaldo else None
                texto_fuente = fuente.strip() + (f" · {respaldo.name}" if respaldo else "")
                repo.guardar_tvb(con, int(anio), tvb, texto_fuente, sha)
                usadas = liquidados.get(int(anio), 0)
                if previa is not None and repo.a_decimal(previa["pesos_kwh"]) != tvb and usadas:
                    st.session_state["tvb_aviso"] = (
                        f"La TVB de {int(anio)} cambió y {usadas} liquidaciones guardadas usaron el valor anterior "
                        f"({texto_colombiano(repo.a_decimal(previa['pesos_kwh']), 5)}). Vuelva a guardar esos meses "
                        f"en «Liquidación»: queda una versión nueva y la anterior se conserva.")
                st.toast(f"TVB de {int(anio)} guardada.")
                st.rerun()

if aviso := st.session_state.pop("tvb_aviso", None):
    st.warning(aviso, title="Hay liquidaciones con la TVB anterior", icon=":material/warning:")

# ---------------------------------------------------------------------------
# Parámetros del Decreto
# ---------------------------------------------------------------------------

st.subheader("Parámetros del Decreto 800 de 2026")
st.caption(f"Versión `{P.version}`. Están en un único archivo versionado (`motor/parametros.py`) y no se editan "
           "desde la aplicación: cambiarlos exige un acto administrativo y una versión nueva.")
st.table([
    {"Parámetro": "Transferencia total", "Valor": "6 % de las ventas brutas",
     "Norma": "Art. 45 de la Ley 99 de 1993, modificado por el art. 24 de la Ley 1930 de 2018"},
    {"Parámetro": "Bolsa de corporaciones y Parques Nacionales", "Valor": "3 % de las ventas brutas",
     "Norma": "Art. 2.2.9.2.1.5, numeral 1, del Decreto 1076 de 2015 (sust. Decreto 800 de 2026)"},
    {"Parámetro": "α, ponderación de páramos (Fórmula 2)", "Valor": texto_colombiano(P.alpha, 4),
     "Norma": "Art. 2.2.9.2.1.5, numeral 1"},
    {"Parámetro": "Umbral de sujeción", "Valor": f"más de {texto_colombiano(P.capacidad_minima_kw, 0)} kW, por empresa",
     "Norma": "Art. 45 de la Ley 99 de 1993"},
    {"Parámetro": "Tipos de generación que suman al umbral", "Valor": ", ".join(t.capitalize() for t in P.tipos_generacion_del_umbral),
     "Norma": "Hoja «Empresas Sujetos activos» de XM; por confirmar con la ONVS"},
    {"Parámetro": "Participaciones", "Valor": "Fracciones exactas; 5 decimales solo para mostrar",
     "Norma": "Parágrafo 2.º del art. 2.2.9.2.1.2 (lectura decidida el 11/11/2026)"},
    {"Parámetro": "Montos", "Valor": "A centavos, mitad hacia arriba; el residuo va al mayor beneficiario",
     "Norma": "Supuesto operativo, por confirmar con la ONVS"},
], border="horizontal")
