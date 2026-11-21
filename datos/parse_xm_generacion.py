"""
Lector de los archivos de generación de XM (paso 4, 2026-11-14).

XM entrega dos formatos, y `leer_xm` detecta cuál es:

**Mensual** (2018-2025): `GeneraciónReal_Y_CapacidadEfectiva_kWh_por_Departamento_
Municipio_Agente_<AAAA>.xlsx`. Fila 5 con encabezados fijos (Departamento,
Municipio, Agente, Codigo Submercado Generación, Recurso, Tipo Generación) y 12
pares de columnas (Generación, Capacidad Efectiva Neta), con el mes fusionado
en la fila 4. Trae capacidad; **no trae versión** (se declara al cargar).
Departamento y Municipio solo vienen en la primera fila de cada grupo (celdas
combinadas): se rellenan hacia abajo.

**Diario-horario** (2026): `Generacion_(kWh)_<AAAA>.xlsx`. Fila 3 con Fecha,
Recurso, Código Recurso (= código de submercado), Tipo Generación, Combustible,
Código Agente, …, 24 columnas horarias «0»…«23» y **Version** (TX1…TX5, TXF).
Una fila por recurso, día y combustible: una térmica que quema dos
combustibles el mismo día trae dos filas, que se suman. No trae capacidad ni
municipio. Se suma horas → días → mes.

Reglas que se cumplen aquí:
  * Cifras exactas: un entero de Excel pasa a `Decimal` tal cual; un real, por
    su representación más corta (`repr`), que es la cifra que XM escribió en
    el archivo. Nunca se suma en `float` (CLAUDE.md, regla 5).
  * Mes completo = el ARCHIVO cubre todos los días del mes (`dias_archivo`).
    Un mes incompleto se guarda, pero no se liquida (regla 2).
  * XM omite casi siempre los días en que una central no generó (solo a
    veces escribe la fila en ceros): en el archivo 2026, Tunjita trae 2 días
    de enero y ninguno de febrero. Por eso un día ausente de una central, en
    un mes que el archivo cubre completo, se toma como «sin generación» y se
    señala (`dias_reportados` < `dias_archivo`) para revisión; no bloquea.
    Una central ausente todo el mes no tiene fila: queda sin generación (no
    cero). Criterio por confirmar con Jorge (2026-11-14).
  * Un día cuenta como reportado solo si trae las 24 horas en todas sus filas.
  * La versión de XM se toma del archivo cuando la trae. Si un recurso tiene
    varias versiones dentro del mismo mes, se reportan todas y quien guarda
    decide (`datos/cargue_xm.py` toma la menos avanzada).
"""
from __future__ import annotations

import calendar
import hashlib
import io
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

MESES = [
    "ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO",
    "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE",
]

FILA_MESES = 4
FILA_HEADER = 5
HORAS = tuple(str(h) for h in range(24))


class ArchivoXMInvalido(ValueError):
    """El archivo no tiene ninguno de los formatos conocidos de XM."""


# ---------------------------------------------------------------------------
# Números exactos
# ---------------------------------------------------------------------------

def exacto(valor) -> Decimal | None:
    """Celda de Excel → Decimal exacto. Vacío → None. Una celda que no es una
    cifra (texto, «NaN») es `ArchivoXMInvalido`, no un error técnico."""
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        return None
    if isinstance(valor, bool):
        raise ArchivoXMInvalido(f"Valor no numérico: {valor!r}")
    if isinstance(valor, int):
        return Decimal(valor)
    if isinstance(valor, float):
        numero = Decimal(repr(valor))
    elif isinstance(valor, Decimal):
        numero = valor
    else:
        try:
            numero = Decimal(str(valor).strip())
        except InvalidOperation:
            raise ArchivoXMInvalido(f"«{valor}» no es una cifra") from None
    if not numero.is_finite():
        raise ArchivoXMInvalido(f"«{valor}» no es una cifra")
    return numero


def _hash(contenido: bytes) -> str:
    return hashlib.sha256(contenido).hexdigest()


# ---------------------------------------------------------------------------
# Resultado común a los dos formatos
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FilaMensualXM:
    """Generación de un recurso en un mes, tal como sale del archivo."""
    periodo: str                      # AAAA-MM
    codigo_submercado: str
    recurso: str
    agente: str                       # nombre (mensual) o código (diario, p. ej. EPMG)
    tipo_generacion: str
    generacion_kwh: Decimal | None
    capacidad_kw: Decimal | None      # solo formato mensual
    dias_mes: int
    dias_reportados: int | None       # días de ESTA central con las 24 horas; None = formato mensual
    versiones: tuple[str, ...] = ()   # versiones que trae el archivo para este recurso y mes
    dias_archivo: int | None = None   # días que el archivo cubre en ese mes; None = formato mensual

    @property
    def completo(self) -> bool:
        """El archivo cubre todo el mes (condición para liquidar)."""
        return self.dias_archivo is None or self.dias_archivo == self.dias_mes

    @property
    def dias_sin_reporte(self) -> int:
        """Días del mes cubierto que esta central no trae (o trae sin las 24 horas)."""
        if self.dias_archivo is None or self.dias_reportados is None:
            return 0
        return self.dias_archivo - self.dias_reportados


@dataclass
class LecturaXM:
    archivo_nombre: str
    archivo_sha256: str
    formato: str                      # 'mensual' | 'diario'
    anio: int
    filas: list[FilaMensualXM]
    # Formato diario: días que trae el archivo por mes (cobertura del archivo).
    dias_en_archivo: dict[str, int] = field(default_factory=dict)
    # Formato mensual: registros con capacidad, para el umbral por empresa.
    registros_capacidad: list = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    # Formato diario: generación por (código, fecha) y los días que no traen
    # las 24 horas. Sirve para liquidar un mes parcial (esquema v6).
    diario: dict = field(default_factory=dict)
    dias_sin_24_horas: set = field(default_factory=set)

    @property
    def trae_version(self) -> bool:
        return any(f.versiones for f in self.filas)

    def versiones_por_mes(self) -> dict[str, set[str]]:
        salida: dict[str, set[str]] = defaultdict(set)
        for f in self.filas:
            salida[f.periodo].update(f.versiones)
        return dict(sorted(salida.items()))

    def periodos(self) -> list[str]:
        return sorted({f.periodo for f in self.filas})


def leer_xm(contenido: bytes, nombre: str) -> LecturaXM:
    """Lee un archivo de XM en cualquiera de sus dos formatos. Un archivo que
    no se puede abrir como .xlsx, o una celda que no es una cifra, es
    `ArchivoXMInvalido` con un mensaje para la persona (2026-11-15)."""
    try:
        wb = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
    except (zipfile.BadZipFile, InvalidFileException, KeyError, OSError, ValueError) as e:
        raise ArchivoXMInvalido(
            f"«{nombre}» no es un archivo de Excel (.xlsx) que se pueda abrir. Revise que sea el archivo de XM "
            f"sin modificar (detalle: {type(e).__name__})."
        ) from e
    try:
        for ws in wb.worksheets:
            encabezado = _buscar_encabezado_diario(ws)
            if encabezado is not None:
                return _leer_diario(ws, encabezado, nombre, _hash(contenido))
    finally:
        wb.close()
    # El formato mensual necesita celdas combinadas y columnas por posición:
    # se abre en modo normal (el archivo es pequeño).
    wb = openpyxl.load_workbook(io.BytesIO(contenido), data_only=True)
    try:
        return _leer_mensual(wb, nombre, _hash(contenido))
    except ArchivoXMInvalido:
        raise  # ya trae su mensaje (p. ej., una celda que no es una cifra)
    except ValueError as e:
        raise ArchivoXMInvalido(
            f"«{nombre}» no tiene el formato diario-horario (Fecha, Código Recurso, horas 0-23) "
            f"ni el mensual (Generación y Capacidad Efectiva Neta por mes): {e}"
        ) from e


# ---------------------------------------------------------------------------
# Formato diario-horario (2026)
# ---------------------------------------------------------------------------

def _texto(v) -> str:
    return "" if v is None else str(v).strip()


def _buscar_encabezado_diario(ws, max_filas: int = 10) -> tuple[int, dict[str, int]] | None:
    for n, fila in enumerate(ws.iter_rows(max_row=max_filas, values_only=True), start=1):
        textos = [_texto(v) for v in fila]
        if "Fecha" in textos and "Código Recurso" in textos:
            col = {t: i for i, t in enumerate(textos) if t}
            faltan = [c for c in ("Recurso", "Tipo Generación", "Código Agente", *HORAS) if c not in col]
            if faltan:
                raise ArchivoXMInvalido(f"Faltan columnas en el encabezado de la fila {n}: {', '.join(faltan)}")
            return n, col
    return None


def _leer_diario(ws, encabezado: tuple[int, dict[str, int]], nombre: str, sha: str) -> LecturaXM:
    fila_enc, col = encabezado
    i_fecha, i_cod, i_rec = col["Fecha"], col["Código Recurso"], col["Recurso"]
    i_tipo, i_ag, i_ver = col["Tipo Generación"], col["Código Agente"], col.get("Version")
    i_horas = [col[h] for h in HORAS]

    suma: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    dias: dict[tuple[str, str], set[date]] = defaultdict(set)
    dias_incompletos: dict[tuple[str, str], set[date]] = defaultdict(set)
    versiones: dict[tuple[str, str], set[str]] = defaultdict(set)
    datos: dict[tuple[str, str], tuple[str, str, str]] = {}
    fechas_por_mes: dict[str, set[date]] = defaultdict(set)
    anios: set[int] = set()
    sin_codigo = 0
    por_dia: dict[tuple[str, date], Decimal] = defaultdict(Decimal)
    sin_24: set[tuple[str, date]] = set()

    for fila in ws.iter_rows(min_row=fila_enc + 1, values_only=True):
        if not fila or all(v is None for v in fila):
            continue
        fecha = fila[i_fecha]
        if isinstance(fecha, datetime):
            fecha = fecha.date()
        elif isinstance(fecha, str):
            fecha = date.fromisoformat(fecha.strip()[:10])
        if not isinstance(fecha, date):
            raise ArchivoXMInvalido(f"Fecha no reconocida en «{nombre}»: {fila[i_fecha]!r}")
        codigo = _texto(fila[i_cod])
        if not codigo:
            sin_codigo += 1
            continue
        periodo = f"{fecha.year}-{fecha.month:02d}"
        clave = (periodo, codigo)
        anios.add(fecha.year)
        fechas_por_mes[periodo].add(fecha)
        datos.setdefault(clave, (_texto(fila[i_rec]), _texto(fila[i_ag]), _texto(fila[i_tipo])))
        try:
            horas = [exacto(fila[i]) if i < len(fila) else None for i in i_horas]
        except ArchivoXMInvalido as e:
            raise ArchivoXMInvalido(f"«{nombre}», {codigo} del {fecha.isoformat()}: {e}.") from None
        if any(h is None for h in horas):
            dias_incompletos[clave].add(fecha)
            sin_24.add((codigo, fecha))
        del_dia = sum((h for h in horas if h is not None), Decimal(0))
        suma[clave] += del_dia
        por_dia[(codigo, fecha)] += del_dia
        dias[clave].add(fecha)
        if i_ver is not None and _texto(fila[i_ver]):
            versiones[clave].add(_texto(fila[i_ver]).upper())

    if not datos:
        raise ArchivoXMInvalido(f"«{nombre}» no trae filas de generación.")
    if len(anios) > 1:
        raise ArchivoXMInvalido(f"«{nombre}» mezcla años ({sorted(anios)}); se espera un archivo por año.")
    anio = anios.pop()

    filas = []
    for (periodo, codigo), (recurso, agente, tipo) in sorted(datos.items()):
        a, m = map(int, periodo.split("-"))
        completos = dias[(periodo, codigo)] - dias_incompletos[(periodo, codigo)]
        filas.append(FilaMensualXM(
            periodo=periodo, codigo_submercado=codigo, recurso=recurso, agente=agente,
            tipo_generacion=tipo, generacion_kwh=suma[(periodo, codigo)], capacidad_kw=None,
            dias_mes=calendar.monthrange(a, m)[1], dias_reportados=len(completos),
            versiones=tuple(sorted(versiones[(periodo, codigo)])),
            dias_archivo=len(fechas_por_mes[periodo]),
        ))

    lectura = LecturaXM(
        archivo_nombre=nombre, archivo_sha256=sha, formato="diario", anio=anio, filas=filas,
        dias_en_archivo={p: len(f) for p, f in sorted(fechas_por_mes.items())},
        diario=dict(por_dia), dias_sin_24_horas=sin_24,
    )
    if sin_codigo:
        lectura.avisos.append(f"{sin_codigo} filas sin código de recurso: no se tuvieron en cuenta.")
    if i_ver is None:
        lectura.avisos.append("El archivo no trae la columna «Version»: hay que declararla al cargar.")
    for periodo, n in lectura.dias_en_archivo.items():
        a, m = map(int, periodo.split("-"))
        if n < calendar.monthrange(a, m)[1]:
            lectura.avisos.append(
                f"{periodo}: el archivo trae {n} de {calendar.monthrange(a, m)[1]} días. "
                f"El mes queda guardado como incompleto y no se liquida."
            )
    for periodo, vs in lectura.versiones_por_mes().items():
        if len(vs) > 1:
            lectura.avisos.append(
                f"{periodo}: el archivo mezcla versiones ({', '.join(sorted(vs))}) dentro del mes."
            )
    return lectura


# ---------------------------------------------------------------------------
# Formato mensual (2018-2025)
# ---------------------------------------------------------------------------

def _hoja_generacion_mensual(wb):
    """El archivo de 2025 trae una pestaña de resumen ('Empresas Sujetos
    activos') antes de la de datos: se busca por nombre y, si no aparece, se
    usa la de más filas."""
    for nombre in wb.sheetnames:
        if nombre.lower().startswith(("generaciónreal", "generacionreal")):
            return wb[nombre]
    return max((wb[n] for n in wb.sheetnames), key=lambda ws: ws.max_row)


def _mapear_columnas_fijas(fila_header: tuple) -> dict[str, int]:
    """Las columnas fijas no tienen posición fija entre años (2018-2019 no
    traen una columna en blanco antes de 'Agente'): se ubican por texto."""
    mapeo: dict[str, int] = {}
    for idx, etiqueta in enumerate(fila_header):
        if not isinstance(etiqueta, str):
            continue
        et = etiqueta.strip()
        sin_tilde = et.replace("ó", "o")
        if et == "Departamento":
            mapeo["departamento"] = idx
        elif et == "Municipio":
            mapeo["municipio"] = idx
        elif et == "Agente":
            mapeo["agente"] = idx
        elif sin_tilde.startswith("Codigo Submercado"):
            mapeo["codigo_submercado"] = idx
        elif et == "Recurso":
            mapeo["recurso"] = idx
        elif et == "Tipo Generación":
            mapeo["tipo_generacion"] = idx
    faltantes = {"departamento", "municipio", "agente", "codigo_submercado", "recurso", "tipo_generacion"} - set(mapeo)
    if faltantes:
        raise ValueError(f"No se pudieron ubicar las columnas fijas: {sorted(faltantes)}")
    return mapeo


def _mapear_columnas_mensuales(ws, col_tipo_generacion: int) -> dict[str, tuple[int, int]]:
    """Ubica las columnas de cada mes por las filas 4 y 5. No se asume un
    ancho fijo: en algún año un mes trae una columna en blanco de más."""
    fila_meses = next(ws.iter_rows(min_row=FILA_MESES, max_row=FILA_MESES, values_only=True))
    fila_header = next(ws.iter_rows(min_row=FILA_HEADER, max_row=FILA_HEADER, values_only=True))

    columnas: dict[str, tuple[int | None, int | None]] = {m: (None, None) for m in MESES}
    mes_actual = None
    for idx in range(col_tipo_generacion, len(fila_header)):
        etiqueta_mes = fila_meses[idx] if idx < len(fila_meses) else None
        if etiqueta_mes in MESES:
            mes_actual = etiqueta_mes
        if mes_actual is None:
            continue
        gen, cap = columnas[mes_actual]
        if fila_header[idx] == "Generación":
            columnas[mes_actual] = (idx, cap)
        elif fila_header[idx] == "Capacidad Efectiva Neta":
            columnas[mes_actual] = (gen, idx)

    faltantes = [m for m, (g, c) in columnas.items() if g is None or c is None]
    if faltantes:
        raise ValueError(f"No se pudieron ubicar columnas de Generación/Capacidad para: {faltantes}")
    return columnas  # type: ignore[return-value]


@dataclass(frozen=True)
class RegistroGeneracionMensual:
    departamento: str
    municipio: str | None
    agente: str
    codigo_submercado: str
    recurso: str  # nombre de la central/planta
    tipo_generacion: str
    anio: int
    mes: int  # 1-12
    generacion_kwh: Decimal | None
    capacidad_efectiva_kw: Decimal | None
    archivo_fuente: str
    hash_archivo_fuente: str


def _anio_del_nombre(nombre: str) -> int:
    tallo = Path(nombre).stem
    if not tallo[-4:].isdigit():
        raise ValueError(f"No se encontró el año al final del nombre «{nombre}» (p. ej. …_2025.xlsx).")
    return int(tallo[-4:])


def _registros_mensuales(wb, nombre: str, sha: str) -> list[RegistroGeneracionMensual]:
    ws = _hoja_generacion_mensual(wb)
    anio = _anio_del_nombre(nombre)
    fila_header = next(ws.iter_rows(min_row=FILA_HEADER, max_row=FILA_HEADER, values_only=True))
    col = _mapear_columnas_fijas(fila_header)
    columnas_mes = _mapear_columnas_mensuales(ws, col["tipo_generacion"])

    registros: list[RegistroGeneracionMensual] = []
    departamento_actual = municipio_actual = None
    for fila in ws.iter_rows(min_row=FILA_HEADER + 1, max_row=ws.max_row, values_only=True):
        agente, recurso = fila[col["agente"]], fila[col["recurso"]]
        if agente is None and recurso is None:
            continue
        if fila[col["departamento"]]:
            departamento_actual = fila[col["departamento"]]
        if fila[col["municipio"]]:
            municipio_actual = fila[col["municipio"]]
        codigo, tipo = fila[col["codigo_submercado"]], fila[col["tipo_generacion"]]
        for i, mes_nombre in enumerate(MESES):
            col_gen, col_cap = columnas_mes[mes_nombre]
            try:
                generacion = exacto(fila[col_gen]) if col_gen < len(fila) else None
                capacidad = exacto(fila[col_cap]) if col_cap < len(fila) else None
            except ArchivoXMInvalido as e:
                raise ArchivoXMInvalido(f"«{nombre}», {_texto(codigo) or _texto(recurso)}, "
                                        f"{mes_nombre.lower()}: {e}.") from None
            registros.append(RegistroGeneracionMensual(
                departamento=departamento_actual, municipio=municipio_actual,
                agente=_texto(agente), codigo_submercado=_texto(codigo), recurso=_texto(recurso),
                tipo_generacion=_texto(tipo), anio=anio, mes=i + 1,
                generacion_kwh=generacion, capacidad_efectiva_kw=capacidad,
                archivo_fuente=nombre, hash_archivo_fuente=sha,
            ))
    return registros


def parsear_xm_generacion_anual(ruta_xlsx: str | Path) -> list[RegistroGeneracionMensual]:
    """Formato mensual, desde una ruta. Lo usa el umbral por empresa."""
    ruta = Path(ruta_xlsx)
    contenido = ruta.read_bytes()
    wb = openpyxl.load_workbook(io.BytesIO(contenido), data_only=True)
    return _registros_mensuales(wb, ruta.name, _hash(contenido))


def _leer_mensual(wb, nombre: str, sha: str) -> LecturaXM:
    registros = _registros_mensuales(wb, nombre, sha)
    anio = _anio_del_nombre(nombre)
    agrupado: dict[tuple[str, str], list[RegistroGeneracionMensual]] = defaultdict(list)
    sin_codigo = 0
    for r in registros:
        if r.generacion_kwh is None and r.capacidad_efectiva_kw is None:
            continue  # mes sin dato para ese recurso
        if not r.codigo_submercado:
            sin_codigo += 1
            continue
        agrupado[(f"{r.anio}-{r.mes:02d}", r.codigo_submercado)].append(r)

    lectura = LecturaXM(archivo_nombre=nombre, archivo_sha256=sha, formato="mensual", anio=anio,
                        filas=[], registros_capacidad=registros)
    repetidos = set()
    for (periodo, codigo), grupo in sorted(agrupado.items()):
        if len(grupo) > 1:
            repetidos.add(codigo)
        gens = [r.generacion_kwh for r in grupo if r.generacion_kwh is not None]
        caps = [r.capacidad_efectiva_kw for r in grupo if r.capacidad_efectiva_kw is not None]
        a, m = map(int, periodo.split("-"))
        lectura.filas.append(FilaMensualXM(
            periodo=periodo, codigo_submercado=codigo, recurso=grupo[0].recurso, agente=grupo[0].agente,
            tipo_generacion=grupo[0].tipo_generacion,
            generacion_kwh=sum(gens, Decimal(0)) if gens else None,
            capacidad_kw=sum(caps, Decimal(0)) if caps else None,
            dias_mes=calendar.monthrange(a, m)[1], dias_reportados=None,
        ))
    if sin_codigo:
        lectura.avisos.append(f"{sin_codigo} registros mensuales sin código de submercado: no se tuvieron en cuenta.")
    if repetidos:
        lectura.avisos.append(
            f"{len(repetidos)} códigos aparecen en más de una fila del mismo mes (p. ej. "
            f"{', '.join(sorted(repetidos)[:5])}): se sumaron generación y capacidad."
        )
    lectura.avisos.append("El formato mensual de XM no trae la versión: hay que declararla al cargar.")
    return lectura


if __name__ == "__main__":
    import sys

    ruta = Path(sys.argv[1])
    lect = leer_xm(ruta.read_bytes(), ruta.name)
    print(f"{ruta.name}: formato {lect.formato}, año {lect.anio}, {len(lect.filas)} filas recurso-mes")
    print(f"sha256 {lect.archivo_sha256}")
    for p, vs in lect.versiones_por_mes().items():
        print(p, ", ".join(sorted(vs)) or "sin versión", lect.dias_en_archivo.get(p, ""))
    for a in lect.avisos:
        print("AVISO:", a)
