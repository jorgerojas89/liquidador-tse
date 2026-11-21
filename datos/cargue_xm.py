"""
Cargue de la generación de XM y cruce central ↔ proyecto (paso 4, 2026-11-14).

  leer_xm (parse_xm_generacion)  →  guardar_lectura  →  resumen_carga

Reglas:
  * Un mismo archivo (mismo SHA-256) no se carga dos veces.
  * Versión de XM: si el archivo la trae (formato 2026), manda el archivo; si
    no la trae (formato 2018-2025), la declara la persona. Una versión que no
    está en el catálogo se rechaza (`repo.agregar_version_xm` para sumarla).
    Si un recurso trae varias versiones en el mismo mes, se guarda la menos
    avanzada: el mes completo no está más allá de esa versión.
  * El cruce vive en un solo lugar: `proyectos.codigo_submercado` en la base
    (sembrado con `insumos/delimitaciones-igac/precarga_proyectos.csv`).
  * Umbral del art. 45 por EMPRESA. El archivo de 2026 no trae capacidad: se
    toma la del archivo mensual más reciente cargado con año ≤ al del periodo,
    y el agente se identifica por el código de la central en ese archivo
    (2026 trae el código del agente, p. ej. EPMG; 2025, el nombre). Sin
    capacidad conocida, la sujeción queda «sin determinar» (None), no se supone.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from datos import repositorio as repo
from datos.parse_xm_generacion import ArchivoXMInvalido, LecturaXM, leer_xm
from datos.sujecion_empresas import capacidades_por_empresa


class ErrorCargue(repo.ErrorRepositorio):
    pass


def leer_archivo(contenido: bytes, nombre: str) -> tuple[LecturaXM | None, str | None]:
    """Lee un archivo de XM para la pantalla: (lectura, None) o (None, mensaje
    para la persona). Un insumo dañado o inesperado se informa; no tumba la
    pantalla con un error técnico (corregido el 2026-11-15)."""
    try:
        return leer_xm(contenido, nombre), None
    except ArchivoXMInvalido as e:
        return None, str(e)
    except Exception as e:  # insumo externo: cualquier otra falla también se informa
        return None, (f"No se pudo leer «{nombre}» ({type(e).__name__}: {e}). Revise que sea el archivo de XM "
                      f"sin modificar.")


def carga_existente(con, archivo_sha256: str):
    return con.execute(
        "SELECT * FROM cargas_xm WHERE archivo_sha256 = ? ORDER BY id LIMIT 1", (archivo_sha256,)
    ).fetchone()


def _ordenes(con) -> dict[str, int]:
    return {r["codigo"]: r["orden"] for r in con.execute("SELECT codigo, orden FROM versiones_xm")}


def versiones_desconocidas(con, lectura: LecturaXM) -> set[str]:
    conocidas = _ordenes(con)
    return {v for f in lectura.filas for v in f.versiones if v not in conocidas}


def guardar_lectura(con, lectura: LecturaXM, version_declarada: str | None = None,
                    observaciones: str | None = None) -> int:
    """Guarda un archivo de XM ya leído. Devuelve el id del cargue."""
    previa = carga_existente(con, lectura.archivo_sha256)
    if previa is not None:
        raise ErrorCargue(
            f"Este archivo ya se cargó el {previa['cargada_en'][:10]} (cargue {previa['id']}, "
            f"«{previa['archivo_nombre']}»). No se carga dos veces."
        )
    ordenes = _ordenes(con)
    if lectura.trae_version:
        if version_declarada:
            raise ErrorCargue("El archivo ya trae la versión de XM en cada fila: no se declara.")
        raras = versiones_desconocidas(con, lectura)
        if raras:
            raise ErrorCargue(
                f"El archivo trae versiones de XM que no están en el catálogo: {', '.join(sorted(raras))}."
            )
        if any(not f.versiones for f in lectura.filas):
            raise ErrorCargue("Hay filas sin versión en un archivo que la trae: revise el archivo.")
    else:
        if not version_declarada:
            raise ErrorCargue("El archivo no trae la versión de XM: declárela al cargar (TX1…TXF).")
        if version_declarada not in ordenes:
            raise ErrorCargue(f"Versión de XM desconocida: «{version_declarada}».")

    # La versión es del MES, no de cada recurso: la menos avanzada que trae el
    # archivo para ese mes. Un recurso que solo generó en los días ya firmes
    # de un mes que aún tiene días preliminares no queda rotulado como firme
    # (corregido el 2026-11-14).
    del_mes = {p: min(vs, key=ordenes.__getitem__) for p, vs in lectura.versiones_por_mes().items() if vs}

    filas = [{
        "periodo": f.periodo,
        "version_xm": del_mes[f.periodo] if f.versiones else version_declarada,
        "codigo_submercado": f.codigo_submercado, "recurso": f.recurso, "agente": f.agente,
        "tipo_generacion": f.tipo_generacion, "generacion_kwh": f.generacion_kwh,
        "capacidad_kw": f.capacidad_kw, "dias_reportados": f.dias_reportados,
        "dias_archivo": f.dias_archivo, "dias_mes": f.dias_mes,
    } for f in lectura.filas]

    capacidades = []
    if lectura.registros_capacidad:
        for c in capacidades_por_empresa(lectura.registros_capacidad, lectura.anio, lectura.archivo_nombre).values():
            capacidades.append({
                "anio": c.anio, "agente": c.nombre, "capacidad_total_kw": c.capacidad_total_kw,
                "capacidad_hidraulica_kw": c.capacidad_hidraulica_kw, "sujeta": c.es_sujeto_activo,
            })

    # Generación por día (v6), para liquidar un mes parcial. Solo de las
    # centrales hidráulicas y de las enlazadas a un proyecto: el resto no se
    # liquida y triplicaría el tamaño de la base.
    enlazadas = {r[0] for r in con.execute(
        "SELECT codigo_submercado FROM proyectos WHERE codigo_submercado IS NOT NULL")}
    hidraulicas = {f.codigo_submercado for f in lectura.filas if (f.tipo_generacion or "").upper() == "HIDRAULICA"}
    guardar = enlazadas | hidraulicas
    diaria = ({"codigo_submercado": codigo, "fecha": fecha.isoformat(), "generacion_kwh": kwh,
               "horas_completas": (codigo, fecha) not in lectura.dias_sin_24_horas}
              for (codigo, fecha), kwh in lectura.diario.items() if codigo in guardar)

    return repo.registrar_carga_xm(
        con, lectura.archivo_nombre, lectura.archivo_sha256, filas, observaciones=observaciones,
        formato=lectura.formato, version_declarada=None if lectura.trae_version else version_declarada,
        capacidades=capacidades, diaria=diaria,
    )


# ---------------------------------------------------------------------------
# Umbral por empresa
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Sujecion:
    agente: str                  # nombre de la empresa en el archivo mensual de XM
    anio_capacidad: int          # año del archivo del que sale la capacidad
    capacidad_total_kw: Decimal
    capacidad_hidraulica_kw: Decimal
    sujeta: bool
    carga_id: int


def sujecion_central(con, codigo_submercado: str, anio: int) -> Sujecion | None:
    """Sujeción de la empresa dueña de la central en el año `anio`, con la
    capacidad del archivo mensual más reciente con año ≤ `anio`. None = no hay
    capacidad cargada para esa central: la sujeción no se puede determinar."""
    fila = con.execute(
        "SELECT ce.* FROM generacion g "
        "JOIN capacidad_empresas ce ON ce.carga_id = g.carga_id AND ce.agente = g.agente "
        "WHERE g.codigo_submercado = ? AND ce.anio <= ? "
        "ORDER BY ce.anio DESC, g.carga_id DESC LIMIT 1",
        (codigo_submercado, anio),
    ).fetchone()
    if fila is None:
        return None
    return Sujecion(
        agente=fila["agente"], anio_capacidad=fila["anio"],
        capacidad_total_kw=Decimal(fila["capacidad_total_kw"]),
        capacidad_hidraulica_kw=Decimal(fila["capacidad_hidraulica_kw"]),
        sujeta=bool(fila["sujeta"]), carga_id=fila["carga_id"],
    )


# ---------------------------------------------------------------------------
# Resumen de un cargue: qué reconoció y cómo cruza
# ---------------------------------------------------------------------------

@dataclass
class CentralEnCarga:
    codigo: str
    recurso: str
    agente: str
    tipo_generacion: str
    proyectos: list[str]
    sujecion: Sujecion | None
    por_periodo: dict[str, dict] = field(default_factory=dict)  # periodo -> kwh, version, días

    @property
    def estado_umbral(self) -> str:
        if self.sujecion is None:
            return "sin determinar"
        return "sujeta" if self.sujecion.sujeta else "bajo el umbral"


@dataclass
class ResumenCarga:
    carga: dict
    periodos: list[dict]                       # periodo, versiones, recursos, dias_mes, dias_archivo
    con_proyecto: list[CentralEnCarga]         # centrales enlazadas a un proyecto
    hidraulicas_sin_proyecto: list[CentralEnCarga]
    proyectos_sin_generacion: list[dict]       # enlazados, pero su central no está en el archivo
    proyectos_sin_central: list[str]           # sin código de submercado

    @property
    def bajo_umbral(self) -> list[CentralEnCarga]:
        return [c for c in self.con_proyecto + self.hidraulicas_sin_proyecto if c.estado_umbral != "sujeta"]


def resumen_carga(con, carga_id: int) -> ResumenCarga:
    carga = con.execute("SELECT * FROM cargas_xm WHERE id = ?", (carga_id,)).fetchone()
    if carga is None:
        raise ErrorCargue(f"No existe el cargue {carga_id}")
    filas = con.execute(
        "SELECT * FROM generacion WHERE carga_id = ? ORDER BY codigo_submercado, periodo", (carga_id,)
    ).fetchall()

    periodos: dict[str, dict] = {}
    for f in filas:
        p = periodos.setdefault(f["periodo"], {"periodo": f["periodo"], "versiones": set(), "recursos": 0,
                                               "dias_mes": f["dias_mes"], "dias_archivo": f["dias_archivo"]})
        p["versiones"].add(f["version_xm"])
        p["recursos"] += 1

    proyectos = con.execute("SELECT * FROM proyectos ORDER BY nombre").fetchall()
    por_codigo: dict[str, list[str]] = {}
    for p in proyectos:
        if p["codigo_submercado"]:
            por_codigo.setdefault(p["codigo_submercado"], []).append(p["nombre"])

    centrales: dict[str, CentralEnCarga] = {}
    for f in filas:
        codigo = f["codigo_submercado"]
        enlazada = codigo in por_codigo
        if not enlazada and (f["tipo_generacion"] or "").upper() != "HIDRAULICA":
            continue
        c = centrales.get(codigo)
        if c is None:
            anio = int(f["periodo"][:4])
            c = centrales[codigo] = CentralEnCarga(
                codigo=codigo, recurso=f["recurso"] or "", agente=f["agente"] or "",
                tipo_generacion=f["tipo_generacion"] or "", proyectos=por_codigo.get(codigo, []),
                sujecion=sujecion_central(con, codigo, anio),
            )
        c.por_periodo[f["periodo"]] = {
            "generacion_kwh": repo.a_decimal(f["generacion_kwh"]), "version_xm": f["version_xm"],
            "dias_reportados": f["dias_reportados"], "dias_archivo": f["dias_archivo"],
            "dias_mes": f["dias_mes"],
        }

    en_archivo = set(centrales)
    return ResumenCarga(
        carga=dict(carga),
        periodos=[periodos[k] for k in sorted(periodos)],
        con_proyecto=sorted((c for c in centrales.values() if c.proyectos), key=lambda c: c.proyectos[0]),
        hidraulicas_sin_proyecto=sorted((c for c in centrales.values() if not c.proyectos), key=lambda c: c.recurso),
        proyectos_sin_generacion=[
            {"proyecto": p["nombre"], "codigo": p["codigo_submercado"]}
            for p in proyectos if p["codigo_submercado"] and p["codigo_submercado"] not in en_archivo
        ],
        proyectos_sin_central=[p["nombre"] for p in proyectos if not p["codigo_submercado"]],
    )


def listar_cargas(con) -> list:
    return con.execute(
        "SELECT c.*, count(g.periodo) AS n_filas, min(g.periodo) AS desde, max(g.periodo) AS hasta "
        "FROM cargas_xm c LEFT JOIN generacion g ON g.carga_id = c.id GROUP BY c.id ORDER BY c.id DESC"
    ).fetchall()
