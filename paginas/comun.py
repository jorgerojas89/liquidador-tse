"""Utilidades compartidas por las pantallas de Streamlit."""
from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import streamlit as st  # noqa: E402

from datos import repositorio as repo  # noqa: E402
from datos.maestro_autoridades import cargar_maestro  # noqa: E402


def conexion():
    """Una conexión por ejecución de la página (SQLite no se comparte entre
    hilos). El maestro de autoridades se sincroniza una vez por sesión."""
    con = repo.abrir()
    if not st.session_state.get("_maestro_cargado"):
        repo.cargar_autoridades(con, cargar_maestro().values())
        st.session_state["_maestro_cargado"] = True
    return con


ESTADOS = {"borrador": "Borrador", "vigente": "Vigente", "reemplazada": "Reemplazada"}


# --- formato colombiano para las pantallas --------------------------------------

from motor.delimitacion import texto_colombiano  # noqa: E402

MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre")


def pesos(v) -> str:
    return "—" if v is None else "$ " + texto_colombiano(v, 2)


def kwh(v) -> str:
    return "—" if v is None else texto_colombiano(v, 2)


def mes_largo(periodo: str) -> str:
    """«2026-12» → «diciembre de 2026»."""
    return f"{MESES[int(periodo[5:7]) - 1]} de {periodo[:4]}"


def autoridades_maestro(con) -> dict[str, dict]:
    return {r["sigla"]: dict(r) for r in con.execute("SELECT * FROM autoridades ORDER BY tipo, sigla")}
