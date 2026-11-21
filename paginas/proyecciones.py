"""Pantalla de proyecciones (Jorge, 2026-11-14): ingreso estimado de un año
futuro por autoridad ambiental, mensual y anual.

Es una ESTIMACIÓN: no liquida, no guarda nada en la base y la TVB estimada no
entra a la serie real. Método en `motor/proyeccion.py` y `datos/proyeccion.py`.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import streamlit as st

from paginas.comun import autoridades_maestro, conexion, kwh, pesos, repo
from datos import proyeccion as py
from motor.delimitacion import SIN_ASIGNAR, NumeroInvalido, numero_desde_texto, texto_colombiano
from motor.proyeccion import ESCENARIOS, VENTANA

NOMBRE_ESC = {"bajo": "Bajo", "medio": "Medio", "alto": "Alto"}

con = conexion()
st.title("Proyecciones")
st.warning(
    "Estima el ingreso de un año futuro a partir de la generación histórica de XM y de una TVB aproximada. "
    "No es una liquidación ni un valor a transferir, y no se guarda en la base.",
    title="Esto es una estimación", icon=":material/trending_up:",
)

autoridades = autoridades_maestro(con)
serie = py.serie_tvb(con)
anios_xm = sorted({int(p[:4]) for p in repo.periodos_con_generacion(con)})
if not anios_xm:
    st.info("Aún no hay generación de XM cargada. La proyección se calcula con el histórico de «Generación XM»: "
            "cargue allí los archivos de los años anteriores (idealmente, los últimos 5).",
            icon=":material/electric_bolt:")
    st.stop()

# ---------------------------------------------------------------------------
# Supuestos
# ---------------------------------------------------------------------------

with st.container(border=True):
    st.markdown("**Supuestos**")
    hoy = date.today().year
    a, b = st.columns(2)
    anio = a.selectbox("Año a proyectar", [hoy + 1, hoy + 2, hoy + 3], key="proy_anio")
    real = serie.get(anio)
    sugerencia = None if real is not None else py.tvb_sugerida(con, anio)

    if real is not None:
        tvb, tvb_es_real = real, True
        b.text_input("TVB del año ($/kWh)", value=texto_colombiano(real, 5), disabled=True, key=f"proy_tvb_real_{anio}",
                     help="Ya está publicada por la CREG y registrada en Parámetros: se usa la real.")
    else:
        texto = b.text_input(
            "TVB estimada del año ($/kWh)", key=f"proy_tvb_{anio}",
            value=texto_colombiano(sugerencia.valor, 5) if sugerencia else "",
            placeholder="p. ej. 205,00000",
            help="La aplicación la sugiere; puede cambiarla (por ejemplo, con el IPP esperado). No se guarda como TVB real.",
        )
        tvb_es_real = False
        try:
            tvb = numero_desde_texto(texto)
        except NumeroInvalido as e:
            tvb = None
            st.error(str(e), icon=":material/error:")

    if sugerencia is not None:
        cambios = " · ".join(f"{a_}: {py.porcentaje(v)}" for a_, v in sugerencia.variaciones)
        st.caption(
            f"TVB sugerida: **{texto_colombiano(sugerencia.valor, 5)} $/kWh** = TVB de {sugerencia.base_anio} "
            f"({texto_colombiano(sugerencia.base_valor, 5)}) × (1 + {py.porcentaje(sugerencia.variacion_promedio)})"
            + (f" elevado a {sugerencia.anios_de_distancia}" if sugerencia.anios_de_distancia > 1 else "")
            + f". La variación es el promedio de los últimos {len(sugerencia.variaciones)} cambios anuales ({cambios})."
        )
        if tvb is not None and tvb != sugerencia.valor:
            st.caption(f"Está usando {texto_colombiano(tvb, 5)} $/kWh, digitada por usted, en lugar de la sugerida.")
    elif real is None:
        st.caption("No hay serie de TVB suficiente en Parámetros para sugerir un valor: digítela.")

    st.caption(
        f"Generación: para cada central y cada mes, el promedio del mismo mes en los últimos {VENTANA} años con dato "
        f"(escenario medio), el menor (bajo) y el mayor (alto). Años de XM cargados: "
        f"{', '.join(map(str, anios_xm))}. Reparto: solo delimitaciones vigentes."
    )
    faltan = [a_ for a_ in range(anio - VENTANA, anio) if a_ not in anios_xm and a_ <= hoy]
    if faltan:
        st.caption(f":orange[:material/warning: Faltan por cargar en «Generación XM» los años {', '.join(map(str, faltan))}: "
                   f"la proyección usa menos de {VENTANA} años donde falten.]")

if tvb is None or tvb <= 0:
    st.info("Escriba la TVB estimada del año para calcular la proyección.")
    st.stop()

p = py.proyectar_anio(con, anio, tvb, tvb_es_real)

# ---------------------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------------------

if not p.proyectadas:
    motivos = {}
    for c in p.sin_proyeccion:
        motivos[py.MOTIVOS[c.estado]] = motivos.get(py.MOTIVOS[c.estado], 0) + 1
    st.error("Ninguna central se puede proyectar: " + "; ".join(f"{n} — {m.lower()}" for m, n in motivos.items())
             + ". La proyección usa solo delimitaciones vigentes: confírmelas en «Delimitaciones».",
             title="Sin proyección", icon=":material/error:")
else:
    a, b, c, d = st.columns(4)
    a.metric(f"A transferir en {anio} (medio)", pesos(p.anual("medio")))
    b.metric("Escenario bajo", pesos(p.anual("bajo")))
    c.metric("Escenario alto", pesos(p.anual("alto")))
    d.metric("Centrales proyectadas", f"{len(p.proyectadas)} de {len(p.centrales)}")
    st.caption("El rango anual suma los extremos de cada mes (todos los meses secos, o todos lluviosos, a la vez): "
               "es un rango amplio para dimensionar, no una probabilidad. "
               + (f"Además, quedarían retenidos sin asignar {pesos(p.anual('medio', 'retenido'))} (medio)."
                  if p.anual("medio", "retenido") > 0 else ""))

vista = st.segmented_control(
    "Ver", ["Anual por autoridad", "Mensual por autoridad", "Detalle de una autoridad", "Por central", "Generación base", "Sin proyección"],
    default="Anual por autoridad", required=True, key="proy_vista",
)
por = p.por_autoridad()


def _nombre(sigla: str) -> str:
    if sigla == SIN_ASIGNAR:
        return "Retenido sin asignar (autoridad urbana o área en litigio)"
    return (autoridades.get(sigla) or {}).get("nombre") or sigla


if vista == "Anual por autoridad" and p.proyectadas:
    # Solo para dibujar: la gráfica necesita números de coma flotante; los cálculos son exactos.
    st.line_chart(
        pd.DataFrame({NOMBRE_ESC[e]: [float(v) / 1e6 for v in p.mensual(e)] for e in ESCENARIOS},
                     index=pd.Index([f"{m:02d} {py.MESES[m - 1]}" for m in range(1, 13)], name="Mes")),
        y_label="A transferir (millones de $)", height=280,
        alt=f"Total a transferir por mes en {anio}, escenarios bajo, medio y alto",
    )
    st.dataframe(pd.DataFrame([{
        "Sigla": s, "Autoridad ambiental": _nombre(s),
        **{NOMBRE_ESC[e]: pesos(sum(v[e], Decimal(0))) for e in ESCENARIOS},
    } for s, v in por.items()]), hide_index=True, width="stretch", height="content",
        alt=f"Ingreso anual estimado por autoridad en {anio}")

elif vista == "Mensual por autoridad" and p.proyectadas:
    esc = st.segmented_control("Escenario", list(ESCENARIOS), default="medio", required=True,
                               format_func=NOMBRE_ESC.get, key="proy_esc")
    filas = [{"Sigla": s, **{py.MESES[i].capitalize(): pesos(m) for i, m in enumerate(v[esc])},
              "Total año": pesos(sum(v[esc], Decimal(0)))} for s, v in por.items()]
    filas.append({"Sigla": "Total a transferir", **{py.MESES[i].capitalize(): pesos(m) for i, m in enumerate(p.mensual(esc))},
                  "Total año": pesos(p.anual(esc))})
    st.dataframe(pd.DataFrame(filas), hide_index=True, width="stretch", height="content",
                 alt=f"Ingreso mensual estimado por autoridad en {anio}, escenario {esc}")
    st.caption("«Total a transferir» no incluye lo retenido sin asignar.")

elif vista == "Detalle de una autoridad" and p.proyectadas:
    detalle = p.por_autoridad_y_central()
    sigla = st.selectbox("Autoridad ambiental", list(detalle), key="proy_autoridad",
                         format_func=lambda s: _nombre(s) if s == SIN_ASIGNAR else f"{s} — {_nombre(s)}")
    aportes = detalle[sigla]
    a, b, c = st.columns(3)
    for col, e in zip((a, b, c), ("medio", "bajo", "alto")):
        col.metric(f"{'Retenido' if sigla == SIN_ASIGNAR else 'Recibiría'} en {anio} ({NOMBRE_ESC[e].lower()})",
                   pesos(sum(por[sigla][e], Decimal(0))))
    st.markdown(f"**De qué proyectos sale** ({len(aportes)})")
    st.dataframe(pd.DataFrame([{
        "Proyecto": x.central.nombre, "Central XM": x.central.codigo, "Participación en el 3 %": x.participacion,
        "Meses": f"{len(x.meses)} de 12", **{NOMBRE_ESC[e]: pesos(x.anual(e)) for e in ESCENARIOS},
    } for x in aportes]), hide_index=True, width="stretch", height="content",
        alt=f"Ingreso anual estimado de {sigla} en {anio}, por proyecto")
    esc = st.segmented_control("Escenario del detalle mensual", list(ESCENARIOS), default="medio", required=True,
                               format_func=NOMBRE_ESC.get, key="proy_esc_autoridad")
    filas = [{"Proyecto": x.central.nombre, **{py.MESES[i].capitalize(): pesos(m) for i, m in enumerate(x.montos[esc])},
              "Total año": pesos(x.anual(esc))} for x in aportes]
    if len(aportes) > 1:
        filas.append({"Proyecto": "Total", **{py.MESES[i].capitalize(): pesos(m) for i, m in enumerate(por[sigla][esc])},
                      "Total año": pesos(sum(por[sigla][esc], Decimal(0)))})
    st.dataframe(pd.DataFrame(filas), hide_index=True, width="stretch", height="content",
                 alt=f"Ingreso mensual estimado de {sigla} en {anio} por proyecto, escenario {esc}")
    st.caption("La participación es la de la delimitación vigente; si cambia durante el año, aparecen las dos.")

elif vista == "Por central" and p.proyectadas:
    st.dataframe(pd.DataFrame([{
        "Proyecto": c.nombre, "Central XM": c.codigo, "Meses proyectados": f"{c.meses_proyectados} de 12",
        "Generación media (kWh)": kwh(c.generacion_anual("medio")),
        "Bolsa 3 % baja": pesos(c.anual("bajo")), "Bolsa 3 % media": pesos(c.anual("medio")),
        "Bolsa 3 % alta": pesos(c.anual("alto")),
    } for c in p.proyectadas]), hide_index=True, width="stretch", height=520,
        alt=f"Generación y bolsa anual estimadas por central en {anio}")

elif vista == "Generación base" and p.proyectadas:
    nombres = {c.codigo: c for c in p.proyectadas}
    codigo = st.selectbox("Central", list(nombres), format_func=lambda k: f"{nombres[k].nombre} ({k})", key="proy_central")
    c = nombres[codigo]
    st.dataframe(pd.DataFrame([{
        "Mes": f"{py.MESES[m.mes - 1].capitalize()} {anio}",
        "Años usados": ", ".join(map(str, m.generacion.anios)) or "—",
        "Baja (kWh)": kwh(m.generacion.bajo), "Media (kWh)": kwh(m.generacion.medio), "Alta (kWh)": kwh(m.generacion.alto),
        "Bolsa 3 % media": pesos(m.calculo["medio"].bolsa_car_pnn) if m.proyectado else "—",
        "Delimitación": m.radicados or m.nota,
    } for m in c.meses]), hide_index=True, width="stretch", height="content",
        alt=f"Generación mensual estimada de la central {codigo} en {anio}")
    st.caption(f"Umbral: {c.sujecion.agente}, {texto_colombiano(c.sujecion.capacidad_total_kw, 0)} kW "
               f"({c.sujecion.anio_capacidad}). Un mes sin dato en un año no cuenta como cero: no entra al promedio.")

elif vista == "Sin proyección" or not p.proyectadas:
    parciales = [c for c in p.proyectadas if c.meses_proyectados < 12]
    if not p.sin_proyeccion and not parciales:
        st.success("Todas las centrales tienen proyección en los 12 meses.", icon=":material/check_circle:")
    filas = [{"Proyecto": c.nombre, "Central XM": c.codigo or "—", "Motivo": py.MOTIVOS[c.estado], "Detalle": c.detalle}
             for c in p.sin_proyeccion]
    filas += [{"Proyecto": c.nombre, "Central XM": c.codigo, "Motivo": f"Solo {c.meses_proyectados} de 12 meses",
               "Detalle": "; ".join(sorted({m.nota for m in c.meses if m.nota}))} for c in parciales]
    if filas:
        st.dataframe(pd.DataFrame(filas), hide_index=True, width="stretch", height="content",
                     alt="Proyectos sin proyección o con proyección parcial, y por qué")

if p.proyectadas:
    st.download_button(
        "Descargar la proyección en XLSX", data=lambda: py.xlsx_proyeccion(p, autoridades, sugerencia),
        file_name=f"Proyeccion_TSE_{anio}_ESTIMACION.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        icon=":material/table_view:", on_click="ignore", key=f"proy_xlsx_{anio}",
    )
