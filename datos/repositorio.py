"""
Repositorio SQLite del Liquidador TSE (paso 2 del orden de trabajo).

Un solo archivo, `almacen/liquidador.db`. Solo biblioteca estándar (`sqlite3`).
El motor (`motor/`) no importa este módulo: la liquidación sigue corriendo en
memoria (CLAUDE.md, regla 10). Este módulo guarda y recupera; no calcula.

Reglas que se hacen cumplir aquí:
  * Cifras como texto exacto; `float` rechazado (regla 5).
  * Sin delimitación confirmada no hay delimitación aplicable (reglas 2 y 3).
  * Mes parcial (Jorge, 2026-11-14; reemplaza «solo meses completos» del
    2026-11-12): si una delimitación empieza a regir después del día 1, ese
    mes se liquida solo desde ese día (`delimitacion_del_tramo`,
    `generacion_del_tramo`). `exigir_delimitacion` y `MesPartido` conservan el
    sentido «rige el mes completo» para la proyección, que solo estima meses
    completos.
  * Nada se borra: delimitaciones se reemplazan, liquidaciones se versionan
    (reglas 3 y 7). Las liquidaciones de un mes se guardan en una sola
    transacción (`guardar_liquidaciones`).
  * Versiones de XM (decisión de Jorge, 2026-11-12): se guardan todas; solo la
    versión firme (TXF) produce una liquidación «oficial». Con TX1, TX2… la
    liquidación es «preliminar», de referencia.

Validaciones de contenido de una delimitación (paso 3): viven en
`motor/delimitacion.py`; `confirmar_delimitacion` las corre antes de
`marcar_vigente`. La pantalla usa siempre `confirmar_delimitacion`.
"""
from __future__ import annotations

import calendar
import os
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from motor import delimitacion as dm
from motor.delimitacion import SIN_ASIGNAR

RAIZ = Path(__file__).resolve().parents[1]
# La variable de entorno LIQUIDADOR_BD permite usar otra ruta (p. ej., fuera de OneDrive).
RUTA_BD = Path(os.environ.get("LIQUIDADOR_BD") or RAIZ / "almacen" / "liquidador.db")
ESQUEMA = Path(__file__).with_name("esquema.sql")
VERSION_ESQUEMA = 6

# Catálogo inicial de versiones de liquidación de XM. El archivo de 2026 trae
# TX1 a TX5 y TXF (v4). Se pueden agregar otras (p. ej. TXR) con
# `agregar_version_xm`; el orden decide cuál es más reciente.
VERSIONES_XM_INICIALES = (
    ("TX1", 10, 0), ("TX2", 20, 0), ("TX3", 30, 0), ("TX4", 40, 0), ("TX5", 50, 0), ("TXF", 100, 1),
)


class ErrorRepositorio(Exception):
    """Operación que rompe una regla del Liquidador."""


class SinDelimitacionVigente(ErrorRepositorio):
    pass


class SinGeneracion(ErrorRepositorio):
    pass


class MesIncompleto(SinGeneracion):
    """XM no reporta todos los días del mes para la central: no se liquida."""


class SinDatoDiario(SinGeneracion):
    """Mes parcial sin generación por día: no se puede sumar solo el tramo."""


class MesPartido(ErrorRepositorio):
    """Una delimitación empieza a regir dentro del mes (no el día 1), así que
    ninguna rige el mes COMPLETO. Lo usa la proyección, que solo estima meses
    completos. La liquidación sí liquida ese mes desde el día de vigencia (mes
    parcial, Jorge 2026-11-14): ver `delimitacion_del_tramo`."""


# ---------------------------------------------------------------------------
# Números exactos <-> texto
# ---------------------------------------------------------------------------

def a_texto(valor) -> str | None:
    """Decimal/Fraction/int -> texto exacto. None se conserva (dato ausente).

    Un texto se lee como en el formulario («86.537,28373», «2900.92344»,
    «3/7»; «N/A» o vacío = None) y se guarda NORMALIZADO, con punto decimal:
    antes se validaba «43,21875» pero se guardaba con coma y al leerlo
    fallaba (corregido el 2026-11-15)."""
    if valor is None:
        return None
    if isinstance(valor, (float, bool)):
        raise ErrorRepositorio(f"Valor no exacto rechazado: {valor!r}")
    if isinstance(valor, Fraction):
        return str(valor.numerator) if valor.denominator == 1 else f"{valor.numerator}/{valor.denominator}"
    if isinstance(valor, Decimal):
        if not valor.is_finite():
            raise ErrorRepositorio(f"Valor no finito rechazado: {valor!r}")
        return str(valor)
    if isinstance(valor, int):
        return str(valor)
    if isinstance(valor, str):
        texto = valor.strip()
        es_fraccion = "/" in texto and texto.upper() != dm.NA
        try:
            numero = Fraction(texto) if es_fraccion else dm.numero_desde_texto(texto)
        except (ValueError, ZeroDivisionError) as e:
            raise ErrorRepositorio(f"«{valor}» no es un número exacto") from e
        return a_texto(numero)
    raise ErrorRepositorio(f"Tipo no admitido: {type(valor).__name__}")


def a_decimal(texto: str | None) -> Decimal | None:
    return None if texto is None else Decimal(texto)


def a_fraccion(texto: str | None) -> Fraction | None:
    return None if texto is None else Fraction(texto)


def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Conexión y esquema
# ---------------------------------------------------------------------------

def abrir(ruta: str | Path = RUTA_BD) -> sqlite3.Connection:
    """Abre (y si hace falta crea) la base. `":memory:"` sirve para pruebas."""
    if str(ruta) != ":memory:":
        Path(ruta).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(ruta))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if version == 2:
        _migrar_2_a_3(con)
        version = 3
    if version in (3, 4, 5, 6):
        _migrar_3_a_4(con)  # en una v4 completa no cambia nada
        version = max(version, 4)
    if version in (4, 5, 6):
        _migrar_4_a_5(con)  # en una v5 completa no cambia nada
        version = max(version, 5)
    if version in (5, 6):
        _migrar_5_a_6(con)  # en una v6 completa no cambia nada
        version = 6
    if version not in (0, VERSION_ESQUEMA):
        raise ErrorRepositorio(
            f"La base {ruta} es de la versión {version} del esquema y esta aplicación usa la "
            f"{VERSION_ESQUEMA}. Muévala a otra carpeta y vuelva a abrir para crear una nueva."
        )
    if version == 0:
        with con:
            con.executescript(ESQUEMA.read_text(encoding="utf-8"))
            con.executemany(
                "INSERT INTO versiones_xm (codigo, orden, es_firme) VALUES (?, ?, ?)",
                VERSIONES_XM_INICIALES,
            )
            con.execute(
                "INSERT INTO autoridades (sigla, tipo, nombre) VALUES (?, 'SIN_ASIGNAR', ?)",
                (SIN_ASIGNAR, "Autoridad ambiental urbana o área en litigio (retenido sin asignar)"),
            )
            con.execute(f"PRAGMA user_version = {VERSION_ESQUEMA}")
    return con


def _migrar_2_a_3(con) -> None:
    """v3: `proyectos.codigo_submercado` deja de ser única (centrales que XM
    agrupa en un solo código). SQLite no quita un UNIQUE: se reconstruye la tabla."""
    con.execute("PRAGMA foreign_keys = OFF")
    con.executescript("""
        BEGIN;
        CREATE TABLE proyectos_v3 (
            id                 INTEGER PRIMARY KEY,
            nombre             TEXT NOT NULL UNIQUE,
            codigo_submercado  TEXT,
            notas              TEXT
        );
        INSERT INTO proyectos_v3 (id, nombre, codigo_submercado, notas)
            SELECT id, nombre, codigo_submercado, notas FROM proyectos;
        DROP TABLE proyectos;
        ALTER TABLE proyectos_v3 RENAME TO proyectos;
        CREATE INDEX proyectos_central ON proyectos (codigo_submercado);
        PRAGMA user_version = 3;
        COMMIT;
    """)
    con.execute("PRAGMA foreign_keys = ON")
    if con.execute("PRAGMA foreign_key_check").fetchall():
        raise ErrorRepositorio("La migración a la versión 3 dejó referencias rotas.")


def _migrar_3_a_4(con) -> None:
    """v4 (paso 4): formato y versión declarada del cargue, días por mes,
    capacidad por empresa y versiones TX3-TX5 de XM. Idempotente: completa
    también una base que quedó en v4 a medias (le pasó a la base real el
    2026-11-14, abierta por la app durante el desarrollo)."""
    columnas = {
        "cargas_xm": (("formato", "TEXT CHECK (formato IN ('mensual', 'diario'))"),
                      ("version_declarada", "TEXT REFERENCES versiones_xm(codigo)")),
        "generacion": (("dias_reportados", "INTEGER"), ("dias_archivo", "INTEGER"), ("dias_mes", "INTEGER")),
    }
    faltan = [
        (tabla, nombre, tipo) for tabla, cols in columnas.items()
        for nombre, tipo in cols
        if nombre not in {r[1] for r in con.execute(f"PRAGMA table_info({tabla})")}
    ]
    tiene_capacidad = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'capacidad_empresas'").fetchone()
    versiones = {r[0] for r in con.execute("SELECT codigo FROM versiones_xm")}
    completa = not faltan and tiene_capacidad and {v[0] for v in VERSIONES_XM_INICIALES} <= versiones
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if completa and version >= 4:  # también en v5 y v6
        return  # nada que hacer: no se escribe en la base
    with con:
        for tabla, nombre, tipo in faltan:
            con.execute(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {tipo}")
        con.execute("""
            CREATE TABLE IF NOT EXISTS capacidad_empresas (
                carga_id                 INTEGER NOT NULL REFERENCES cargas_xm(id),
                anio                     INTEGER NOT NULL,
                agente                   TEXT NOT NULL,
                capacidad_total_kw       TEXT NOT NULL,
                capacidad_hidraulica_kw  TEXT NOT NULL,
                sujeta                   INTEGER NOT NULL CHECK (sujeta IN (0, 1)),
                PRIMARY KEY (carga_id, agente)
            )""")
        con.executemany(
            "INSERT OR IGNORE INTO versiones_xm (codigo, orden, es_firme) VALUES (?, ?, ?)",
            VERSIONES_XM_INICIALES,
        )
        if version < 4:  # una v5 a la que le falta algo de la v4 se completa sin bajarla de versión
            con.execute("PRAGMA user_version = 4")


COLUMNAS_V5 = (
    ("codigo_submercado", "TEXT"), ("empresa", "TEXT"), ("capacidad_empresa_kw", "TEXT"),
    ("capacidad_anio", "INTEGER"), ("dias_reportados", "INTEGER"), ("dias_archivo", "INTEGER"),
    ("tvb_fuente", "TEXT"),
)


def _migrar_4_a_5(con) -> None:
    """v5 (paso 5): columnas de trazabilidad de la liquidación y tabla de las
    delimitaciones que la respaldan. Solo agrega; idempotente (completa una v5
    a medias y no escribe en una v5 completa)."""
    tiene = {r[1] for r in con.execute("PRAGMA table_info(liquidaciones)")}
    faltan = [(n, t) for n, t in COLUMNAS_V5 if n not in tiene]
    tabla = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'liquidacion_delimitaciones'").fetchone()
    version = con.execute("PRAGMA user_version").fetchone()[0]
    if not faltan and tabla and version >= 5:
        return
    with con:
        for nombre, tipo in faltan:
            con.execute(f"ALTER TABLE liquidaciones ADD COLUMN {nombre} {tipo}")
        con.execute("""
            CREATE TABLE IF NOT EXISTS liquidacion_delimitaciones (
                liquidacion_id   INTEGER NOT NULL REFERENCES liquidaciones(id),
                delimitacion_id  INTEGER NOT NULL REFERENCES delimitaciones(id),
                proyecto_id      INTEGER NOT NULL REFERENCES proyectos(id),
                radicado         TEXT NOT NULL,
                pdf_sha256       TEXT,
                PRIMARY KEY (liquidacion_id, delimitacion_id)
            )""")
        # Las liquidaciones anteriores a la v5 citaban una sola delimitación.
        con.execute(
            "INSERT OR IGNORE INTO liquidacion_delimitaciones "
            "(liquidacion_id, delimitacion_id, proyecto_id, radicado, pdf_sha256) "
            "SELECT l.id, d.id, d.proyecto_id, d.radicado, d.pdf_sha256 FROM liquidaciones l "
            "JOIN delimitaciones d ON d.id = l.delimitacion_id")
        if version < 5:
            con.execute("PRAGMA user_version = 5")


COLUMNAS_V6 = (("dia_desde", "INTEGER"), ("dia_hasta", "INTEGER"))
TABLAS_V6 = {
    "generacion_diaria": """
        CREATE TABLE IF NOT EXISTS generacion_diaria (
            carga_id            INTEGER NOT NULL REFERENCES cargas_xm(id),
            codigo_submercado   TEXT NOT NULL,
            fecha               TEXT NOT NULL,
            generacion_kwh      TEXT NOT NULL,
            horas_completas     INTEGER NOT NULL CHECK (horas_completas IN (0, 1)),
            PRIMARY KEY (carga_id, codigo_submercado, fecha)
        )""",
    "delimitacion_cambios": """
        CREATE TABLE IF NOT EXISTS delimitacion_cambios (
            id               INTEGER PRIMARY KEY,
            delimitacion_id  INTEGER NOT NULL REFERENCES delimitaciones(id),
            campo            TEXT NOT NULL,
            antes            TEXT,
            despues          TEXT,
            motivo           TEXT,
            cambiado_en      TEXT NOT NULL
        )""",
}


def _migrar_5_a_6(con) -> None:
    """v6 (2026-11-14): generación por día y mes parcial (se liquida desde el
    día en que empieza a regir la delimitación), y registro de los cambios de
    vigencia. Solo agrega; idempotente."""
    tiene = {r[1] for r in con.execute("PRAGMA table_info(liquidaciones)")}
    faltan = [(n, t) for n, t in COLUMNAS_V6 if n not in tiene]
    tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not faltan and set(TABLAS_V6) <= tablas and con.execute("PRAGMA user_version").fetchone()[0] == 6:
        return
    with con:
        for nombre, tipo in faltan:
            con.execute(f"ALTER TABLE liquidaciones ADD COLUMN {nombre} {tipo}")
        for sql in TABLAS_V6.values():
            con.execute(sql)
        con.execute("PRAGMA user_version = 6")


def agregar_version_xm(con, codigo: str, orden: int, es_firme: bool = False) -> None:
    with con:
        con.execute(
            "INSERT INTO versiones_xm (codigo, orden, es_firme) VALUES (?, ?, ?)",
            (codigo.strip().upper(), orden, int(es_firme)),
        )


def _version_xm(con, codigo: str) -> sqlite3.Row:
    fila = con.execute("SELECT * FROM versiones_xm WHERE codigo = ?", (codigo,)).fetchone()
    if fila is None:
        raise ErrorRepositorio(f"Versión de XM desconocida: «{codigo}»")
    return fila


# ---------------------------------------------------------------------------
# Maestros
# ---------------------------------------------------------------------------

def cargar_autoridades(con, autoridades: Iterable) -> int:
    """Inserta o actualiza autoridades (objetos con sigla, tipo, nombre)."""
    n = 0
    with con:
        for a in autoridades:
            con.execute(
                "INSERT INTO autoridades (sigla, tipo, nombre) VALUES (?, ?, ?) "
                "ON CONFLICT(sigla) DO UPDATE SET tipo = excluded.tipo, nombre = excluded.nombre",
                (a.sigla, a.tipo, a.nombre),
            )
            n += 1
    return n


def crear_proyecto(con, nombre: str, codigo_submercado: str | None = None, notas: str | None = None) -> int:
    with con:
        cur = con.execute(
            "INSERT INTO proyectos (nombre, codigo_submercado, notas) VALUES (?, ?, ?)",
            (nombre, codigo_submercado, notas),
        )
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Delimitaciones
# ---------------------------------------------------------------------------

def crear_delimitacion(
    con,
    proyecto_id: int,
    radicado: str,
    fecha_documento: str,
    vigente_desde: str | None,
    modo: str,
    filas: Iterable[Mapping],
    pdf_nombre: str | None = None,
    pdf_ruta: str | None = None,
    pdf_sha256: str | None = None,
    observaciones: str | None = None,
    trae_s2: bool | None = None,
    paramo_declarado: bool | None = None,
    s1_t_declarado=None,
    s2_t_declarado=None,
    origen: str = "formulario",
) -> int:
    """Crea una delimitación en `borrador`. `filas`: dicts con `autoridad` y,
    según el modo, `s1_ha`/`s2_ha` o `porcentaje`. Una celda «N/A» de la
    delimitación se pasa como None y queda NULL. `trae_s2` = la delimitación
    trae la COLUMNA S2; si no se indica, se deduce de las filas. La vigencia
    puede quedar vacía en borrador; se exige al confirmar."""
    filas = list(filas)
    if trae_s2 is None:
        trae_s2 = any(f.get("s2_ha") is not None for f in filas)
    with con:
        cur = con.execute(
            "INSERT INTO delimitaciones (proyecto_id, radicado, fecha_documento, vigente_desde, "
            "modo, trae_s2, paramo_declarado, s1_t_declarado, s2_t_declarado, origen, "
            "pdf_nombre, pdf_ruta, pdf_sha256, creada_en, observaciones) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (proyecto_id, radicado, fecha_documento, vigente_desde or None, modo, int(bool(trae_s2)),
             None if paramo_declarado is None else int(paramo_declarado),
             a_texto(s1_t_declarado), a_texto(s2_t_declarado), origen,
             pdf_nombre, pdf_ruta, pdf_sha256, _ahora(), observaciones),
        )
        delim_id = cur.lastrowid
        _insertar_filas(con, delim_id, filas)
    return delim_id


def _insertar_filas(con, delim_id: int, filas: Iterable[Mapping]) -> None:
    for f in filas:
        con.execute(
            "INSERT INTO delimitacion_filas (delimitacion_id, autoridad_sigla, s1_ha, s2_ha, porcentaje) "
            "VALUES (?, ?, ?, ?, ?)",
            (delim_id, f["autoridad"], a_texto(f.get("s1_ha")),
             a_texto(f.get("s2_ha")), a_texto(f.get("porcentaje"))),
        )


CAMPOS_EDITABLES_BORRADOR = (
    "radicado", "fecha_documento", "vigente_desde", "modo", "trae_s2", "paramo_declarado",
    "s1_t_declarado", "s2_t_declarado", "pdf_nombre", "pdf_ruta", "pdf_sha256", "observaciones",
)


def actualizar_borrador(con, delimitacion_id: int, filas: Iterable[Mapping] | None = None, **campos) -> None:
    """Corrige un borrador (cabecera y/o filas). Una delimitación confirmada
    no se edita: se reemplaza por otra (regla 3)."""
    d = _delimitacion(con, delimitacion_id)
    if d["estado"] != "borrador":
        raise ErrorRepositorio(
            f"La delimitación {d['radicado']} está «{d['estado']}»: no se edita; se reemplaza con una nueva."
        )
    raros = set(campos) - set(CAMPOS_EDITABLES_BORRADOR)
    if raros:
        raise ErrorRepositorio(f"Campos no editables: {', '.join(sorted(raros))}")
    valores = dict(campos)
    for c in ("s1_t_declarado", "s2_t_declarado"):
        if c in valores:
            valores[c] = a_texto(valores[c])
    for c in ("trae_s2", "paramo_declarado"):
        if c in valores and valores[c] is not None:
            valores[c] = int(bool(valores[c]))
    if "vigente_desde" in valores:
        valores["vigente_desde"] = valores["vigente_desde"] or None
    with con:
        if valores:
            con.execute(
                f"UPDATE delimitaciones SET {', '.join(f'{c} = ?' for c in valores)} WHERE id = ?",
                (*valores.values(), delimitacion_id),
            )
        if filas is not None:
            con.execute("DELETE FROM delimitacion_filas WHERE delimitacion_id = ?", (delimitacion_id,))
            _insertar_filas(con, delimitacion_id, filas)


def _delimitacion(con, delimitacion_id: int) -> sqlite3.Row:
    d = con.execute("SELECT * FROM delimitaciones WHERE id = ?", (delimitacion_id,)).fetchone()
    if d is None:
        raise ErrorRepositorio(f"No existe la delimitación {delimitacion_id}")
    return d


def delimitacion_en_memoria(con, delimitacion_id: int) -> dm.Delimitacion:
    """La delimitación tal como la ve el motor (`motor/delimitacion.py`)."""
    d = _delimitacion(con, delimitacion_id)
    filas = tuple(
        dm.FilaDelimitacion(
            autoridad=f["autoridad_sigla"],
            s1_ha=a_decimal(f["s1_ha"]),
            s2_ha=a_decimal(f["s2_ha"]),
            porcentaje=a_fraccion(f["porcentaje"]),
        )
        for f in con.execute(
            "SELECT * FROM delimitacion_filas WHERE delimitacion_id = ? ORDER BY rowid", (delimitacion_id,)
        )
    )
    return dm.Delimitacion(
        modo=d["modo"], filas=filas, trae_s2=bool(d["trae_s2"]),
        paramo_declarado=None if d["paramo_declarado"] is None else bool(d["paramo_declarado"]),
        s1_t_declarado=a_decimal(d["s1_t_declarado"]), s2_t_declarado=a_decimal(d["s2_t_declarado"]),
    )


def validar_delimitacion(con, delimitacion_id: int) -> dm.ResultadoValidacion:
    """Validaciones de la regla 4, sobre lo guardado."""
    d = _delimitacion(con, delimitacion_id)
    p = con.execute("SELECT * FROM proyectos WHERE id = ?", (d["proyecto_id"],)).fetchone()
    siglas = {r["sigla"] for r in con.execute("SELECT sigla FROM autoridades")}
    r = dm.validar(
        delimitacion_en_memoria(con, delimitacion_id), siglas,
        proyecto_enlazado=bool(p["codigo_submercado"]), vigente_desde=d["vigente_desde"],
        fecha_documento=d["fecha_documento"],
    )
    if not d["pdf_sha256"]:
        r.errores.append("Falta el PDF de la delimitación como respaldo.")
    if d["estado"] == "borrador" and d["vigente_desde"]:
        actual = con.execute(
            "SELECT * FROM delimitaciones WHERE proyecto_id = ? AND estado = 'vigente'", (d["proyecto_id"],)
        ).fetchone()
        if actual is not None and actual["vigente_desde"] > d["vigente_desde"]:
            r.errores.append(
                f"La vigente actual ({actual['radicado']}) rige desde {actual['vigente_desde']}: esta debe regir "
                f"desde esa fecha (para corregirla) o desde una posterior."
            )
        elif actual is not None and actual["vigente_desde"] == d["vigente_desde"]:
            r.avisos.append(
                f"Rige desde la misma fecha que la vigente ({actual['radicado']}): la corrige y la sustituye en "
                f"todos sus meses. Los meses ya liquidados se deben volver a liquidar; queda una versión nueva."
            )
    if p["codigo_submercado"]:
        _validar_central_compartida(con, d, p, r)
    return r


def proyectos_de_central(con, codigo_submercado: str) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM proyectos WHERE codigo_submercado = ? ORDER BY nombre", (codigo_submercado,)
    ).fetchall()


def _reparto(con, delimitacion_id: int) -> dict[str, Fraction]:
    """Razones exactas por autoridad, sin las de 0 %."""
    return {x.autoridad_id: x.razon for x in dm.razones(delimitacion_en_memoria(con, delimitacion_id)) if x.razon}


def _validar_central_compartida(con, d, p, r) -> None:
    """Varios proyectos en una misma central de XM solo se aceptan si reparten
    igual: así agruparlos no cambia lo que recibe cada autoridad (Jorge,
    2026-11-14). Se compara contra la delimitación vigente de cada uno o, si
    no tiene, contra su borrador más reciente."""
    otros = [o for o in proyectos_de_central(con, p["codigo_submercado"]) if o["id"] != p["id"]]
    if not otros:
        return
    try:
        mio = _reparto(con, d["id"])
    except Exception:
        return  # el contenido aún no cuadra; los otros errores ya lo dicen
    # Marca explícita, no búsqueda de texto: antes se buscaba «reparte distinto»
    # en los mensajes, el error dice «reparten distinto» y salían a la vez el
    # error y el aviso «Mismo reparto» (corregido el 2026-11-15).
    distinto, iguales = False, []
    for o in otros:
        ref = con.execute(
            "SELECT * FROM delimitaciones WHERE proyecto_id = ? "
            "ORDER BY estado = 'vigente' DESC, id DESC LIMIT 1", (o["id"],)
        ).fetchone()
        if ref is None:
            continue
        try:
            suyo = _reparto(con, ref["id"])
        except Exception:
            continue  # su borrador aún no cuadra; se validará al confirmarlo
        distinto = distinto or suyo != mio
        if suyo == mio:
            iguales.append(o["nombre"])
        if suyo != mio and ref["estado"] != "vigente":
            r.avisos.append(
                f"El borrador de «{o['nombre']}» ({ref['radicado']}), que comparte la central "
                f"{p['codigo_submercado']}, reparte distinto. Al confirmarlo, se bloqueará si no se corrige."
            )
        elif suyo != mio:
            r.errores.append(
                f"Comparte la central {p['codigo_submercado']} con «{o['nombre']}» ({ref['radicado']}), pero "
                f"reparten distinto: XM da una sola generación y no se puede partir por proyecto."
            )
        elif ref["estado"] == "vigente" and ref["vigente_desde"] != d["vigente_desde"]:
            r.avisos.append(
                f"«{o['nombre']}», que comparte la central {p['codigo_submercado']}, rige desde "
                f"{ref['vigente_desde']}; esta, desde {d['vigente_desde'] or '—'}. La central se liquida "
                f"solo en los meses en que todas sus delimitaciones estén vigentes."
            )
    if iguales and not distinto:  # solo se afirma «mismo reparto» de las que se pudieron comparar
        r.avisos.append(
            f"Comparte la central {p['codigo_submercado']} con: {', '.join(iguales)}. "
            f"Mismo reparto, así que se liquidan juntas, una sola vez, con la generación de la central."
        )


def confirmar_delimitacion(con, delimitacion_id: int, vigente_desde: str | None = None) -> dm.ResultadoValidacion:
    """La persona confirmó las cifras contra el PDF. Valida y, si todo cuadra,
    la pasa a `vigente`. Si no cuadra, no cambia nada y devuelve los errores."""
    if vigente_desde:
        actualizar_borrador(con, delimitacion_id, vigente_desde=vigente_desde)
    r = validar_delimitacion(con, delimitacion_id)
    if r.ok:
        marcar_vigente(con, delimitacion_id)
    return r


def enlazar_proyecto(con, proyecto_id: int, codigo_submercado: str | None) -> None:
    """Enlaza (o desenlaza, con None) un proyecto a su central de XM."""
    codigo = (codigo_submercado or "").strip().upper() or None
    with con:
        con.execute("UPDATE proyectos SET codigo_submercado = ? WHERE id = ?", (codigo, proyecto_id))


def listar_delimitaciones(con) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT d.*, p.nombre AS proyecto, p.codigo_submercado, p.notas AS proyecto_notas, "
        "(SELECT count(*) FROM delimitacion_filas f WHERE f.delimitacion_id = d.id) AS n_filas "
        "FROM delimitaciones d JOIN proyectos p ON p.id = d.proyecto_id "
        "ORDER BY p.nombre, d.vigente_desde, d.id"
    ).fetchall()


def marcar_vigente(con, delimitacion_id: int) -> None:
    """Pasa una delimitación de `borrador` a `vigente`. La vigente anterior del
    mismo proyecto pasa a `reemplazada` (no se borra y sigue rigiendo los
    periodos previos). No valida el contenido: para eso, `confirmar_delimitacion`."""
    d = _delimitacion(con, delimitacion_id)
    if d["estado"] != "borrador":
        raise ErrorRepositorio(f"La delimitación {delimitacion_id} ya está «{d['estado']}»")
    if not d["vigente_desde"]:
        raise ErrorRepositorio(f"La delimitación {d['radicado']} no tiene fecha de vigencia.")
    anterior = con.execute(
        "SELECT * FROM delimitaciones WHERE proyecto_id = ? AND estado = 'vigente'",
        (d["proyecto_id"],),
    ).fetchone()
    # Misma fecha = corrección: la nueva la sustituye en todos sus meses (la
    # anterior queda «reemplazada», con su rastro). Una fecha anterior no se acepta.
    if anterior is not None and anterior["vigente_desde"] > d["vigente_desde"]:
        raise ErrorRepositorio(
            f"La vigente actual ({anterior['radicado']}) rige desde {anterior['vigente_desde']}; "
            f"la nueva debe regir desde esa fecha (para corregirla) o desde una posterior."
        )
    with con:
        if anterior is not None:
            con.execute(
                "UPDATE delimitaciones SET estado = 'reemplazada', reemplazada_por = ? WHERE id = ?",
                (delimitacion_id, anterior["id"]),
            )
        con.execute(
            "UPDATE delimitaciones SET estado = 'vigente', confirmada_en = ? WHERE id = ?",
            (_ahora(), delimitacion_id),
        )


def _primer_dia(periodo: str) -> str:
    return f"{periodo}-01"


def delimitacion_que_parte_el_mes(con, proyecto_id: int, periodo: str) -> sqlite3.Row | None:
    """Delimitación confirmada que empieza a regir DENTRO del mes, después del
    día 1. Si existe, el mes está partido y no se liquida."""
    return con.execute(
        "SELECT * FROM delimitaciones WHERE proyecto_id = ? "
        "AND estado IN ('vigente', 'reemplazada') "
        "AND substr(vigente_desde, 1, 7) = ? AND vigente_desde > ? LIMIT 1",
        (proyecto_id, periodo, _primer_dia(periodo)),
    ).fetchone()


def delimitacion_aplicable(con, proyecto_id: int, periodo: str) -> sqlite3.Row | None:
    """La delimitación confirmada que rige el mes AAAA-MM COMPLETO: la de mayor
    `vigente_desde` que no sea posterior al día 1. Los borradores nunca
    aplican. None = «sin delimitación» o «mes partido» (ver
    `exigir_delimitacion` para distinguirlos)."""
    if delimitacion_que_parte_el_mes(con, proyecto_id, periodo) is not None:
        return None
    return con.execute(
        "SELECT * FROM delimitaciones WHERE proyecto_id = ? "
        "AND estado IN ('vigente', 'reemplazada') AND vigente_desde <= ? "
        "ORDER BY vigente_desde DESC, id DESC LIMIT 1",  # misma fecha: manda la que corrigió
        (proyecto_id, _primer_dia(periodo)),
    ).fetchone()


def exigir_delimitacion(con, proyecto_id: int, periodo: str) -> sqlite3.Row:
    partida = delimitacion_que_parte_el_mes(con, proyecto_id, periodo)
    if partida is not None:
        raise MesPartido(
            f"En {periodo} empieza a regir la delimitación {partida['radicado']} "
            f"({partida['vigente_desde']}): mes partido, no se liquida."
        )
    d = delimitacion_aplicable(con, proyecto_id, periodo)
    if d is None:
        raise SinDelimitacionVigente(
            f"El proyecto {proyecto_id} no tiene delimitación vigente para {periodo}: no se liquida."
        )
    return d


def ultimo_dia(periodo: str) -> int:
    return calendar.monthrange(int(periodo[:4]), int(periodo[5:7]))[1]


def delimitacion_del_tramo(con, proyecto_id: int, periodo: str) -> tuple[sqlite3.Row, int, sqlite3.Row | None]:
    """Mes parcial (Jorge, 2026-11-14). Devuelve (delimitación, día desde el
    que se liquida, delimitación anterior o None):

    * Si la delimitación que rige al cierre del mes empezó antes del mes (o el
      día 1), el día es 1: mes completo.
    * Si empezó DENTRO del mes, se liquida desde ese día (incluido) hasta el
      último. Los días anteriores no se liquidan con ella; si los regía otra
      delimitación, se devuelve para avisarlo.
    """
    fin = f"{periodo}-{ultimo_dia(periodo):02d}"
    d = con.execute(
        "SELECT * FROM delimitaciones WHERE proyecto_id = ? AND estado IN ('vigente', 'reemplazada') "
        "AND vigente_desde <= ? ORDER BY vigente_desde DESC, id DESC LIMIT 1", (proyecto_id, fin),
    ).fetchone()
    if d is None:
        raise SinDelimitacionVigente(
            f"El proyecto {proyecto_id} no tiene delimitación vigente para {periodo}: no se liquida.")
    dia = int(d["vigente_desde"][8:10]) if d["vigente_desde"][:7] == periodo else 1
    anterior = None
    if dia > 1:
        anterior = con.execute(
            "SELECT * FROM delimitaciones WHERE proyecto_id = ? AND estado IN ('vigente', 'reemplazada') "
            "AND vigente_desde < ? ORDER BY vigente_desde DESC, id DESC LIMIT 1",
            (proyecto_id, d["vigente_desde"]),
        ).fetchone()
    return d, dia, anterior


def _corregidas_por(con, d: sqlite3.Row) -> list[sqlite3.Row]:
    """Delimitaciones que `d` corrigió: las que reemplazó con la MISMA fecha de
    vigencia, directamente o en cadena (la corrección de una corrección). Una
    delimitación corregida no debe volver a regir ningún día."""
    cadena, actual = [], d
    while True:
        corregida = con.execute(
            "SELECT * FROM delimitaciones WHERE reemplazada_por = ? AND vigente_desde = ?",
            (actual["id"], actual["vigente_desde"])).fetchone()
        if corregida is None:
            return cadena
        cadena.append(corregida)
        actual = corregida


def cambiar_vigencia(con, delimitacion_id: int, nueva_fecha: str, motivo: str | None = None) -> list[int]:
    """Ajusta la fecha de vigencia de una delimitación YA CONFIRMADA (Jorge,
    2026-11-14). Las cifras siguen sin poder editarse. Queda registrado en
    `delimitacion_cambios`. Los meses ya liquidados con la fecha anterior se
    deben volver a guardar (la pantalla Liquidación los señala).

    Si la delimitación es una CORRECCIÓN (reemplazó a otra con la misma
    fecha), la corregida se mueve con ella y su cambio también queda
    registrado: si se quedara en la fecha vieja, volvería a regir los días
    intermedios con las cifras que se corrigieron (fallo corregido el
    2026-11-15). Devuelve los ids de las corregidas que se movieron."""
    d = _delimitacion(con, delimitacion_id)
    if d["estado"] != "vigente":
        raise ErrorRepositorio(
            f"La delimitación {d['radicado']} está «{d['estado']}»: solo se ajusta la vigencia de una vigente "
            f"(la de un borrador se cambia en el formulario).")
    try:
        datetime.strptime(nueva_fecha, "%Y-%m-%d")
    except (TypeError, ValueError):
        raise ErrorRepositorio(f"Fecha de vigencia inválida: «{nueva_fecha}» (use AAAA-MM-DD).") from None
    if nueva_fecha == d["vigente_desde"]:
        return []
    if nueva_fecha < d["fecha_documento"]:
        raise ErrorRepositorio(
            f"No puede regir desde {nueva_fecha}, antes de la fecha del documento ({d['fecha_documento']}): "
            f"las delimitaciones rigen hacia adelante.")
    corregidas = _corregidas_por(con, d)
    # La que se reemplazó con una fecha ANTERIOR (no una corrección) pone el límite.
    base = corregidas[-1] if corregidas else d
    previa = con.execute(
        "SELECT * FROM delimitaciones WHERE reemplazada_por = ? ORDER BY vigente_desde DESC LIMIT 1",
        (base["id"],)).fetchone()
    if previa is not None and nueva_fecha < previa["vigente_desde"]:
        raise ErrorRepositorio(
            f"Reemplaza a {previa['radicado']}, que rige desde {previa['vigente_desde']}: no puede regir antes.")
    motivo = (motivo or "").strip() or None
    acompana = (f"Se mueve con la delimitación {d['radicado']} (id {d['id']}), que la corrigió: una "
                f"delimitación corregida no vuelve a regir." + (f" Motivo: {motivo}" if motivo else ""))
    with con:
        for fila, texto in [(d, motivo), *((c, acompana) for c in corregidas)]:
            con.execute("UPDATE delimitaciones SET vigente_desde = ? WHERE id = ?", (nueva_fecha, fila["id"]))
            con.execute(
                "INSERT INTO delimitacion_cambios (delimitacion_id, campo, antes, despues, motivo, cambiado_en) "
                "VALUES (?, 'vigente_desde', ?, ?, ?, ?)",
                (fila["id"], fila["vigente_desde"], nueva_fecha, texto, _ahora()))
    return [c["id"] for c in corregidas]


def cambios_de_delimitacion(con, delimitacion_id: int) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM delimitacion_cambios WHERE delimitacion_id = ? ORDER BY id", (delimitacion_id,)).fetchall()


def filas_delimitacion(con, delimitacion_id: int) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM delimitacion_filas WHERE delimitacion_id = ? ORDER BY autoridad_sigla",
        (delimitacion_id,),
    ).fetchall()


# ---------------------------------------------------------------------------
# Generación de XM y TVB
# ---------------------------------------------------------------------------

def registrar_carga_xm(con, archivo_nombre: str, archivo_sha256: str,
                       filas: Iterable[Mapping], observaciones: str | None = None,
                       formato: str | None = None, version_declarada: str | None = None,
                       capacidades: Iterable[Mapping] = (), diaria: Iterable[Mapping] = ()) -> int:
    """Guarda un archivo de XM. Cada fila trae su `periodo` y su `version_xm`
    (un archivo puede mezclar meses en firme y meses preliminares) y, si viene
    del formato diario, `dias_reportados`, `dias_archivo` y `dias_mes`. `capacidades`: filas
    de `capacidad_empresas` (agente, anio, totales y si es sujeta). Para leer
    y validar un archivo completo, usar `datos/cargue_xm.py`."""
    with con:
        if version_declarada is not None:
            _version_xm(con, version_declarada)
        cur = con.execute(
            "INSERT INTO cargas_xm (archivo_nombre, archivo_sha256, cargada_en, observaciones, "
            "formato, version_declarada) VALUES (?, ?, ?, ?, ?, ?)",
            (archivo_nombre, archivo_sha256, _ahora(), observaciones, formato, version_declarada),
        )
        carga_id = cur.lastrowid
        conocidas = {r["codigo"] for r in con.execute("SELECT codigo FROM versiones_xm")}
        for f in filas:
            if f["version_xm"] not in conocidas:
                _version_xm(con, f["version_xm"])  # lanza el error con el mensaje
            con.execute(
                "INSERT INTO generacion (carga_id, periodo, version_xm, codigo_submercado, recurso, "
                "agente, tipo_generacion, generacion_kwh, capacidad_kw, dias_reportados, dias_archivo, "
                "dias_mes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (carga_id, f["periodo"], f["version_xm"], f["codigo_submercado"], f.get("recurso"),
                 f.get("agente"), f.get("tipo_generacion"),
                 a_texto(f.get("generacion_kwh")), a_texto(f.get("capacidad_kw")),
                 f.get("dias_reportados"), f.get("dias_archivo"), f.get("dias_mes")),
            )
        con.executemany(
            "INSERT INTO generacion_diaria (carga_id, codigo_submercado, fecha, generacion_kwh, horas_completas) "
            "VALUES (?, ?, ?, ?, ?)",
            ((carga_id, d["codigo_submercado"], d["fecha"], a_texto(d["generacion_kwh"]),
              int(bool(d["horas_completas"]))) for d in diaria),
        )
        for c in capacidades:
            con.execute(
                "INSERT INTO capacidad_empresas (carga_id, anio, agente, capacidad_total_kw, "
                "capacidad_hidraulica_kw, sujeta) VALUES (?, ?, ?, ?, ?, ?)",
                (carga_id, c["anio"], c["agente"], a_texto(c["capacidad_total_kw"]),
                 a_texto(c["capacidad_hidraulica_kw"]), int(bool(c["sujeta"]))),
            )
    return carga_id


def generacion_para(con, codigo_submercado: str, periodo: str, caracter: str) -> sqlite3.Row:
    """Generación a usar: `oficial` exige versión firme; `preliminar` toma la
    versión más reciente disponible (que puede ser la firme). Entre cargas de
    la misma versión, la última cargada. Si el archivo de esa generación no
    cubre todos los días del mes, `MesIncompleto`: no se liquida ni se busca
    otra. (Los días que la central no trae dentro de un mes cubierto no
    bloquean: XM omite los días sin generación.)"""
    if caracter not in ("oficial", "preliminar"):
        raise ErrorRepositorio(f"Carácter desconocido: {caracter}")
    filtro_firme = "AND v.es_firme = 1" if caracter == "oficial" else ""
    # Primero las filas de un archivo que cubre el mes entero: un archivo
    # parcial viejo con versión «más avanzada» no puede tapar al mes completo
    # que llegó después (corregido el 2026-11-14).
    fila = con.execute(
        f"SELECT g.*, v.orden, v.es_firme, c.archivo_sha256, c.archivo_nombre FROM generacion g "
        f"JOIN versiones_xm v ON v.codigo = g.version_xm "
        f"JOIN cargas_xm c ON c.id = g.carga_id "
        f"WHERE g.codigo_submercado = ? AND g.periodo = ? AND g.generacion_kwh IS NOT NULL {filtro_firme} "
        f"ORDER BY (g.dias_archivo IS NULL OR g.dias_mes IS NULL OR g.dias_archivo >= g.dias_mes) DESC, "
        f"v.orden DESC, g.carga_id DESC LIMIT 1",
        (codigo_submercado, periodo),
    ).fetchone()
    if fila is None:
        que = "en versión firme (TXF)" if caracter == "oficial" else ""
        raise SinGeneracion(f"No hay generación de XM {que} para {codigo_submercado} en {periodo}.".replace("  ", " "))
    if fila["dias_archivo"] is not None and fila["dias_mes"] is not None \
            and fila["dias_archivo"] < fila["dias_mes"]:
        raise MesIncompleto(
            f"El archivo de XM cubre {fila['dias_archivo']} de {fila['dias_mes']} días de {periodo} "
            f"({fila['version_xm']}): mes incompleto, no se liquida."
        )
    return fila


def generacion_del_tramo(con, codigo_submercado: str, periodo: str, dia_desde: int, caracter: str) -> dict:
    """Generación de un mes PARCIAL: del día `dia_desde` (incluido) al último
    del mes. Usa el mismo archivo que `generacion_para` (el que cubre el mes
    entero, en la versión pedida) y suma sus días. Un día ausente de la central
    se toma como sin generación, igual que en el mes completo.

    `SinDatoDiario` si ese archivo no trae generación por día (formato mensual,
    o un cargue anterior a la versión 6: hay que volver a cargar el archivo)."""
    fila = dict(generacion_para(con, codigo_submercado, periodo, caracter))
    fin = ultimo_dia(periodo)
    dias = con.execute(
        "SELECT fecha, generacion_kwh, horas_completas FROM generacion_diaria "
        "WHERE carga_id = ? AND codigo_submercado = ? AND fecha BETWEEN ? AND ? ORDER BY fecha",
        (fila["carga_id"], codigo_submercado, f"{periodo}-01", f"{periodo}-{fin:02d}"),
    ).fetchall()
    if not dias:
        raise SinDatoDiario(
            f"El archivo de XM de {periodo} ({fila['version_xm']}) no tiene generación por día de "
            f"{codigo_submercado}: no se puede liquidar solo del día {dia_desde} al {fin}. Cargue el archivo "
            f"diario de XM de ese mes.")
    desde = f"{periodo}-{dia_desde:02d}"
    tramo = [d for d in dias if d["fecha"] >= desde]
    fila.update(
        generacion_kwh=str(sum((Decimal(d["generacion_kwh"]) for d in tramo), Decimal(0))),
        dias_reportados=sum(d["horas_completas"] for d in tramo),
        dias_archivo=fin - dia_desde + 1, dia_desde=dia_desde, dia_hasta=fin,
    )
    return fila


def guardar_tvb(con, anio: int, pesos_kwh, fuente: str, fuente_sha256: str | None = None) -> None:
    with con:
        con.execute(
            "INSERT INTO tvb (anio, pesos_kwh, fuente, fuente_sha256) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(anio) DO UPDATE SET pesos_kwh = excluded.pesos_kwh, "
            "fuente = excluded.fuente, fuente_sha256 = excluded.fuente_sha256",
            (anio, a_texto(pesos_kwh), fuente, fuente_sha256),
        )


def tvb_de(con, anio: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM tvb WHERE anio = ?", (anio,)).fetchone()


def listar_tvb(con) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM tvb ORDER BY anio DESC").fetchall()


def periodos_con_generacion(con) -> list[str]:
    """Meses con generación cargada, del más reciente al más antiguo."""
    return [r[0] for r in con.execute("SELECT DISTINCT periodo FROM generacion ORDER BY periodo DESC")]


def periodos_liquidados(con) -> list[str]:
    return [r[0] for r in con.execute("SELECT DISTINCT periodo FROM liquidaciones ORDER BY periodo DESC")]


# ---------------------------------------------------------------------------
# Liquidaciones versionadas
# ---------------------------------------------------------------------------

CAMPOS_CABECERA = (
    "proyecto_id", "periodo", "caracter", "version_xm", "carga_xm_id", "carga_xm_sha256",
    "generacion_kwh", "tvb_anio", "tvb_pesos_kwh", "ventas_brutas", "bolsa_car_pnn",
    "formula", "delimitacion_id", "delimitacion_sha256", "parametros_version",
)


CAMPOS_OPCIONALES = tuple(n for n, _ in COLUMNAS_V5 + COLUMNAS_V6)


def guardar_liquidacion(con, cabecera: Mapping, filas: Iterable[Mapping],
                        delimitaciones: Iterable[int] | None = None) -> int:
    """Guarda una liquidación como versión nueva; nunca pisa la anterior.

    `delimitaciones`: ids de TODAS las delimitaciones que la respaldan. En una
    central compartida (GUATRON, PAJARITO) debe venir la que rige el mes para
    cada uno de sus proyectos: si a alguno le falta, la central no se liquida.
    Si no se indica, es solo la de la cabecera."""
    return guardar_liquidaciones(con, [(cabecera, filas, delimitaciones)])[0]


def guardar_liquidaciones(con, lote: Iterable[tuple[Mapping, Iterable[Mapping], Iterable[int] | None]]) -> list[int]:
    """Guarda varias liquidaciones en UNA transacción: o quedan todas o
    ninguna. Primero se validan todas (sin escribir); si una falla, no se
    escribe nada. Antes se guardaba central por central y un error a mitad de
    camino dejaba el mes guardado a medias (corregido el 2026-11-15).

    Si otra sesión (otra pestaña) guardó una versión al mismo tiempo, la base
    rechaza el número de versión repetido: se deshace todo y se avisa."""
    planes = [_preparar_liquidacion(con, cabecera, filas, delimitaciones)
              for cabecera, filas, delimitaciones in lote]
    try:
        with con:
            return [_insertar_liquidacion(con, plan) for plan in planes]
    except sqlite3.IntegrityError as e:
        if "liquidaciones.version_numero" in str(e):  # UNIQUE (proyecto_id, periodo, version_numero)
            raise ErrorRepositorio(
                "Otra sesión guardó una liquidación de este mes al mismo tiempo (¿la aplicación está abierta en "
                "otra pestaña?). No se guardó nada: vuelva a abrir la pantalla y guarde otra vez."
            ) from e
        raise ErrorRepositorio(f"No se pudo guardar y no se guardó nada. Detalle técnico: {e}") from e


def _preparar_liquidacion(con, cabecera: Mapping, filas: Iterable[Mapping],
                          delimitaciones: Iterable[int] | None) -> dict:
    """Valida una liquidación y arma lo que se va a insertar. No escribe."""
    faltan = [c for c in CAMPOS_CABECERA if c not in cabecera]
    if faltan:
        raise ErrorRepositorio(f"Faltan campos de trazabilidad: {', '.join(faltan)}")
    version = _version_xm(con, cabecera["version_xm"])
    if cabecera["caracter"] == "oficial" and not version["es_firme"]:
        raise ErrorRepositorio(
            f"Una liquidación oficial exige versión firme de XM; llegó «{cabecera['version_xm']}»."
        )
    proyecto = con.execute("SELECT * FROM proyectos WHERE id = ?", (cabecera["proyecto_id"],)).fetchone()
    if proyecto["codigo_submercado"]:
        doble = con.execute(
            "SELECT p.nombre FROM liquidaciones l JOIN proyectos p ON p.id = l.proyecto_id "
            "WHERE p.codigo_submercado = ? AND p.id <> ? AND l.periodo = ? LIMIT 1",
            (proyecto["codigo_submercado"], proyecto["id"], cabecera["periodo"]),
        ).fetchone()
        if doble:
            raise ErrorRepositorio(
                f"La central {proyecto['codigo_submercado']} ya se liquidó en {cabecera['periodo']} con "
                f"«{doble['nombre']}»: se liquida una sola vez por central, no por proyecto."
            )
    # Mes parcial (v6): con `dia_desde` > 1 se liquida solo desde que empieza a
    # regir la delimitación. Sin él, se exige que rija el mes completo.
    dia_desde = cabecera.get("dia_desde") or 1
    if dia_desde > 1:
        def _rige(pid):
            d, dia, _ = delimitacion_del_tramo(con, pid, cabecera["periodo"])
            if dia > dia_desde:
                raise ErrorRepositorio(
                    f"La delimitación {d['radicado']} rige desde el día {dia} de {cabecera['periodo']}, "
                    f"después del día {dia_desde} indicado en la liquidación.")
            return d
        if cabecera.get("dia_hasta") != ultimo_dia(cabecera["periodo"]):
            raise ErrorRepositorio("Un mes parcial se liquida hasta el último día del mes.")
    else:
        def _rige(pid):
            return exigir_delimitacion(con, pid, cabecera["periodo"])
    aplicable = _rige(cabecera["proyecto_id"])
    if aplicable["id"] != cabecera["delimitacion_id"]:
        raise ErrorRepositorio(
            f"Para {cabecera['periodo']} rige la delimitación {aplicable['radicado']}, "
            f"no la indicada en la liquidación."
        )
    respaldo = {aplicable["id"]: aplicable}
    ids = set(delimitaciones) if delimitaciones is not None else {aplicable["id"]}
    if proyecto["codigo_submercado"] and delimitaciones is not None:
        for vecino in proyectos_de_central(con, proyecto["codigo_submercado"]):
            d = _rige(vecino["id"])  # todas deben regir el mes (o el tramo)
            respaldo[d["id"]] = d
    if ids != set(respaldo):
        raise ErrorRepositorio(
            f"Las delimitaciones indicadas no son las que rigen {cabecera['periodo']} para la central "
            f"{proyecto['codigo_submercado'] or '—'}: se esperaban {sorted(r['radicado'] for r in respaldo.values())}."
        )

    previa = con.execute(
        "SELECT id, version_numero FROM liquidaciones WHERE proyecto_id = ? AND periodo = ? "
        "ORDER BY version_numero DESC LIMIT 1",
        (cabecera["proyecto_id"], cabecera["periodo"]),
    ).fetchone()
    numero = 1 if previa is None else previa["version_numero"] + 1
    valores = {c: cabecera[c] for c in CAMPOS_CABECERA}
    valores.update({c: cabecera.get(c) for c in CAMPOS_OPCIONALES})
    if valores["codigo_submercado"] is None:
        valores["codigo_submercado"] = proyecto["codigo_submercado"]
    for c in ("generacion_kwh", "tvb_pesos_kwh", "ventas_brutas", "bolsa_car_pnn", "capacidad_empresa_kw"):
        valores[c] = a_texto(valores[c])
    return {
        "valores": valores, "numero": numero, "reemplaza_a": None if previa is None else previa["id"],
        "respaldo": list(respaldo.values()),
        "filas": [(f["autoridad"], a_texto(f.get("s1_ha")), a_texto(f.get("s2_ha")), a_texto(f["razon"]),
                   a_texto(f["porcentaje_mostrado"]), a_texto(f["monto"]), int(bool(f.get("ajuste_residuo", False))))
                  for f in filas],
    }


def _insertar_liquidacion(con, plan: dict) -> int:
    """Escribe una liquidación ya validada. Quien llama abre la transacción."""
    valores = plan["valores"]
    cur = con.execute(
        f"INSERT INTO liquidaciones ({', '.join(valores)}, version_numero, creada_en, reemplaza_a) "
        f"VALUES ({', '.join('?' for _ in valores)}, ?, ?, ?)",
        (*valores.values(), plan["numero"], _ahora(), plan["reemplaza_a"]),
    )
    liq_id = cur.lastrowid
    for d in plan["respaldo"]:
        con.execute(
            "INSERT INTO liquidacion_delimitaciones (liquidacion_id, delimitacion_id, proyecto_id, "
            "radicado, pdf_sha256) VALUES (?, ?, ?, ?, ?)",
            (liq_id, d["id"], d["proyecto_id"], d["radicado"], d["pdf_sha256"]),
        )
    con.executemany(
        "INSERT INTO liquidacion_filas (liquidacion_id, autoridad_sigla, s1_ha, s2_ha, razon, "
        "porcentaje_mostrado, monto, ajuste_residuo) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(liq_id, *fila) for fila in plan["filas"]],
    )
    return liq_id


def ultima_liquidacion(con, proyecto_id: int, periodo: str, caracter: str = "oficial") -> sqlite3.Row | None:
    return con.execute(
        "SELECT * FROM liquidaciones WHERE proyecto_id = ? AND periodo = ? AND caracter = ? "
        "ORDER BY version_numero DESC LIMIT 1",
        (proyecto_id, periodo, caracter),
    ).fetchone()


def liquidaciones_del_periodo(con, periodo: str) -> list[sqlite3.Row]:
    """La última versión guardada de cada central en el mes."""
    return con.execute(
        "SELECT l.*, p.nombre AS proyecto FROM liquidaciones l JOIN proyectos p ON p.id = l.proyecto_id "
        "WHERE l.periodo = ? AND l.version_numero = (SELECT max(x.version_numero) FROM liquidaciones x "
        "WHERE x.proyecto_id = l.proyecto_id AND x.periodo = l.periodo) ORDER BY p.nombre",
        (periodo,),
    ).fetchall()


def delimitaciones_de_liquidacion(con, liquidacion_id: int) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT ld.*, p.nombre AS proyecto FROM liquidacion_delimitaciones ld "
        "JOIN proyectos p ON p.id = ld.proyecto_id WHERE ld.liquidacion_id = ? ORDER BY p.nombre",
        (liquidacion_id,),
    ).fetchall()


def filas_liquidacion(con, liquidacion_id: int) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM liquidacion_filas WHERE liquidacion_id = ? ORDER BY autoridad_sigla",
        (liquidacion_id,),
    ).fetchall()
