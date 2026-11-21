"""
Delimitación del IGAC en memoria: validaciones y razones de reparto (paso 3).

Sin base de datos ni interfaz (CLAUDE.md, regla 10). `datos/repositorio.py`
arma un `Delimitacion` desde SQLite y la pantalla lo muestra; las reglas viven
aquí, con pruebas propias.

Lo que se aprendió de las 37 delimitaciones del Decreto 800 (radicadas por el
IGAC el 11-11-2026) y que este módulo recoge:

* Traen hectáreas S1_i y S2_i por autoridad, nunca porcentajes. El modo
  `porcentaje` se conserva por si llega otro formato.
* Una celda «N/A» es una declaración del IGAC (no hay intersección en esa
  jurisdicción), no un dato faltante. Se guarda como `None` y en la fórmula
  vale 0 ha. Ejemplo: la CRC en Betania tiene S1 = N/A y S2 = 66.860 ha.
* «S2 para todas o ninguna» se aplica a la COLUMNA: o la delimitación trae la
  columna S2 (`trae_s2`) o no la trae (7 proyectos sin páramo). Dentro de la
  columna, una fila puede ser N/A.
* Sin páramo, S2_i = S1_i, así que Fórmula 1 y Fórmula 2 dan lo mismo.
* La fila «Autoridad ambiental urbana o área en litigio» no es una autoridad
  del maestro. Su parte se calcula y se RETIENE sin asignar (sigla
  `SIN_ASIGNAR`, decisión de Jorge, 2026-11-13): no se reparte entre las demás.
* En las 37, la suma de las filas es exactamente la fila TOTAL de la tabla por
  autoridad. Esa fila TOTAL es el `s1_t_declarado` / `s2_t_declarado` contra
  el que se valida. (El cuadro de áreas totales de la primera página puede
  diferir en la 5.ª cifra decimal; el IGAC lo advierte en sus notas.)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from motor.parametros import ParametrosDecreto800, PARAMETROS_VIGENTES
from motor.tse_calculator import AreaAutoridad, RazonReparto, calcular_razones_de_reparto, presentar

SIN_ASIGNAR = "SIN_ASIGNAR"
CERO = Decimal("0")
NA = "N/A"


class NumeroInvalido(ValueError):
    pass


# ---------------------------------------------------------------------------
# Números como los escribe el IGAC: «86.537,28373», «N/A»
# ---------------------------------------------------------------------------

def numero_desde_texto(texto) -> Decimal | None:
    """Lee una cifra digitada. Acepta formato colombiano («86.537,28373») o
    con punto decimal («86537.28373»). Vacío o «N/A» → None. Nunca float."""
    if texto is None:
        return None
    if isinstance(texto, float):
        raise NumeroInvalido(f"Valor no exacto rechazado: {texto!r}")
    if isinstance(texto, (Decimal, int)):
        return Decimal(texto)
    s = str(texto).strip().replace(" ", "").replace(" ", "")
    if s == "" or s.upper() in ("N/A", "NA"):
        return None
    if "," in s:                      # formato colombiano: el punto es de miles
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1:            # «1.312.351» sin decimales
        s = s.replace(".", "")
    try:
        return Decimal(s)
    except InvalidOperation as e:
        raise NumeroInvalido(f"«{texto}» no es una cifra válida") from e


def texto_colombiano(valor: Decimal | None, decimales: int = 5) -> str:
    """Decimal → «86.537,28373». None → «N/A»."""
    if valor is None:
        return NA
    q = presentar(Decimal(valor), decimales) if decimales is not None else Decimal(valor)
    entero, _, frac = f"{q:,.{decimales}f}".partition(".")
    return entero.replace(",", ".") + ("," + frac if frac else "")


# ---------------------------------------------------------------------------
# Estructuras
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FilaDelimitacion:
    autoridad: str                      # sigla del maestro, o SIN_ASIGNAR
    s1_ha: Decimal | None = None        # None = N/A en la delimitación
    s2_ha: Decimal | None = None
    porcentaje: Fraction | None = None  # solo modo porcentaje


@dataclass(frozen=True)
class Delimitacion:
    modo: str                           # 'hectareas' | 'porcentaje'
    filas: tuple[FilaDelimitacion, ...]
    trae_s2: bool = False
    paramo_declarado: bool | None = None   # «existe intersección entre páramos…»
    s1_t_declarado: Decimal | None = None  # fila TOTAL de la tabla por autoridad
    s2_t_declarado: Decimal | None = None


@dataclass
class ResultadoValidacion:
    errores: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errores


def _ha(v: Decimal | None) -> Decimal:
    return CERO if v is None else v


def _fmt(v: Decimal) -> str:
    return texto_colombiano(v, 5)


# ---------------------------------------------------------------------------
# Validaciones (CLAUDE.md, regla 4). La app no ajusta sola: muestra la diferencia.
# ---------------------------------------------------------------------------

def validar(
    d: Delimitacion,
    autoridades_validas: set[str],
    proyecto_enlazado: bool,
    vigente_desde: str | None,
    fecha_documento: str | None = None,
) -> ResultadoValidacion:
    r = ResultadoValidacion()
    E, A = r.errores.append, r.avisos.append

    if not proyecto_enlazado:
        E("El proyecto no está enlazado a una central de XM.")
    if not vigente_desde:
        E("Falta la fecha de vigencia.")
    else:
        try:
            v = date.fromisoformat(vigente_desde)
            # Las delimitaciones rigen hacia adelante, no hacia atrás (Jorge,
            # 2026-11-14): no pueden regir antes de la fecha del documento.
            if fecha_documento and vigente_desde < fecha_documento:
                E(f"Rige desde {v.strftime('%d/%m/%Y')}, antes de la fecha del documento "
                  f"({date.fromisoformat(fecha_documento).strftime('%d/%m/%Y')}): las delimitaciones rigen "
                  f"hacia adelante, no hacia atrás.")
            if v.day != 1:
                A(f"Rige desde el {v.day} de su mes: ese mes se liquida solo del día {v.day} al último "
                  f"(mes parcial), con la generación diaria de XM de esos días.")
        except ValueError:
            E(f"Fecha de vigencia inválida: «{vigente_desde}» (use AAAA-MM-DD).")

    if not d.filas:
        E("La delimitación no tiene filas por autoridad.")
        return r

    vistas: set[str] = set()
    for f in d.filas:
        if f.autoridad in vistas:
            E(f"La autoridad {f.autoridad} aparece dos veces.")
        vistas.add(f.autoridad)
        if f.autoridad != SIN_ASIGNAR and f.autoridad not in autoridades_validas:
            E(f"La autoridad «{f.autoridad}» no existe en el maestro de autoridades.")

    if d.modo == "porcentaje":
        _validar_porcentajes(d, E)
    elif d.modo == "hectareas":
        _validar_hectareas(d, E, A)
    else:
        E(f"Modo desconocido: {d.modo}")

    if r.ok:
        for rz in razones(d):
            if rz.autoridad_id == SIN_ASIGNAR and rz.razon > 0:
                A(f"El {texto_colombiano(rz.porcentaje, 5)} % corresponde a «autoridad ambiental urbana "
                  f"o área en litigio»: se calcula y queda retenido sin asignar.")
            elif rz.razon == 0:
                A(f"{rz.autoridad_id} figura en la delimitación sin área que reparta: 0 %.")
    return r


def _validar_porcentajes(d: Delimitacion, E) -> None:
    total = Fraction(0)
    for f in d.filas:
        if f.porcentaje is None:
            E(f"{f.autoridad}: falta el porcentaje.")
        elif f.porcentaje < 0:
            E(f"{f.autoridad}: porcentaje negativo.")
        else:
            total += f.porcentaje
    if total != 100:
        dif = Decimal(total.numerator) / Decimal(total.denominator) - 100
        E(f"Los porcentajes suman {texto_colombiano(Decimal(total.numerator) / Decimal(total.denominator), 5)} %, "
          f"no 100 %: diferencia {texto_colombiano(dif, 5)} puntos.")


def _validar_hectareas(d: Delimitacion, E, A) -> None:
    for f in d.filas:
        for nombre, v in (("S1", f.s1_ha), ("S2", f.s2_ha)):
            if v is not None and v <= 0:
                E(f"{f.autoridad}: {nombre} debe ser mayor que 0 o N/A (llegó {_fmt(v)}).")
        if not d.trae_s2 and f.s2_ha is not None:
            E(f"{f.autoridad}: trae S2 pero la delimitación está marcada como sin columna S2.")
        if d.trae_s2 and _ha(f.s2_ha) < _ha(f.s1_ha):
            E(f"{f.autoridad}: S2 ({_fmt(_ha(f.s2_ha))}) es menor que S1 ({_fmt(_ha(f.s1_ha))}); "
              f"diferencia {_fmt(_ha(f.s2_ha) - _ha(f.s1_ha))}.")

    s1_t = sum((_ha(f.s1_ha) for f in d.filas), CERO)
    s2_t = sum((_ha(f.s2_ha) for f in d.filas), CERO)
    if s1_t <= 0:
        E("S1_T = 0: ninguna autoridad tiene área de intersección; no hay nada que repartir.")
    if d.trae_s2 and s2_t <= 0:
        E("La delimitación trae columna S2 pero S2_T = 0.")

    for nombre, suma, declarado in (("S1", s1_t, d.s1_t_declarado), ("S2", s2_t, d.s2_t_declarado)):
        if nombre == "S2" and not d.trae_s2:
            if declarado is not None:
                E("Hay un total S2 declarado pero la delimitación está marcada como sin columna S2.")
            continue
        if declarado is None:
            A(f"No se digitó el total {nombre} de la delimitación: no se pudo cotejar la suma de las filas.")
        elif suma != declarado:
            E(f"La suma de {nombre}ᵢ ({_fmt(suma)}) no es igual al total de la delimitación "
              f"({_fmt(declarado)}): diferencia {_fmt(suma - declarado)} ha.")

    if d.paramo_declarado is True and not d.trae_s2:
        E("La delimitación declara intersección con páramo pero no trae la columna S2.")
    if d.paramo_declarado is False and d.trae_s2:
        distintas = [f.autoridad for f in d.filas if _ha(f.s1_ha) != _ha(f.s2_ha)]
        if distintas:
            E(f"Sin páramo, S2ᵢ debería ser igual a S1ᵢ; difiere en: {', '.join(distintas)}.")


# ---------------------------------------------------------------------------
# Razones de reparto
# ---------------------------------------------------------------------------

def razones(d: Delimitacion, parametros: ParametrosDecreto800 = PARAMETROS_VIGENTES) -> list[RazonReparto]:
    """Razón exacta de cada fila (Σ = 1). En hectáreas aplica el Decreto 800:
    Fórmula 2 si la delimitación trae S2; si no, Fórmula 1. N/A = 0 ha."""
    if d.modo == "porcentaje":
        out = []
        for f in d.filas:
            razon = Fraction(f.porcentaje) / 100
            out.append(RazonReparto(f.autoridad, razon, presentar(razon * 100, parametros.decimales_precision), 0))
        if sum((x.razon for x in out), Fraction(0)) != 1:
            raise ValueError("Los porcentajes no suman 100 %.")
        return out
    areas = [AreaAutoridad(f.autoridad, _ha(f.s1_ha), _ha(f.s2_ha)) for f in d.filas]
    return calcular_razones_de_reparto(areas, hay_paramo=d.trae_s2, parametros=parametros)


def formula(d: Delimitacion) -> int | None:
    if d.modo != "hectareas":
        return None
    return 2 if d.trae_s2 else 1
