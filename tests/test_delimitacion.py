"""
Captura y validación de delimitaciones (paso 3).

Cifras REALES, transcritas de las delimitaciones del IGAC bajo el Decreto 800
(insumos/delimitaciones-igac/), salvo que se diga lo contrario:
  * Guavio   — 2420SGEO-2026-0001188-EE: con páramo; CORPOCHIVOR todo N/A;
               la CAR con S1 = N/A y S2 > 0.
  * Betania  — 2400DGIG-2026-0000630-EE: con páramo y una fila «autoridad
               ambiental urbana o área en litigio» con S2 = 2.506,64547 ha.
  * Guatapé  — 2420SGEO-2026-0001189-EE: sin páramo y sin columna S2.
  * El Edén  — oficio de 2023 (Decreto 644): solo S1; el PNN figura sin área.
"""
from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

import pytest

from datos import repositorio as repo
from motor import delimitacion as dm
from motor.delimitacion import SIN_ASIGNAR, Delimitacion, FilaDelimitacion as F_, numero_desde_texto as n
from motor.parametros import PARAMETROS_VIGENTES

D = Decimal
ALFA = Fraction(PARAMETROS_VIGENTES.alpha)
SIGLAS = {"PNNChingaza", "CORPOCHIVOR", "CORPOGUAVIO", "CAR", "CAM", "CRC", "PNNAlto Fragua  Indi-Wasi",
          "PNNSerrania de los Churumbelos", "PNNCueva de los Guacharos", "PNNPuracé", "PNNNevado del Huila",
          "CORNARE", "CORANTIOQUIA", "CORPOCALDAS", "PNNSelva de Florencia"}


def guavio(**kw):
    base = dict(
        modo="hectareas", trae_s2=True, paramo_declarado=True,
        s1_t_declarado=n("82.017,00034"), s2_t_declarado=n("108.297,21898"),
        filas=(
            F_("PNNChingaza", n("4.170,87048"), n("6.786,02319")),
            F_("CORPOCHIVOR", None, None),
            F_("CORPOGUAVIO", n("77.846,12986"), n("93.208,98127")),
            F_("CAR", None, n("8.302,21452")),
        ),
    )
    base.update(kw)
    return Delimitacion(**base)


def betania():
    return Delimitacion(
        modo="hectareas", trae_s2=True, paramo_declarado=True,
        s1_t_declarado=n("86.537,28373"), s2_t_declarado=n("266.302,77501"),
        filas=(
            F_("CAM", n("86.537,28373"), n("98.976,41696")),
            F_("CRC", None, n("66.860,11153")),
            F_("PNNAlto Fragua  Indi-Wasi"),
            F_("PNNSerrania de los Churumbelos"),
            F_("PNNCueva de los Guacharos"),
            F_("PNNPuracé", None, n("48.302,63666")),
            F_("PNNNevado del Huila", None, n("49.656,96439")),
            F_(SIN_ASIGNAR, None, n("2.506,64547")),
        ),
    )


def guatape():
    return Delimitacion(
        modo="hectareas", trae_s2=False, paramo_declarado=False,
        s1_t_declarado=n("49.192,28553"),
        filas=(F_("CORNARE", n("49.192,28553")), F_("CORANTIOQUIA")),
    )


def validar(d, enlazado=True, vigencia="2026-12-01"):
    return dm.validar(d, SIGLAS, proyecto_enlazado=enlazado, vigente_desde=vigencia)


# --- lectura de cifras --------------------------------------------------------

def test_lee_cifras_en_formato_colombiano_y_na():
    assert n("86.537,28373") == D("86537.28373")
    assert n("86537.28373") == D("86537.28373")
    assert n("1.312.351") == D("1312351")
    assert n("N/A") is None and n("") is None and n(None) is None
    with pytest.raises(dm.NumeroInvalido):
        n(86537.28373)
    with pytest.raises(dm.NumeroInvalido):
        n("ochenta")


def test_presenta_en_formato_colombiano():
    assert dm.texto_colombiano(D("86537.28373")) == "86.537,28373"
    assert dm.texto_colombiano(None) == "N/A"


# --- casos reales que cuadran ------------------------------------------------------

def test_guavio_cuadra_y_aplica_formula_2():
    r = validar(guavio())
    assert r.ok, r.errores
    razones = {x.autoridad_id: x.razon for x in dm.razones(guavio())}
    assert sum(razones.values()) == 1
    assert dm.formula(guavio()) == 2
    # N/A vale 0 ha: CORPOCHIVOR no recibe nada, la CAR solo por S2.
    assert razones["CORPOCHIVOR"] == 0
    assert razones["CAR"] == ALFA * Fraction("8302.21452") / Fraction("108297.21898")
    assert any("CORPOCHIVOR" in a and "0 %" in a for a in r.avisos)


def test_betania_retiene_la_parte_urbana_o_en_litigio():
    r = validar(betania())
    assert r.ok, r.errores
    razones = {x.autoridad_id: x for x in dm.razones(betania())}
    assert sum(x.razon for x in razones.values()) == 1
    retenido = razones[SIN_ASIGNAR].razon
    assert retenido == ALFA * Fraction("2506.64547") / Fraction("266302.77501")
    assert any("retenido sin asignar" in a for a in r.avisos)


def test_guatape_sin_columna_s2_aplica_formula_1():
    r = validar(guatape())
    assert r.ok, r.errores
    assert dm.formula(guatape()) == 1
    assert {x.autoridad_id: x.razon for x in dm.razones(guatape())} == {"CORNARE": 1, "CORANTIOQUIA": 0}


def test_el_eden_un_solo_beneficiario_con_s1():
    """Oficio de El Edén (Decreto 644): CORPOCALDAS S1 = 2.900,92344;
    PNN Selva de Florencia figura sin intersección (N/A)."""
    d = Delimitacion(modo="hectareas", s1_t_declarado=n("2.900,92344"),
                     filas=(F_("CORPOCALDAS", n("2.900,92344")), F_("PNNSelva de Florencia")))
    assert validar(d).ok
    assert {x.autoridad_id: x.razon for x in dm.razones(d)} == {"CORPOCALDAS": 1, "PNNSelva de Florencia": 0}


# --- lo que no cuadra se muestra, no se ajusta -------------------------------------

def test_total_que_no_cuadra_muestra_la_diferencia():
    r = validar(guavio(s2_t_declarado=n("108.297,21900")))   # el del cuadro de la 1.ª página
    assert not r.ok
    assert any("diferencia -0,00002" in e for e in r.errores), r.errores


def test_s2_menor_que_s1():
    d = guavio(filas=(F_("PNNChingaza", n("4.170,87048"), n("4.000")),) + guavio().filas[1:],
               s2_t_declarado=None)
    assert any("menor que S1" in e for e in validar(d).errores)


def test_paramo_declarado_sin_columna_s2():
    d = Delimitacion(modo="hectareas", trae_s2=False, paramo_declarado=True,
                     filas=(F_("CORNARE", D("10")),), s1_t_declarado=D("10"))
    assert any("no trae la columna S2" in e for e in validar(d).errores)


def test_sin_paramo_s2_debe_ser_igual_a_s1():
    d = Delimitacion(modo="hectareas", trae_s2=True, paramo_declarado=False,
                     filas=(F_("CORNARE", D("10"), D("12")),), s1_t_declarado=D("10"), s2_t_declarado=D("12"))
    assert any("debería ser igual a S1" in e for e in validar(d).errores)


def test_cero_digitado_no_es_na():
    d = guatape()
    d = Delimitacion(**{**d.__dict__, "filas": (F_("CORNARE", n("49.192,28553")), F_("CORANTIOQUIA", D("0")))})
    assert any("mayor que 0 o N/A" in e for e in validar(d).errores)


def test_autoridad_fuera_del_maestro_proyecto_sin_enlazar_y_sin_vigencia():
    d = Delimitacion(modo="hectareas", filas=(F_("CVS", D("10")),), s1_t_declarado=D("10"))
    errores = " | ".join(validar(d, enlazado=False, vigencia=None).errores)
    assert "«CVS» no existe en el maestro" in errores
    assert "no está enlazado a una central de XM" in errores
    assert "Falta la fecha de vigencia" in errores


def test_vigencia_a_mitad_de_mes_avisa_mes_parcial():
    r = validar(guavio(), vigencia="2026-11-11")
    assert r.ok
    assert any("mes parcial" in a for a in r.avisos)


def test_modo_porcentaje_exige_100_exacto():
    ok = Delimitacion(modo="porcentaje", filas=(F_("CAM", porcentaje=Fraction(2, 3) * 100),
                                                 F_("CRC", porcentaje=Fraction(1, 3) * 100)))
    assert validar(ok).ok
    malo = Delimitacion(modo="porcentaje", filas=(F_("CAM", porcentaje=Fraction("66.66667")),
                                                   F_("CRC", porcentaje=Fraction("33.33334"))))
    assert any("no 100 %" in e and "diferencia 0,00001" in e for e in validar(malo).errores)


# --- en el repositorio: borrador → confirmar ----------------------------------------

def _guardar_guavio(con, vigente_desde=None, enlazado=True, pdf=True):
    p = repo.crear_proyecto(con, "Guavio", codigo_submercado="GVIO" if enlazado else None)
    d = guavio()
    return p, repo.crear_delimitacion(
        con, p, "2420SGEO-2026-0001188-EE", "2026-11-11", vigente_desde, "hectareas",
        [{"autoridad": f.autoridad, "s1_ha": f.s1_ha, "s2_ha": f.s2_ha} for f in d.filas],
        pdf_nombre="Guavio.pdf", pdf_sha256=("c" * 64) if pdf else None,
        trae_s2=True, paramo_declarado=True, s1_t_declarado=d.s1_t_declarado,
        s2_t_declarado=d.s2_t_declarado, origen="precarga",
    )


def test_el_na_se_guarda_null_y_vuelve_igual(con):
    _, d = _guardar_guavio(con)
    filas = {f["autoridad_sigla"]: f for f in repo.filas_delimitacion(con, d)}
    assert filas["CORPOCHIVOR"]["s1_ha"] is None and filas["CORPOCHIVOR"]["s2_ha"] is None
    assert repo.delimitacion_en_memoria(con, d) == guavio()


def test_confirmar_sin_vigencia_no_cambia_nada(con):
    _, d = _guardar_guavio(con)
    r = repo.confirmar_delimitacion(con, d)
    assert not r.ok and any("vigencia" in e for e in r.errores)
    assert con.execute("SELECT estado FROM delimitaciones WHERE id=?", (d,)).fetchone()[0] == "borrador"


def test_confirmar_exige_pdf_y_central(con):
    _, d = _guardar_guavio(con, enlazado=False, pdf=False)
    errores = " | ".join(repo.confirmar_delimitacion(con, d, vigente_desde="2026-12-01").errores)
    assert "Falta el PDF" in errores and "central de XM" in errores


def test_confirmar_con_vigencia_la_deja_vigente_y_aplica_desde_ese_mes(con):
    p, d = _guardar_guavio(con)
    r = repo.confirmar_delimitacion(con, d, vigente_desde="2026-12-01")
    assert r.ok, r.errores
    assert repo.delimitacion_aplicable(con, p, "2026-11") is None
    assert repo.delimitacion_aplicable(con, p, "2026-12")["id"] == d


def test_una_confirmada_no_se_edita(con):
    _, d = _guardar_guavio(con)
    repo.confirmar_delimitacion(con, d, vigente_desde="2026-12-01")
    with pytest.raises(repo.ErrorRepositorio, match="no se edita"):
        repo.actualizar_borrador(con, d, radicado="otro")


def test_corregir_un_borrador(con):
    _, d = _guardar_guavio(con)
    repo.actualizar_borrador(con, d, filas=[{"autoridad": "CORPOGUAVIO", "s1_ha": D("1")}],
                             trae_s2=False, paramo_declarado=False, s1_t_declarado=D("1"), s2_t_declarado=None)
    assert len(repo.filas_delimitacion(con, d)) == 1
    assert repo.validar_delimitacion(con, d).errores == ["Falta la fecha de vigencia."]


def test_sin_asignar_existe_en_la_base(con):
    p = repo.crear_proyecto(con, "Betania", codigo_submercado="CHBG")
    d = repo.crear_delimitacion(
        con, p, "2400DGIG-2026-0000630-EE", "2026-11-11", "2026-12-01", "hectareas",
        [{"autoridad": f.autoridad, "s1_ha": f.s1_ha, "s2_ha": f.s2_ha} for f in betania().filas],
        pdf_sha256="d" * 64, trae_s2=True, paramo_declarado=True,
        s1_t_declarado=betania().s1_t_declarado, s2_t_declarado=betania().s2_t_declarado,
    )
    assert repo.confirmar_delimitacion(con, d).ok


def test_el_residuo_de_centavos_nunca_va_a_la_parte_sin_asignar():
    """Sintético: SIN_ASIGNAR tiene la mayor participación y aun así el
    residuo de centavos va a la mayor autoridad real (en empate, la primera
    por sigla)."""
    from motor.liquidacion import liquidar_central
    d = dm.Delimitacion(modo="hectareas", filas=(
        dm.FilaDelimitacion(SIN_ASIGNAR, D("3")), dm.FilaDelimitacion("CORNARE", D("2")),
        dm.FilaDelimitacion("CVC", D("2"))))
    k = liquidar_central(D("1000001"), D("189.02930"), d)
    assert sum(f.monto for f in k.filas) == k.bolsa_car_pnn
    assert [f.autoridad for f in k.filas if f.ajuste_residuo] == ["CORNARE"]


# --- centrales que XM agrupa en un solo código (Jorge, 2026-11-14) ------------------
# Cifras reales: Guadalupe III (2420SGEO-2026-0001192-EE) y Troneras
# (2420SGEO-2026-0001191-EE), ambas 100 % CORANTIOQUIA, central GUATRON (GTRG).

def _en_central(con, nombre, radicado, filas, codigo="GTRG", vigente_desde="2026-12-01", **kw):
    p = repo.crear_proyecto(con, nombre, codigo_submercado=codigo)
    s1 = sum((f.get("s1_ha") or 0 for f in filas), D(0))
    s2 = sum((f.get("s2_ha") or 0 for f in filas), D(0)) if kw.get("trae_s2") else None
    return p, repo.crear_delimitacion(con, p, radicado, min("2026-11-11", vigente_desde), vigente_desde, "hectareas", filas,
                                      pdf_sha256="e" * 64, s1_t_declarado=s1, s2_t_declarado=s2, **kw)


def test_central_compartida_con_el_mismo_reparto_se_confirma(con):
    _, g3 = _en_central(con, "Guadalupe III", "2420SGEO-2026-0001192-EE",
                        [{"autoridad": "CORANTIOQUIA", "s1_ha": n("46.695,78962")}],
                        trae_s2=False, paramo_declarado=False)
    _, tr = _en_central(con, "Troneras", "2420SGEO-2026-0001191-EE",
                        [{"autoridad": "CORANTIOQUIA", "s1_ha": n("47.114,19977"), "s2_ha": n("47.114,19977")}],
                        trae_s2=True, paramo_declarado=False)
    assert repo.confirmar_delimitacion(con, g3).ok
    r = repo.confirmar_delimitacion(con, tr)
    assert r.ok, r.errores
    assert any("se liquidan juntas, una sola vez" in a for a in r.avisos)


def test_central_compartida_con_reparto_distinto_se_bloquea(con):
    """Sintético: si los repartos difieren, agrupar cambiaría los montos."""
    _, a = _en_central(con, "Planta A", "RAD-A", [{"autoridad": "CORANTIOQUIA", "s1_ha": D("10")}], codigo="SINT9")
    _, b = _en_central(con, "Planta B", "RAD-B", [{"autoridad": "CORANTIOQUIA", "s1_ha": D("5")},
                                                  {"autoridad": "CORNARE", "s1_ha": D("5")}], codigo="SINT9")
    r = repo.confirmar_delimitacion(con, a)                    # B aún es borrador: solo aviso
    assert r.ok and any("reparte distinto" in x for x in r.avisos)
    r = repo.confirmar_delimitacion(con, b)                    # A ya es vigente: se bloquea
    assert not r.ok and any("reparten distinto" in e for e in r.errores)


def test_reparto_distinto_no_dice_a_la_vez_mismo_reparto(con):
    """Fallo corregido el 2026-11-15: el aviso «Mismo reparto» se decidía
    buscando «reparte distinto» en los mensajes, pero el error dice «reparten
    distinto», así que salían los dos a la vez."""
    _, a = _en_central(con, "Planta A", "RAD-A", [{"autoridad": "CORANTIOQUIA", "s1_ha": D("10")}], codigo="SINT9")
    _, b = _en_central(con, "Planta B", "RAD-B", [{"autoridad": "CORANTIOQUIA", "s1_ha": D("5")},
                                                  {"autoridad": "CORNARE", "s1_ha": D("5")}], codigo="SINT9")
    assert repo.confirmar_delimitacion(con, a).ok
    r = repo.validar_delimitacion(con, b)
    assert any("reparten distinto" in e for e in r.errores)
    assert not any("Mismo reparto" in x for x in r.avisos)

    # «Mismo reparto» solo se afirma de las que se compararon: no de un proyecto sin delimitación.
    _, c = _en_central(con, "Planta C", "RAD-C", [{"autoridad": "CVC", "s1_ha": D("3")}], codigo="SINT8")
    assert repo.confirmar_delimitacion(con, c).ok
    repo.crear_proyecto(con, "Planta sin delimitar", codigo_submercado="SINT8")
    _, e = _en_central(con, "Planta E", "RAD-E", [{"autoridad": "CVC", "s1_ha": D("7")}], codigo="SINT8")
    aviso = next(x for x in repo.validar_delimitacion(con, e).avisos if "Mismo reparto" in x)
    assert "Planta C" in aviso and "sin delimitar" not in aviso


def test_una_central_compartida_no_se_liquida_dos_veces(con):
    from tests.conftest import cabecera
    pa, a = _en_central(con, "Planta A", "RAD-A", [{"autoridad": "CVC", "s1_ha": D("10")}],
                        codigo="SINT1", vigente_desde="2025-01-01")
    pb, b = _en_central(con, "Planta B", "RAD-B", [{"autoridad": "CVC", "s1_ha": D("7")}],
                        codigo="SINT1", vigente_desde="2025-01-01")
    assert repo.confirmar_delimitacion(con, a).ok and repo.confirmar_delimitacion(con, b).ok
    from tests.conftest import cargar_xm
    carga = cargar_xm(con, "TXF")
    repo.guardar_liquidacion(con, cabecera(pa, a, carga), [])
    with pytest.raises(repo.ErrorRepositorio, match="una sola vez por central"):
        repo.guardar_liquidacion(con, cabecera(pb, b, carga), [])
