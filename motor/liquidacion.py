"""
Liquidación de una central en un mes, a partir de su delimitación (paso 5).

Sin base de datos ni interfaz (CLAUDE.md, regla 10): recibe la generación, la
TVB y la delimitación en memoria y devuelve la cadena completa.

    ventas_brutas = generación_kWh × TVB_$/kWh
    bolsa_CAR_PNN = 3 % × ventas_brutas          (a centavos, una sola vez)
    monto_i       = bolsa × razón_i               (razón exacta de la delimitación)

Diferencias con `liquidar_proyecto`, el orquestador heredado del SISTSE (se
retiró de `motor/tse_calculator.py` el 2026-11-15; sus casos de prueba corren
ahora contra este motor):
  * El reparto sale de `motor.delimitacion.razones`, de modo que sirve igual
    para hectáreas (Fórmulas 1 y 2) y para el modo `porcentaje`.
  * El umbral del art. 45 es por EMPRESA y lo decide quien llama (necesita la
    capacidad de toda la empresa, que no está en la delimitación). Aquí no se
    mira la capacidad de la central.

Cada monto se calcula exacto (fracciones) y se cuantiza una vez a centavos con
ROUND_HALF_UP; el residuo de centavos va al beneficiario de mayor
participación, nunca a `SIN_ASIGNAR` (regla 5).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from fractions import Fraction

from motor import delimitacion as dm
from motor.delimitacion import SIN_ASIGNAR
from motor.parametros import PARAMETROS_VIGENTES, ParametrosDecreto800
from motor.tse_calculator import TseCalculatorError, _ajustar_residuo, a_fraccion, centavos

CERO = Decimal("0.00")

CITA_VENTAS = ("Art. 2.2.9.2.1.2 del Decreto 1076 de 2015 (sust. Decreto 800 de 2026): ventas brutas = "
               "generación propia × tarifa de venta en bloque de la CREG.")
CITA_TSE = "Art. 45 de la Ley 99 de 1993, modificado por el art. 24 de la Ley 1930 de 2018: 6 % de las ventas brutas."
CITA_BOLSA = ("Art. 2.2.9.2.1.5, numeral 1, del Decreto 1076 de 2015 (sust. Decreto 800 de 2026): 3 % para las "
              "corporaciones autónomas regionales o Parques Nacionales Naturales.")
CITA_FORMULA = {
    1: "Art. 2.2.9.2.1.5, numeral 1 — Fórmula 1: S1ᵢ / S1_T.",
    2: "Art. 2.2.9.2.1.5, numeral 1 — Fórmula 2: (1 − α)·S1ᵢ/S1_T + α·S2ᵢ/S2_T.",
    None: "Porcentajes digitados tal como vienen en la delimitación del IGAC.",
}
CITA_UMBRAL = ("Art. 45 de la Ley 99 de 1993: empresas cuya potencia nominal instalada total supere "
               "los 10.000 kW (por empresa, no por central).")


def _decimal_exacto(f: Fraction) -> Decimal:
    """Fracción de denominador 2ᵃ·5ᵇ → Decimal sin pérdida."""
    with localcontext() as ctx:
        ctx.prec = 80
        d = (Decimal(f.numerator) / Decimal(f.denominator)).normalize()
        return d.quantize(Decimal(1)) if d.as_tuple().exponent > 0 else d  # «7E+3» → «7000»


@dataclass(frozen=True)
class FilaLiquidada:
    autoridad: str
    s1_ha: Decimal | None
    s2_ha: Decimal | None
    razon: Fraction              # exacta; Σ = 1
    porcentaje: Decimal          # presentación, 5 decimales
    monto: Decimal               # pesos, a centavos
    ajuste_residuo: bool = False

    @property
    def retenido(self) -> bool:
        return self.autoridad == SIN_ASIGNAR


@dataclass(frozen=True)
class LiquidacionCentral:
    generacion_kwh: Decimal
    tvb_pesos_kwh: Decimal
    ventas_brutas: Decimal       # exacta, sin redondear
    tse_total: Decimal           # 6 %, a centavos (solo informativo)
    bolsa_car_pnn: Decimal       # 3 %, a centavos
    formula: int | None          # 1, 2 o None (modo porcentaje)
    sin_paramo_con_s2: bool      # trae S2 = S1 sin páramo: la Fórmula 2 da lo mismo que la 1
    filas: tuple[FilaLiquidada, ...]
    parametros_version: str
    alpha: Decimal

    @property
    def retenido(self) -> Decimal:
        return sum((f.monto for f in self.filas if f.retenido), CERO)

    @property
    def a_transferir(self) -> Decimal:
        return sum((f.monto for f in self.filas if not f.retenido), CERO)

    def texto_formula(self) -> str:
        texto = CITA_FORMULA[self.formula]
        if self.formula == 2:
            texto += f" α = {str(self.alpha).replace('.', ',')}."
            if self.sin_paramo_con_s2:
                texto += " La delimitación no declara páramo y trae S2ᵢ = S1ᵢ: el resultado es el de la Fórmula 1."
        return texto


def liquidar_central(
    generacion_kwh: Decimal,
    tvb_pesos_kwh: Decimal,
    delimitacion: dm.Delimitacion,
    parametros: ParametrosDecreto800 = PARAMETROS_VIGENTES,
) -> LiquidacionCentral:
    """Liquida el 3 % de una central para un mes. La delimitación debe estar
    ya validada (`motor.delimitacion.validar`)."""
    for nombre, v in (("generación", generacion_kwh), ("TVB", tvb_pesos_kwh)):
        if not isinstance(v, Decimal):
            raise TseCalculatorError(f"La {nombre} debe ser Decimal, no {type(v).__name__}.")
        if v < 0:
            raise TseCalculatorError(f"La {nombre} no puede ser negativa.")

    ventas = a_fraccion(generacion_kwh) * a_fraccion(tvb_pesos_kwh)
    bolsa_exacta = ventas * a_fraccion(parametros.porcentaje_car_pnn)
    bolsa = centavos(bolsa_exacta)

    filas = [{
        "autoridad_id": f.autoridad, "s1_ha": f.s1_ha, "s2_ha": f.s2_ha, "razon": r.razon,
        "porcentaje": r.porcentaje, "monto": centavos(r.razon * bolsa_exacta), "ajuste_residuo_aplicado": False,
    } for f, r in zip(delimitacion.filas, dm.razones(delimitacion, parametros))]
    filas = _ajustar_residuo(filas, bolsa)

    return LiquidacionCentral(
        generacion_kwh=generacion_kwh,
        tvb_pesos_kwh=tvb_pesos_kwh,
        ventas_brutas=_decimal_exacto(ventas),
        tse_total=centavos(ventas * a_fraccion(parametros.porcentaje_tse_total)),
        bolsa_car_pnn=bolsa,
        formula=dm.formula(delimitacion),
        sin_paramo_con_s2=bool(delimitacion.modo == "hectareas" and delimitacion.trae_s2
                               and delimitacion.paramo_declarado is False),
        filas=tuple(FilaLiquidada(
            autoridad=f["autoridad_id"], s1_ha=f["s1_ha"], s2_ha=f["s2_ha"], razon=f["razon"],
            porcentaje=f["porcentaje"], monto=f["monto"], ajuste_residuo=f["ajuste_residuo_aplicado"],
        ) for f in filas),
        parametros_version=parametros.version,
        alpha=parametros.alpha,
    )
