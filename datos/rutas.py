"""
Ubicación de la carpeta de insumos reales del Liquidador TSE.

Traído de SISTSE `backend/etl/rutas.py` el 2026-11-11 y simplificado: los
insumos viven en `<raíz>/insumos/` (estructura del CLAUDE.md). Se puede forzar
otra ubicación con la variable `LIQUIDADOR_INSUMOS_DIR`. Los archivos fuente
nunca se modifican: solo se leen y se guarda su hash.
"""
from __future__ import annotations

import os
from pathlib import Path

# .../datos/rutas.py -> raíz del proyecto
RAIZ_PROYECTO = Path(__file__).resolve().parents[1]
INSUMOS_DEL_PROYECTO = RAIZ_PROYECTO / "insumos"


def dir_insumos() -> Path | None:
    """Carpeta de insumos vigente, o None si no existe."""
    forzada = os.getenv("LIQUIDADOR_INSUMOS_DIR")
    ruta = Path(forzada) if forzada else INSUMOS_DEL_PROYECTO
    return ruta if ruta.exists() else None


def _buscar(nombre: str) -> Path | None:
    base = dir_insumos()
    if base is None:
        return None
    ruta = base / nombre
    return ruta if ruta.exists() else None


def dir_xm() -> Path | None:
    return _buscar("xm-generacion")


def dir_creg() -> Path | None:
    return _buscar("creg-tarifas")
