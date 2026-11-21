"""
tse_calculator — Núcleo de cálculo de las Transferencias del Sector Eléctrico (TSE)

Traído de SISTSE `backend/services/tse_calculator.py` el 2026-11-11 (paso 1 del
orden de trabajo). Cambios: imports a `motor.parametros` y se retiró
`calcular_monto_municipios` (el 3% de municipios está fuera de alcance).

Depurado el 2026-11-15 (decisión de Jorge, revisión de código): se retiró lo
heredado que la aplicación no usaba — `liquidar_proyecto`, `distribuir_formula_1`
y `_2`, `es_sujeta_a_tse`, `calcular_ventas_brutas`, `calcular_tse_total`,
`calcular_monto_car_pnn`, `redondear` y sus estructuras —, que además aplicaba
el umbral del art. 45 por CENTRAL, contra la regla 9. La liquidación vive en
`motor/liquidacion.py`. Sus casos de prueba se pasaron a ese motor
(`tests/test_tse_calculator.py`). El original sigue en la carpeta del SISTSE.

Queda aquí lo que sí usa la aplicación: las razones de reparto del Decreto 800
(`calcular_razones_de_reparto`), la aritmética exacta (`a_fraccion`, `centavos`,
`presentar`), el umbral por empresa (`es_empresa_sujeta_a_tse`) y el ajuste del
residuo de centavos (`_ajustar_residuo`).

Implementa el artículo 45 de la Ley 99 de 1993 (modificado por el art. 24 de la
Ley 1930 de 2018) y los artículos 2.2.9.2.1.2 y 2.2.9.2.1.5 del Decreto 1076 de
2015, TAL COMO FUERON SUSTITUIDOS por el Decreto 800 de 2026.

Texto fuente verificado línea a línea: `insumos/normativa/Decreto 800 Final.docx`
(leído y transcrito el 2026-08-26). Ante cualquier discrepancia entre este módulo
y el texto oficial del Decreto, manda el Decreto — repórtalo, no "ajustes" la
fórmula para que cuadre con un resultado esperado.

Regla de modularidad (CLAUDE.md, regla 10): este módulo NO importa nada de base de
datos, red ni framework web. Todo corre con datos en memoria, para poder testear
y auditar el cálculo de forma aislada.

=== Exactitud: fracciones, no 5 decimales (decisión de Jorge, 2026-11-11) ===
Las razones de reparto se calculan como fracciones exactas (`Fraction`): S1_i/S1_T
y la mezcla con α = 5228/10000 son racionales, así que Σ razones = 1 exacto y
no hay residuo de porcentaje que repartir. Los 5 decimales del parágrafo 2° del
art. 2.2.9.2.1.2 se usan SOLO para presentar. Es una lectura deliberadamente
distinta de la letra del parágrafo, que redondea los cálculos intermedios. El
único redondeo de cálculo es el del monto a centavos (una vez, ROUND_HALF_UP);
el residuo de centavos va al mayor beneficiario. Nunca `float`.

=== Supuestos operativos (no definidos por el texto del Decreto) ===
Se documentan aquí y en `motor/parametros.py` (`ParametrosDecreto800`), y
quedan PENDIENTES DE CONFIRMACIÓN CON LA ONVS (decisión operativa tomada con
Jorge Rojas el 2026-08-26 para no bloquear el desarrollo):

  1. Desempate de redondeo en la mitad → ROUND_HALF_UP (centavos y presentación).
  2. Ajuste del residuo de CENTAVOS para que Σ monto = 3% exacto → se suma
     al beneficiario con mayor participación (nunca a `SIN_ASIGNAR`).
  3. S2_T = 0 con páramo formalmente presente → se usa la Fórmula 1.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
import math

from motor.parametros import ParametrosDecreto800, PARAMETROS_VIGENTES

CERO = Decimal("0")
UNO = Fraction(1)
NO_RECIBE_RESIDUO = "SIN_ASIGNAR"  # = motor.delimitacion.SIN_ASIGNAR (se define aquí para no crear un import circular)

Numero = Decimal | Fraction


# ---------------------------------------------------------------------------
# Excepciones
# ---------------------------------------------------------------------------

class TseCalculatorError(Exception):
    """Error de dominio del motor de cálculo (datos de entrada inválidos)."""


# ---------------------------------------------------------------------------
# Estructuras
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AreaAutoridad:
    """Áreas de una autoridad ambiental i para un proyecto, en hectáreas,
    tal como las declara la delimitación del IGAC.

    s1_i: AIJAA_i ∩ ACHAA_i — intersección del área de influencia y la
          cuenca hidrográfica en la jurisdicción de i.
          (Art. 2.2.9.2.1.5, numeral 1, Decreto 1076/2015 sust. Decreto 800/2026)
    s2_i: (AIJAA_i∩ACHAA_i) ∪ (AIJAA_i∩APJAA_i) ∪ (ACHAA_i∩APJAA_i) —
          área de convergencia (al menos 2 de los 3 criterios) en la
          jurisdicción de i. Solo aplica cuando la delimitación trae S2.
          Si no aplica, queda en 0.
    """

    autoridad_id: str
    s1_i: Numero
    s2_i: Numero = CERO

    def __post_init__(self) -> None:
        a_fraccion(self.s1_i)  # rechaza float y texto no numérico
        a_fraccion(self.s2_i)
        if self.s1_i < CERO or self.s2_i < CERO:
            raise TseCalculatorError(
                f"Las áreas no pueden ser negativas (autoridad={self.autoridad_id})"
            )


@dataclass(frozen=True)
class RazonReparto:
    """Participación de una autoridad en el 3%.

    `razon` es la fracción EXACTA de la bolsa (Σ razon = 1 exacto, siempre).
    `porcentaje` es solo PRESENTACIÓN: razon × 100 con 5 decimales
    (43,21875 %). Por redondeo de presentación, los porcentajes mostrados
    pueden no sumar 100,00000 al último dígito; la suma que manda es la de
    `razon`."""

    autoridad_id: str
    razon: Fraction
    porcentaje: Decimal
    formula_aplicada: int   # 1 o 2


# ---------------------------------------------------------------------------
# Aritmética exacta (Decreto 800, art. 2.2.9.2.1.2, parágrafo 2°; regla 5)
# ---------------------------------------------------------------------------

def redondear_pesos(valor: Decimal) -> Decimal:
    """Redondea un monto en pesos colombianos a 2 decimales (no hay regla
    específica del Decreto para el redondeo monetario final; se usa la
    convención contable estándar half-up a centavos)."""
    return valor.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def a_fraccion(valor: Numero | int | str) -> Fraction:
    """Convierte a fracción exacta. Acepta Decimal, int, Fraction o texto
    («2900.92344», «3/7», «43,21875»). Rechaza float: un float ya perdió
    exactitud antes de llegar aquí (CLAUDE.md, regla 5)."""
    if isinstance(valor, bool) or isinstance(valor, float):
        raise TseCalculatorError(f"Valor no exacto (float) rechazado: {valor!r}")
    if isinstance(valor, str):
        texto = valor.strip().replace(" ", "")
        if "," in texto and "." not in texto:
            texto = texto.replace(",", ".")
        try:
            return Fraction(texto)
        except (ValueError, ZeroDivisionError) as e:
            raise TseCalculatorError(f"Valor no numérico: {valor!r}") from e
    if isinstance(valor, (Decimal, int, Fraction)):
        return Fraction(valor)
    raise TseCalculatorError(f"Tipo no admitido: {type(valor).__name__}")


def centavos(valor: Fraction | Decimal) -> Decimal:
    """Cuantiza a centavos con ROUND_HALF_UP, de forma exacta (sin pasar por
    una aproximación decimal intermedia)."""
    f = a_fraccion(valor) * 100
    signo = -1 if f < 0 else 1
    entero = math.floor(abs(f) + Fraction(1, 2))
    return (Decimal(signo * entero) / Decimal(100)).quantize(Decimal("0.01"))


def presentar(valor: Fraction | Decimal, decimales: int = 5) -> Decimal:
    """Cifra para MOSTRAR (no para calcular): redondeo half-up a `decimales`."""
    f = a_fraccion(valor) * (10 ** decimales)
    signo = -1 if f < 0 else 1
    entero = math.floor(abs(f) + Fraction(1, 2))
    return Decimal(signo * entero).scaleb(-decimales)


# ---------------------------------------------------------------------------
# Sujeción al tributo
# ---------------------------------------------------------------------------

def es_empresa_sujeta_a_tse(
    capacidad_empresa_kw: Decimal, parametros: ParametrosDecreto800 = PARAMETROS_VIGENTES
) -> bool:
    """Art. 45 Ley 99 de 1993: "Las empresas generadoras de energía
    hidroeléctrica cuya potencia nominal instalada total supere los 10.000
    kilovatios...". El sujeto es la EMPRESA y el umbral se mide sobre su
    potencia total, no sobre cada central. Estrictamente mayor.

    `capacidad_empresa_kw` es la suma de la capacidad de las centrales del
    agente generador según `ParametrosDecreto800.tipos_generacion_del_umbral`
    (ver `datos/sujecion_empresas.py`)."""
    return capacidad_empresa_kw > parametros.capacidad_minima_kw


# ---------------------------------------------------------------------------
# Residuo de centavos
# ---------------------------------------------------------------------------

def _ajustar_residuo(
    participaciones: list[dict], monto_objetivo: Decimal
) -> list[dict]:
    """Ajusta el residuo de redondeo para que Σ monto = monto_objetivo exacto.

    SUPUESTO OPERATIVO (Decreto silente): el residuo se asigna al beneficiario
    de mayor participación (ver `ParametrosDecreto800.ajuste_residuo`,
    pendiente de confirmación ONVS). En empate, gana el primero por
    `autoridad_id` ordenado ascendentemente (determinístico y auditable)."""
    if not participaciones:
        return participaciones
    suma_actual = sum(p["monto"] for p in participaciones)
    residuo = redondear_pesos(monto_objetivo - suma_actual)
    if residuo == CERO:
        return participaciones
    # La parte retenida «sin asignar» (autoridad ambiental urbana o área en
    # litigio, Jorge 2026-11-13) nunca recibe el residuo: no es un beneficiario.
    candidatas = [p for p in participaciones if p["autoridad_id"] != NO_RECIBE_RESIDUO] or participaciones
    # Selección determinística: mayor monto; empate -> menor autoridad_id
    objetivo = min(
        candidatas, key=lambda p: (-p["monto"], p["autoridad_id"])
    )
    objetivo["monto"] = redondear_pesos(objetivo["monto"] + residuo)
    objetivo["ajuste_residuo_aplicado"] = True
    return participaciones


# ---------------------------------------------------------------------------
# Reparto a partir de las hectáreas de la delimitación del IGAC
# (en el SISTSE se llamaba «oficio»; desde 2026-11-11, «delimitación»)
# ---------------------------------------------------------------------------

def calcular_razones_de_reparto(
    areas: Sequence[AreaAutoridad],
    hay_paramo: bool,
    parametros: ParametrosDecreto800 = PARAMETROS_VIGENTES,
) -> list[RazonReparto]:
    """Aplica la razón del Decreto a las hectáreas DECLARADAS en la delimitación.

    Art. 2.2.9.2.1.5 numeral 1 del Decreto 1076 de 2015, sustituido por el
    Decreto 800 de 2026:

        Fórmula 1 (sin S2):  S1_i / S1_T
        Fórmula 2 (con S2):  (1−α)·(S1_i/S1_T) + α·(S2_i/S2_T)

    Cálculo exacto con fracciones: no hay residuo de porcentaje ni ajuste.

    `hay_paramo` lo decide la DELIMITACIÓN, no la aplicación: es verdadero
    cuando trae S2_i. Si no la trae, no hay Fórmula 2.
    """
    if not areas:
        raise TseCalculatorError("La delimitación no declara ninguna autoridad ambiental.")

    s1 = [a_fraccion(a.s1_i) for a in areas]
    s2 = [a_fraccion(a.s2_i) for a in areas]
    s1_t, s2_t = sum(s1, Fraction(0)), sum(s2, Fraction(0))
    if s1_t <= 0:
        raise TseCalculatorError(
            "S1_T = 0: la delimitación no declara área de intersección para ninguna "
            "autoridad, así que no hay nada que repartir."
        )
    # Caso degenerado que el Decreto no resuelve: hay páramo pero S2_T = 0.
    # Se usa la Fórmula 1 (`ParametrosDecreto800.s2_t_cero_con_paramo`).
    usa_formula_2 = hay_paramo and s2_t > 0
    alpha = a_fraccion(parametros.alpha)

    resultado = []
    for a, s1_i, s2_i in zip(areas, s1, s2):
        razon = (UNO - alpha) * (s1_i / s1_t) + alpha * (s2_i / s2_t) if usa_formula_2 else s1_i / s1_t
        resultado.append(RazonReparto(
            autoridad_id=a.autoridad_id,
            razon=razon,
            porcentaje=presentar(razon * 100, parametros.decimales_precision),
            formula_aplicada=2 if usa_formula_2 else 1,
        ))
    assert sum((r.razon for r in resultado), Fraction(0)) == UNO  # invariante exacto
    return resultado
