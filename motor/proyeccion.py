"""
Proyecciones: estimación de la generación y de la TVB de un año futuro
(capacidad nueva, Jorge 2026-11-14). Sin base de datos ni interfaz.

**Esto es una estimación, no una liquidación.** No toca la regla 2 («nunca
estimar»), que sigue rigiendo la liquidación: nada de lo que sale de aquí se
guarda como liquidación ni como TVB real.

Método (decisiones de Jorge, 2026-11-14):

* **Generación.** Para cada central y cada mes del año, se toman los últimos 5
  años con dato de ESE mismo mes (anteriores al año proyectado):
      medio = promedio · bajo = el menor · alto = el mayor.
  Así se conserva la estacionalidad. Un mes sin dato en un año no cuenta como
  cero: simplemente no entra. Con menos de 5 años se usa lo que haya y se
  informa cuántos fueron.
* **TVB.** Se sugiere: última TVB real × (1 + variación promedio anual)ⁿ, con
  la variación promedio de los últimos 5 cambios anuales de la serie y n = años
  entre la última TVB real y el año proyectado. La persona puede cambiarla.

Cifras exactas (`Decimal`/`Fraction`), nunca `float`. El promedio se presenta
con 2 decimales de kWh y la TVB con 5, como las reales.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from motor.tse_calculator import TseCalculatorError, presentar

VENTANA = 5
ESCENARIOS = ("bajo", "medio", "alto")


@dataclass(frozen=True)
class MesProyectado:
    mes: int                              # 1-12
    anios: tuple[int, ...]                # años del histórico que se usaron
    bajo: Decimal | None                  # None = sin histórico para ese mes
    medio: Decimal | None
    alto: Decimal | None

    def de(self, escenario: str) -> Decimal | None:
        return getattr(self, escenario)


def proyectar_generacion(historia: Mapping[str, Decimal], anio: int, ventana: int = VENTANA) -> list[MesProyectado]:
    """`historia`: generación mensual de UNA central, por periodo «AAAA-MM»
    (solo meses completos). Devuelve los 12 meses del año `anio`."""
    salida = []
    for mes in range(1, 13):
        datos = sorted(
            ((int(p[:4]), v) for p, v in historia.items() if int(p[5:7]) == mes and int(p[:4]) < anio and v is not None),
            reverse=True,
        )[:ventana]
        if not datos:
            salida.append(MesProyectado(mes, (), None, None, None))
            continue
        for _, v in datos:
            if not isinstance(v, Decimal):
                raise TseCalculatorError(f"La generación histórica debe ser Decimal, no {type(v).__name__}.")
        valores = [v for _, v in datos]
        promedio = sum((Fraction(v) for v in valores), Fraction(0)) / len(valores)
        salida.append(MesProyectado(
            mes=mes, anios=tuple(sorted(a for a, _ in datos)),
            bajo=min(valores), medio=presentar(promedio, 2), alto=max(valores),
        ))
    return salida


@dataclass(frozen=True)
class SugerenciaTVB:
    anio: int
    valor: Decimal                         # $/kWh, 5 decimales
    base_anio: int                         # última TVB real
    base_valor: Decimal
    variacion_promedio: Fraction           # p. ej. 0,0987 = 9,87 % anual
    variaciones: tuple[tuple[int, Fraction], ...]   # (año, variación frente al año anterior)

    @property
    def anios_de_distancia(self) -> int:
        return self.anio - self.base_anio


def sugerir_tvb(serie: Mapping[int, Decimal], anio: int, ventana: int = VENTANA) -> SugerenciaTVB | None:
    """TVB estimada del año `anio` a partir de la serie real. None si no hay
    al menos dos años consecutivos anteriores para medir una variación."""
    previos = {a: v for a, v in serie.items() if a < anio}
    variaciones = [(a, Fraction(previos[a]) / Fraction(previos[a - 1]) - 1)
                   for a in sorted(previos) if a - 1 in previos and previos[a - 1] > 0][-ventana:]
    if not variaciones:
        return None
    base_anio = max(previos)
    promedio = sum((v for _, v in variaciones), Fraction(0)) / len(variaciones)
    valor = Fraction(previos[base_anio]) * (1 + promedio) ** (anio - base_anio)
    return SugerenciaTVB(
        anio=anio, valor=presentar(valor, 5), base_anio=base_anio, base_valor=previos[base_anio],
        variacion_promedio=promedio, variaciones=tuple(variaciones),
    )
