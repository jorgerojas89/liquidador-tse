"""Liquidador TSE — herramienta interna de la ONVS (Minambiente).

Ejecutar desde la carpeta del proyecto:  streamlit run app.py
"""
import streamlit as st

st.set_page_config(page_title="Liquidador TSE", page_icon=":material/bolt:", layout="wide")

paginas = [
    st.Page("paginas/delimitaciones.py", title="Delimitaciones", icon=":material/description:", default=True),
    st.Page("paginas/generacion_xm.py", title="Generación XM", icon=":material/electric_bolt:"),
    st.Page("paginas/liquidacion.py", title="Liquidación", icon=":material/calculate:"),
    st.Page("paginas/exportar.py", title="Exportar", icon=":material/download:"),
    st.Page("paginas/proyecciones.py", title="Proyecciones", icon=":material/trending_up:"),
    st.Page("paginas/parametros.py", title="Parámetros", icon=":material/tune:"),
]
st.navigation(paginas, position="top").run()
