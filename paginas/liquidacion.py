"""Pantalla 3 — Liquidación del mes.

Elegir el periodo; tabla central × autoridad con monto; detalle paso a paso;
lista aparte de lo que no se liquida y por qué (CLAUDE.md, «Pantallas», 3).

El cálculo se hace en memoria cada vez que se abre la pantalla y no escribe
nada. «Guardar» crea una versión nueva de cada central cuyo cálculo cambió;
nunca pisa la anterior (regla 7).
"""
from __future__ import annotations

import sqlite3
from decimal import Decimal

import pandas as pd
import streamlit as st

from paginas.comun import autoridades_maestro, conexion, kwh, mes_largo, pesos, repo
from datos import liquidar_mes as lm
from motor.delimitacion import SIN_ASIGNAR, texto_colombiano
from motor.liquidacion import CITA_BOLSA, CITA_UMBRAL, CITA_VENTAS

con = conexion()
st.title("Liquidación del mes")
st.caption(
    "ventas brutas = generación de XM × TVB de la CREG · bolsa = 3 % de las ventas brutas · "
    "monto = bolsa × participación de la autoridad según la delimitación del IGAC. "
    "Sin delimitación vigente, sin mes completo de XM o sin TVB, no se liquida. Si la delimitación empieza a regir "
    "dentro del mes, se liquida solo desde ese día."
)

periodos = repo.periodos_con_generacion(con)
if not periodos:
    st.info("Aún no hay generación de XM cargada. Cargue el archivo en la pantalla «Generación XM».",
            icon=":material/electric_bolt:")
    st.stop()

periodo = st.selectbox("Periodo", periodos, format_func=lambda p: f"{mes_largo(p).capitalize()} ({p})",
                       key="liq_periodo")
mes = lm.calcular_mes(con, periodo)
autoridades = autoridades_maestro(con)


def _autoridad(sigla: str) -> str:
    if sigla == SIN_ASIGNAR:
        return "Retenido sin asignar (autoridad urbana o área en litigio)"
    return (autoridades.get(sigla) or {}).get("nombre") or sigla


def _pct(v: Decimal) -> str:
    return texto_colombiano(v, 5) + " %"


# ---------------------------------------------------------------------------
# Resumen
# ---------------------------------------------------------------------------

if mes.tvb is None:
    st.error(f"No hay tarifa de venta en bloque (TVB) de {periodo[:4]}. Regístrela en la pantalla «Parámetros»; "
             "no se usa la de otro año.", title="Falta la TVB del año", icon=":material/error:")

a, b, c, d = st.columns(4)
a.metric("Centrales liquidadas", f"{len(mes.liquidadas)} de {len(mes.centrales)}",
         help="Se liquida una vez por central de XM. GUATRON y PAJARITO agrupan varios proyectos del IGAC.")
b.metric("Bolsa del 3 %", pesos(mes.total("bolsa_car_pnn")) if mes.liquidadas else "—")
c.metric("A transferir", pesos(mes.total("a_transferir")) if mes.liquidadas else "—")
d.metric("Retenido sin asignar", pesos(mes.total("retenido")) if mes.liquidadas else "—",
         help="Parte de «autoridad ambiental urbana o área en litigio»: se calcula y no se transfiere.")

parciales = [x for x in mes.liquidadas if x.parcial]
if parciales:
    tramos = sorted({x.dias for x in parciales})
    st.info(f"{len(parciales)} de {len(mes.liquidadas)} centrales se liquidan solo por los días {', '.join(tramos)}: "
            "su delimitación empieza a regir dentro del mes. Se usa la generación de XM de esos días.",
            title="Mes parcial", icon=":material/date_range:")

sin_gen = [x for x in mes.liquidadas if x.sin_generacion]
if sin_gen:
    st.warning(f"{', '.join(x.nombre for x in sin_gen)}: XM no reporta generación en los días liquidados. "
               "Su transferencia es $ 0,00 por esa razón y figura como «Sin generación».",
               title=lm.SIN_GENERACION, icon=":material/power_off:")

if mes.caracter == "oficial":
    st.success("Todas las centrales liquidadas usan la versión firme de XM (TXF).",
               title="Liquidación oficial", icon=":material/verified:")
elif mes.caracter == "preliminar":
    n = sum(c.caracter == "preliminar" for c in mes.liquidadas)
    st.warning(f"{n} de {len(mes.liquidadas)} centrales se liquidaron con una versión de XM que no es la firme "
               "(TXF). Sirve de referencia; no es la liquidación oficial.",
               title="Liquidación preliminar", icon=":material/hourglass_top:")

vista = st.segmented_control(
    "Ver", ["Por central y autoridad", "Por autoridad", "Paso a paso", "No liquidados"],
    default="Por central y autoridad", required=True, key="liq_vista",
)

# ---------------------------------------------------------------------------
# Vistas
# ---------------------------------------------------------------------------

if vista == "Por central y autoridad":
    if not mes.liquidadas:
        st.info("Ninguna central se puede liquidar en este mes. Vea el motivo en «No liquidados».")
    else:
        filas = []
        for central in mes.liquidadas:
            k = central.calculo
            for f in k.filas:
                if f.razon == 0:
                    continue
                filas.append({
                    "Proyecto": central.nombre, "Central XM": central.codigo, "Días": central.dias,
                    "Carácter": "Oficial" if central.caracter == "oficial" else f"Preliminar ({central.generacion['version_xm']})",
                    "Generación (kWh)": (lm.SIN_GENERACION if central.sin_generacion
                                         else kwh(k.generacion_kwh) + (" *" if central.dias_ausentes else "")),
                    "Bolsa del 3 %": pesos(k.bolsa_car_pnn),
                    "Autoridad": f.autoridad,
                    "Participación": _pct(f.porcentaje),
                    "Monto": pesos(f.monto),
                })
        st.dataframe(pd.DataFrame(filas), hide_index=True, width="stretch", height=520,
                     alt="Monto por central y autoridad ambiental")
        st.caption("Un asterisco (*) marca una central con días ausentes en el archivo de XM, tomados como sin "
                   "generación. Las cifras se muestran en formato colombiano; el XLSX de «Exportar» las trae como números.")

elif vista == "Por autoridad":
    por = mes.por_autoridad()
    if not por:
        st.info("Ninguna central se puede liquidar en este mes.")
    else:
        filas = [{
            "Sigla": s, "Autoridad ambiental": _autoridad(s),
            "Centrales": sum(any(f.autoridad == s and f.razon > 0 for f in x.calculo.filas) for x in mes.liquidadas),
            "Monto": pesos(m),
        } for s, m in sorted(por.items(), key=lambda kv: (kv[0] == SIN_ASIGNAR, kv[0].casefold()))]
        st.dataframe(pd.DataFrame(filas), hide_index=True, width="stretch", height="content",
                     alt="Monto total por autoridad ambiental")

elif vista == "Paso a paso":
    if not mes.liquidadas:
        st.info("Ninguna central se puede liquidar en este mes.")
    else:
        nombres = {x.codigo: x for x in mes.liquidadas}
        codigo = st.selectbox("Central", list(nombres), key="liq_central",
                              format_func=lambda c: f"{nombres[c].nombre} ({c})")
        x = nombres[codigo]
        k, g, s = x.calculo, x.generacion, x.sujecion
        for alerta in x.alertas:
            st.warning(alerta, icon=":material/info:")
        dias = "total mensual" if g["dias_archivo"] is None else f"{g['dias_reportados']} de {g['dias_archivo']} días"
        st.table([
            {"Paso": f"1. Generación del {x.dia_desde} al {x.dia_hasta} del mes" if x.parcial else "1. Generación del mes",
             "Valor": f"{kwh(k.generacion_kwh)} kWh" + (f" — {lm.SIN_GENERACION.lower()}" if x.sin_generacion else ""),
             "De dónde sale": f"XM, versión {g['version_xm']} ({dias}). Archivo «{g['archivo_nombre']}», "
                              f"SHA-256 {g['archivo_sha256'][:16]}…"},
            {"Paso": "2. Tarifa de venta en bloque", "Valor": f"{texto_colombiano(k.tvb_pesos_kwh, 5)} $/kWh",
             "De dónde sale": f"CREG, año {mes.tvb['anio']}. Fuente: {mes.tvb['fuente']}."},
            {"Paso": "3. Ventas brutas = 1 × 2", "Valor": pesos(k.ventas_brutas), "De dónde sale": CITA_VENTAS},
            {"Paso": "4. Bolsa = 3 % × ventas brutas", "Valor": pesos(k.bolsa_car_pnn), "De dónde sale": CITA_BOLSA},
            {"Paso": "5. Umbral del art. 45", "Valor": f"{texto_colombiano(s.capacidad_total_kw, 0)} kW",
             "De dónde sale": f"{s.agente}, capacidad de {s.anio_capacidad}: empresa sujeta. {CITA_UMBRAL}"},
            {"Paso": "6. Participación de cada autoridad", "Valor": "ver la tabla",
             "De dónde sale": f"Delimitación {x.radicados}. {k.texto_formula()}"},
        ], border="horizontal")

        s1_t = sum((f.s1_ha or 0 for f in k.filas), Decimal(0))
        s2_t = sum((f.s2_ha or 0 for f in k.filas), Decimal(0))
        filas = []
        for f in k.filas:
            fila = {"Autoridad": f.autoridad, "Nombre": _autoridad(f.autoridad)}
            if k.formula:
                fila["S1ᵢ (ha)"] = texto_colombiano(f.s1_ha, 5)
                if k.formula == 2:
                    fila["S2ᵢ (ha)"] = texto_colombiano(f.s2_ha, 5)
            fila["Participación en el 3 %"] = _pct(f.porcentaje)
            fila["Monto = bolsa × participación"] = pesos(f.monto) + (" (+ residuo)" if f.ajuste_residuo else "")
            filas.append(fila)
        st.dataframe(pd.DataFrame(filas), hide_index=True, width="stretch", height="content",
                     alt="Participación y monto de cada autoridad en la central")
        notas = []
        if k.formula:
            notas.append(f"S1_T = {texto_colombiano(s1_t, 5)} ha" + (f" · S2_T = {texto_colombiano(s2_t, 5)} ha" if k.formula == 2 else ""))
        notas.append("La participación se calcula como fracción exacta; los 5 decimales son solo de presentación.")
        if any(f.ajuste_residuo for f in k.filas):
            notas.append("«(+ residuo)»: recibió el residuo de centavos para que la suma sea exactamente la bolsa.")
        st.caption(" · ".join(notas))
        st.caption(f"Comprobación: Σ montos = {pesos(sum((f.monto for f in k.filas), Decimal(0)))} = bolsa "
                   f"{pesos(k.bolsa_car_pnn)}. De ahí, a transferir {pesos(k.a_transferir)} y retenido {pesos(k.retenido)}.")

elif vista == "No liquidados":
    if not mes.no_liquidadas:
        st.success("Todas las centrales se liquidaron en este mes.", icon=":material/check_circle:")
    else:
        st.caption("Quedan listados y sin monto. La aplicación no estima ni reparte por partes iguales.")
        st.dataframe(pd.DataFrame([{
            "Proyecto": x.nombre, "Central XM": x.codigo or "—", "Motivo": x.motivo, "Detalle": x.detalle,
        } for x in mes.no_liquidadas]), hide_index=True, width="stretch", height="content",
            alt="Proyectos que no se liquidan en el mes y por qué")

# ---------------------------------------------------------------------------
# Guardar
# ---------------------------------------------------------------------------

st.divider()
st.subheader("Guardar la liquidación")
pendientes = lm.pendientes_de_guardar(con, mes)
guardadas = lm.liquidaciones_guardadas(con, periodo)
huerfanas = lm.guardadas_que_ya_no_liquidan(con, mes)

if guardadas:
    ultima = max(g["creada_en"] for g in guardadas)
    st.caption(f"Guardadas: {len(guardadas)} centrales · última vez el {ultima[:10]} a las {ultima[11:16]} · "
               f"versión más alta: {max(g['version_numero'] for g in guardadas)}.")
if huerfanas:
    st.warning(
        "Tienen una liquidación guardada, pero con los datos de hoy ya no se liquidan: "
        + ", ".join(sorted(h["proyecto"] for h in huerfanas))
        + ". La versión guardada se conserva; revise el motivo en «No liquidados».",
        title="Liquidaciones guardadas que ya no aplican", icon=":material/warning:")

if not mes.liquidadas:
    st.info("No hay nada que guardar en este mes.")
elif not pendientes:
    st.success("Lo guardado coincide con el cálculo actual. Puede exportarlo en la pantalla «Exportar».",
               icon=":material/check_circle:")
else:
    nuevas = [c for c in pendientes if not any(g["proyecto_id"] == c.proyecto_id for g in guardadas)]
    cambiadas = [c for c in pendientes if c not in nuevas]
    texto = []
    if nuevas:
        texto.append(f"{len(nuevas)} centrales sin guardar")
    if cambiadas:
        texto.append(f"{len(cambiadas)} con un cálculo distinto del guardado ({', '.join(c.nombre for c in cambiadas[:5])}"
                     + ("…" if len(cambiadas) > 5 else "") + ")")
    st.info(" y ".join(texto) + ". Guardar crea una versión nueva de cada una; las anteriores se conservan.",
            icon=":material/save:")
    etiqueta = "preliminar" if mes.caracter == "preliminar" else "oficial"
    if st.button(f"Guardar liquidación {etiqueta} de {mes_largo(periodo)}", type="primary",
                 icon=":material/save:", key="liq_guardar"):
        try:
            r = lm.guardar_mes(con, mes)   # una sola transacción: o todas o ninguna
        except repo.ErrorRepositorio as e:
            st.error(str(e), title="No se pudo guardar", icon=":material/error:")
        except sqlite3.Error as e:         # p. ej., base bloqueada por la sincronización de OneDrive
            st.error(f"No se pudo escribir en la base y no se guardó nada. Vuelva a intentarlo; si se repite, "
                     f"revise que la base no esté abierta en otro programa. Detalle técnico: {e}",
                     title="No se pudo guardar", icon=":material/error:")
        else:
            st.toast(f"{len(r['guardadas'])} liquidaciones guardadas.")
            st.rerun()
