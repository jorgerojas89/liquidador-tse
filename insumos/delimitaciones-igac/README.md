# insumos/igac-distribucion

Aquí van los **documentos de distribución del IGAC**: los archivos que establecen
qué porcentaje de la transferencia corresponde a cada CAR y a cada Parque
Nacional Natural en un proyecto hidroeléctrico.

Desde el cambio de alcance del 2026-08-28, **este es el único origen válido del
reparto** en la aplicación. Sin al menos un documento real aquí, no se puede
probar ni el módulo de cargue ni la liquidación.

## Qué poner

- El PDF **tal como lo emitió o entregó el IGAC**, sin editar ni recomprimir: el
  sistema guarda su hash SHA-256 para trazabilidad y cualquier retoque lo cambia.
- Uno por proyecto, con nombre que permita identificarlo, por ejemplo:
  `2026-la-miel-distribucion-igac.pdf`.
- Si el documento cubre varios proyectos, déjalo con un nombre que lo diga y
  anótalo en `notas.md`.

## Qué se necesita saber de cada uno

Anótalo en un `notas.md` en esta misma carpeta, aunque sea en dos líneas:

- Proyecto o proyectos que cubre.
- Fecha del documento y desde cuándo aplica.
- Si es PDF nativo (texto seleccionable) o escaneado (imagen). De esto depende si
  la extracción automática funciona o si toca digitar los porcentajes a mano.
- Quién lo entregó (empresa generadora, autoridad ambiental, el propio IGAC).

## Estado actual

**Vacío.** Es la tarea que bloquea todo lo demás — Bloque 0 de
`TAREAS-JORGE.md`.
