# Revisión general del Liquidador TSE — noviembre de 2026

Fecha: 2026-11-15 · Fase 1 (solo lectura y pruebas; no se cambió código).

## Resumen

- **Pruebas:** 158 pasan, 0 fallan, con Python 3.12.16 y `requirements.txt` instalado desde cero. Coincide con las 158 del CLAUDE.md.
- **Pantallas:** las seis abren y todas sus vistas se ejecutan sin excepciones ni avisos de Python.
- **Cálculo:** con datos reales de XM, los 33 montos liquidados coinciden con una comprobación independiente (generación por día × TVB × 3 % × razón exacta). Las sumas cierran contra la bolsa y el residuo nunca cae en `SIN_ASIGNAR`.
- **Hallazgos:** 1 crítico, 5 importantes y 14 menores.
- **El crítico:** en un mes parcial, una central que no trae ningún día del tramo se liquida en $ 0,00 en vez de quedar «sin generación». Puede ocurrir en la primera liquidación real (25 al 30 de noviembre).
- **La base real está vacía de insumos:** tiene las 37 delimitaciones vigentes, pero ningún archivo de XM, ninguna TVB y ninguna liquidación guardada.

Necesito 9 decisiones suyas; están en la [sección 7](#7-pendientes-abiertos-y-decisiones-que-necesito).

---

## 1. Entorno y pruebas

| Punto | Resultado |
|---|---|
| Entorno | No había venv. Lo creé en `~/.venvs/liquidador-tse` (fuera de OneDrive, para no sincronizar miles de archivos). |
| Python | 3.12.16. El `python3` del sistema es 3.14 y `.vscode/settings.json` usa el del sistema. |
| Dependencias | streamlit 1.67.0, openpyxl 3.1.5, reportlab 5.1.0, striprtf 0.0.35, pytest 9.2.0. Altair 6.3.0 llega con Streamlit. |
| `pytest -q` | **158 pasan, 0 fallan** (16 s). |
| Cobertura | 62 % en total. `motor/` entre 90 % y 100 %; `datos/` entre 80 % y 98 %, salvo `parse_creg_tvb.py` (0 %); `paginas/` y `herramientas/` 0 %. |

## 2. Recorrido de las pantallas

**Cómo lo hice.** No tengo navegador en esta sesión. Ejecuté cada pantalla y cada vista con el probador de Streamlit (`AppTest`) y arranqué `streamlit run app.py` en el puerto 8765 para ver la consola. No vi la app dibujada: disposición, colores y anchos quedan sin revisar.

**Contra qué base.** Nunca contra la real. Usé tres copias fuera del proyecto:

| Copia | Contenido |
|---|---|
| A | La base real tal cual. |
| B | A + la serie TVB de la CREG + los archivos reales de XM 2025 (declarado TXF) y 2026. |
| C | B con la vigencia movida al 25-10-2026, **solo para probar**: octubre es el único mes de 2026 completo y en TXF, y así se ejercita el mes parcial (25 al 31) con datos reales. |

La base real quedó intacta (mismo SHA-256 antes y después).

| Pantalla | Copia A (estado real) | Copias B y C |
|---|---|---|
| Delimitaciones | 37 de 37 confirmadas; PDF, cifras y validación sin error. | Igual. |
| Generación XM | «Aún no hay archivos de XM cargados». | 34 centrales con proyecto, 0 proyectos sin generación; noviembre figura incompleto (11 de 30 días). |
| Liquidación | «Aún no hay generación de XM cargada». | C, octubre: 33 de 34 liquidadas, oficial, mes parcial 25 al 31. Cuatro vistas y guardar sin error. |
| Exportar | «Aún no hay liquidaciones guardadas». | XLSX (5 hojas) y PDF generados sin error. |
| Proyecciones | Aviso de que falta generación. | 2027: 34 de 34 centrales; seis vistas sin error. |
| Parámetros | Sin TVB registrada; ofrece cargar la serie. | 10 años (2017–2026). |

**Consola:** limpia. Solo el arranque del servidor.

**Textos que no están en español colombiano:**

- **Interfaz propia de Streamlit, en inglés:** botón «Deploy» y menú de la barra superior; el cargador de archivos («Drag and drop file here», «Browse files», «Limit 200MB per file»). El botón «Deploy» conviene ocultarlo de todos modos, porque la app no tiene login (hallazgo M6).
- **Fechas en formato ISO** en varios textos: «última vez el 2026-11-15», «cargado el 2026-11-15», «rige desde 2026-11-25».
- **Cifras sin formato colombiano:** «suma 9900 kW… no supera los 10000 kW»; «3.00 %» en el XLSX de proyección.
- **Maestro de autoridades:** varias corporaciones en mayúsculas y sin tildes («CORPORACION AUTONOMA REGIONAL RIONEGRO  NARE», con doble espacio).

Los textos propios de la app están todos en español.

---

## 3. Cumplimiento de las reglas obligatorias

| Regla | Estado | Evidencia |
|---|---|---|
| 1. No mide; divide | Cumple | Sin geometría en `motor/`, `datos/`, `paginas/`. |
| 2. Sin delimitación no se liquida; ausente = NULL | **Falla en un caso** | Hallazgo C1. Lo demás cumple: octubre real (antes de la vigencia) da 34 «sin delimitación» y ningún monto. |
| 2. Mes parcial | Cumple | Días 25 al 31 con la generación por día; el día 25 entra. |
| 3. Confirmación explícita | Cumple en el código | Solo `vigente` y `reemplazada` liquidan. Las 37 se confirmaron sin cotejo manual (decisión D8). |
| 4. Validaciones | Cumple | Todas en `motor/delimitacion.py:126-233` y `datos/repositorio.py:485-513`. «PDF adjunto» revisa que haya hash, no que el archivo exista (M11). |
| 5. Precisión | Cumple | Único `float(` del código: `paginas/proyecciones.py:136`, para dibujar la gráfica. Ningún `round()`. Razones en `Fraction`; montos cuantizados una vez (`motor/tse_calculator.py:201-207`). |
| 6. α, 6 % y 3 % centralizados | Cumple en el cálculo | Los valores solo están en `motor/parametros.py`. Hay textos con «3 %», «6 %» y «10.000 kW» escritos a mano (M3). |
| 7. Trazabilidad | Cumple | Cada liquidación guarda hash de XM, versión de XM, TVB, año y fuente, radicado y hash de cada PDF, hectáreas, razón y %. Recalcular crea versión nueva; guardar dos veces lo mismo no crea otra. |
| 8. Explicabilidad | Cumple | Vista «Paso a paso» con las citas normativas. |
| 9. Umbral por empresa | Cumple | `datos/cargue_xm.py:144-162`. `liquidar_proyecto` y `filtrar_centrales_sujetas_tse` (por central) solo los llaman las pruebas. Las 33 liquidadas: sujetas con capacidad de 2025. |
| 10. El motor corre solo | Cumple | `motor/` solo importa biblioteca estándar y a sí mismo. |
| 11. Datos reales o marcados | Cumple | Los sintéticos están en `tests/` y rotulados. |
| 12. Idioma | Cumple, con detalles | Ver sección 2. |
| 13. Versiones de XM | Cumple | Una liquidación oficial exige TXF (`datos/repositorio.py:988-991`). |
| Sin geometría, login ni lectura de PDF | Cumple | El único lector de PDF es `herramientas/precarga_delimitaciones.py`, que la app no importa. `st.pdf` solo muestra el documento. |
| Centrales compartidas | Cumple | GUATRON se liquida una vez y cita tres radicados; PAJARITO, dos. |
| `SIN_ASIGNAR` y residuo | Cumple | Betania retiene 0,49 %, Manantiales 0,31 %, La Ayurá 45,65 %. En las 33, el residuo fue a una autoridad real. |

---

## 4. CRÍTICO

### C1. En un mes parcial, una central sin ningún día en el tramo se liquida en $ 0,00

- **Dónde:** `datos/repositorio.py:892-904` (`generacion_del_tramo`) y `datos/liquidar_mes.py:221-236`.
- **Qué pasa:** `generacion_del_tramo` solo falla si la central no tiene ningún día en **todo el mes**. Si tiene días antes del tramo pero ninguno dentro, el tramo suma 0 kWh y la central se liquida: bolsa $ 0,00, cada autoridad con $ 0,00, guardada como «oficial» y contada entre las liquidadas. Solo queda un aviso («XM trae 0 de 7 días»).
- **Por qué rompe la regla:** en un mes completo, una central ausente queda «sin generación» y sin monto («no se toma como cero»). En el mes parcial el mismo caso produce ceros. La regla 2 dice que un dato ausente es `NULL`, no cero.
- **Ejemplo con datos reales (copia C):** Guacaica (GCC1), octubre de 2026. XM trae 5 días de la central, ninguno entre el 25 y el 31. Resultado: «Liquidada, 25 al 31, 0 kWh, bolsa $ 0,00», dentro de «33 de 34 centrales liquidadas».
- **Riesgo para la primera liquidación real:** en el archivo actual, Guacaica trae en noviembre los días 1, 2 y 8 al 11. Si no genera del 25 al 30, saldrá liquidada en ceros.
- **Corrección propuesta:** si ningún día del tramo trae fila de la central, `generacion_del_tramo` lanza `SinGeneracion` y la central queda en «No liquidados» con el motivo «Sin generación de XM en los días 25 al 30». Una prueba nueva con ese caso. Depende de la decisión D1.
- **Decisión de Jorge (2026-11-15) y estado: resuelto de otra forma.** La central se sigue liquidando en $ 0,00, pero rotulada «Sin generación» en Liquidación, en Exportar y dentro del XLSX y el PDF. Hecho el mismo día, con dos pruebas nuevas (160 en total). Una central sin fila en todo el mes sigue en «No liquidados», sin monto.

---

## 5. IMPORTANTE

### I1. Una versión firme incompleta bloquea el mes aunque exista una preliminar completa

- **Dónde:** `datos/liquidar_mes.py:153-158` (`except repo.MesIncompleto: raise`).
- **Qué pasa:** primero se busca la generación oficial, mirando solo filas TXF. Si la única TXF es de un archivo que no cubre el mes, se lanza `MesIncompleto` y no se intenta la preliminar.
- **Ejemplo (sintético):** un cargue TXF con 10 de 30 días y otro TX2 con 30 de 30. Resultado: «mes incompleto, no se liquida». `generacion_para(..., "preliminar")` sola sí devuelve el TX2.
- **Alcance:** no produce cifras malas; deja sin liquidar un mes que se podía liquidar como preliminar. Es el caso que el CLAUDE.md da por corregido el 2026-11-14, y sigue vivo por esta ruta.
- **Corrección propuesta:** si la oficial da `MesIncompleto`, intentar la preliminar; reportar incompleto solo si esa también lo está. Una prueba.

### I2. El modo `porcentaje` no acepta «a/b» en el formulario y daña el que esté guardado

- **Dónde:** `paginas/delimitaciones.py:58-59`, `:397`, `:415-420`, `:475-476`; `motor/delimitacion.py:51-70`.
- **Qué pasa:** la regla 5 dice que en modo `porcentaje` el valor se acepta como a/b. El repositorio lo acepta, pero el formulario lee con `numero_desde_texto`, que rechaza «1/3». Además, una fracción guardada se muestra en el editor como decimal de 28 cifras, que ya no es igual a lo guardado.
- **Ejemplo:** guardado «200/3»; el editor muestra «66,66666666666666666666666667». La pantalla marca «Hay cambios sin guardar» sin que nadie haya tocado nada y bloquea confirmar. Si se guarda, la suma deja de ser 100 exacto y la validación falla.
- **Alcance:** las 37 delimitaciones son de hectáreas; hoy no afecta ninguna cifra.
- **Corrección propuesta:** en la columna de porcentaje, aceptar «a/b» y mostrar como «a/b» lo que no tenga decimal finito. Una prueba de ida y vuelta.

### I3. Los errores de SQLite y de OneDrive tumban casi todas las pantallas

- **Dónde:**
  - `paginas/comun.py:17-24` — abrir la base, sin manejo; `datos/repositorio.py:137` no fija `timeout`.
  - `paginas/parametros.py:77` y `:107` — guardar TVB.
  - `paginas/generacion_xm.py:138-145` — solo atrapa `ErrorRepositorio`.
  - `paginas/delimitaciones.py:172-177`, `:218`, `:545`, `:573`.
- **Qué pasa:** solo «Guardar liquidación» (`paginas/liquidacion.py:233-240`) y dos botones de Delimitaciones atrapan `sqlite3.Error`. En el resto, una base bloqueada por la sincronización muestra el error técnico de Python, en inglés.
- **Ejemplo:** OneDrive tiene tomado `liquidador.db` mientras se pulsa «Guardar TVB»: sale «sqlite3.OperationalError: database is locked» con la traza.
- **Además:** al registrar una delimitación, el PDF se escribe en `almacen/` (`:173`) antes de insertar la fila (`:174`); si la inserción falla, queda un PDF huérfano.
- **Corrección propuesta:** un solo ayudante en `paginas/comun.py` que convierta `sqlite3.Error` en un mensaje en español («La base está ocupada; vuelva a intentarlo…») y usarlo en todas las escrituras y en `conexion()`; `timeout=15` al conectar; escribir el PDF después de insertar.

### I4. Caminos que mueven cifras y no tienen prueba

- **`datos/parse_creg_tvb.py`: 0 %.** Es la fuente de la serie TVB que carga la pantalla Parámetros. Un cambio en el RTF podría desalinear años y valores sin que nada lo detecte.
- **`paginas/`: 0 %.** Las recorrí a mano en esta revisión; no queda ninguna prueba que lo repita.
- **`datos/liquidar_mes.py:216-219`:** «reparto distinto» en una central compartida al calcular el mes.
- **`datos/repositorio.py:1010-1016`, `:1022`, `:1033`:** las guardas del mes parcial al guardar.
- **`datos/repositorio.py:176-195`:** migración 2 → 3.
- **`motor/delimitacion.py:188-190`, `:205`, `:213-220`:** porcentaje faltante o negativo, S2 sin columna, S1_T = 0, S2_T = 0, total S2 sin columna.
- **Corrección propuesta:** una prueba del lector de TVB contra el RTF real (10 años, 2026 = 192,23886); una prueba de humo por pantalla con `AppTest`; una prueba por cada rama anterior.

### I5. El proyecto no está en Git

- **Qué pasa:** hay `.gitignore`, pero la carpeta no es un repositorio. La regla 6 habla de un archivo de parámetros «versionado»; hoy la única historia del código es la de OneDrive.
- **Ejemplo:** si se edita α en `motor/parametros.py`, no queda registro de quién, cuándo ni cuál era el valor anterior.
- **Corrección propuesta:** `git init` con `almacen/` fuera, como ya lo prevé el `.gitignore`. Es decisión suya (D9).

---

## 6. MENOR

| N.º | Dónde | Qué pasa | Corrección propuesta |
|---|---|---|---|
| M1 | `motor/tse_calculator.py:165-172`, `:218-297`, `:331-556`; `datos/parse_xm_generacion.py:434-439`, `:486-501`; `datos/sujecion_empresas.py:98-132`; `datos/parse_creg_tvb.py:94-133`; `datos/repositorio.py:785-789`, `:1082-1087`; `datos/rutas.py:42` | **Código sin uso en la app.** Unas 330 líneas heredadas del SISTSE en el motor (`liquidar_proyecto`, `distribuir_formula_1/2`, `es_sujeta_a_tse`…) que solo ejercitan las pruebas de `tests/test_tse_calculator.py`. `parsear_tvb_ppc_cva_xlsx`, `ultima_liquidacion` y `dir_delimitaciones` no los llama nadie. | Mover lo heredado a `motor/heredado.py` o borrarlo con sus pruebas (D9). |
| M2 | `motor/parametros.py:82`, `:95-97`, `:70`, `:26`, `:107-109` | **Parámetros que el código no lee:** `redondeo_desempate`, `base_redondeo_reparto`, `ajuste_residuo`, `s2_t_cero_con_paramo`, `unidad_superficie`, `vigente_desde`, `REDONDEO_MAP`. Cambiarlos no cambia nada: el redondeo está fijo en `centavos`. | Borrarlos o dejarlos como comentario. |
| M3 | `motor/liquidacion.py:38-47`; `paginas/parametros.py:128-131`; `paginas/liquidacion.py:26`, `:152`; `paginas/generacion_xm.py:241`; `datos/liquidar_mes.py:245` | **«3 %», «6 %» y «10.000 kW» escritos a mano** en textos. No entran al cálculo, pero quedarían falsos si cambia el parámetro. | Armar los textos con los valores de `PARAMETROS_VIGENTES`. |
| M4 | Ver lista abajo | **Código duplicado** entre `datos/` y `paginas/`. | Una sola función por cada caso. |
| M5 | `datos/repositorio.py:211`, `:222`, `:261`, `:316`, `:449`, `:857-862`, `:1063` | **SQL armado con f-string.** Solo interpola constantes y nombres de columna de listas fijas; todos los valores van como parámetros. Sin riesgo de inyección. | Ninguna. |
| M6 | `.streamlit/config.toml`; `paginas/liquidacion.py:205`; `paginas/exportar.py:93`; `paginas/generacion_xm.py:122`, `:162`; `datos/repositorio.py:503`, `:624`, `:756`, `:766`; `datos/liquidar_mes.py:248-249`; `datos/proyeccion.py:255-256`, `:331`, `:344` | **Formato e idioma** (sección 2): fechas ISO, kW sin separador de miles, «3.00 %», botón «Deploy» visible. | `toolbarMode = "minimal"` en la configuración; un formateador de fecha y de kW en `paginas/comun.py`. |
| M7 | `motor/delimitacion.py:63-66` | **«1.312» se lee como 1,312**, no como 1.312 (mil trescientos doce). Si la fila y el total se digitan igual, pasa la validación. El IGAC siempre escribe 5 decimales con coma, así que el riesgo es bajo. | Rechazar un punto seguido de exactamente tres cifras sin coma, y pedir que se reescriba. |
| M8 | `datos/exportar.py:107`, `:151-152` | **Excel guarda 15 cifras.** Las ventas brutas con más de 15 cifras significativas se ven redondeadas en el XLSX. La razón sí va exacta, como texto. | Agregar las ventas brutas exactas como texto, o dejarlo anotado en la hoja. |
| M9 | `datos/exportar.py:146`, `:191-201`; `motor/parametros.py:25` | **El XLSX usa los parámetros de hoy**, no los de la versión guardada, para el «TSE 6 %» y la hoja «Parámetros y normas». La liquidación guarda `parametros_version` pero no α ni el 3 %, y nada obliga a subir la versión al cambiarlos. | Una prueba que amarre versión ↔ valores; avisar en el XLSX si la versión guardada no es la vigente. |
| M10 | `motor/tse_calculator.py:623` | **Σ razones = 1 es un `assert`**: desaparece si Python corre con `-O`. | Cambiarlo por una excepción. |
| M11 | `datos/repositorio.py:495-496` | **«PDF adjunto» revisa el hash, no el archivo.** Hoy los 37 PDF existen y coinciden con su hash (verificado). | Revisar existencia y hash al confirmar. |
| M12 | `paginas/parametros.py:107`; `datos/repositorio.py:907-914` | **Corregir una TVB pisa la anterior** en la tabla `tvb`. Las liquidaciones conservan la suya, pero no el hash de la fuente. | Guardar el cambio en una tabla de historial, como `delimitacion_cambios`. |
| M13 | `paginas/comun.py:17-24` | **La conexión no se cierra** y cada sesión nueva reescribe el maestro de autoridades: abrir la app escribe en la base y dispara la sincronización de OneDrive. | Cerrar al terminar; reescribir el maestro solo si cambió. |
| M14 | `datos/cargue_xm.py:148-154` | **Central que cambia de empresa en el año:** la sujeción toma una de las dos sin criterio de fecha. Ninguna de las 34 centrales está en ese caso. | Tomar la empresa del último mes con dato. |

**Detalle de M4 (duplicados):**

- Escritura de hojas XLSX: `datos/exportar.py:93-113` y `datos/proyeccion.py:292-307`.
- Nombre de la autoridad: `datos/exportar.py:66-72`, `datos/proyeccion.py:311-316`, `paginas/liquidacion.py:44-47`, `paginas/proyecciones.py:127-130`, `paginas/exportar.py:80-81`.
- Lista de meses: `paginas/comun.py:34`, `datos/exportar.py:29`, `datos/proyeccion.py:44`.
- Agrupar proyectos por central: `datos/liquidar_mes.py:168-170` y `datos/proyeccion.py:229-231`.
- Comparar repartos: `datos/repositorio.py:522-524`, `datos/liquidar_mes.py:215-216`, `datos/proyeccion.py:213-214`.
- Carácter del mes: `datos/exportar.py:40-42` y `datos/liquidar_mes.py:123-127`.
- Formato de kWh: `paginas/comun.py:42` y `paginas/generacion_xm.py:36`.
- Vigencia por defecto: `herramientas/precarga_delimitaciones.py:35` repite la de `motor/parametros.py:105`.
- El «paso a paso» está escrito dentro de `paginas/liquidacion.py:132-181`, no en una función. La Fase 2 pide reutilizarlo.

**Orden de la carpeta:** `almacen/_to_delete/` (una base fallida y un journal vacío) y dos respaldos del 14-11; cinco comentarios citan `TAREAS-JORGE.md`, que no existe aquí; `docs/03-liquidacion-desde-porcentajes-igac.md` todavía habla de «oficios»; `tests/__pycache__` tiene archivos de Python 3.10, señal de que alguna vez se corrieron las pruebas con otra versión.

---

## 7. Pendientes abiertos y decisiones que necesito

### Decisiones

| N.º | Tema | Lo que encontré | Lo que necesito |
|---|---|---|---|
| D1 | Días ausentes de una central en XM | 28 de las 34 centrales tienen al menos un mes completo de 2026 con días ausentes (79 de 320 central-mes). Entre ellas hay grandes: Guavio 28 de 31 en enero, Chivor 26 de 28 en febrero, Porce III 26 de 31 en julio. Hoy se toman como «sin generación» y no bloquean. | Confirmar ese criterio. Y para C1: si la central no trae **ningún** día del tramo, ¿queda sin monto? Lo recomiendo, por coherencia con el mes completo. |
| D2 | TXF en septiembre y octubre de 2026 | El archivo trae agosto en TX3 y septiembre–octubre en TXF: la versión no avanza en orden de meses. Choca con «la firme sale ~3 meses después». | Confirmar con XM qué significa esa columna. Mientras tanto, septiembre y octubre saldrían como «oficial». |
| D3 | Destino de `SIN_ASIGNAR` | En la simulación de octubre: La Ayurá retiene 45,65 % de su bolsa, Betania 0,49 %, Manantiales 0,31 %. | A quién va, sobre todo en La Ayurá. |
| D4 | Residuo de centavos | Hoy va al mayor beneficiario; en empate, por orden de sigla. | Confirmarlo con la ONVS. |
| D5 | Capacidad para el umbral | Se usa la capacidad efectiva neta de XM (máximo mensual de 2025) y se suman hidráulica, térmica, cogeneración y eólica. El art. 45 habla de potencia nominal instalada. Las 33 centrales liquidadas quedan sujetas en cualquier caso. | Maestro oficial de capacidad nominal y qué tipos suman. |
| D6 | Respaldo de la TVB | La fuente que queda en cada liquidación es «TVB Consolidado.rtf · Resolución CREG 010 de 2018». | La resolución o el documento de cada año, si quiere citarlo. |
| D7 | Riogrande I – Embalse Quebradona (RGRN) | XM no trae generación de RGRN en octubre ni en noviembre de 2026; el último dato es septiembre, con 7 días. En noviembre saldrá «sin generación». | Confirmar que el código es el correcto y que la central no está generando. |
| D8 | Las 37 confirmadas sin cotejo manual | Pasan todas las validaciones, incluida Σ filas = total declarado, y sus 37 PDF coinciden con su hash. No hay cotejo cifra por cifra. | ¿Hacemos un cotejo por muestreo antes de la primera liquidación oficial? |
| D9 | Git y código heredado | I5 y M1. | ¿Inicializo Git? ¿Borro lo heredado del SISTSE o lo aparto? |

### Otros pendientes del CLAUDE.md que siguen abiertos

- **`ParametrosDecreto800.vigente_desde`:** sigue en «2026-01-01» como marcador y ningún código lo lee (M2). Falta la fecha real de publicación del Decreto 800.
- **SQLite dentro de OneDrive:** la base está sana (`integrity_check` correcto, modo `delete`). El riesgo es el de I3. La salida ya existe: `LIQUIDADOR_BD` hacia una ruta fuera de OneDrive.
- **Proyecto «TSE ALL» de Claude:** sigue sin leerse.
- **Un solo tramo por mes:** el límite sigue igual. Lo confirmé con un caso sintético: un reemplazo el día 10 liquida del 10 al 30 (2.100 de 3.000 kWh) y avisa que los días 1 al 9 no quedan liquidados.

### Ya cerrados, verificados en esta revisión

- Los 37 proyectos están enlazados a una central (34 centrales).
- El cruce vive solo en `proyectos.codigo_submercado`.
- Las 37 delimitaciones rigen desde el 25-11-2026.
- El residuo nunca cae en `SIN_ASIGNAR`.
- El archivo XM 2026 no trae filas repetidas por día en centrales hidráulicas (las 263 repeticiones son térmicas con dos combustibles), ni horas vacías o negativas en las 34 centrales.

---

## 8. Dos cosas que afectan la Fase 2

1. **El reporte saldrá vacío hasta que haya liquidaciones guardadas.** La base real no tiene XM, TVB ni liquidaciones, y la primera posible es noviembre (25 al 30), cuando XM publique el mes completo.
2. **El motivo de «sin monto» no está guardado.** La base guarda liquidaciones, no las centrales que no se liquidaron ni por qué. La tabla «sin monto con su motivo» no puede salir solo de lo guardado. Opciones: calcular el motivo con los datos de hoy y rotularlo así (lo que ya hace Exportar), o guardar el motivo al guardar el mes (cambio de esquema). Recomiendo la primera.

Altair 6.3.0 ya viene con Streamlit: no hace falta agregar dependencias.
