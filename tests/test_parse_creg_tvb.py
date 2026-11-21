"""TVB de la CREG: la serie consolidada y el control cruzado con los archivos
PPC-CVA de XM (revisión del 2026-11-15).

`parsear_tvb_ppc_cva_xlsx` no lo usa la aplicación, pero se conserva como
control: la app toma la TVB de `TVB Consolidado.rtf`, y estos archivos de XM son
la fuente oficial de cada año. El 2026-11-15 coinciden en 2026 (el año que se
liquida) y difieren en el 4.º–5.º decimal en 2022-2025 (pendiente de revisar
con la ONVS, ver CLAUDE.md). Los .xls de 2018-2021 no se leen (openpyxl no
abre el formato viejo)."""
from decimal import Decimal

import openpyxl
import pytest

from datos.parse_creg_tvb import parsear_tvb_consolidado_rtf, parsear_tvb_ppc_cva_xlsx
from datos.rutas import dir_creg

D = Decimal


def test_lee_la_tvb_y_el_anio_de_un_archivo_ppc_cva(tmp_path):
    """Sintético, con la estructura del archivo de XM."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Precio promedio de contratos — valores para el mes de febrero de 2027"])
    ws.append(["Concepto", "Descripción", "VALOR"])
    ws.append(["TVB", "Tarifa de Venta en Bloque vigente", 205.12345])
    ruta = tmp_path / "PPC-CVA_FEBRERO_2027_V1.xlsx"
    wb.save(ruta)
    t = parsear_tvb_ppc_cva_xlsx(ruta)
    assert (t.anio, t.tvb_pesos_kwh, t.fuente) == (2027, D("205.12345"), ruta.name)
    assert len(t.hash_archivo_fuente) == 64


def _reales():
    carpeta = dir_creg()
    if carpeta is None or not (carpeta / "TVB Consolidado.rtf").exists():
        return None
    ppc = sorted(carpeta.glob("PPC-CVA*2026*.xlsx"))
    return (carpeta / "TVB Consolidado.rtf", ppc[0]) if ppc else None


@pytest.mark.skipif(_reales() is None, reason="sin los archivos de la CREG/XM en insumos")
def test_la_tvb_2026_de_la_serie_coincide_con_la_de_xm():
    """La TVB con la que se liquida 2026 es la misma en las dos fuentes."""
    rtf, ppc = _reales()
    serie = {t.anio: t.tvb_pesos_kwh for t in parsear_tvb_consolidado_rtf(rtf)}
    xm = parsear_tvb_ppc_cva_xlsx(ppc)
    assert xm.anio == 2026
    assert serie[2026] == xm.tvb_pesos_kwh == D("192.23886")
