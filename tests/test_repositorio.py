"""Repositorio SQLite (paso 2). Datos sintéticos, salvo el maestro de autoridades."""
from decimal import Decimal
from fractions import Fraction

import pytest

from datos import repositorio as repo
from tests.conftest import cabecera, cargar_xm, nueva_delimitacion

D = Decimal


def test_el_esquema_se_crea_con_las_versiones_de_xm(con):
    versiones = {f["codigo"]: f for f in con.execute("SELECT * FROM versiones_xm")}
    assert set(versiones) == {"TX1", "TX2", "TX3", "TX4", "TX5", "TXF"}
    assert [c for c, v in versiones.items() if v["es_firme"]] == ["TXF"]
    assert sorted(versiones, key=lambda c: versiones[c]["orden"]) == ["TX1", "TX2", "TX3", "TX4", "TX5", "TXF"]
    assert con.execute("SELECT count(*) FROM autoridades WHERE tipo IN ('CAR', 'PNN')").fetchone()[0] == 49
    assert con.execute("SELECT tipo FROM autoridades WHERE sigla = 'SIN_ASIGNAR'").fetchone()[0] == "SIN_ASIGNAR"


def test_se_puede_agregar_otra_version_de_xm(con):
    repo.agregar_version_xm(con, "TXR", 60)
    assert con.execute("SELECT orden FROM versiones_xm WHERE codigo='TXR'").fetchone()[0] == 60


def test_una_base_v3_se_migra_hasta_la_version_actual(tmp_path):
    ruta = tmp_path / "v3.db"
    c = repo.abrir(ruta)
    with c:  # devolverla al estado v3: sin las columnas ni la tabla nuevas
        c.execute("DROP TABLE capacidad_empresas")
        for col in ("dias_reportados", "dias_archivo", "dias_mes"):
            c.execute(f"ALTER TABLE generacion DROP COLUMN {col}")
        for col in ("formato", "version_declarada"):
            c.execute(f"ALTER TABLE cargas_xm DROP COLUMN {col}")
        c.execute("DELETE FROM versiones_xm WHERE codigo IN ('TX3', 'TX4', 'TX5')")
        c.execute("PRAGMA user_version = 3")
    c.close()
    c = repo.abrir(ruta)
    assert c.execute("PRAGMA user_version").fetchone()[0] == repo.VERSION_ESQUEMA
    assert c.execute("SELECT count(*) FROM versiones_xm").fetchone()[0] == 6
    assert "dias_archivo" in {r[1] for r in c.execute("PRAGMA table_info(generacion)")}
    c.execute("SELECT * FROM capacidad_empresas").fetchall()
    c.close()


def test_una_base_v4_a_medias_se_completa(tmp_path):
    ruta = tmp_path / "v4.db"
    c = repo.abrir(ruta)
    with c:
        c.execute("ALTER TABLE generacion DROP COLUMN dias_archivo")
    c.close()
    c = repo.abrir(ruta)
    assert "dias_archivo" in {r[1] for r in c.execute("PRAGMA table_info(generacion)")}
    c.close()


# --- números exactos -------------------------------------------------------

def test_las_cifras_se_guardan_exactas_y_el_dato_ausente_queda_null(con, proyecto):
    d = nueva_delimitacion(con, proyecto, filas=[
        {"autoridad": "CVC", "s1_ha": D("2900.92344")},           # sin S2
    ])
    fila = repo.filas_delimitacion(con, d)[0]
    assert fila["s1_ha"] == "2900.92344"
    assert fila["s2_ha"] is None, "ausente es NULL, no cero"


def test_rechaza_float():
    with pytest.raises(repo.ErrorRepositorio):
        repo.a_texto(2900.92344)


def test_la_fraccion_ida_y_vuelta_es_exacta():
    f = Fraction(1234567891, 2987654321)
    assert repo.a_fraccion(repo.a_texto(f)) == f


def test_un_texto_con_coma_decimal_se_guarda_normalizado_y_se_puede_leer():
    """Fallo latente corregido el 2026-11-15: «43,21875» pasaba la validación
    pero se guardaba con coma, y al leerlo `Decimal` fallaba."""
    assert repo.a_texto("43,21875") == "43.21875"
    assert repo.a_decimal(repo.a_texto("86.537,28373")) == D("86537.28373")
    assert repo.a_texto("2900.92344") == "2900.92344"
    assert repo.a_texto(" 3/7 ") == "3/7" and repo.a_fraccion(repo.a_texto("6/14")) == Fraction(3, 7)
    assert repo.a_texto("N/A") is None, "N/A es dato ausente, como en el formulario"
    for malo in ("abc", "1,2,3", "1/0", "NaN", D("NaN"), D("Infinity")):
        with pytest.raises(repo.ErrorRepositorio):
            repo.a_texto(malo)


# --- delimitaciones ----------------------------------------------------------

def test_sin_delimitacion_no_hay_aplicable(con, proyecto):
    assert repo.delimitacion_aplicable(con, proyecto, "2025-06") is None
    with pytest.raises(repo.SinDelimitacionVigente):
        repo.exigir_delimitacion(con, proyecto, "2025-06")


def test_un_borrador_no_aplica(con, proyecto):
    nueva_delimitacion(con, proyecto)
    assert repo.delimitacion_aplicable(con, proyecto, "2025-06") is None


def test_una_nueva_reemplaza_desde_su_vigencia_y_la_anterior_no_se_borra(con, proyecto):
    vieja = nueva_delimitacion(con, proyecto, "2025-01-01", "RAD-1")
    repo.marcar_vigente(con, vieja)
    nueva = nueva_delimitacion(con, proyecto, "2025-07-01", "RAD-2")
    repo.marcar_vigente(con, nueva)

    assert repo.delimitacion_aplicable(con, proyecto, "2025-06")["id"] == vieja
    assert repo.delimitacion_aplicable(con, proyecto, "2025-07")["id"] == nueva
    estados = dict(con.execute("SELECT id, estado FROM delimitaciones").fetchall())
    assert estados == {vieja: "reemplazada", nueva: "vigente"}


def test_mes_partido_no_se_liquida(con, proyecto):
    """Solo meses completos: el mes en que una delimitación empieza a regir
    después del día 1 queda fuera del Liquidador."""
    vieja = nueva_delimitacion(con, proyecto, "2025-01-01", "RAD-1")
    repo.marcar_vigente(con, vieja)
    nueva = nueva_delimitacion(con, proyecto, "2025-06-15", "RAD-2")
    repo.marcar_vigente(con, nueva)

    assert repo.delimitacion_aplicable(con, proyecto, "2025-05")["id"] == vieja
    assert repo.delimitacion_aplicable(con, proyecto, "2025-06") is None
    with pytest.raises(repo.MesPartido):
        repo.exigir_delimitacion(con, proyecto, "2025-06")
    assert repo.delimitacion_aplicable(con, proyecto, "2025-07")["id"] == nueva


def test_primera_delimitacion_a_mitad_de_mes_tambien_parte_el_mes(con, proyecto):
    repo.marcar_vigente(con, nueva_delimitacion(con, proyecto, "2025-06-15"))
    with pytest.raises(repo.MesPartido):
        repo.exigir_delimitacion(con, proyecto, "2025-06")


def test_no_se_guarda_liquidacion_de_un_mes_partido(con, proyecto):
    d = nueva_delimitacion(con, proyecto, "2025-01-01", "RAD-1"); repo.marcar_vigente(con, d)
    repo.marcar_vigente(con, nueva_delimitacion(con, proyecto, "2025-06-15", "RAD-2"))
    with pytest.raises(repo.MesPartido):
        repo.guardar_liquidacion(con, cabecera(proyecto, d, cargar_xm(con, "TXF")), _filas())


def test_no_se_confirma_una_nueva_con_vigencia_anterior_a_la_actual(con, proyecto):
    repo.marcar_vigente(con, nueva_delimitacion(con, proyecto, "2025-07-01", "RAD-1"))
    atrasada = nueva_delimitacion(con, proyecto, "2025-01-01", "RAD-2")
    with pytest.raises(repo.ErrorRepositorio):
        repo.marcar_vigente(con, atrasada)


# --- versiones de XM ---------------------------------------------------------

def test_preliminar_toma_la_version_mas_reciente_y_oficial_exige_firme(con):
    cargar_xm(con, "TX1", kwh=D("100"))
    cargar_xm(con, "TX2", kwh=D("110"))
    assert repo.generacion_para(con, "SINT1", "2025-06", "preliminar")["version_xm"] == "TX2"
    with pytest.raises(repo.SinGeneracion):
        repo.generacion_para(con, "SINT1", "2025-06", "oficial")

    cargar_xm(con, "TXF", kwh=D("112"))
    oficial = repo.generacion_para(con, "SINT1", "2025-06", "oficial")
    assert (oficial["version_xm"], oficial["generacion_kwh"]) == ("TXF", "112")
    # todas las versiones quedan guardadas
    assert con.execute("SELECT count(*) FROM generacion").fetchone()[0] == 3


def test_version_de_xm_desconocida_se_rechaza(con):
    with pytest.raises(repo.ErrorRepositorio):
        cargar_xm(con, "TX9")


# --- liquidaciones -----------------------------------------------------------

def _filas():
    return [
        {"autoridad": "CVC", "s1_ha": D("2900.92344"), "razon": Fraction(7, 10),
         "porcentaje_mostrado": D("70.00000"), "monto": D("147365821.39"), "ajuste_residuo": True},
        {"autoridad": "PNNLos Farallones de Cali", "s1_ha": D("889.88840"), "razon": Fraction(3, 10),
         "porcentaje_mostrado": D("30.00000"), "monto": D("63156780.59")},
    ]


def test_liquidacion_oficial_con_version_preliminar_se_rechaza(con, proyecto):
    d = nueva_delimitacion(con, proyecto); repo.marcar_vigente(con, d)
    carga = cargar_xm(con, "TX2")
    with pytest.raises(repo.ErrorRepositorio, match="firme"):
        repo.guardar_liquidacion(con, cabecera(proyecto, d, carga, "TX2", "oficial"), _filas())
    # como preliminar sí se guarda
    repo.guardar_liquidacion(con, cabecera(proyecto, d, carga, "TX2", "preliminar"), _filas())


def test_liquidacion_sobre_borrador_se_rechaza(con, proyecto):
    d = nueva_delimitacion(con, proyecto)
    carga = cargar_xm(con, "TXF")
    with pytest.raises(repo.SinDelimitacionVigente):
        repo.guardar_liquidacion(con, cabecera(proyecto, d, carga), _filas())


def test_recalcular_crea_version_nueva_y_conserva_la_anterior(con, proyecto):
    d = nueva_delimitacion(con, proyecto); repo.marcar_vigente(con, d)
    pre = repo.guardar_liquidacion(con, cabecera(proyecto, d, cargar_xm(con, "TX1"), "TX1", "preliminar"), _filas())
    ofi = repo.guardar_liquidacion(con, cabecera(proyecto, d, cargar_xm(con, "TXF")), _filas())
    filas = con.execute("SELECT id, version_numero, caracter, reemplaza_a FROM liquidaciones ORDER BY id").fetchall()
    assert [tuple(f) for f in filas] == [(pre, 1, "preliminar", None), (ofi, 2, "oficial", pre)]
    assert repo.ultima_liquidacion(con, proyecto, "2025-06")["id"] == ofi


def test_la_liquidacion_guarda_toda_la_trazabilidad(con, proyecto):
    d = nueva_delimitacion(con, proyecto); repo.marcar_vigente(con, d)
    liq = repo.guardar_liquidacion(con, cabecera(proyecto, d, cargar_xm(con, "TXF")), _filas())
    l = con.execute("SELECT * FROM liquidaciones WHERE id = ?", (liq,)).fetchone()
    assert l["carga_xm_sha256"] == "b" * 64
    assert l["delimitacion_sha256"] == "a" * 64
    assert (l["tvb_anio"], l["tvb_pesos_kwh"]) == (2025, "189.02930")
    filas = repo.filas_liquidacion(con, liq)
    assert repo.a_fraccion(filas[0]["razon"]) == Fraction(7, 10)
    assert filas[0]["s1_ha"] == "2900.92344"


def test_sin_campos_de_trazabilidad_no_se_guarda(con, proyecto):
    d = nueva_delimitacion(con, proyecto); repo.marcar_vigente(con, d)
    incompleta = cabecera(proyecto, d, cargar_xm(con, "TXF"))
    del incompleta["carga_xm_sha256"]
    with pytest.raises(repo.ErrorRepositorio, match="trazabilidad"):
        repo.guardar_liquidacion(con, incompleta, _filas())
