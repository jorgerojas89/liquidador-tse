"""
Proyecciones (capacidad nueva, Jorge 2026-11-14). Todas las cifras de este
archivo son SINTÉTICAS, escogidas para seguir las cuentas a mano.
"""
from __future__ import annotations

import io
from decimal import Decimal
from fractions import Fraction

import openpyxl
import pytest

from datos import proyeccion as py
from datos import repositorio as repo
from motor.proyeccion import proyectar_generacion, sugerir_tvb
from motor.tse_calculator import TseCalculatorError

D = Decimal


# --- motor ------------------------------------------------------------------------

def test_promedio_del_mismo_mes_en_los_ultimos_5_anios_con_dato():
    historia = {f"{a}-01": D(v) for a, v in zip(range(2019, 2027), (999, 999, 999, 100, 200, 300, 400, 500))}
    historia["2026-02"] = D(70)
    historia["2027-01"] = D(123456)                    # el año proyectado y los posteriores no entran
    ene, feb, mar = proyectar_generacion(historia, 2027)[:3]
    assert ene.anios == (2022, 2023, 2024, 2025, 2026)
    assert (ene.bajo, ene.medio, ene.alto) == (D(100), D("300.00"), D(500))
    assert (feb.anios, feb.medio) == ((2026,), D("70.00"))
    assert mar.medio is None and mar.anios == ()        # sin dato no se estima: no es cero


def test_un_anio_sin_dato_no_cuenta_como_cero():
    historia = {"2024-06": D(10), "2022-06": D(30)}     # 2023 y 2025 sin dato de junio
    jun = proyectar_generacion(historia, 2027)[5]
    assert jun.anios == (2022, 2024) and jun.medio == D("20.00")


def test_la_generacion_historica_no_admite_float():
    with pytest.raises(TseCalculatorError):
        proyectar_generacion({"2025-01": 10.5}, 2027)


def test_tvb_sugerida_con_la_variacion_promedio():
    serie = {2024: D("100"), 2025: D("110"), 2026: D("132")}          # +10 % y +20 % → promedio 15 %
    s = sugerir_tvb(serie, 2027)
    assert s.variacion_promedio == Fraction(15, 100) and s.base_anio == 2026
    assert s.valor == D("151.80000")
    assert sugerir_tvb(serie, 2028).valor == D("174.57000")           # 132 × 1,15²
    assert sugerir_tvb({2026: D("132")}, 2027) is None                # sin dos años no hay variación


# --- con base en memoria -----------------------------------------------------------

def _historia(con, codigo, kwh_por_anio, agente="EMPRESA GRANDE", capacidad=D(50000)):
    """Doce meses iguales por año (formato mensual, versión firme)."""
    for anio, kwh in kwh_por_anio.items():
        filas = [{"periodo": f"{anio}-{m:02d}", "version_xm": "TXF", "codigo_submercado": codigo, "recurso": codigo,
                  "agente": agente, "tipo_generacion": "HIDRAULICA", "generacion_kwh": D(kwh), "capacidad_kw": capacidad}
                 for m in range(1, 13)]
        repo.registrar_carga_xm(con, f"{codigo}_{anio}.xlsx", f"{codigo}{anio}".ljust(64, "0"), filas, formato="mensual",
                                capacidades=[{"anio": anio, "agente": agente, "capacidad_total_kw": capacidad,
                                              "capacidad_hidraulica_kw": capacidad, "sujeta": capacidad > 10000}])


def _delimitar(con, nombre, codigo, filas, confirmar=True, vigente="2026-12-01"):
    p = repo.crear_proyecto(con, nombre, codigo_submercado=codigo)
    d = repo.crear_delimitacion(con, p, f"RAD-{nombre}", "2026-11-11", vigente, "hectareas", filas, pdf_sha256="e" * 64,
                                s1_t_declarado=sum((f["s1_ha"] for f in filas), D(0)), trae_s2=False,
                                paramo_declarado=False)
    if confirmar:
        assert repo.confirmar_delimitacion(con, d).ok
    return p


@pytest.fixture
def base(con):
    _historia(con, "GRA1", {2024: 1000, 2025: 2000, 2026: 3000})
    _delimitar(con, "Central Grande", "GRA1", [{"autoridad": "CVC", "s1_ha": D("75")}, {"autoridad": "CRC", "s1_ha": D("25")}])
    return con


def test_la_proyeccion_anual_es_la_suma_de_los_meses(base):
    p = py.proyectar_anio(base, 2027, D("100"))
    c = p.proyectadas[0]
    assert c.meses_proyectados == 12 and [m.generacion.medio for m in c.meses] == [D("2000.00")] * 12
    # mes medio: 2.000 kWh × 100 $ × 3 % = 6.000 → CVC 4.500, CRC 1.500; año × 12
    por = p.por_autoridad()
    assert por["CVC"]["medio"] == [D("4500.00")] * 12 and sum(por["CRC"]["medio"]) == D("18000.00")
    assert (p.anual("bajo"), p.anual("medio"), p.anual("alto")) == (D("36000.00"), D("72000.00"), D("108000.00"))
    assert p.anual("medio") == sum(p.mensual("medio")) == c.anual("medio")


def test_solo_proyecta_con_delimitacion_vigente(base):
    _historia(base, "BOR1", {2025: 500})
    _delimitar(base, "En borrador", "BOR1", [{"autoridad": "CVC", "s1_ha": D("1")}], confirmar=False)
    p = py.proyectar_anio(base, 2027, D("100"))
    estados = {c.nombre: c.estado for c in p.centrales}
    assert estados == {"Central Grande": py.PROYECTADA, "En borrador": "sin_delimitacion"}


def test_lo_que_falta_deja_la_central_sin_proyeccion(base):
    repo.crear_proyecto(base, "Sin enlazar")
    _delimitar(base, "Sin historia", "NADA", [{"autoridad": "CVC", "s1_ha": D("1")}])
    _historia(base, "CHI1", {2025: 500}, agente="EMPRESA CHICA", capacidad=D(9000))
    _delimitar(base, "PCH Chica", "CHI1", [{"autoridad": "CVC", "s1_ha": D("1")}])
    estados = {c.nombre: c.estado for c in py.proyectar_anio(base, 2027, D("100")).centrales}
    assert estados == {"Central Grande": py.PROYECTADA, "Sin enlazar": "sin_central",
                       "Sin historia": "sin_historico", "PCH Chica": "bajo_umbral"}


def test_una_delimitacion_que_empieza_a_mitad_de_anio_proyecta_solo_sus_meses(con):
    _historia(con, "GRA1", {2026: 1000})
    _delimitar(con, "Central Grande", "GRA1", [{"autoridad": "CVC", "s1_ha": D("1")}], vigente="2027-07-01")
    c = py.proyectar_anio(con, 2027, D("100")).proyectadas[0]
    assert [m.mes for m in c.meses if m.proyectado] == [7, 8, 9, 10, 11, 12]
    assert "no tiene delimitación" in c.meses[0].nota


def test_la_proyeccion_no_escribe_en_la_base(base):
    antes = base.total_changes
    p = py.proyectar_anio(base, 2027, D("205.5"))
    py.xlsx_proyeccion(p, {})
    assert base.total_changes == antes
    assert repo.tvb_de(base, 2027) is None and base.execute("SELECT count(*) FROM liquidaciones").fetchone()[0] == 0
    with pytest.raises(repo.ErrorRepositorio):
        py.proyectar_anio(base, 2027, D("0"))


def test_el_xlsx_va_rotulado_como_estimacion_y_cuadra(base):
    repo.guardar_tvb(base, 2025, D("100"), "sintética")
    repo.guardar_tvb(base, 2026, D("110"), "sintética")
    s = py.tvb_sugerida(base, 2027)
    p = py.proyectar_anio(base, 2027, s.valor)
    aut = {r["sigla"]: dict(r) for r in base.execute("SELECT * FROM autoridades")}
    wb = openpyxl.load_workbook(io.BytesIO(py.xlsx_proyeccion(p, aut, s)))
    assert wb.sheetnames == ["Supuestos", "Anual por autoridad", "Mensual bajo", "Mensual medio", "Mensual alto",
                             "Autoridad por central", "Por central y mes", "Sin proyección"]
    assert all("ESTIMACIÓN" in wb[h]["A1"].value for h in wb.sheetnames)
    anual = {f[0]: f[3] for f in wb["Anual por autoridad"].iter_rows(min_row=4, values_only=True) if f and f[0]}
    assert round(anual["CVC"] + anual["CRC"], 2) == float(p.anual("medio"))
    supuestos = dict(f[:2] for f in wb["Supuestos"].iter_rows(min_row=4, values_only=True))
    assert supuestos["Origen de la TVB"].startswith("Estimada") and supuestos["Variación promedio anual usada"] == "10,00 %"


def test_cada_autoridad_se_desglosa_por_central_y_cuadra(base):
    _historia(base, "OTR1", {2025: 500, 2026: 1500})
    _delimitar(base, "Otra Central", "OTR1", [{"autoridad": "CVC", "s1_ha": D("1")}], vigente="2027-07-01")
    p = py.proyectar_anio(base, 2027, D("100"))
    detalle, por = p.por_autoridad_y_central(), p.por_autoridad()
    assert [a.central.nombre for a in detalle["CVC"]] == ["Central Grande", "Otra Central"]
    assert [a.central.nombre for a in detalle["CRC"]] == ["Central Grande"]
    grande, otra = detalle["CVC"]
    assert (grande.participacion, len(grande.meses), grande.anual("medio")) == ("75,00 %", 12, D("54000.00"))
    # Otra: 1.000 kWh × 100 $ × 3 % = 3.000 al mes, solo de julio a diciembre
    assert (otra.participacion, sorted(otra.meses), otra.anual("medio")) == ("100,00 %", [7, 8, 9, 10, 11, 12], D("18000.00"))
    for sigla, aportes in detalle.items():
        for e in py.ESCENARIOS:
            assert sum(a.anual(e) for a in aportes) == sum(por[sigla][e])
    wb = openpyxl.load_workbook(io.BytesIO(py.xlsx_proyeccion(p, {})))
    filas = [f for f in wb["Autoridad por central"].iter_rows(min_row=4, values_only=True)]
    assert [(f[0], f[2], f[7]) for f in filas] == [("CRC", "GRA1", 18000), ("CVC", "GRA1", 54000), ("CVC", "OTR1", 18000)]
