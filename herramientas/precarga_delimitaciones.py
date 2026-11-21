"""
Precarga ÚNICA de las delimitaciones del IGAC (Decreto 800) como BORRADORES.

Herramienta aparte, fuera de la aplicación (decisión de Jorge, 2026-11-13): la
app sigue sin leer PDF (CLAUDE.md, «Qué NO es»). Esto solo ahorra digitar unas
200 cifras; nada liquida hasta que la persona confirme cada delimitación contra
su PDF en la pantalla Delimitaciones (regla 3).

Dos pasos, para que quede un intermedio revisable a ojo:

  1. extraer  PDF → insumos/delimitaciones-igac/precarga_delimitaciones.csv
              Necesita `pdftotext` (poppler). Las cifras quedan tal como las
              escribe el IGAC («86.537,28373», «N/A»), una fila por autoridad.
  2. cargar   CSV → almacen/liquidador.db, en estado «borrador», sin fecha de
              vigencia. Copia cada PDF a almacen/delimitaciones/ y guarda su
              SHA-256. Crea los proyectos y los enlaza a la central de XM según
              precarga_proyectos.csv. Si el radicado ya existe, lo salta.

Uso:
    python -m herramientas.precarga_delimitaciones extraer
    python -m herramientas.precarga_delimitaciones cargar [ruta de la base]
"""
from __future__ import annotations

import csv
import hashlib
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
VIGENCIA_DELIMITACIONES_POR_DEFECTO = "2026-11-25"  # = motor.parametros (Jorge, 2026-11-14); editable en la app
CARPETA_PDF = RAIZ / "insumos" / "delimitaciones-igac"
CSV_DELIM = CARPETA_PDF / "precarga_delimitaciones.csv"
CSV_PROYECTOS = CARPETA_PDF / "precarga_proyectos.csv"
ALMACEN_PDF = RAIZ / "almacen" / "delimitaciones"

COLUMNAS = [
    "archivo_pdf", "radicado", "fecha_documento", "proyecto", "paramo_declarado", "trae_s2",
    "s1_t_declarado", "s2_t_declarado", "i", "autoridad_igac", "autoridad_sigla", "s1_ha", "s2_ha",
]

NUM = r"(?:\d{1,3}(?:\.\d{3})*,\d{5}|N/A)"
FILA = re.compile(rf"^\s*(\d+)?\s*(.*?)\s+({NUM})\s+({NUM})\s+({NUM})\s+({NUM})\s+({NUM})\s+({NUM})\s*$")
INICIO_NOMBRE = re.compile(r"(?=CORPORACI[OÓ]N|PARQUES NACIONALES|Autoridad ambiental)")
RUIDO = ("Página |", "Agustín Codazzi", "Carrera 30", "653 1888", "igac.gov.co", "____")
URBANA = "Autoridad ambiental urbana o área en litigio"


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z]", "", s)


# ---------------------------------------------------------------------------
# 1. extraer
# ---------------------------------------------------------------------------

def _texto_pdf(ruta: Path) -> str:
    try:
        return subprocess.run(["pdftotext", "-layout", str(ruta), "-"],
                              check=True, capture_output=True, text=True).stdout
    except FileNotFoundError:
        sys.exit("Falta `pdftotext` (poppler). En Mac: brew install poppler.")


def _tabla_por_autoridad(texto: str) -> tuple[list[dict], list[str]]:
    seccion = texto.split("Áreas por entidad", 1)[1].split("AIJAAi = ", 1)[0].splitlines()
    seccion = [l for l in seccion if l.strip() and not any(r in l for r in RUIDO)]
    ini = next(k for k, l in enumerate(seccion) if re.match(r"^\s*i\s+Autoridad", l))
    seccion = seccion[ini + 1:]
    datos, nombres, total = [], [], None
    for l in seccion:
        m = FILA.match(l)
        if m and m.group(2).startswith("TOTAL"):
            total = list(m.groups()[2:])
            break
        if m:
            datos.append(list(m.groups()[2:]))
            nombres.append(m.group(2))
        else:
            nombres.append(re.sub(r"^\s*\d+\s+", "", l).strip())
    if total is None:
        raise ValueError("no se encontró la fila TOTAL")
    piezas = [p.strip() for p in INICIO_NOMBRE.split(re.sub(r"\s+", " ", " ".join(nombres))) if p.strip()]
    if len(piezas) != len(datos):
        raise ValueError(f"{len(piezas)} nombres para {len(datos)} filas de cifras")
    filas = [{"autoridad_igac": nom, "s1_ha": v[2], "s2_ha": v[5]} for nom, v in zip(piezas, datos)]
    return filas, total


def _sigla(nombre_igac: str, maestro) -> str:
    if nombre_igac.startswith("Autoridad ambiental"):
        return "SIN_ASIGNAR"
    parentesis = re.findall(r"\(([^)]*)\)", nombre_igac)
    if not parentesis:
        return ""
    dentro = parentesis[-1]
    if nombre_igac.startswith("PARQUES NACIONALES"):
        clave = _norm(dentro)
        for a in maestro.values():
            if a.tipo == "PNN":
                m = _norm(a.nombre)
                if clave.startswith(m) or m.startswith(clave) or clave in m or m in clave:
                    return a.sigla
        return ""
    return dentro.strip() if dentro.strip() in maestro else ""


def extraer() -> None:
    from datos.maestro_autoridades import cargar_maestro
    maestro = cargar_maestro()
    salida, avisos = [], []
    for pdf in sorted(CARPETA_PDF.glob("*.pdf")):
        t = _texto_pdf(pdf)
        radicado = re.search(r"Radicado N°:\s*(\S+)", t).group(1)
        dd, mm, aa = re.search(r"Fecha:\s*(\d{2})-(\d{2})-(\d{4})", t).groups()
        asunto = re.sub(r"\s+", " ", re.search(r"ASUNTO:(.*?)Reciba", t, re.S).group(1)).strip()
        proyecto = re.search(r"Proyecto (.+?)\.?$", asunto).group(1).strip()
        cabeza = t.split("Áreas por entidad", 1)[0]
        paramo = "no existe intersección entre páramos" not in cabeza
        filas, total = _tabla_por_autoridad(t)
        trae_s2 = any(f["s2_ha"] != "N/A" for f in filas)
        for i, f in enumerate(filas, 1):
            sigla = _sigla(f["autoridad_igac"], maestro)
            if not sigla:
                avisos.append(f"{proyecto}: «{f['autoridad_igac']}» no está en el maestro")
            salida.append({
                "archivo_pdf": pdf.name, "radicado": radicado, "fecha_documento": f"{aa}-{mm}-{dd}",
                "proyecto": proyecto, "paramo_declarado": "sí" if paramo else "no",
                "trae_s2": "sí" if trae_s2 else "no",
                "s1_t_declarado": total[2], "s2_t_declarado": total[5] if trae_s2 else "N/A",
                "i": i, "autoridad_igac": f["autoridad_igac"], "autoridad_sigla": sigla,
                "s1_ha": f["s1_ha"], "s2_ha": f["s2_ha"],
            })
    with CSV_DELIM.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNAS)
        w.writeheader()
        w.writerows(salida)
    print(f"{len({r['radicado'] for r in salida})} delimitaciones, {len(salida)} filas → {CSV_DELIM.name}")
    for a in avisos:
        print("  ⚠", a)


# ---------------------------------------------------------------------------
# 2. cargar
# ---------------------------------------------------------------------------

def _sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as fh:
        for bloque in iter(lambda: fh.read(1 << 16), b""):
            h.update(bloque)
    return h.hexdigest()


def _si(texto: str) -> bool:
    return texto.strip().lower() in ("sí", "si", "1", "true")


def cargar(ruta_bd: Path | None = None) -> None:
    from datos import repositorio as repo
    from datos.maestro_autoridades import cargar_maestro
    from motor.delimitacion import numero_desde_texto as num

    con = repo.abrir(ruta_bd or repo.RUTA_BD)
    repo.cargar_autoridades(con, cargar_maestro().values())

    proyectos = {}
    if CSV_PROYECTOS.exists():
        with CSV_PROYECTOS.open(encoding="utf-8-sig") as fh:
            proyectos = {r["proyecto"]: r for r in csv.DictReader(fh)}

    with CSV_DELIM.open(encoding="utf-8-sig") as fh:
        filas = list(csv.DictReader(fh))
    por_radicado: dict[str, list[dict]] = {}
    for f in filas:
        por_radicado.setdefault(f["radicado"], []).append(f)

    ALMACEN_PDF.mkdir(parents=True, exist_ok=True)
    siglas = {r["sigla"] for r in con.execute("SELECT sigla FROM autoridades")}
    creadas = saltadas = 0
    bloqueadas = []
    for radicado, grupo in por_radicado.items():
        c = grupo[0]
        if con.execute("SELECT 1 FROM delimitaciones WHERE radicado = ?", (radicado,)).fetchone():
            saltadas += 1
            continue
        faltan = [g["autoridad_igac"] for g in grupo if g["autoridad_sigla"].strip() not in siglas]
        if faltan:
            bloqueadas.append(f"{c['proyecto']}: {'; '.join(faltan)}")
            continue
        p = con.execute("SELECT id FROM proyectos WHERE nombre = ?", (c["proyecto"],)).fetchone()
        if p is None:
            info = proyectos.get(c["proyecto"], {})
            pid = repo.crear_proyecto(con, c["proyecto"], (info.get("codigo_submercado") or "").strip() or None,
                                      (info.get("nota") or "").strip() or None)
        else:
            pid = p["id"]
        origen = CARPETA_PDF / c["archivo_pdf"]
        destino = ALMACEN_PDF / c["archivo_pdf"]
        if not destino.exists():
            shutil.copy2(origen, destino)
        repo.crear_delimitacion(
            con, pid, radicado, c["fecha_documento"], VIGENCIA_DELIMITACIONES_POR_DEFECTO, "hectareas",
            [{"autoridad": g["autoridad_sigla"].strip(),
              "s1_ha": num(g["s1_ha"]), "s2_ha": num(g["s2_ha"])} for g in grupo],
            pdf_nombre=c["archivo_pdf"], pdf_ruta=str(destino.relative_to(RAIZ)), pdf_sha256=_sha256(destino),
            trae_s2=_si(c["trae_s2"]), paramo_declarado=_si(c["paramo_declarado"]),
            s1_t_declarado=num(c["s1_t_declarado"]), s2_t_declarado=num(c["s2_t_declarado"]),
            origen="precarga", observaciones="Precarga desde el PDF; confirmar cifras contra el original.",
        )
        creadas += 1
    print(f"Borradores creados: {creadas}; ya existían: {saltadas}; sin cargar: {len(bloqueadas)}.")
    for b in bloqueadas:
        print(f"  ⚠ No se cargó {b} (autoridad que no está en el maestro: agréguela y vuelva a correr «cargar»).")
    for d in repo.listar_delimitaciones(con):
        r = repo.validar_delimitacion(con, d["id"])
        pendientes = [e for e in r.errores if "vigencia" not in e]
        estado = "listo para confirmar (falta vigencia)" if not pendientes else " | ".join(pendientes)
        print(f"  {d['proyecto']:34} {estado}")
    con.close()


if __name__ == "__main__":
    orden = sys.argv[1] if len(sys.argv) > 1 else ""
    if orden == "extraer":
        extraer()
    elif orden == "cargar":
        cargar(Path(sys.argv[2]) if len(sys.argv) > 2 else None)
    else:
        sys.exit(__doc__)
