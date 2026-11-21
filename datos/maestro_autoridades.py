"""
Lector del maestro de autoridades ambientales (`datos/maestro_autoridades.csv`).

El archivo se edita a mano en Excel y se guarda como «CSV UTF-8 (delimitado
por comas)». El lector tolera también «;» como separador y el «CSV» simple de
Excel para Mac (Mac Roman), pero rechaza un archivo que quedó en una sola
columna.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

RUTA = Path(__file__).with_name("maestro_autoridades.csv")
TIPOS = {"CAR", "PNN"}


class MaestroInvalido(Exception):
    """El maestro de autoridades tiene errores que impiden usarlo."""


@dataclass(frozen=True)
class Autoridad:
    sigla: str
    tipo: str
    nombre: str


def _texto(ruta: Path) -> str:
    crudo = ruta.read_bytes()
    try:
        return crudo.decode("utf-8-sig")
    except UnicodeDecodeError:
        return crudo.decode("mac_roman")


def cargar_maestro(ruta: Path = RUTA) -> dict[str, Autoridad]:
    texto = _texto(ruta)
    encabezado = texto.splitlines()[0] if texto else ""
    separador = ";" if encabezado.count(";") > encabezado.count(",") else ","
    filas = list(csv.DictReader(io.StringIO(texto), delimiter=separador))
    if not filas or not {"sigla", "tipo", "nombre"} <= set(filas[0]):
        raise MaestroInvalido(
            "El maestro no tiene las columnas sigla, tipo y nombre por separado. "
            "Probablemente Excel lo guardó en una sola columna."
        )
    errores: list[str] = []
    maestro: dict[str, Autoridad] = {}
    for n, f in enumerate(filas, start=2):
        sigla = (f.get("sigla") or "").strip()
        tipo = (f.get("tipo") or "").strip().upper()
        nombre = (f.get("nombre") or "").strip()
        if not sigla:
            continue
        if sigla in maestro:
            errores.append(f"fila {n}: sigla repetida «{sigla}»")
        if tipo not in TIPOS:
            errores.append(f"fila {n}: tipo «{tipo}» no es CAR ni PNN")
        if not nombre:
            errores.append(f"fila {n}: «{sigla}» sin nombre")
        if "√" in sigla + nombre or "�" in sigla + nombre:
            errores.append(f"fila {n}: «{sigla}» tiene caracteres dañados (codificación)")
        maestro[sigla] = Autoridad(sigla, tipo, nombre)
    if errores:
        raise MaestroInvalido("Maestro de autoridades con errores:\n" + "\n".join(errores))
    return maestro
