"""Pantalla 4 — Exportar.

XLSX con las variables intermedias y PDF resumen por autoridad (CLAUDE.md,
«Pantallas», 4). Se exporta lo GUARDADO en la pantalla Liquidación: la última
versión de cada central, que es la que tiene trazabilidad en la base.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from paginas.comun import autoridades_maestro, conexion, mes_largo, pesos, repo
from datos import exportar as ex
from datos import liquidar_mes as lm
from motor.delimitacion import SIN_ASIGNAR

con = conexion()
st.title("Exportar")
st.caption("Se exporta la liquidación guardada del mes: la última versión de cada central.")

periodos = repo.periodos_liquidados(con)
if not periodos:
    st.info("Aún no hay liquidaciones guardadas. Calcule y guarde el mes en la pantalla «Liquidación».",
            icon=":material/calculate:")
    st.stop()

periodo = st.selectbox("Periodo", periodos, format_func=lambda p: f"{mes_largo(p).capitalize()} ({p})",
                       key="exp_periodo")
guardadas = lm.liquidaciones_guardadas(con, periodo)
autoridades = autoridades_maestro(con)
mes = lm.calcular_mes(con, periodo)
# Todo sale de lo guardado: «No liquidados» son las centrales sin liquidación
# guardada, y lo que difiera del cálculo de hoy va como aviso dentro del archivo.
no_liquidadas = lm.no_liquidadas_de_lo_guardado(mes, guardadas)
avisos = lm.avisos_de_lo_guardado(con, mes, guardadas)
caracter = ex.caracter_del_mes(guardadas)
t = ex.totales(guardadas)

a, b, c, d = st.columns(4)
a.metric("Centrales guardadas", len(guardadas))
b.metric("Bolsa del 3 %", pesos(t["bolsa"]))
c.metric("A transferir", pesos(t["a_transferir"]))
d.metric("Retenido sin asignar", pesos(t["retenido"]))

if caracter == "preliminar":
    st.warning("Al menos una central está liquidada con una versión de XM que no es la firme (TXF). "
               "Los archivos salen rotulados como preliminares.", title="Liquidación preliminar",
               icon=":material/hourglass_top:")
else:
    st.success("Todas las centrales guardadas usan la versión firme de XM (TXF).", title="Liquidación oficial",
               icon=":material/verified:")
if avisos:
    st.warning("\n".join(f"- {a}" for a in avisos) + "\n\nLos avisos también quedan dentro del XLSX y del PDF. "
               "Si quiere exportar lo actual, guarde primero en «Liquidación».",
               title="Lo guardado no coincide con el cálculo actual", icon=":material/warning:")

with st.container(border=True):
    st.markdown("**Archivos**")
    a, b = st.columns(2)
    a.download_button(
        "Descargar XLSX con variables intermedias",
        data=lambda: ex.xlsx_mes(periodo, guardadas, no_liquidadas, autoridades, avisos=avisos),
        file_name=f"Liquidacion_TSE_{periodo}_{caracter}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        icon=":material/table_view:", type="primary", width="stretch", on_click="ignore", key=f"exp_xlsx_{periodo}",
    )
    b.download_button(
        "Descargar PDF resumen por autoridad",
        data=lambda: ex.pdf_resumen(periodo, guardadas, no_liquidadas, autoridades, avisos=avisos),
        file_name=f"Liquidacion_TSE_{periodo}_{caracter}.pdf", mime="application/pdf",
        icon=":material/picture_as_pdf:", width="stretch", on_click="ignore", key=f"exp_pdf_{periodo}",
    )
    st.caption("El XLSX trae cinco hojas: por autoridad, detalle (generación → TVB → ventas brutas → 3 % → "
               "participación → monto), trazabilidad (archivo de XM y delimitación con su SHA-256), centrales sin "
               "liquidación guardada y parámetros con la cita normativa.")

st.subheader("Resumen por autoridad")
st.dataframe(pd.DataFrame([{
    "Sigla": r["autoridad"],
    "Autoridad ambiental": ex.NOMBRE_RETENIDO if r["autoridad"] == SIN_ASIGNAR
    else (autoridades.get(r["autoridad"]) or {}).get("nombre", r["autoridad"]),
    "Centrales": r["centrales"], "Monto": pesos(r["monto"]),
} for r in ex.resumen_por_autoridad(guardadas)]), hide_index=True, width="stretch", height="content",
    alt="Monto guardado por autoridad ambiental")

st.subheader("Liquidaciones guardadas")
st.dataframe(pd.DataFrame([{
    "Proyecto": " + ".join(l["proyectos"]), "Central XM": l["codigo_submercado"],
    "Días": ex.dias_liquidados(l),
    "Versión": l["version_numero"], "Carácter": l["caracter"].capitalize(), "Versión XM": l["version_xm"],
    "Bolsa del 3 %": pesos(repo.a_decimal(l["bolsa_car_pnn"])),
    "Observación": ex.SIN_GENERACION if ex.sin_generacion(l) else "",
    "Delimitación": "; ".join(d["radicado"] for d in l["delimitaciones"]),
    "Guardada el": l["creada_en"].replace("T", " "),
} for l in guardadas]), hide_index=True, width="stretch", alt="Última versión guardada de cada central")
