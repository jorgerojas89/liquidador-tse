"""Paso 4: lector de XM (dos formatos), cargue y cruce central ↔ proyecto.

Los archivos de XM de estas pruebas son SINTÉTICOS, armados con openpyxl con
la misma estructura que los reales. La última prueba usa el archivo real de
2026 si está en `insumos/` y se salta si no."""
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

import openpyxl
import pytest

from datos import cargue_xm
from datos import repositorio as repo
from datos.cargue_xm import ErrorCargue, guardar_lectura, leer_archivo, resumen_carga, sujecion_central
from datos.parse_xm_generacion import ArchivoXMInvalido, leer_xm
from datos.rutas import dir_xm

D = Decimal
ENC_DIARIO = ["Fecha", "Recurso", "Código Recurso", "Tipo Generación", "Combustible", "Código Agente",
              "Tipo Despacho", "Es Menor", "Clasificación", *[str(h) for h in range(24)], "Version"]


def _xlsx(wb) -> bytes:
    b = BytesIO()
    wb.save(b)
    return b.getvalue()


def diario(filas, version_col=True) -> bytes:
    """filas: (fecha, codigo, tipo, agente, horas[24] o valor por hora, version, combustible)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([])
    ws.append([None, None, "Generación (kWh) 2026"])
    enc = ENC_DIARIO if version_col else ENC_DIARIO[:-1]
    ws.append(enc)
    for fecha, codigo, tipo, agente, horas, version, *comb in filas:
        horas = horas if isinstance(horas, list) else [horas] * 24
        fila = [fecha, f"RECURSO {codigo}", codigo, tipo, comb[0] if comb else "AGUA", agente,
                "DESPACHADO CENTRALMENTE", "NO", "NORMAL", *horas]
        ws.append(fila + ([version] if version_col else []))
    return _xlsx(wb)


def mes_completo(codigo, anio, mes, por_hora, version="TXF", tipo="HIDRAULICA", agente="SING", saltar=()):
    d, filas = date(anio, mes, 1), []
    while d.month == mes:
        if d.day not in saltar:
            filas.append((d, codigo, tipo, agente, por_hora, version))
        d += timedelta(days=1)
    return filas


def mensual(anio, recursos) -> bytes:
    """Formato 2018-2025. recursos: (agente, codigo, nombre, tipo, gen_por_mes, cap)."""
    meses = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO",
             "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "GeneraciónReal"
    for _ in range(3):
        ws.append([])
    fila4 = [None] * 6
    fila5 = ["Departamento", "Municipio", "Agente", "Codigo Submercado Generación", "Recurso", "Tipo Generación"]
    for m in meses:
        fila4 += [m, None]
        fila5 += ["Generación", "Capacidad Efectiva Neta"]
    ws.append(fila4)
    ws.append(fila5)
    for agente, codigo, nombre, tipo, gen, cap in recursos:
        ws.append(["ANTIOQUIA", "MEDELLIN", agente, codigo, nombre, tipo, *[v for _ in meses for v in (gen, cap)]])
    return _xlsx(wb)


# --- lector, formato diario ----------------------------------------------------

def test_diario_suma_horas_dias_y_combustibles_exacto():
    filas = mes_completo("HID1", 2026, 2, 0.1)  # 0,1 en float no es exacto
    filas += [(date(2026, 2, 1), "TER1", "TERMICA", "TERG", 1, "TXF", "GAS"),
              (date(2026, 2, 1), "TER1", "TERMICA", "TERG", 2, "TXF", "GAS NI")]
    lect = leer_xm(diario(filas), "Generacion_(kWh)_2026.xlsx")
    assert (lect.formato, lect.anio, lect.trae_version) == ("diario", 2026, True)
    hid = next(f for f in lect.filas if f.codigo_submercado == "HID1")
    assert hid.generacion_kwh == D("67.2")  # 0,1 × 24 × 28, sin error de float
    assert (hid.dias_reportados, hid.dias_archivo, hid.dias_mes, hid.completo) == (28, 28, 28, True)
    ter = next(f for f in lect.filas if f.codigo_submercado == "TER1")
    assert ter.generacion_kwh == D(72)  # dos combustibles el mismo día se suman


def test_diario_mes_que_el_archivo_no_cubre_entero_queda_incompleto():
    filas = [(date(2026, 11, d), "HID1", "HIDRAULICA", "SINC", 10, "TX1") for d in range(1, 12)]
    lect = leer_xm(diario(filas), "x_2026.xlsx")
    f = lect.filas[0]
    assert (f.dias_archivo, f.dias_mes, f.completo) == (11, 30, False)
    assert any("11 de 30 días" in a for a in lect.avisos)


def test_diario_dia_sin_generacion_de_una_central_no_bloquea_pero_se_senala():
    filas = mes_completo("HID1", 2026, 9, 5) + mes_completo("HID2", 2026, 9, 5, saltar={3, 4})
    lect = leer_xm(diario(filas), "x_2026.xlsx")
    h2 = next(f for f in lect.filas if f.codigo_submercado == "HID2")
    assert h2.completo and h2.dias_sin_reporte == 2
    assert h2.generacion_kwh == D(5 * 24 * 28)


def test_diario_dia_con_horas_vacias_no_cuenta_como_reportado():
    horas = [1] * 23 + [None]
    filas = mes_completo("HID1", 2026, 9, 1)
    filas[0] = (date(2026, 9, 1), "HID1", "HIDRAULICA", "SINC", horas, "TXF")
    f = leer_xm(diario(filas), "x_2026.xlsx").filas[0]
    assert (f.dias_reportados, f.dias_sin_reporte) == (29, 1)


def test_diario_sin_columna_version_avisa():
    lect = leer_xm(diario(mes_completo("HID1", 2026, 9, 1), version_col=False), "x_2026.xlsx")
    assert not lect.trae_version
    assert any("Version" in a for a in lect.avisos)


def test_archivo_sin_formato_conocido_se_rechaza():
    wb = openpyxl.Workbook()
    wb.active.append(["cualquier", "cosa"])
    with pytest.raises(ArchivoXMInvalido):
        leer_xm(_xlsx(wb), "otro_2026.xlsx")


# --- archivos dañados (fallo corregido el 2026-11-15) ------------------------------
# Antes, un archivo que no era Excel (BadZipFile) o una celda con texto
# (InvalidOperation) tumbaban la pantalla Generación XM con un error técnico.

def test_un_archivo_que_no_es_excel_se_informa_sin_tumbar_la_pantalla():
    with pytest.raises(ArchivoXMInvalido, match="no es un archivo de Excel"):
        leer_xm(b"esto no es un xlsx", "x_2026.xlsx")
    lectura, error = leer_archivo(b"esto no es un xlsx", "x_2026.xlsx")
    assert lectura is None and "«x_2026.xlsx» no es un archivo de Excel" in error


def test_una_celda_que_no_es_cifra_dice_donde_esta():
    filas = mes_completo("HID1", 2026, 2, 1)
    filas[3] = (filas[3][0], "HID1", "HIDRAULICA", "SING", [1] * 23 + ["n.d."], "TXF")
    with pytest.raises(ArchivoXMInvalido, match="HID1 del 2026-02-04: «n.d.» no es una cifra"):
        leer_xm(diario(filas), "x_2026.xlsx")
    contenido = mensual(2025, [("EMPRESA", "HID1", "HIDRO UNO", "HIDRAULICA", "sin dato", 20000)])
    lectura, error = leer_archivo(contenido, "G_2025.xlsx")
    assert lectura is None and "HID1, enero: «sin dato» no es una cifra" in error
    assert "ni el mensual" not in error, "no debe decir que el formato es desconocido"


def test_un_error_inesperado_al_leer_tambien_se_informa(monkeypatch):
    def falla(*_):
        raise RuntimeError("algo raro")
    monkeypatch.setattr(cargue_xm, "leer_xm", falla)
    lectura, error = leer_archivo(b"...", "x_2026.xlsx")
    assert lectura is None and "RuntimeError: algo raro" in error


# --- lector, formato mensual ----------------------------------------------------

def test_mensual_lee_generacion_y_capacidad():
    contenido = mensual(2025, [("EMPRESA GRANDE", "HID1", "HIDRO UNO", "HIDRAULICA", 1000.5, 20000)])
    lect = leer_xm(contenido, "GeneraciónReal_2025.xlsx")
    assert (lect.formato, lect.anio, lect.trae_version, len(lect.filas)) == ("mensual", 2025, False, 12)
    f = lect.filas[0]
    assert (f.generacion_kwh, f.capacidad_kw, f.dias_reportados, f.completo) == (D("1000.5"), D(20000), None, True)


# --- cargue ---------------------------------------------------------------------

def test_se_guarda_con_la_version_del_archivo_y_no_se_carga_dos_veces(con):
    lect = leer_xm(diario(mes_completo("HID1", 2026, 9, 1, version="TX3")), "x_2026.xlsx")
    carga = guardar_lectura(con, lect)
    g = con.execute("SELECT * FROM generacion WHERE carga_id = ?", (carga,)).fetchone()
    assert (g["version_xm"], g["periodo"], g["generacion_kwh"], g["dias_archivo"]) == ("TX3", "2026-09", "720", 30)
    assert con.execute("SELECT formato, version_declarada FROM cargas_xm").fetchone()[:] == ("diario", None)
    with pytest.raises(ErrorCargue, match="ya se cargó"):
        guardar_lectura(con, lect)


def test_si_el_archivo_trae_version_no_se_declara(con):
    lect = leer_xm(diario(mes_completo("HID1", 2026, 9, 1)), "x_2026.xlsx")
    with pytest.raises(ErrorCargue, match="no se declara"):
        guardar_lectura(con, lect, version_declarada="TX1")


def test_sin_version_en_el_archivo_hay_que_declararla(con):
    lect = leer_xm(mensual(2025, [("E", "HID1", "H", "HIDRAULICA", 1, 20000)]), "G_2025.xlsx")
    with pytest.raises(ErrorCargue, match="declárela"):
        guardar_lectura(con, lect)
    carga = guardar_lectura(con, lect, version_declarada="TXF")
    assert con.execute("SELECT version_declarada FROM cargas_xm WHERE id = ?", (carga,)).fetchone()[0] == "TXF"


def test_version_que_no_esta_en_el_catalogo_se_rechaza(con):
    lect = leer_xm(diario(mes_completo("HID1", 2026, 9, 1, version="TXR")), "x_2026.xlsx")
    with pytest.raises(ErrorCargue, match="TXR"):
        guardar_lectura(con, lect)
    assert con.execute("SELECT count(*) FROM cargas_xm").fetchone()[0] == 0


def test_mes_con_versiones_mezcladas_se_guarda_con_la_menos_avanzada(con):
    filas = [(date(2026, 11, d), "HID1", "HIDRAULICA", "SINC", 1, "TX2" if d < 5 else "TX1") for d in range(1, 8)]
    guardar_lectura(con, leer_xm(diario(filas), "x_2026.xlsx"))
    assert con.execute("SELECT version_xm FROM generacion").fetchone()[0] == "TX1"


def test_mes_incompleto_no_entrega_generacion_para_liquidar(con):
    filas = [(date(2026, 11, d), "SINT1", "HIDRAULICA", "SINC", 1, "TX1") for d in range(1, 12)]
    guardar_lectura(con, leer_xm(diario(filas), "x_2026.xlsx"))
    with pytest.raises(repo.MesIncompleto, match="11 de 30"):
        repo.generacion_para(con, "SINT1", "2026-11", "preliminar")


def test_mes_cubierto_con_dias_sin_generacion_si_entrega_generacion(con):
    filas = mes_completo("SINT1", 2026, 9, 2, saltar={1}) + mes_completo("OTRA", 2026, 9, 1)
    guardar_lectura(con, leer_xm(diario(filas), "x_2026.xlsx"))
    assert repo.generacion_para(con, "SINT1", "2026-09", "oficial")["generacion_kwh"] == str(2 * 24 * 29)


# --- umbral por empresa y cruce ---------------------------------------------------

def _con_capacidad_2025(con):
    contenido = mensual(2025, [
        ("EMPRESA GRANDE", "SINT1", "PCH SINTÉTICA", "HIDRAULICA", 100, 9000),
        ("EMPRESA GRANDE", "TER9", "TÉRMICA GRANDE", "TERMICA", 100, 50000),
        ("EMPRESA CHICA", "CHI1", "PCH CHICA", "HIDRAULICA", 100, 9000),
    ])
    return guardar_lectura(con, leer_xm(contenido, "G_2025.xlsx"), version_declarada="TXF")


def test_el_umbral_es_por_empresa_y_2026_toma_la_capacidad_de_2025(con):
    _con_capacidad_2025(con)
    grande = sujecion_central(con, "SINT1", 2026)  # 9.000 kW la central, 59.000 kW la empresa
    assert (grande.sujeta, grande.anio_capacidad, grande.capacidad_total_kw) == (True, 2025, D(59000))
    assert sujecion_central(con, "CHI1", 2026).sujeta is False
    assert sujecion_central(con, "NUEVA", 2026) is None   # sin capacidad: no se supone
    assert sujecion_central(con, "SINT1", 2024) is None   # no se usa capacidad de un año posterior


def test_resumen_cruza_centrales_con_proyectos(con, proyecto):
    repo.crear_proyecto(con, "Proyecto gemelo", codigo_submercado="SINT1")   # central compartida
    repo.crear_proyecto(con, "Proyecto sin XM", codigo_submercado="NOESTA")
    repo.crear_proyecto(con, "Proyecto sin central")
    _con_capacidad_2025(con)
    filas = (mes_completo("SINT1", 2026, 9, 1) + mes_completo("CHI1", 2026, 9, 1)
             + mes_completo("SOL1", 2026, 9, 1, tipo="SOLAR"))
    r = resumen_carga(con, guardar_lectura(con, leer_xm(diario(filas), "x_2026.xlsx")))
    assert [(c.codigo, c.proyectos, c.estado_umbral) for c in r.con_proyecto] == \
        [("SINT1", ["PCH SINTÉTICA", "Proyecto gemelo"], "sujeta")]
    assert [c.codigo for c in r.hidraulicas_sin_proyecto] == ["CHI1"]   # la solar no aparece
    assert [c.codigo for c in r.bajo_umbral] == ["CHI1"]
    assert r.proyectos_sin_generacion == [{"proyecto": "Proyecto sin XM", "codigo": "NOESTA"}]
    assert r.proyectos_sin_central == ["Proyecto sin central"]
    assert r.periodos[0]["versiones"] == {"TXF"}


# --- archivo real ---------------------------------------------------------------

REAL_2026 = (dir_xm() / "Generacion_(kWh)_2026.xlsx") if dir_xm() else None


@pytest.mark.skipif(not (REAL_2026 and REAL_2026.exists()), reason="no está el archivo real de XM 2026")
def test_archivo_real_2026():
    lect = leer_xm(REAL_2026.read_bytes(), REAL_2026.name)
    assert lect.formato == "diario" and lect.anio == 2026
    versiones = lect.versiones_por_mes()
    assert versiones["2026-01"] == {"TX5"} and versiones["2026-10"] == {"TXF"}
    assert versiones["2026-11"] == {"TX1", "TX2"} and lect.dias_en_archivo["2026-11"] == 11
    guatron = {f.periodo: f for f in lect.filas if f.codigo_submercado == "GTRG"}
    assert guatron["2026-10"].generacion_kwh == D("158883631.40")
    assert all(f.generacion_kwh is not None for f in lect.filas)
