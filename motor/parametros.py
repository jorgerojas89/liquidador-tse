"""
Parámetros normativos versionados del Decreto 800 de 2026.

Traído de SISTSE `backend/core/config.py` el 2026-11-11 (paso 1 del orden de
trabajo). Único cambio: se retiraron los parámetros del 3% de municipios.

Regla obligatoria (CLAUDE.md, regla 6): α, 6% y 3% centralizados aquí. Estos
parámetros del Decreto NUNCA se hardcodean dispersos en el código.
Viven aquí, centralizados y versionados, porque pueden cambiar por un futuro
acto administrativo que sustituya el Decreto 800 de 2026.

Fuente autoritativa: insumos/normativa/Decreto 800 Final.docx (texto oficial
verificado línea a línea el 2026-08-26). Ante cualquier discrepancia entre
este archivo y el texto del Decreto, manda el Decreto.
"""
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ParametrosDecreto800:
    """Parámetros del Decreto 800 de 2026 (sustituye arts. 2.2.9.2.1.2 y
    2.2.9.2.1.5 del Decreto 1076 de 2015)."""

    version: str = "decreto-800-2026-v1"
    vigente_desde: str = "2026-01-01"  # fecha de publicación real: pendiente (Bloque D, TAREAS-JORGE.md)

    # --- Art. 45 Ley 99/1993, modificado por art. 24 Ley 1930/2018 ---
    porcentaje_tse_total: Decimal = Decimal("0.06")  # 6% de las ventas brutas
    porcentaje_car_pnn: Decimal = Decimal("0.03")  # 3% CAR/PNN (núcleo del sistema)
    # El 3% de municipios y distritos está fuera del alcance del Liquidador TSE
    # (CLAUDE.md, «Qué NO es»): no se parametriza aquí.

    # --- Art. 45 Ley 99/1993: umbral de sujeción ---
    # "Las empresas generadoras de energía hidroeléctrica cuya potencia nominal
    # instalada total supere los 10.000 kilovatios..." El sujeto de la norma es
    # la EMPRESA, no la central: una generadora que supera el umbral hace
    # tributar todas sus centrales hidroeléctricas, incluidas las pequeñas.
    # Antes el sistema aplicaba el umbral central por central y dejaba fuera,
    # por ejemplo, RIO CALI (1.800 kW) o NIMA (6.700 kW), que sí tributan
    # porque sus empresas superan los 10 MW — y para las que la ONVS ya tiene
    # cartografía. Corregido el 2026-08-27 a indicación de Jorge Rojas, con
    # la hoja "Empresas Sujetos activos" del reporte de XM como referencia.
    capacidad_minima_kw: Decimal = Decimal("10000")
    sujecion_por: str = "EMPRESA"  # alternativa histórica: "CENTRAL"

    # Qué capacidad suma el umbral de la empresa. La hoja "Empresas Sujetos
    # activos" de XM (reporte 2025) deja fuera SOLAR y se reproduce exacta para
    # las 68 empresas con valor sumando HIDRAULICA + TERMICA + COGENERADOR, con
    # o sin EOLICA: en enero de 2025 las eólicas (de ISAGEN y CELSIA) no traen
    # capacidad, así que la hoja no permite saber si XM las cuenta (verificado
    # el 2026-11-15). Aquí se incluyen. Hoy no cambia ninguna sujeción: ISAGEN
    # y CELSIA superan los 10.000 kW con su capacidad hidráulica sola.
    # Se deja explícito y configurable porque el Decreto no lo precisa:
    # una lectura más estricta del art. 45 sumaría solo la capacidad
    # hidráulica (pendiente de confirmar con la ONVS, TAREAS-JORGE.md Bloque D).
    tipos_generacion_del_umbral: tuple[str, ...] = (
        "HIDRAULICA", "TERMICA", "COGENERADOR", "EOLICA",
    )

    # --- Art. 2.2.9.2.1.5 Decreto 1076/2015 (sustituido por Decreto 800/2026) ---
    # "0,5228: Factor de ponderación de convergencia de criterios de carácter
    # constante, mediante el cual se incorpora el criterio de conservación de
    # los páramos donde existieren." Calibrado por MADS/ONVS con datos XM,
    # IGAC e IAvH (ver DTS, insumos/normativa/6 Documento Técnico de Soporte).
    alpha: Decimal = Decimal("0.5228")

    # --- Parágrafo 2, art. 2.2.9.2.1.2: estándares de medición y precisión ---
    decimales_precision: int = 5
    unidad_superficie: str = "ha"

    # === SUPUESTOS OPERATIVOS NO DEFINIDOS POR EL DECRETO ===
    # El texto del Decreto 800/2026 dice únicamente "aplicando el sistema de
    # redondeo hacia el valor más cercano" (parágrafo 2, art. 2.2.9.2.1.2),
    # sin resolver el desempate a mitad de camino (x.xxxxx5), ni la regla de
    # ajuste del residuo para que Σ PTSE_i = 3% exacto, ni el comportamiento
    # cuando S2_T = 0 con páramo formalmente presente. TAREAS-JORGE.md
    # (Bloque D) ya marca estos tres puntos como "pendiente de confirmar con
    # ONVS". Mientras eso ocurre, el sistema opera con estos supuestos
    # documentados y fácilmente reemplazables (decisión tomada con Jorge
    # Rojas el 2026-08-26):
    redondeo_desempate: str = "ROUND_HALF_UP"  # 0.5 sube; lectura estándar de "al valor más cercano"
    # ¿Sobre qué magnitud se aplican los 5 decimales del parágrafo 2? El texto
    # dice "cálculos intermedios y resultado final", sin precisar si PTSE_i se
    # acota como razón de reparto (S1_i/S1_T) o como porcentaje ya multiplicado
    # por el 3%. Se acota la RAZÓN: es el "cálculo intermedio" que el Decreto
    # nombra, y acotar el producto por 3% multiplica el error por ~33 (cada
    # unidad de la 5ª cifra pasa a valer 10⁻⁵ de las ventas brutas en vez de
    # 10⁻⁵ del 3%). Ver auditoría 2026-08-26, hallazgo A1.
    # DECISIÓN DE JORGE (2026-11-11), reemplaza lo anterior: las razones se
    # calculan como FRACCIONES EXACTAS y los 5 decimales son solo de
    # presentación. Así Σ razones = 1 exacto y no hay residuo de porcentaje.
    # Se aparta a sabiendas de la letra del parágrafo 2°, que redondea los
    # cálculos intermedios.
    base_redondeo_reparto: str = "FRACCION_EXACTA"
    ajuste_residuo: str = "MAYOR_BENEFICIARIO"  # residuo de CENTAVOS: se suma a quien tiene la mayor participación
    s2_t_cero_con_paramo: str = "DEGRADA_FORMULA_1"  # si S2_T=0 pese a haber páramo, se usa Fórmula 1 y se alerta


PARAMETROS_VIGENTES = ParametrosDecreto800()

# Fecha desde la que rigen las delimitaciones del IGAC bajo el Decreto 800
# (Jorge, 2026-11-14): 25 de noviembre de 2026, ese día incluido. Es el valor
# por defecto al registrar una delimitación; se puede cambiar en cada una.
VIGENCIA_DELIMITACIONES_POR_DEFECTO = "2026-11-25"
