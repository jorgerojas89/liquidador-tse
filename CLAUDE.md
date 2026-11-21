# CLAUDE.md — Liquidador TSE (versión reducida del SISTSE)

> Instrucciones para un proyecto nuevo, escritas el 2026-11-11 a partir del
> SISTSE (carpeta `APP TSE`). Este archivo va como `CLAUDE.md` en la raíz de la
> carpeta nueva y como instrucciones del proyecto en Claude.

## Qué es

Herramienta **interna de la ONVS** (Minambiente) que hace dos cosas y nada más:

1. **Establecer porcentajes.** A partir de cada **delimitación del IGAC** (ya no
   se llaman certificaciones ni oficios de distribución), obtener el porcentaje
   que corresponde a cada autoridad ambiental (CAR o Parques Nacionales) en cada
   proyecto hidroeléctrico.
2. **Liquidar el mes.** Se le carga la generación mensual de XM y computa el
   valor a transferir a cada autoridad ambiental de cada delimitación.

## Qué NO es (decisión de Jorge, 2026-11-11)

- **Sin geovisor, sin mapa, sin cartografía.** Ni Leaflet, ni GeoJSON, ni
  PostGIS, ni GeoPandas. Cero geometría en el repositorio.
- **Sin usuarios, roles ni login.** La usa una persona de la ONVS.
- **Sin extracción automática del PDF.** Las cifras de la delimitación se
  digitan en un formulario y el PDF se adjunta como respaldo.
- Sin vista pública, sin simulador, sin dashboard de indicadores, sin el 3% de
  municipios, sin conexión en línea a XM ni a la CREG.

Si algo de esto vuelve a aparecer en una petición, pregunta antes de construirlo.

## Cadena de cálculo

```
ventas_brutas  = generación_kWh (XM) × TVB_$/kWh (CREG, año del periodo)
bolsa_CAR_PNN  = 3% × ventas_brutas        (mitad del 6% del art. 45 Ley 99/1993)
pct_i          = razón del Decreto 800 de 2026 sobre las hectáreas de la delimitación
monto_i        = bolsa_CAR_PNN × pct_i
```

Cómo sale `pct_i`:

- **Fórmula 1** (la delimitación no trae S2): `pct_i = S1_i / S1_T`
- **Fórmula 2** (trae S2 para todas las autoridades):
  `pct_i = (1 − α)·(S1_i / S1_T) + α·(S2_i / S2_T)`, con α = 0,5228

La fórmula la decide lo que se digitó de la delimitación, no la aplicación.

### Formato real de las delimitaciones (resuelto el 2026-11-13)

Llegaron 37 delimitaciones del IGAC bajo el Decreto 800 (radicadas el
11-11-2026, en `insumos/delimitaciones-igac/`). **Traen hectáreas S1_i y S2_i
por autoridad**, nunca porcentajes. Se captura en modo `hectareas`; el modo
`porcentaje` se conserva por si llega otro formato.

- 26 declaran intersección con páramo (Fórmula 2). 11 no: 4 repiten S1 en la
  columna S2 y 7 traen S2 en N/A (Fórmula 1). Sin páramo, S2_i = S1_i y las
  dos fórmulas dan lo mismo.
- **«N/A» en una celda** es una declaración del IGAC (no hay área en esa
  jurisdicción): se guarda `NULL` y vale 0 ha en la fórmula. Un **cero
  digitado no se acepta**. «S2 para todas o ninguna» se aplica a la **columna**
  (`trae_s2`), no fila por fila: la CRC en Betania tiene S1 = N/A y S2 > 0.
- La fila TOTAL de la tabla por autoridad suma exacto las filas en las 37; es
  el total declarado contra el que se valida. El cuadro de la primera página
  puede diferir en la 5.ª cifra (Guavio: 108.297,21900 contra 108.297,21898).
- **«Autoridad ambiental urbana o área en litigio»** (Jorge, 2026-11-13): su
  parte se calcula y se **retiene sin asignar** (sigla `SIN_ASIGNAR`); no se
  reparte entre las demás. Tiene área en Betania (0,49 % del 3 %),
  Minicentral Manantiales y PCH La Ayurá (≈46 %).
- **Los PDF no traen fecha de vigencia** (Jorge, 2026-11-13): se digita por
  delimitación; es obligatoria para confirmar, no para guardar un borrador.

S2_i **no se deduce** sumando hectáreas sueltas (cuenta doble el traslape).

### Precarga única (Jorge, 2026-11-13)

`herramientas/precarga_delimitaciones.py` es una herramienta aparte, **no
parte de la app**: `extraer` pasa los PDF a `precarga_delimitaciones.csv`
(cifras tal cual las escribe el IGAC) y `cargar` crea borradores sin vigencia,
con el PDF copiado a `almacen/delimitaciones/` y su SHA-256. Los proyectos y
su central de XM salen de `precarga_proyectos.csv`. La app sigue sin leer PDF
y nada liquida hasta que se confirme cada una contra su PDF.

## Reglas obligatorias

1. **La aplicación no mide; divide.** Las hectáreas (o los %) son entrada
   digitada desde la delimitación. Nada se deriva de geometría.
2. **Sin delimitación vigente no se liquida.** Nunca repartir por partes
   iguales ni estimar. El proyecto sale listado como «sin delimitación» y sin
   monto. Un dato ausente es `NULL`, no cero.
   **Mes parcial** (Jorge, 2026-11-14; reemplaza «solo meses completos» del
   2026-11-12): si una delimitación empieza a regir después del día 1, ese mes
   se liquida solo desde ese día (incluido) hasta el último.
3. **Confirmación explícita.** Una delimitación digitada queda en `borrador`
   hasta que la persona la confirme contra el PDF. Solo las `vigente` liquidan.
   Una nueva reemplaza la anterior desde su fecha de vigencia; nunca se borra.
4. **Validaciones antes de confirmar** (`motor/delimitacion.py`). Σ razones = 1
   exacto (fracciones); hectáreas > 0 o N/A; S2_i ≥ S1_i (N/A = 0); columna S2
   presente o ausente; Σ filas = total declarado; páramo declarado ⇒ trae S2,
   sin páramo ⇒ S2_i = S1_i; autoridad existente en el maestro (o
   `SIN_ASIGNAR`); proyecto enlazado a una central de XM; fecha de vigencia;
   PDF adjunto. Si no cuadra, se muestra la diferencia: la app no ajusta sola.
5. **Precisión.** `Decimal` o `Fraction` siempre, `float` nunca. Las razones
   son **fracciones exactas** (a/b) y suman 1 sin residuo; los 5 decimales son
   solo de presentación (decisión de Jorge, 2026-11-11: se aparta a sabiendas
   de la letra del parágrafo 2° del Decreto 800, que redondea los cálculos
   intermedios). En modo `porcentaje` se acepta el valor como a/b. Montos
   cuantizados una sola vez a centavos con ROUND_HALF_UP; el residuo de
   centavos va al beneficiario de mayor participación y queda registrado.
6. **α, 6% y 3% centralizados** en un único archivo de parámetros versionado.
7. **Trazabilidad.** Cada liquidación guarda: hash SHA-256 del archivo de XM,
   TVB y año aplicados, identificador y hash del PDF de la delimitación,
   hectáreas y % usados. Recalcular crea una versión nueva, no pisa la anterior.
8. **Explicabilidad.** Cada monto se despliega paso a paso: generación → TVB →
   ventas brutas → 3% → % de la autoridad → monto, con la cita normativa.
9. **Umbral del art. 45.** Solo empresas con potencia nominal instalada total
   > 10.000 kW (por empresa, no por central).
10. **El motor corre solo.** El cálculo funciona con datos en memoria, sin base
    de datos ni interfaz, y tiene pruebas propias.
11. **Datos reales o marcados.** Si falta un insumo, decirlo y usar datos
    sintéticos rotulados. Nunca inventar cifras presentándolas como reales.
12. **Idioma.** Español colombiano en todo lo que ve el usuario.
13. **Versiones de XM** (Jorge, 2026-11-12). XM publica varias versiones de la
    generación de cada mes (TX1, TX2… y TXF, la firme, ~3 meses después). Se
    guardan todas, cada fila con su versión. **Solo la TXF produce una
    liquidación oficial**; con TX1/TX2 la liquidación es `preliminar`, de
    referencia, y así se rotula. El archivo diario de 2026 **sí trae la
    versión** (columna `Version`, TX1…TX5 y TXF) y manda el archivo; los
    mensuales 2018-2025 no la traen y se declara al cargar (paso 4, 2026-11-14).

### Centrales que XM agrupa en un solo código (Jorge, 2026-11-14)

GUATRON (GTRG, 512 MW) = Guadalupe III + Guadalupe IV + Troneras, y
PAJARITO (PJR1) = Pajarito I + Pajarito II. XM da una sola generación, pero el
IGAC delimitó cada planta. Las cinco son 100 % CORANTIOQUIA, así que
**se agrupan**: agruparlas no permite ver la generación por proyecto, pero no
cambia el valor de ninguna autoridad. Reglas (esquema v3):

- Varios proyectos pueden compartir `codigo_submercado`; cada delimitación
  conserva su radicado y su PDF.
- Solo se permite si **reparten igual**. Si la vecina vigente reparte distinto,
  la confirmación se bloquea; si es borrador, queda un aviso.
- **Se liquida una vez por central**, no por proyecto (`guardar_liquidacion`
  rechaza un segundo proyecto de la misma central en el mismo periodo). En el
  paso 5, una central compartida se liquida solo en los meses en que todas sus
  delimitaciones están vigentes, y la liquidación cita todos los radicados.

## Pantallas (cuatro, no más)

1. **Delimitaciones** — lista por proyecto con estado; formulario de captura
   (proyecto, radicado, fecha, vigencia, modo, filas por autoridad, PDF adjunto);
   tabla de % resultante; botón confirmar.
2. **Generación XM** — subir el Excel mensual/anual de XM; ver qué centrales
   reconoció, cuáles no cruzan con un proyecto y cuáles quedan bajo el umbral.
3. **Liquidación** — elegir periodo; tabla proyecto × autoridad con monto;
   detalle paso a paso; lista aparte de proyectos sin delimitación vigente.
4. **Exportar** — XLSX con variables intermedias y PDF resumen por autoridad.

Más una pantalla mínima de **parámetros**: serie TVB por año y α, y la de
**proyecciones** (estimación, ver abajo).

## Stack propuesto (simple a propósito)

Python 3.12 · SQLite (un archivo) · Streamlit para la interfaz · openpyxl para
leer XM y escribir XLSX · reportlab para el PDF · pytest. Sin Docker, sin Node, sin PostgreSQL. Se
ejecuta con `streamlit run app.py` en el computador de quien la use.

```
liquidador-tse/
  CLAUDE.md
  motor/            # tse_calculator.py, parametros.py  (sin BD ni UI)
  datos/            # parse_xm.py, parse_tvb.py, sujecion_empresas.py, repositorio.py (SQLite)
  app.py  paginas/  # Streamlit
  tests/
  insumos/          # xm-generacion/, creg-tarifas/, delimitaciones-igac/, normativa/
  almacen/          # PDFs adjuntos y liquidador.db (fuera de Git)
```

## Qué se trae del SISTSE y qué se deja

**Copiar y adaptar** (desde `APP TSE/sistse-app/backend/`):

| Origen | Para qué |
|---|---|
| `services/tse_calculator.py` + `tests/test_tse_calculator.py` | Motor del Decreto 800, ya probado. Quitar lo de municipios. |
| `core/config.py` (`ParametrosDecreto800`) | α, 6%, 3%, regla de redondeo. |
| `etl/parse_xm_generacion.py` | Lector del Excel de XM. |
| `etl/parse_creg_tvb.py` | Serie TVB 2017-2026. |
| `etl/sujecion_empresas.py` + su prueba | Umbral de 10.000 kW por empresa. |
| `tests/test_liquidacion_desde_oficio.py` | Casos de prueba de liquidación (renombrar a «delimitación»). |
| Maestro de 53 autoridades (en `db/seeds/seed_demo.py`) | Lista de CAR y Parques. |
| `etl/mapeo_cartografia_xm.json` / `Relacion_capas_IGAC_centrales_XM.xlsx` | Solo el cruce proyecto ↔ central XM. |
| `insumos/xm-generacion`, `creg-tarifas`, `normativa`, `igac-distribucion` | Insumos reales. |
| `docs/03-liquidacion-desde-porcentajes-igac.md` | Base de la especificación (§4, 5, 7, 8, 10). |

**No traer:** `cartografia_procesada/`, `insumos/cartografia/`, `etl/cartografia_igac.py`,
`inventario_capas.py`, `capas_base.py`, `routers/mapa.py`, `geo_service.py`,
`documento_igac.py` (extractor PDF), autenticación y roles, frontend React
completo, Docker, migraciones Alembic, simulador, dashboard, vista pública.

## Orden de trabajo

1. ✅ Crear la carpeta, copiar lo de la tabla y dejar las pruebas del motor en verde.
2. ✅ Modelo SQLite: proyectos, autoridades, delimitaciones (+ filas), generación,
   TVB, liquidaciones (versionadas).
3. ✅ (2026-11-13) Captura de delimitaciones con validaciones, pantalla
   `paginas/delimitaciones.py`, precarga de las 37. Esquema versión 2.
4. ✅ (2026-11-14) Cargue del Excel de XM y cruce central ↔ proyecto: pantalla
   `paginas/generacion_xm.py`, lector de los dos formatos (`leer_xm`),
   `datos/cargue_xm.py`. Esquema versión 4.
5. ✅ (2026-11-14) Liquidación mensual y despliegue paso a paso: `motor/liquidacion.py`
   (puro), `datos/liquidar_mes.py`, pantalla `paginas/liquidacion.py`. Esquema versión 5.
6. ✅ (2026-11-14) Exportables XLSX y PDF: `datos/exportar.py`, pantalla `paginas/exportar.py`
   (PDF con `reportlab`). Pantalla de parámetros: `paginas/parametros.py`.

### Cómo liquida el paso 5 (2026-11-14)

- **Una vez por central de XM.** El cálculo se hace en memoria al abrir la
  pantalla y no escribe; «Guardar» crea una versión solo de las centrales cuyo
  cálculo cambió (no se repiten versiones idénticas). Se exporta lo guardado.
- **Se liquida con `motor.liquidacion.liquidar_central`**, con las razones de
  la delimitación; el umbral por empresa lo resuelve `cargue_xm.sujecion_central`.
  (El heredado `liquidar_proyecto`, que aplicaba el umbral por central, se
  retiró el 2026-11-15.)
- **Oficial** si XM ya trae TXF para el mes; si no, **preliminar** con la versión
  más reciente. Al llegar la TXF se vuelve a guardar: queda una versión nueva.
- **Sin monto** (nunca cero ni estimado): sin central, sin delimitación, mes
  partido, reparto distinto, sin generación, mes incompleto, sin TVB del año,
  bajo el umbral, sujeción sin determinar.
- **Central compartida**: se liquida solo si todas sus delimitaciones rigen el
  mes; la liquidación guarda todas en `liquidacion_delimitaciones` (v5).
- **TVB**: tabla `tvb`, se registra en Parámetros (serie de la CREG o a mano,
  con fuente obligatoria). Nunca se usa la de otro año. α, 6 % y 3 % no se
  editan en la app (regla 6).

### Vigencia desde el 25-11-2026 y mes parcial (Jorge, 2026-11-14)

**Reemplaza «solo meses completos» (2026-11-12) y la regla 2 en ese punto.**

- Las 37 delimitaciones quedaron **vigentes desde el 2026-11-25** (ese día
  incluido), confirmadas por indicación de Jorge **sin cotejo manual contra el
  PDF**: las cifras vienen de la precarga y pasan todas las validaciones.
- **Fecha por defecto** al registrar una delimitación: `2026-11-25`
  (`motor/parametros.py`, `VIGENCIA_DELIMITACIONES_POR_DEFECTO`), modificable.
- **La vigencia de una confirmada se puede ajustar** (`repo.cambiar_vigencia`,
  bloque «Ajustar la fecha de vigencia» de la pantalla): solo la fecha, nunca
  las cifras; no puede ser anterior al documento; queda en `delimitacion_cambios`.
  Los meses ya guardados se señalan para volver a guardarlos.
- **Mes parcial**: si la delimitación empieza a regir dentro del mes, ese mes
  se liquida **solo desde ese día hasta el último** con la generación por día
  de XM (tabla `generacion_diaria`, esquema **v6**; `repo.generacion_del_tramo`,
  `repo.delimitacion_del_tramo`). Noviembre de 2026 = del 25 al 30. Sigue
  exigiendo que el archivo de XM cubra el mes entero. Sin dato por día
  (formato mensual) no se liquida. `liquidaciones.dia_desde/dia_hasta`
  (NULL = mes completo).
- Solo se guarda por día lo hidráulico y lo enlazado a un proyecto. Un archivo
  cargado antes de enlazar una central no hidráulica hay que volver a cargarlo.
- **Límite conocido**: se liquida un solo tramo por mes. Si una delimitación
  reemplaza a otra a mitad de mes, los días anteriores (de la anterior) no se
  liquidan y la pantalla lo avisa; conviene que un reemplazo rija desde el día 1.
- `exigir_delimitacion`/`delimitacion_aplicable` conservan el sentido «rige el
  mes completo» (los usa la proyección, que solo estima meses completos).

### Proyecciones (capacidad nueva, Jorge 2026-11-14)

Jorge pidió estimar ingresos futuros; es la única excepción a «sin simulador» y
**no cambia la liquidación**, donde sigue rigiendo «nunca estimar» (regla 2).
`motor/proyeccion.py` (puro), `datos/proyeccion.py`, pantalla `paginas/proyecciones.py`.

- **Generación**: por central y mes, promedio del mismo mes en los **últimos 5
  años con dato** (medio), el menor (bajo) y el mayor (alto). Un mes sin dato
  no cuenta como cero. El histórico es lo cargado en «Generación XM» (solo
  meses completos); para 5 años hay que cargar los archivos 2021-2025 y 2026.
- **TVB**: sugerida = última real × (1 + variación promedio de los últimos 5
  cambios anuales)ⁿ, **editable**. Nunca se guarda en la tabla `tvb`.
- **Reparto**: **solo delimitaciones vigentes** (igual que la liquidación).
- Salida mensual y anual por autoridad y por central, y XLSX rotulado
  «ESTIMACIÓN». **No guarda nada en la base** ni crea liquidaciones.
- El rango anual suma los extremos de cada mes: es amplio, no una probabilidad.
- **Detalle de una autoridad** (Jorge, 2026-11-14): además del total por
  autoridad, se ve de qué proyectos sale lo de cada una, con su participación
  (`Proyeccion.por_autoridad_y_central`, vista «Detalle de una autoridad» y
  hoja «Autoridad por central»). `por_autoridad` es la suma de ese desglose.

### Ajustes del 2026-11-14 (revisión completa)

- `generacion_para` prefiere las filas de un archivo que cubre el mes entero:
  un archivo parcial viejo con versión «más avanzada» ya no bloquea el mes.
- La versión de XM es **del mes** (la menos avanzada del archivo en ese mes),
  no de cada recurso.
- **La vigencia no puede ser anterior a la fecha del documento** (error al
  confirmar): las delimitaciones rigen hacia adelante.
- **Corrección de una confirmada**: se registra otra con el mismo radicado y la
  **misma fecha de vigencia**; la sustituye en todos sus meses y la anterior
  queda `reemplazada`. Los meses ya liquidados se vuelven a guardar.

### «Sin generación» (Jorge, 2026-11-15)

Si XM no reporta generación de una central en los días liquidados (0 kWh), la
central **se liquida en $ 0,00 y además se rotula «Sin generación»**: un cero a
secas no dice por qué. `CentralMes.sin_generacion`, `exportar.sin_generacion`;
se ve en Liquidación (columna de generación, aviso y paso a paso), en Exportar
(columna «Observación») y dentro del XLSX y el PDF. Sale en un mes parcial
cuando la central no trae ningún día del tramo (Guacaica en la revisión de
`docs/revision-2026-11.md`). Una central sin fila en **todo** el mes sigue en
«No liquidados», sin monto.

### Fallos corregidos el 2026-11-15 (revisión de código)

- **Mover una corrección mueve a la corregida** (`repo.cambiar_vigencia`,
  `_corregidas_por`): si se quedaba en la fecha vieja, volvía a regir los días
  intermedios con las cifras malas. El cambio de la corregida queda en
  `delimitacion_cambios`. Una corrección ahora también se puede mover hacia
  atrás (hasta la delimitación que reemplazó con fecha anterior).
- **Exportar sale todo de lo guardado**: «No liquidados» son las centrales
  sin liquidación guardada (`liquidar_mes.no_liquidadas_de_lo_guardado`; una
  calculada sin guardar figura como «Calculada pero sin guardar»), y lo que
  difiera del cálculo de hoy va como aviso dentro del XLSX y el PDF
  (`avisos_de_lo_guardado`). Antes una central podía quedar en el total y en
  «No liquidados» a la vez.
- **Un archivo de XM dañado no tumba la pantalla**: `leer_xm` convierte un
  archivo que no abre o una celda que no es cifra en `ArchivoXMInvalido`, con
  la ubicación; la pantalla usa `cargue_xm.leer_archivo` y recuerda el error.
- **`repo.a_texto` guarda un texto normalizado** («43,21875» → «43.21875»);
  rechaza NaN e infinito.
- **El mes se guarda en una sola transacción** (`repo.guardar_liquidaciones`:
  valida todo y luego escribe; o todas las centrales o ninguna). Si otra
  pestaña guardó al mismo tiempo, se deshace y se avisa. La pantalla también
  informa un error de SQLite (p. ej., base bloqueada por OneDrive).
- **Central compartida**: el aviso «Mismo reparto» ya no sale junto con el
  error «reparten distinto» (se decidía buscando un texto que no coincidía),
  y solo nombra los proyectos que se pudieron comparar.
- **Sin lógica atada a textos**: el * de días ausentes sale de
  `CentralMes.dias_ausentes`, no de buscar en las alertas.
- **La huella de una liquidación incluye empresa y capacidad del umbral**: si
  llega otra capacidad, la central queda pendiente de volver a guardar.
- **Nombres de una central compartida en orden alfabético** en Liquidación,
  Exportar y Proyecciones (`liquidar_mes.ordenar_nombres`). Se guarda bajo
  el proyecto de menor id, como antes.
- **Umbral y eólica**: la hoja de XM de 2025 cuadra 68/68 con o sin EOLICA (en
  enero las eólicas no traen capacidad); el parámetro la incluye y hoy no
  cambia ninguna sujeción. Sigue pendiente confirmarlo con la ONVS.
- **Código sin uso retirado** (decisión de Jorge, 2026-11-15): de
  `motor/tse_calculator.py`, el orquestador heredado del SISTSE
  (`liquidar_proyecto`, `distribuir_formula_1/2`, `es_sujeta_a_tse`,
  `calcular_ventas_brutas`, `calcular_tse_total`, `calcular_monto_car_pnn`,
  `redondear` y sus estructuras), que además aplicaba el umbral por central
  (regla 9); también `filtrar_centrales_sujetas_tse`, `REDONDEO_MAP` y
  `rutas.dir_delimitaciones`. Sus casos de prueba se pasaron, con las mismas
  cifras, a `liquidar_central` (`tests/test_tse_calculator.py`). El original
  sigue en la carpeta del SISTSE. **Se conservan aunque la app no los llame**,
  porque verifican datos reales: `reproducir_hoja_xm` y
  `leer_hoja_sujetos_activos` (umbral contra la hoja de XM) y
  `parsear_tvb_ppc_cva_xlsx` (TVB contra los archivos PPC-CVA de XM, con
  prueba nueva).

## Pendientes que siguen vivos

- ✅ Centrales de XM (Jorge, 2026-11-14): los 37 proyectos quedaron enlazados
  (`precarga_proyectos.csv`). Mocorongo → RGR1, PCH Porce III → 2S8U,
  PCH La Ayurá → AYR1.
- Definir con la ONVS a quién va la parte `SIN_ASIGNAR` (sobre todo PCH La Ayurá).
  El residuo de centavos ya nunca cae en `SIN_ASIGNAR` (`_ajustar_residuo`, 2026-11-13).
- **XM 2026** (Jorge, 2026-11-14): `insumos/xm-generacion/Generacion_(kWh)_2026.xlsx`.
  Las delimitaciones rigen **hacia adelante, no hacia atrás**: la generación
  de 2026 anterior a la vigencia es **dato histórico**, no se liquida con
  ellas. Formato: una fila por recurso, **día** y combustible (una térmica
  con dos combustibles trae dos filas, que se suman), 24 columnas horarias,
  `Código Recurso` (= código de submercado), código de agente (EPMG),
  `Version`, sin capacidad ni municipio. Resuelto en el paso 4:
  - Versiones del archivo al 2026-11-14: ene–feb TX5, mar–abr TX4, may–ago
    TX3, **sep–oct TXF**, nov TX1/TX2 con 11 días. **Por confirmar con Jorge**:
    TXF en septiembre y octubre choca con «la firme sale ~3 meses después».
  - Mes completo = el **archivo** cubre todos los días. Noviembre (11 de 30)
    queda guardado como incompleto y no se liquida (`repo.MesIncompleto`).
  - XM **omite los días sin generación** (solo a veces escribe la fila en
    ceros). Un día ausente de una central en un mes cubierto se toma como
    «sin generación» y se marca con * en la pantalla; no bloquea. Una central
    ausente todo el mes no tiene generación (no cero). **Criterio por
    confirmar con Jorge.**
  - Si un recurso trae varias versiones en un mes, se guarda la menos avanzada.
  - Umbral: la capacidad sale del archivo mensual más reciente con año ≤ al
    periodo (para 2026, el de 2025), cruzando por código de central. Las 34
    centrales de los 37 proyectos quedan **sujetas**. Sin capacidad cargada,
    la sujeción queda «sin determinar», no se supone.
  - El 2026-11-14 la app abierta en el puerto 8501 (runOnSave) migró la base
    real con una versión intermedia del código; la migración 3→4 quedó
    idempotente y completa una v4 a medias.
- ✅ Fecha de vigencia (Jorge, 2026-11-14): 25-11-2026 para las 37, ajustable. `ParametrosDecreto800.vigente_desde` también está en
  «pendiente».
- ✅ Una sola fuente del cruce (paso 4): `proyectos.codigo_submercado` en la
  base. `mapeo_cartografia_xm.json` y `Relacion_capas_IGAC_centrales_XM.xlsx`
  pasaron a `docs/historico/`.
- SQLite dentro de OneDrive: funciona en el Mac, pero si hay errores de
  sincronización, apuntar `LIQUIDADOR_BD` a una ruta fuera de OneDrive.
- Confirmar con la ONVS la regla del residuo de centavos (hoy: al mayor beneficiario).
- Maestro oficial de centrales con capacidad nominal (hoy se aproxima).
- Resoluciones CREG que respaldan la serie TVB. **Discrepancia encontrada el
  2026-11-15**: `TVB Consolidado.rtf` (la serie que carga Parámetros) y los
  archivos PPC-CVA de XM coinciden en **2026** (192,23886) pero difieren en el
  4.º–5.º decimal en 2022-2025 (p. ej. 2025: serie 189,0293, XM 189,02925;
  2022: 127,32413 contra 127,32412). No afecta lo que se liquida (desde
  noviembre de 2026); sí la TVB sugerida de las proyecciones, muy poco.
  Definir cuál fuente manda. Los .xls de 2018-2021 no se pueden leer con openpyxl.
- El proyecto «TSE ALL» de Claude no se pudo leer al escribir esto: si tiene
  decisiones que cambian algo de lo anterior, pegarlas en el proyecto nuevo.
- ✅ Python 3.12: las pruebas (145 tras retirar el código sin uso: los 33 casos del orquestador viejo quedaron en 19 contra el motor real; 160 con «Sin generación»; 158 con los fallos menores del 2026-11-15; 152 con los fallos; 144 con mes parcial; 137 con proyecciones; 127 al paso 6; 108 al paso 4) y las pantallas pasan con Python 3.12.16 y las
  dependencias de `requirements.txt` instaladas desde cero (2026-11-13). En el
  Mac basta crear el entorno con 3.12.
