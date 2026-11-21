"""
Liquidación del mes, exportables y ajustes del 2026-11-14 (pasos 5 y 6).

Todas las cifras de generación, capacidad y TVB de este archivo son
SINTÉTICAS, escogidas para que las cuentas se puedan seguir a mano.
"""
from __future__ import annotations

import io
import sqlite3
from datetime import date
from decimal import Decimal
from fractions import Fraction

import openpyxl
import pytest

from datos import exportar as ex
from datos import liquidar_mes as lm
from datos import repositorio as repo
from datos.cargue_xm import guardar_lectura
from datos.parse_xm_generacion import leer_xm
from motor import delimitacion as dm
from motor.delimitacion import SIN_ASIGNAR, Delimitacion, FilaDelimitacion as F_
from motor.liquidacion import liquidar_central
from motor.tse_calculator import TseCalculatorError
from tests.test_cargue_xm import diario, mensual, mes_completo

D = Decimal
PERIODO = "2026-12"


# --- motor, en memoria ------------------------------------------------------------

def _delim(*filas, **kw):
    return Delimitacion(modo="hectareas", filas=tuple(filas), **kw)


def test_la_cadena_se_puede_seguir_a_mano():
    # 1.000.000 kWh × 200 $/kWh = 200.000.000; 3 % = 6.000.000; 3/4 y 1/4.
    k = liquidar_central(D("1000000"), D("200"), _delim(F_("CVC", D("75")), F_("CRC", D("25"))))
    assert (k.ventas_brutas, k.bolsa_car_pnn, k.tse_total) == (D("200000000"), D("6000000.00"), D("12000000.00"))
    assert {f.autoridad: f.monto for f in k.filas} == {"CVC": D("4500000.00"), "CRC": D("1500000.00")}
    assert k.formula == 1 and k.retenido == 0 and k.a_transferir == k.bolsa_car_pnn


def test_los_montos_suman_la_bolsa_y_el_residuo_no_va_a_lo_retenido():
    d = _delim(F_("CVC", D("1")), F_("CRC", D("1")), F_(SIN_ASIGNAR, D("1")))
    k = liquidar_central(D("1000"), D("0.01"), d)       # bolsa = 0,30 → 0,10 c/u; con 0,31 sobraría 1 centavo
    assert sum(f.monto for f in k.filas) == k.bolsa_car_pnn
    k = liquidar_central(D("1000"), D("0.0105"), d)     # bolsa exacta 0,315 → 0,32; tercios 0,105 → 0,11
    assert sum(f.monto for f in k.filas) == k.bolsa_car_pnn == D("0.32")
    assert [f.autoridad for f in k.filas if f.ajuste_residuo] == ["CRC"]   # nunca SIN_ASIGNAR
    assert k.retenido == D("0.11") and k.a_transferir == D("0.21")


def test_el_motor_rechaza_float_y_negativos():
    d = _delim(F_("CVC", D("1")))
    with pytest.raises(TseCalculatorError):
        liquidar_central(1000.5, D("200"), d)
    with pytest.raises(TseCalculatorError):
        liquidar_central(D("-1"), D("200"), d)


def test_sin_paramo_con_s2_repetido_se_explica_como_formula_1():
    d = _delim(F_("CVC", D("3"), D("3")), F_("CRC", D("1"), D("1")), trae_s2=True, paramo_declarado=False)
    k = liquidar_central(D("1000000"), D("200"), d)
    assert k.formula == 2 and "resultado es el de la Fórmula 1" in k.texto_formula()
    assert [f.razon for f in k.filas] == [Fraction(3, 4), Fraction(1, 4)]


def test_vigencia_anterior_al_documento_se_rechaza():
    d = _delim(F_("CVC", D("1")), s1_t_declarado=D("1"))
    r = dm.validar(d, {"CVC"}, True, vigente_desde="2026-10-01", fecha_documento="2026-11-11")
    assert any("hacia adelante" in e for e in r.errores)
    assert dm.validar(d, {"CVC"}, True, vigente_desde="2026-12-01", fecha_documento="2026-11-11").ok


# --- escenario con base en memoria -------------------------------------------------

def _delimitar(con, nombre, codigo, filas, vigente="2026-12-01", radicado=None, doc="2026-11-11"):
    fila = con.execute("SELECT id FROM proyectos WHERE nombre = ?", (nombre,)).fetchone()
    p = fila["id"] if fila else repo.crear_proyecto(con, nombre, codigo_submercado=codigo)
    s1 = sum((f["s1_ha"] for f in filas), D(0))
    d = repo.crear_delimitacion(con, p, radicado or f"RAD-{nombre}", doc, vigente, "hectareas", filas,
                                pdf_sha256="e" * 64, s1_t_declarado=s1, trae_s2=False, paramo_declarado=False)
    r = repo.confirmar_delimitacion(con, d)
    assert r.ok, r.errores
    return p, d


def _capacidad_2025(con):
    guardar_lectura(con, leer_xm(mensual(2025, [
        ("EMPRESA GRANDE", "GRA1", "CENTRAL GRANDE", "HIDRAULICA", 100, 400000),
        ("EMPRESA GRANDE", "PCH1", "PCH DE LA GRANDE", "HIDRAULICA", 100, 900),
        ("EMPRESA CHICA", "CHI1", "PCH CHICA", "HIDRAULICA", 100, 9000),
    ]), "G_2025.xlsx"), version_declarada="TXF")


def _generacion_dic(con, version="TXF", nombre="x_2026.xlsx", por_hora=1):
    filas = (mes_completo("GRA1", 2026, 12, por_hora, version) + mes_completo("PCH1", 2026, 12, por_hora, version)
             + mes_completo("CHI1", 2026, 12, por_hora, version))
    return guardar_lectura(con, leer_xm(diario(filas), nombre))


@pytest.fixture
def escenario(con):
    _capacidad_2025(con)
    _delimitar(con, "Central Grande", "GRA1", [{"autoridad": "CVC", "s1_ha": D("75")}, {"autoridad": "CRC", "s1_ha": D("25")}])
    _delimitar(con, "PCH de la Grande", "PCH1", [{"autoridad": "CVC", "s1_ha": D("10")}])
    _delimitar(con, "PCH Chica", "CHI1", [{"autoridad": "CVC", "s1_ha": D("10")}])
    repo.guardar_tvb(con, 2026, D("200"), "sintética")
    return con


def _central(mes, codigo):
    return next(c for c in mes.centrales if c.codigo == codigo)


def test_el_mes_se_liquida_y_el_umbral_es_por_empresa(escenario):
    _generacion_dic(escenario)
    mes = lm.calcular_mes(escenario, PERIODO)
    gra, pch, chi = (_central(mes, c) for c in ("GRA1", "PCH1", "CHI1"))
    # 744 kWh × 200 = 148.800; 3 % = 4.464
    assert gra.liquidada and gra.caracter == "oficial" and gra.calculo.bolsa_car_pnn == D("4464.00")
    assert {f.autoridad: f.monto for f in gra.calculo.filas} == {"CVC": D("3348.00"), "CRC": D("1116.00")}
    assert pch.liquidada, "una central de 900 kW de una empresa de más de 10.000 kW sí tributa"
    assert not gra.dias_ausentes, "XM trae todos los días: sin *"
    assert chi.estado == "bajo_umbral" and chi.calculo is None
    assert mes.caracter == "oficial" and mes.por_autoridad() == {"CRC": D("1116.00"), "CVC": D("7812.00")}


def test_con_version_no_firme_es_preliminar(escenario):
    _generacion_dic(escenario, version="TX1")
    mes = lm.calcular_mes(escenario, PERIODO)
    assert _central(mes, "GRA1").caracter == "preliminar" and mes.caracter == "preliminar"


def test_lo_que_falta_deja_la_central_sin_monto(con):
    _capacidad_2025(con)
    _generacion_dic(con)
    repo.crear_proyecto(con, "Sin enlazar")
    repo.crear_proyecto(con, "Sin delimitar", codigo_submercado="PCH1")
    _delimitar(con, "Central Grande", "GRA1", [{"autoridad": "CVC", "s1_ha": D("1")}])
    _delimitar(con, "Sin capacidad", "NUEVA", [{"autoridad": "CVC", "s1_ha": D("1")}])
    mes = lm.calcular_mes(con, PERIODO)
    estados = {c.nombre: c.estado for c in mes.centrales}
    assert estados == {"Sin enlazar": "sin_central", "Sin delimitar": "sin_delimitacion",
                       "Central Grande": "sin_tvb", "Sin capacidad": "sin_generacion"}
    assert all(c.calculo is None for c in mes.centrales) and not mes.liquidadas

    repo.guardar_tvb(con, 2026, D("200"), "sintética")
    guardar_lectura(con, leer_xm(diario(mes_completo("NUEVA", 2026, 12, 1) + mes_completo("GRA1", 2026, 12, 1)),
                                 "y_2026.xlsx"))
    estados = {c.nombre: c.estado for c in lm.calcular_mes(con, PERIODO).centrales}
    assert estados["Central Grande"] == lm.LIQUIDADA and estados["Sin capacidad"] == "sujecion_sin_determinar"


def test_mes_incompleto_no_se_liquida_ni_siquiera_parcial(escenario):
    con = escenario
    _generacion_dic(con)
    _delimitar(con, "Central Grande", "GRA1", [{"autoridad": "CVC", "s1_ha": D("1")}], vigente="2027-01-15",
               radicado="RAD-2", doc="2027-01-10")
    filas = [(date(2027, 1, d), "GRA1", "HIDRAULICA", "SING", 1, "TX1") for d in range(1, 12)]
    guardar_lectura(con, leer_xm(diario(filas), "x_2027.xlsx"))
    repo.guardar_tvb(con, 2027, D("210"), "sintética")
    assert _central(lm.calcular_mes(con, "2027-01"), "GRA1").estado == "mes_incompleto"   # 11 de 31 días
    assert _central(lm.calcular_mes(con, "2027-01"), "PCH1").estado == "sin_generacion"
    filas = [(date(2027, 1, d), "PCH1", "HIDRAULICA", "SING", 1, "TX1") for d in range(1, 12)]
    guardar_lectura(con, leer_xm(diario(filas), "z_2027.xlsx"))
    assert _central(lm.calcular_mes(con, "2027-01"), "PCH1").estado == "mes_incompleto"


def test_central_compartida_se_liquida_una_vez_y_cita_todas_las_delimitaciones(escenario):
    con = escenario
    _generacion_dic(con)
    _delimitar(con, "Central Grande II", "GRA1", [{"autoridad": "CVC", "s1_ha": D("3")}, {"autoridad": "CRC", "s1_ha": D("1")}])
    mes = lm.calcular_mes(con, PERIODO)
    gra = _central(mes, "GRA1")
    assert gra.liquidada and gra.proyectos == ["Central Grande", "Central Grande II"]
    assert sum(c.codigo == "GRA1" for c in mes.centrales) == 1
    lm.guardar_mes(con, mes)
    guardada = next(l for l in lm.liquidaciones_guardadas(con, PERIODO) if l["codigo_submercado"] == "GRA1")
    assert [d["radicado"] for d in guardada["delimitaciones"]] == ["RAD-Central Grande", "RAD-Central Grande II"]
    assert con.execute("SELECT count(*) FROM liquidaciones l JOIN proyectos p ON p.id = l.proyecto_id "
                       "WHERE p.codigo_submercado = 'GRA1'").fetchone()[0] == 1


def test_central_compartida_no_se_liquida_si_a_un_proyecto_le_falta_delimitacion(escenario):
    con = escenario
    _generacion_dic(con)
    repo.crear_proyecto(con, "Central Grande II", codigo_submercado="GRA1")
    gra = _central(lm.calcular_mes(con, PERIODO), "GRA1")
    assert gra.estado == "sin_delimitacion" and "Central Grande II" in gra.detalle


def test_guardar_no_repite_versiones_y_recalcular_crea_una_nueva(escenario):
    con = escenario
    _generacion_dic(con)
    r = lm.guardar_mes(con, lm.calcular_mes(con, PERIODO))
    assert sorted(r["guardadas"]) == ["Central Grande", "PCH de la Grande"]
    r = lm.guardar_mes(con, lm.calcular_mes(con, PERIODO))
    assert r["guardadas"] == [] and len(r["sin_cambios"]) == 2
    assert con.execute("SELECT count(*) FROM liquidaciones").fetchone()[0] == 2

    repo.guardar_tvb(con, 2026, D("201"), "sintética, corregida")
    mes = lm.calcular_mes(con, PERIODO)
    assert len(lm.pendientes_de_guardar(con, mes)) == 2
    lm.guardar_mes(con, mes)
    versiones = [r[0] for r in con.execute("SELECT version_numero FROM liquidaciones ORDER BY id")]
    assert versiones == [1, 1, 2, 2]
    guardada = lm.liquidaciones_guardadas(con, PERIODO)[0]
    assert guardada["version_numero"] == 2 and guardada["tvb_pesos_kwh"] == "201"
    assert (guardada["empresa"], guardada["capacidad_anio"], guardada["dias_archivo"]) == ("EMPRESA GRANDE", 2025, 31)


def test_preliminar_y_luego_oficial_quedan_como_versiones_distintas(escenario):
    con = escenario
    _generacion_dic(con, version="TX2", nombre="a_2026.xlsx")
    lm.guardar_mes(con, lm.calcular_mes(con, PERIODO))
    _generacion_dic(con, version="TXF", nombre="b_2026.xlsx", por_hora=2)
    lm.guardar_mes(con, lm.calcular_mes(con, PERIODO))
    filas = con.execute("SELECT caracter, version_xm, version_numero FROM liquidaciones l JOIN proyectos p "
                        "ON p.id = l.proyecto_id WHERE p.codigo_submercado = 'GRA1' ORDER BY l.id").fetchall()
    assert [tuple(f) for f in filas] == [("preliminar", "TX2", 1), ("oficial", "TXF", 2)]
    assert ex.caracter_del_mes(lm.liquidaciones_guardadas(con, PERIODO)) == "oficial"


# --- ajustes del 2026-11-14 --------------------------------------------------------

def test_un_archivo_parcial_viejo_no_tapa_al_mes_completo(con):
    f = dict(periodo="2026-11", codigo_submercado="X1", recurso="X", agente="A", tipo_generacion="HIDRAULICA",
             dias_mes=30)
    repo.registrar_carga_xm(con, "a.xlsx", "a" * 64, [dict(f, version_xm="TX2", generacion_kwh=D(100),
                                                           dias_reportados=8, dias_archivo=11)], formato="diario")
    repo.registrar_carga_xm(con, "b.xlsx", "b" * 64, [dict(f, version_xm="TX1", generacion_kwh=D(900),
                                                           dias_reportados=25, dias_archivo=30)], formato="diario")
    g = repo.generacion_para(con, "X1", "2026-11", "preliminar")
    assert (g["generacion_kwh"], g["version_xm"]) == ("900", "TX1")


def test_la_version_es_del_mes_y_no_de_cada_recurso(con):
    """Un recurso que solo generó en los días ya firmes de un mes que todavía
    tiene días preliminares no queda rotulado como firme."""
    filas = [(date(2026, 11, d), "TODO", "HIDRAULICA", "SING", 1, "TXF" if d <= 20 else "TX3") for d in range(1, 31)]
    filas += [(date(2026, 11, d), "POCO", "HIDRAULICA", "SING", 1, "TXF") for d in range(1, 6)]
    guardar_lectura(con, leer_xm(diario(filas), "x_2026.xlsx"))
    versiones = dict(con.execute("SELECT codigo_submercado, version_xm FROM generacion").fetchall())
    assert versiones == {"TODO": "TX3", "POCO": "TX3"}


def test_una_correccion_con_la_misma_vigencia_sustituye_a_la_anterior(con):
    p, mala = _delimitar(con, "Planta", "SINT1", [{"autoridad": "CVC", "s1_ha": D("10")}], radicado="RAD-1")
    _, buena = _delimitar(con, "Planta", "SINT1", [{"autoridad": "CVC", "s1_ha": D("12")}], radicado="RAD-1")
    assert repo.delimitacion_aplicable(con, p, "2026-12")["id"] == buena
    estados = dict(con.execute("SELECT id, estado FROM delimitaciones").fetchall())
    assert estados == {mala: "reemplazada", buena: "vigente"}
    assert con.execute("SELECT reemplazada_por FROM delimitaciones WHERE id = ?", (mala,)).fetchone()[0] == buena


def test_mover_una_correccion_no_hace_regir_otra_vez_la_corregida(con):
    """Fallo corregido el 2026-11-15: al mover hacia adelante la vigencia de una
    corrección, la corregida (con las cifras malas) se quedaba en la fecha vieja
    y volvía a regir los días intermedios."""
    p, mala = _delimitar(con, "Planta", "SINT1", [{"autoridad": "CVC", "s1_ha": D("10")},
                                                  {"autoridad": "CRC", "s1_ha": D("30")}],
                         vigente="2026-11-25", radicado="RAD-1")
    _, buena = _delimitar(con, "Planta", "SINT1", [{"autoridad": "CVC", "s1_ha": D("30")},
                                                   {"autoridad": "CRC", "s1_ha": D("10")}],
                          vigente="2026-11-25", radicado="RAD-1")
    assert repo.delimitacion_del_tramo(con, p, "2026-11")[0]["id"] == buena

    assert repo.cambiar_vigencia(con, buena, "2026-12-01", "fecha mal digitada") == [mala]
    with pytest.raises(repo.SinDelimitacionVigente):
        repo.delimitacion_del_tramo(con, p, "2026-11")       # en noviembre ya no rige ninguna
    d, dia, _ = repo.delimitacion_del_tramo(con, p, "2026-12")
    assert (d["id"], dia) == (buena, 1)
    assert con.execute("SELECT vigente_desde FROM delimitaciones WHERE id = ?", (mala,)).fetchone()[0] == "2026-12-01"
    cambio = repo.cambios_de_delimitacion(con, mala)[0]       # el movimiento de la corregida queda registrado
    assert (cambio["antes"], cambio["despues"]) == ("2026-11-25", "2026-12-01")
    assert "RAD-1" in cambio["motivo"] and "fecha mal digitada" in cambio["motivo"]

    repo.cambiar_vigencia(con, buena, "2026-11-25")           # y se puede devolver: se mueven juntas
    assert repo.delimitacion_del_tramo(con, p, "2026-11")[0]["id"] == buena

    # Un reemplazo (fecha posterior, no corrección) sigue sin poder regir antes del que reemplazó.
    _, nueva = _delimitar(con, "Planta", "SINT1", [{"autoridad": "CVC", "s1_ha": D("1")}],
                          vigente="2027-03-01", radicado="RAD-2")
    with pytest.raises(repo.ErrorRepositorio, match="no puede regir antes"):
        repo.cambiar_vigencia(con, nueva, "2026-11-20")
    assert repo.cambiar_vigencia(con, nueva, "2027-04-01") == []   # no arrastra a la que reemplazó


def test_una_base_v4_se_migra_a_v5_sin_perder_liquidaciones(tmp_path):
    ruta = tmp_path / "v4.db"
    c = repo.abrir(ruta)
    with c:
        c.execute("DROP TABLE liquidacion_delimitaciones")
        for col, _ in repo.COLUMNAS_V5:
            c.execute(f"ALTER TABLE liquidaciones DROP COLUMN {col}")
        c.execute("PRAGMA user_version = 4")
    c.close()
    c = repo.abrir(ruta)
    assert c.execute("PRAGMA user_version").fetchone()[0] == repo.VERSION_ESQUEMA
    assert {n for n, _ in repo.COLUMNAS_V5} <= {r[1] for r in c.execute("PRAGMA table_info(liquidaciones)")}
    c.execute("SELECT * FROM liquidacion_delimitaciones").fetchall()
    c.close()
    repo.abrir(ruta).close()   # abrirla otra vez no cambia nada


# --- exportables -------------------------------------------------------------------

def _guardado(con):
    _generacion_dic(con)
    # corrige la de la PCH (misma vigencia) con una que trae área en litigio
    _delimitar(con, "PCH de la Grande", "PCH1", [{"autoridad": "CVC", "s1_ha": D("3")},
                                                 {"autoridad": SIN_ASIGNAR, "s1_ha": D("1")}], radicado="RAD-L")
    mes = lm.calcular_mes(con, PERIODO)
    lm.guardar_mes(con, mes)
    autoridades = {r["sigla"]: dict(r) for r in con.execute("SELECT * FROM autoridades")}
    return mes, lm.liquidaciones_guardadas(con, PERIODO), autoridades


def test_el_xlsx_trae_la_cadena_completa_y_cuadra(escenario):
    mes, guardadas, autoridades = _guardado(escenario)
    wb = openpyxl.load_workbook(io.BytesIO(ex.xlsx_mes(PERIODO, guardadas, mes.no_liquidadas, autoridades)))
    assert wb.sheetnames == ["Por autoridad", "Detalle", "Trazabilidad", "No liquidados", "Parámetros y normas"]
    detalle = list(wb["Detalle"].iter_rows(min_row=3, values_only=True))
    enc, filas = detalle[0], detalle[1:]
    col = {n: i for i, n in enumerate(enc)}
    for f in filas:  # generación × TVB = ventas; 3 % = bolsa
        assert f[col["Generación (kWh)"]] * f[col["TVB ($/kWh)"]] == f[col["Ventas brutas ($)"]]
        assert round(f[col["Ventas brutas ($)"]] * 0.03, 2) == f[col["Bolsa 3 % ($)"]]
    t = ex.totales(guardadas)
    assert round(sum(f[col["Monto ($)"]] for f in filas), 2) == float(t["bolsa"])
    assert t["bolsa"] == t["a_transferir"] + t["retenido"] and t["retenido"] > 0
    no = list(wb["No liquidados"].iter_rows(min_row=4, values_only=True))
    assert [(n[0], n[2]) for n in no] == [("PCH Chica", "Bajo el umbral del art. 45")]
    traza = list(wb["Trazabilidad"].iter_rows(min_row=2, values_only=True))
    assert all(len(f[7]) == 64 and f[14] for f in traza)   # SHA-256 de XM y radicado


def test_el_pdf_se_genera_y_marca_lo_preliminar(escenario):
    mes, guardadas, autoridades = _guardado(escenario)
    pdf = ex.pdf_resumen(PERIODO, guardadas, mes.no_liquidadas, autoridades)
    assert pdf[:5] == b"%PDF-" and len(pdf) > 2000
    assert ex.nombre_mes(PERIODO) == "diciembre de 2026"
    resumen = ex.resumen_por_autoridad(guardadas)
    assert resumen[-1]["autoridad"] == SIN_ASIGNAR      # lo retenido va al final


def test_el_export_no_cuenta_una_central_como_liquidada_y_sin_liquidar(escenario):
    """Fallo corregido el 2026-11-15: «No liquidados» salía del cálculo de hoy y
    los totales de lo guardado. Una central guardada que hoy ya no se liquida
    quedaba sumada en el total y a la vez listada como sin liquidar."""
    con = escenario
    _, guardadas, autoridades = _guardado(con)       # Central Grande y PCH de la Grande guardadas
    with con:
        con.execute("DELETE FROM tvb")                # hoy ya no se liquidaría ninguna
    hoy = lm.calcular_mes(con, PERIODO)
    assert {c.nombre for c in hoy.no_liquidadas} >= {"Central Grande", "PCH de la Grande"}

    no = lm.no_liquidadas_de_lo_guardado(hoy, guardadas)
    assert [(c.nombre, c.motivo) for c in no] == [("PCH Chica", "Bajo el umbral del art. 45")]
    avisos = lm.avisos_de_lo_guardado(con, hoy, guardadas)
    assert len(avisos) == 1 and "2 centrales guardadas ya no se liquidarían" in avisos[0]

    wb = openpyxl.load_workbook(io.BytesIO(ex.xlsx_mes(PERIODO, guardadas, no, autoridades, avisos=avisos)))
    assert [f[0] for f in wb["No liquidados"].iter_rows(min_row=4, values_only=True)] == ["PCH Chica"]
    assert "AVISO: 2 centrales guardadas ya no se liquidarían" in wb["Por autoridad"]["A1"].value
    assert ex.pdf_resumen(PERIODO, guardadas, no, autoridades, avisos=avisos)[:5] == b"%PDF-"


def test_el_export_muestra_una_central_calculada_y_sin_guardar(escenario):
    """La otra cara del mismo fallo: una central que hoy se liquida pero no se
    ha guardado no aparecía en ninguna parte del archivo."""
    con = escenario
    _generacion_dic(con)
    hoy = lm.calcular_mes(con, PERIODO)                       # nada guardado todavía
    no = lm.no_liquidadas_de_lo_guardado(hoy, guardadas=[])
    assert {(c.nombre, c.motivo) for c in no} == {
        ("Central Grande", lm.SIN_GUARDAR), ("PCH de la Grande", lm.SIN_GUARDAR),
        ("PCH Chica", "Bajo el umbral del art. 45")}
    assert lm.avisos_de_lo_guardado(con, hoy, []) == [
        "2 centrales se pueden liquidar hoy pero no se han guardado: figuran en «No liquidados» y no entran en "
        "los totales."]

    lm.guardar_mes(con, hoy)                                  # guardadas: ya no figuran como sin liquidar
    _delimitar(con, "Central Nueva", "PCH1", [{"autoridad": "CVC", "s1_ha": D("10")}])   # comparte PCH1
    hoy, guardadas = lm.calcular_mes(con, PERIODO), lm.liquidaciones_guardadas(con, PERIODO)
    assert [c.nombre for c in lm.no_liquidadas_de_lo_guardado(hoy, guardadas)] == ["PCH Chica"]
    assert lm.avisos_de_lo_guardado(con, hoy, guardadas) == [
        "1 centrales tienen hoy un cálculo distinto del guardado (Central Nueva + PCH de la Grande): "
        "se exporta lo guardado."]


# --- fallos menores corregidos el 2026-11-15 --------------------------------------

def test_el_mes_se_guarda_entero_o_no_se_guarda(escenario, monkeypatch):
    """Antes cada central se guardaba en su propia transacción: un error a
    mitad de camino dejaba el mes guardado a medias."""
    con = escenario
    _generacion_dic(con)
    insertar, llamadas = repo._insertar_liquidacion, []

    def falla_en_la_segunda(c, plan):
        llamadas.append(plan)
        if len(llamadas) == 2:
            raise sqlite3.OperationalError("database is locked")
        return insertar(c, plan)

    monkeypatch.setattr(repo, "_insertar_liquidacion", falla_en_la_segunda)
    with pytest.raises(sqlite3.OperationalError):
        lm.guardar_mes(con, lm.calcular_mes(con, PERIODO))
    assert len(llamadas) == 2
    assert con.execute("SELECT count(*) FROM liquidaciones").fetchone()[0] == 0, "la primera se deshizo"
    assert con.execute("SELECT count(*) FROM liquidacion_filas").fetchone()[0] == 0


def test_si_otra_pestana_guarda_al_mismo_tiempo_no_se_guarda_nada_y_se_avisa(escenario, monkeypatch):
    con = escenario
    _generacion_dic(con)
    lm.guardar_mes(con, lm.calcular_mes(con, PERIODO))          # versión 1 de las dos centrales
    repo.guardar_tvb(con, 2026, D("210"), "sintética")         # cambia el cálculo: las dos quedan pendientes
    preparar = repo._preparar_liquidacion

    def como_si_la_otra_pestana_ya_hubiera_guardado(*args):
        plan = preparar(*args)
        plan["numero"] = 1                                       # el número que acaba de tomar la otra sesión
        return plan

    monkeypatch.setattr(repo, "_preparar_liquidacion", como_si_la_otra_pestana_ya_hubiera_guardado)
    with pytest.raises(repo.ErrorRepositorio, match="al mismo tiempo"):
        lm.guardar_mes(con, lm.calcular_mes(con, PERIODO))
    assert con.execute("SELECT count(*), max(version_numero) FROM liquidaciones").fetchone()[:] == (2, 1)


def test_si_cambia_la_capacidad_del_umbral_lo_guardado_queda_pendiente(escenario):
    """La liquidación guarda con qué capacidad se verificó el umbral; antes, si
    llegaba otra, no se creaba versión nueva y lo guardado quedaba desactualizado."""
    con = escenario
    _generacion_dic(con)
    lm.guardar_mes(con, lm.calcular_mes(con, PERIODO))
    with con:
        con.execute("UPDATE capacidad_empresas SET capacidad_total_kw = '500000' WHERE agente = 'EMPRESA GRANDE'")
    mes = lm.calcular_mes(con, PERIODO)
    assert sorted(c.nombre for c in lm.pendientes_de_guardar(con, mes)) == ["Central Grande", "PCH de la Grande"]
    lm.guardar_mes(con, mes)
    gra = next(l for l in lm.liquidaciones_guardadas(con, PERIODO) if l["codigo_submercado"] == "GRA1")
    assert (gra["version_numero"], gra["capacidad_empresa_kw"]) == (2, "500000")
    assert lm.pendientes_de_guardar(con, lm.calcular_mes(con, PERIODO)) == []


def test_una_central_compartida_se_nombra_igual_en_liquidacion_y_exportar(escenario):
    """Liquidación ordenaba los proyectos por id y Exportar por nombre."""
    con = escenario
    _generacion_dic(con)
    _delimitar(con, "Alfa", "GRA1", [{"autoridad": "CVC", "s1_ha": D("3")}, {"autoridad": "CRC", "s1_ha": D("1")}])
    mes = lm.calcular_mes(con, PERIODO)
    assert _central(mes, "GRA1").nombre == "Alfa + Central Grande"      # «Alfa» se creó después
    lm.guardar_mes(con, mes)
    gra = next(l for l in lm.liquidaciones_guardadas(con, PERIODO) if l["codigo_submercado"] == "GRA1")
    assert " + ".join(gra["proyectos"]) == "Alfa + Central Grande"


# --- mes parcial y ajuste de vigencia (Jorge, 2026-11-14) ---------------------------

def _parcial(con, vigente="2026-12-25", doc="2026-11-11"):
    """Central Grande con delimitación que empieza a regir dentro de diciembre."""
    _capacidad_2025(con)
    _delimitar(con, "Central Grande", "GRA1", [{"autoridad": "CVC", "s1_ha": D("75")}, {"autoridad": "CRC", "s1_ha": D("25")}],
               vigente=vigente, doc=doc)
    repo.guardar_tvb(con, 2026, D("200"), "sintética")


def test_mes_parcial_se_liquida_desde_el_dia_de_vigencia_incluido(con):
    _parcial(con)
    _generacion_dic(con)
    gra = _central(lm.calcular_mes(con, PERIODO), "GRA1")
    # del 25 al 31 son 7 días × 24 kWh = 168 kWh; × 200 $ × 3 % = 1.008
    assert gra.liquidada and (gra.dia_desde, gra.dia_hasta, gra.dias) == (25, 31, "25 al 31")
    assert gra.calculo.generacion_kwh == D("168") and gra.calculo.bolsa_car_pnn == D("1008.00")
    assert {f.autoridad: f.monto for f in gra.calculo.filas} == {"CVC": D("756.00"), "CRC": D("252.00")}
    assert any("Mes parcial" in a for a in gra.alertas)
    assert _central(lm.calcular_mes(con, "2026-11"), "GRA1").estado == "sin_delimitacion"   # antes no rige

    assert lm.guardar_mes(con, lm.calcular_mes(con, PERIODO))["guardadas"] == ["Central Grande"]
    assert lm.guardar_mes(con, lm.calcular_mes(con, PERIODO))["guardadas"] == []
    guardada = lm.liquidaciones_guardadas(con, PERIODO)[0]
    assert (guardada["dia_desde"], guardada["dia_hasta"], guardada["generacion_kwh"]) == (25, 31, "168")
    assert ex.dias_liquidados(guardada) == "25 al 31"


def test_mes_parcial_sin_generacion_por_dia_no_se_liquida(con):
    _parcial(con, vigente="2025-12-25", doc="2025-12-01")   # 2025 solo está en formato mensual
    repo.guardar_tvb(con, 2025, D("190"), "sintética")
    gra = _central(lm.calcular_mes(con, "2025-12"), "GRA1")
    assert gra.estado == "sin_dato_diario" and gra.calculo is None
    assert _central(lm.calcular_mes(con, "2026-12"), "GRA1").estado == "sin_generacion"


def test_un_dia_ausente_en_el_tramo_no_bloquea_y_se_senala(con):
    _parcial(con)
    filas = mes_completo("GRA1", 2026, 12, 1, saltar={26, 27}) + mes_completo("PCH1", 2026, 12, 1)
    guardar_lectura(con, leer_xm(diario(filas), "x_2026.xlsx"))
    gra = _central(lm.calcular_mes(con, PERIODO), "GRA1")
    assert gra.calculo.generacion_kwh == D("120") and any("5 de 7 días" in a for a in gra.alertas)
    # el * de la pantalla sale de esta marca, no de buscar un texto en las alertas (2026-11-15)
    assert gra.dias_ausentes


def test_sin_generacion_en_el_tramo_da_cero_y_se_rotula(con):
    """Jorge, 2026-11-15: si XM no trae generación en los días liquidados, la
    transferencia es $ 0 y además dice «Sin generación», en pantalla y exportables."""
    _parcial(con)
    filas = mes_completo("GRA1", 2026, 12, 1, saltar=set(range(25, 32))) + mes_completo("PCH1", 2026, 12, 1)
    guardar_lectura(con, leer_xm(diario(filas), "x_2026.xlsx"))
    mes = lm.calcular_mes(con, PERIODO)
    gra = _central(mes, "GRA1")
    assert gra.liquidada and gra.sin_generacion and gra.calculo.bolsa_car_pnn == D("0.00")
    assert all(f.monto == D("0.00") for f in gra.calculo.filas)
    assert any(a.startswith(lm.SIN_GENERACION) for a in gra.alertas)

    lm.guardar_mes(con, mes)
    guardadas = lm.liquidaciones_guardadas(con, PERIODO)
    assert ex.sin_generacion(guardadas[0])
    autoridades = {r["sigla"]: dict(r) for r in con.execute("SELECT * FROM autoridades")}
    wb = openpyxl.load_workbook(io.BytesIO(ex.xlsx_mes(PERIODO, guardadas, [], autoridades)))
    detalle = list(wb["Detalle"].iter_rows(min_row=3, values_only=True))
    col = {n: i for i, n in enumerate(detalle[0])}
    assert {f[col["Observación"]] for f in detalle[1:]} == {"Sin generación"}
    assert ex.pdf_resumen(PERIODO, guardadas, [], autoridades)[:5] == b"%PDF-"


def test_con_generacion_no_se_rotula_sin_generacion(con):
    _parcial(con)
    _generacion_dic(con)
    mes = lm.calcular_mes(con, PERIODO)
    assert not _central(mes, "GRA1").sin_generacion
    lm.guardar_mes(con, mes)
    assert not any(ex.sin_generacion(l) for l in lm.liquidaciones_guardadas(con, PERIODO))


def test_solo_se_guarda_por_dia_lo_hidraulico_y_lo_enlazado(con):
    repo.crear_proyecto(con, "Enlazada no hidráulica", codigo_submercado="TER1")
    filas = (mes_completo("HID1", 2026, 12, 1) + mes_completo("TER1", 2026, 12, 1, tipo="TERMICA")
             + mes_completo("TER2", 2026, 12, 1, tipo="TERMICA"))
    guardar_lectura(con, leer_xm(diario(filas), "x_2026.xlsx"))
    por_central = dict(con.execute("SELECT codigo_submercado, count(*) FROM generacion_diaria GROUP BY 1").fetchall())
    assert por_central == {"HID1": 31, "TER1": 31}
    assert con.execute("SELECT count(DISTINCT codigo_submercado) FROM generacion").fetchone()[0] == 3


def test_reemplazo_a_mitad_de_mes_avisa_los_dias_que_no_quedan_liquidados(escenario):
    con = escenario
    _generacion_dic(con)
    _delimitar(con, "Central Grande", "GRA1", [{"autoridad": "CVC", "s1_ha": D("1")}], vigente="2026-12-10",
               radicado="RAD-NUEVA")
    gra = _central(lm.calcular_mes(con, PERIODO), "GRA1")
    assert gra.liquidada and gra.dia_desde == 10 and gra.radicados == "RAD-NUEVA"
    assert any("NO quedan liquidados" in a and "RAD-Central Grande" in a for a in gra.alertas)


def test_se_puede_ajustar_la_vigencia_de_una_confirmada_y_queda_registrado(escenario):
    con = escenario
    _generacion_dic(con)
    lm.guardar_mes(con, lm.calcular_mes(con, PERIODO))
    d = con.execute("SELECT d.id FROM delimitaciones d JOIN proyectos p ON p.id = d.proyecto_id "
                    "WHERE p.codigo_submercado = 'GRA1'").fetchone()[0]
    repo.cambiar_vigencia(con, d, "2026-12-25", "prueba")
    fila = con.execute("SELECT estado, vigente_desde FROM delimitaciones WHERE id = ?", (d,)).fetchone()
    assert tuple(fila) == ("vigente", "2026-12-25")
    cambio = repo.cambios_de_delimitacion(con, d)[0]
    assert (cambio["antes"], cambio["despues"], cambio["motivo"]) == ("2026-12-01", "2026-12-25", "prueba")

    mes = lm.calcular_mes(con, PERIODO)                       # lo guardado ya no coincide: se señala
    assert [c.nombre for c in lm.pendientes_de_guardar(con, mes)] == ["Central Grande"]
    lm.guardar_mes(con, mes)
    guardada = next(l for l in lm.liquidaciones_guardadas(con, PERIODO) if l["codigo_submercado"] == "GRA1")
    assert (guardada["version_numero"], guardada["dia_desde"]) == (2, 25)

    with pytest.raises(repo.ErrorRepositorio, match="hacia adelante"):
        repo.cambiar_vigencia(con, d, "2026-11-01")           # antes del documento (11/11/2026)
    with pytest.raises(repo.ErrorRepositorio, match="inválida"):
        repo.cambiar_vigencia(con, d, "25/12/2026")
    p = repo.crear_proyecto(con, "Otro", codigo_submercado="OTR1")
    borrador = repo.crear_delimitacion(con, p, "RAD-B", "2026-11-11", None, "hectareas", [])
    with pytest.raises(repo.ErrorRepositorio, match="solo se ajusta"):
        repo.cambiar_vigencia(con, borrador, "2026-12-01")


def test_una_base_v5_se_migra_a_v6(tmp_path):
    ruta = tmp_path / "v5.db"
    c = repo.abrir(ruta)
    with c:
        for tabla in repo.TABLAS_V6:
            c.execute(f"DROP TABLE {tabla}")
        for col, _ in repo.COLUMNAS_V6:
            c.execute(f"ALTER TABLE liquidaciones DROP COLUMN {col}")
        c.execute("PRAGMA user_version = 5")
    c.close()
    c = repo.abrir(ruta)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 6
    assert {"dia_desde", "dia_hasta"} <= {r[1] for r in c.execute("PRAGMA table_info(liquidaciones)")}
    c.execute("SELECT * FROM generacion_diaria").fetchall()
    c.execute("SELECT * FROM delimitacion_cambios").fetchall()
    c.close()
    repo.abrir(ruta).close()
