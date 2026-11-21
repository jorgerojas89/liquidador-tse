# datos/ — origen de cada archivo (paso 1, 2026-11-11)

| Archivo | Viene de (APP TSE) | Nota |
|---|---|---|
| `parse_xm_generacion.py` | `backend/etl/` | Lector de XM. Paso 4: lee el formato mensual (2018-2025) y el diario-horario (2026) con `leer_xm`. |
| `parse_creg_tvb.py` | `backend/etl/` | Serie TVB 2017-2026 desde `TVB Consolidado.rtf` (necesita `striprtf`). |
| `sujecion_empresas.py` | `backend/etl/` | Umbral de 10.000 kW por empresa. |
| `rutas.py` | `backend/etl/` | Simplificado: los insumos están en `insumos/`. |
| `mapeo_cartografia_xm.json`, `Relacion_capas_IGAC_centrales_XM.xlsx` | `backend/etl/` y la raíz | **Movidos a `docs/historico/`** (paso 4, 2026-11-14): el cruce vive solo en la base (`proyectos.codigo_submercado`). |
| `cargue_xm.py` | Nuevo (paso 4) | Guarda un archivo de XM leído, umbral por empresa y cruce central ↔ proyecto. |
| `liquidar_mes.py` | Nuevo (paso 5) | Une generación, TVB, delimitación, umbral por empresa y motor (`motor/liquidacion.py`). Calcula en memoria y guarda versiones. |
| `exportar.py` | Nuevo (paso 6) | XLSX con variables intermedias (openpyxl) y PDF resumen por autoridad (reportlab), a partir de lo guardado. |
| `proyeccion.py` | Nuevo (2026-11-14) | Proyección de ingresos de un año futuro (estimación; solo lee la base) y su XLSX. Método en `motor/proyeccion.py`. |
| `maestro_autoridades.csv` | Generado a partir de `insumos/onvs-referencia/Ejemplos.xlsx` | Ver abajo. |

## maestro_autoridades.csv

Las 53 autoridades de `Ejemplos.xlsx`, revisadas por Jorge el 2026-11-12: quedan
**48** (22 CAR y 26 PNN), todas con nombre. El 2026-11-13 se agregó la **CVS**
(Valles del Sinú y San Jorge), autoridad de Urrá en la delimitación del IGAC: quedan **49**
(23 CAR y 26 PNN). Se excluyeron `San_Bartolome_Isagen`
(era un proyecto) y las autoridades urbanas AMVA, DAGMA, SDA y EPA Buenaventura.

Se edita en Excel (separado por comas). Al guardar, usar **«CSV UTF-8 (delimitado
por comas)»**: el «CSV» simple de Excel para Mac guarda en Mac Roman y daña las tildes. El lector
(`maestro_autoridades.py`) avisa si encuentra siglas repetidas, tipos distintos
de CAR/PNN, filas sin nombre o caracteres dañados.
