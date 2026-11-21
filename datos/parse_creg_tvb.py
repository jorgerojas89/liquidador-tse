"""
ETL — Tarifa de Venta en Bloque (TVB), fuente CREG (Resolución CREG 010/2018).

La TVB es anual (aplica del 1 de enero al 31 de diciembre del mismo año,
según `insumos/creg-tarifas/README.md`). Dos fuentes reales coinciden en el
valor 2026 (192,23886 $/kWh), lo cual sirvió de control cruzado:

  1. `insumos/creg-tarifas/TVB Consolidado.rtf` — serie consolidada 2017-2026
     preparada por Jorge, una fila con los 10 años.
  2. `insumos/creg-tarifas/PPC-CVA_FEBRERO_<AAAA>_V*.xlsx` — un archivo por
     año publicado por XM S.A. E.S.P. según la Resolución CREG 010/2018, con
     el detalle de PPC/CVA/TVB de ese año (fila "TVB": "Tarifa de Venta en
     Bloque vigente...").

Se usa (1) como fuente primaria por ser la serie ya consolidada, y (2) queda
disponible como reconciliación/trazabilidad fila por fila cuando se necesite
la resolución exacta de un año puntual.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import openpyxl

try:
    from striprtf.striprtf import rtf_to_text
except ImportError:  # pragma: no cover
    rtf_to_text = None


@dataclass(frozen=True)
class TarifaTVBAnual:
    anio: int
    tvb_pesos_kwh: Decimal
    fuente: str
    hash_archivo_fuente: str
    resolucion: str = "Resolución CREG 010 de 2018"


def _hash_archivo(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def _decimal_colombiano(texto: str) -> Decimal:
    """Convierte un número en formato colombiano a Decimal.

    Punto = separador de miles, coma = separador decimal ("1.234,56" ->
    1234.56; "192,23886" -> 192.23886). Antes había dos asignaciones seguidas
    de `valor`: la primera contemplaba el separador de miles y la segunda,
    que la pisaba, no — de modo que un valor con miles habría producido
    "1.234.56" y un `InvalidOperation` (auditoría 2026-08-26, hallazgo E4)."""
    limpio = texto.strip()
    if "," in limpio:
        limpio = limpio.replace(".", "").replace(",", ".")
    return Decimal(limpio)


def parsear_tvb_consolidado_rtf(ruta_rtf: str | Path) -> list[TarifaTVBAnual]:
    """Parsea `TVB Consolidado.rtf`: una tabla con fila 'AÑO' (2017..2026) y
    fila 'TARIFA DE VENTA EN BLOQUE O TVS ($/KwH)' con los valores, separados
    con '|' en el texto plano extraído."""
    if rtf_to_text is None:
        raise RuntimeError("Falta la librería 'striprtf' (pip install striprtf)")
    ruta = Path(ruta_rtf)
    texto = rtf_to_text(ruta.read_text(encoding="utf-8", errors="ignore"))
    hash_archivo = _hash_archivo(ruta)

    lineas = [l for l in texto.splitlines() if l.strip()]
    fila_anios = next(l for l in lineas if l.upper().startswith("AÑO") or l.upper().startswith("ANO"))
    fila_tvb = next(l for l in lineas if "TARIFA DE VENTA EN BLOQUE" in l.upper())

    anios = [a for a in fila_anios.split("|") if a.strip().isdigit()]
    valores = [v for v in fila_tvb.split("|")[1:] if v.strip()]
    # La celda 0 de fila_tvb es la etiqueta, no un valor; se descartó arriba.
    valores = [v for v in valores if re.match(r"^[\d.,]+$", v.strip())]

    resultado = []
    for anio_str, valor_str in zip(anios, valores):
        resultado.append(
            TarifaTVBAnual(
                anio=int(anio_str),
                tvb_pesos_kwh=_decimal_colombiano(valor_str),
                fuente="TVB Consolidado.rtf",
                hash_archivo_fuente=hash_archivo,
            )
        )
    return resultado


def parsear_tvb_ppc_cva_xlsx(ruta_xlsx: str | Path) -> TarifaTVBAnual:
    """Extrae la fila 'TVB' de un archivo PPC-CVA_<MES>_<AAAA>_V*.xlsx
    (formato real de XM S.A. E.S.P., Resolución CREG 010/2018)."""
    ruta = Path(ruta_xlsx)
    wb = openpyxl.load_workbook(str(ruta), data_only=True)
    ws = wb[wb.sheetnames[0]]
    anio = None
    tvb = None
    for row in ws.iter_rows(values_only=True):
        if tvb is not None and anio is not None:
            break  # ya se encontró todo: seguir solo permitía sobrescribirlo
        for i, celda in enumerate(row):
            if not isinstance(celda, str):
                continue
            texto = celda.lower()
            # Comparaciones en minúsculas a ambos lados: antes se buscaba
            # "según el mes de" contra el texto sin normalizar, así que la
            # exclusión no funcionaba si el archivo traía otra capitalización
            # (auditoría 2026-08-26, hallazgo E6).
            if "para el mes de" in texto and "según el mes de" not in texto:
                m = re.search(r"(\d{4})", celda)
                if m:
                    anio = int(m.group(1))
            if tvb is None and celda.strip().upper() == "TVB":
                # el valor está en la misma fila, columna 'VALOR'
                for v in row[i + 1:]:
                    if isinstance(v, (int, float, Decimal)):
                        tvb = Decimal(str(v))
                        break
    if anio is None:
        m = re.search(r"(\d{4})", ruta.stem)
        anio = int(m.group(1)) if m else None
    if tvb is None:
        raise ValueError(f"No se encontró la fila 'TVB' en {ruta.name}")
    return TarifaTVBAnual(
        anio=anio,
        tvb_pesos_kwh=tvb,
        fuente=ruta.name,
        hash_archivo_fuente=_hash_archivo(ruta),
    )


if __name__ == "__main__":
    import sys

    ruta = sys.argv[1] if len(sys.argv) > 1 else "insumos/creg-tarifas/TVB Consolidado.rtf"
    if ruta.endswith(".rtf"):
        for t in parsear_tvb_consolidado_rtf(ruta):
            print(t.anio, t.tvb_pesos_kwh, t.fuente)
    else:
        t = parsear_tvb_ppc_cva_xlsx(ruta)
        print(t.anio, t.tvb_pesos_kwh, t.fuente)
