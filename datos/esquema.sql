-- Liquidador TSE — esquema SQLite (paso 2, 2026-11-12; v2 2026-11-13; v3 a v6 2026-11-14)
--
-- Convenciones:
--  * Toda cifra (hectáreas, kWh, $/kWh, pesos, razones) se guarda como TEXTO
--    exacto: Decimal como «2900.92344», fracción como «1234567891/2987654321».
--    SQLite no tiene tipo numérico exacto y REAL es float (CLAUDE.md, regla 5).
--  * Dato ausente = NULL, nunca '0' (regla 2).
--  * Nada se borra: delimitaciones se reemplazan; liquidaciones se versionan.

PRAGMA foreign_keys = ON;

CREATE TABLE autoridades (
    sigla   TEXT PRIMARY KEY,
    -- SIN_ASIGNAR: «autoridad ambiental urbana o área en litigio» del IGAC. Su
    -- parte se calcula y se retiene; no se transfiere (Jorge, 2026-11-13).
    tipo    TEXT NOT NULL CHECK (tipo IN ('CAR', 'PNN', 'SIN_ASIGNAR')),
    nombre  TEXT NOT NULL
);

CREATE TABLE proyectos (
    id                 INTEGER PRIMARY KEY,
    nombre             TEXT NOT NULL UNIQUE,
    -- Central de XM; NULL = aún sin enlazar. NO es única (v3, Jorge 2026-11-14):
    -- XM reporta en un solo código centrales que el IGAC delimita por separado
    -- (GUATRON = Guadalupe III + IV + Troneras; PAJARITO = Pajarito I + II).
    -- Se permite solo si sus delimitaciones reparten igual, y se liquida una
    -- vez por central (`datos/repositorio.py`).
    codigo_submercado  TEXT,
    notas              TEXT
);
CREATE INDEX proyectos_central ON proyectos (codigo_submercado);

-- Delimitaciones del IGAC -------------------------------------------------
-- Estados: borrador -> vigente -> reemplazada. Una «reemplazada» sigue rigiendo
-- los periodos anteriores a la vigencia de la que la reemplazó.
CREATE TABLE delimitaciones (
    id               INTEGER PRIMARY KEY,
    proyecto_id      INTEGER NOT NULL REFERENCES proyectos(id),
    radicado         TEXT NOT NULL,
    fecha_documento  TEXT NOT NULL,          -- AAAA-MM-DD
    vigente_desde    TEXT,                   -- AAAA-MM-DD; obligatoria para confirmar
    modo             TEXT NOT NULL CHECK (modo IN ('hectareas', 'porcentaje')),
    trae_s2          INTEGER NOT NULL DEFAULT 0 CHECK (trae_s2 IN (0, 1)),  -- la COLUMNA S2
    paramo_declarado INTEGER CHECK (paramo_declarado IN (0, 1)),  -- «existe intersección entre páramos…»
    s1_t_declarado   TEXT,                   -- fila TOTAL de la tabla por autoridad
    s2_t_declarado   TEXT,
    origen           TEXT NOT NULL DEFAULT 'formulario' CHECK (origen IN ('formulario', 'precarga')),
    estado           TEXT NOT NULL DEFAULT 'borrador'
                     CHECK (estado IN ('borrador', 'vigente', 'reemplazada')),
    pdf_nombre       TEXT,
    pdf_ruta         TEXT,
    pdf_sha256       TEXT,
    creada_en        TEXT NOT NULL,
    confirmada_en    TEXT,
    reemplazada_por  INTEGER REFERENCES delimitaciones(id),
    observaciones    TEXT
);

CREATE TABLE delimitacion_filas (
    delimitacion_id  INTEGER NOT NULL REFERENCES delimitaciones(id),
    autoridad_sigla  TEXT NOT NULL REFERENCES autoridades(sigla),
    s1_ha            TEXT,                   -- modo hectareas; NULL = «N/A» en la delimitación (0 ha)
    s2_ha            TEXT,                   -- NULL = «N/A», o la delimitación no trae columna S2
    porcentaje       TEXT,                   -- modo porcentaje, exacto («a/b» o decimal)
    PRIMARY KEY (delimitacion_id, autoridad_sigla)
);

-- Generación de XM, con versión de liquidación ------------------------------
CREATE TABLE versiones_xm (
    codigo    TEXT PRIMARY KEY,              -- TX1 ... TX5, TXF
    orden     INTEGER NOT NULL UNIQUE,       -- mayor = más reciente
    es_firme  INTEGER NOT NULL CHECK (es_firme IN (0, 1))
);

CREATE TABLE cargas_xm (
    id                 INTEGER PRIMARY KEY,
    archivo_nombre     TEXT NOT NULL,
    archivo_sha256     TEXT NOT NULL,
    cargada_en         TEXT NOT NULL,
    observaciones      TEXT,
    -- v4: 'mensual' (2018-2025, trae capacidad) o 'diario' (2026, horas 0-23).
    formato            TEXT CHECK (formato IN ('mensual', 'diario')),
    -- v4: versión que declaró la persona. NULL = la trae el propio archivo.
    version_declarada  TEXT REFERENCES versiones_xm(codigo)
);

-- Una fila por recurso, mes y versión. Se guardan TODOS los recursos del
-- archivo (no solo hidráulicos): el umbral del art. 45 suma la capacidad de la
-- empresa en varios tipos de generación.
CREATE TABLE generacion (
    carga_id            INTEGER NOT NULL REFERENCES cargas_xm(id),
    periodo             TEXT NOT NULL,       -- AAAA-MM
    version_xm          TEXT NOT NULL REFERENCES versiones_xm(codigo),
    codigo_submercado   TEXT NOT NULL,
    recurso             TEXT,
    agente              TEXT,                -- nombre (mensual) o código de agente (diario)
    tipo_generacion     TEXT,
    generacion_kwh      TEXT,                -- NULL si XM no lo reporta
    capacidad_kw        TEXT,
    -- v4 (formato diario; NULL en el mensual, que trae el total del mes):
    --   dias_archivo    días del mes que cubre el archivo. Si < dias_mes, el
    --                   mes está incompleto y no se liquida.
    --   dias_reportados días con las 24 horas de esta central. XM omite los
    --                   días sin generación: si < dias_archivo, se señala.
    dias_reportados     INTEGER,
    dias_archivo        INTEGER,
    dias_mes            INTEGER,
    PRIMARY KEY (periodo, version_xm, codigo_submercado, carga_id)
);

-- v6: generación por día (solo formato diario), de las centrales hidráulicas
-- y de las enlazadas a un proyecto. Sirve para liquidar un mes parcial: los
-- días desde que empieza a regir una delimitación (Jorge, 2026-11-14).
CREATE TABLE generacion_diaria (
    carga_id            INTEGER NOT NULL REFERENCES cargas_xm(id),
    codigo_submercado   TEXT NOT NULL,
    fecha               TEXT NOT NULL,       -- AAAA-MM-DD
    generacion_kwh      TEXT NOT NULL,
    horas_completas     INTEGER NOT NULL CHECK (horas_completas IN (0, 1)),
    PRIMARY KEY (carga_id, codigo_submercado, fecha)
);

-- v6: cambios de la fecha de vigencia de una delimitación ya confirmada.
CREATE TABLE delimitacion_cambios (
    id               INTEGER PRIMARY KEY,
    delimitacion_id  INTEGER NOT NULL REFERENCES delimitaciones(id),
    campo            TEXT NOT NULL,
    antes            TEXT,
    despues          TEXT,
    motivo           TEXT,
    cambiado_en      TEXT NOT NULL
);

-- v4: capacidad por empresa (art. 45: > 10.000 kW por EMPRESA), calculada al
-- cargar un archivo mensual de XM, que es el único que trae capacidad.
CREATE TABLE capacidad_empresas (
    carga_id                 INTEGER NOT NULL REFERENCES cargas_xm(id),
    anio                     INTEGER NOT NULL,
    agente                   TEXT NOT NULL,
    capacidad_total_kw       TEXT NOT NULL,
    capacidad_hidraulica_kw  TEXT NOT NULL,
    sujeta                   INTEGER NOT NULL CHECK (sujeta IN (0, 1)),
    PRIMARY KEY (carga_id, agente)
);

CREATE TABLE tvb (
    anio            INTEGER PRIMARY KEY,
    pesos_kwh       TEXT NOT NULL,
    fuente          TEXT NOT NULL,
    fuente_sha256   TEXT
);

-- Liquidaciones versionadas -------------------------------------------------
-- caracter = 'oficial' solo con versión firme de XM (TXF); con TX1/TX2 es
-- 'preliminar' (referencia). Recalcular crea version_numero + 1.
CREATE TABLE liquidaciones (
    id                    INTEGER PRIMARY KEY,
    proyecto_id           INTEGER NOT NULL REFERENCES proyectos(id),
    periodo               TEXT NOT NULL,
    version_numero        INTEGER NOT NULL,
    caracter              TEXT NOT NULL CHECK (caracter IN ('preliminar', 'oficial')),
    version_xm            TEXT NOT NULL REFERENCES versiones_xm(codigo),
    carga_xm_id           INTEGER NOT NULL REFERENCES cargas_xm(id),
    carga_xm_sha256       TEXT NOT NULL,
    generacion_kwh        TEXT NOT NULL,
    tvb_anio              INTEGER NOT NULL,
    tvb_pesos_kwh         TEXT NOT NULL,
    ventas_brutas         TEXT NOT NULL,
    bolsa_car_pnn         TEXT NOT NULL,
    formula               INTEGER CHECK (formula IN (1, 2)),
    delimitacion_id       INTEGER NOT NULL REFERENCES delimitaciones(id),
    delimitacion_sha256   TEXT,
    parametros_version    TEXT NOT NULL,
    creada_en             TEXT NOT NULL,
    reemplaza_a           INTEGER REFERENCES liquidaciones(id),
    -- v5 (paso 5): central liquidada, empresa y capacidad con que se verificó
    -- el umbral del art. 45, días de la central en el archivo y fuente de la TVB.
    codigo_submercado     TEXT,
    empresa               TEXT,
    capacidad_empresa_kw  TEXT,
    capacidad_anio        INTEGER,
    dias_reportados       INTEGER,
    dias_archivo          INTEGER,
    tvb_fuente            TEXT,
    -- v6: mes parcial. NULL = se liquidó el mes completo. Si la delimitación
    -- empieza a regir dentro del mes, se liquida solo de dia_desde a dia_hasta.
    dia_desde             INTEGER,
    dia_hasta             INTEGER,
    UNIQUE (proyecto_id, periodo, version_numero)
);

-- v5: delimitaciones que respaldan una liquidación. Una central que XM agrupa
-- (GUATRON, PAJARITO) se liquida una vez y cita la de cada proyecto.
CREATE TABLE liquidacion_delimitaciones (
    liquidacion_id   INTEGER NOT NULL REFERENCES liquidaciones(id),
    delimitacion_id  INTEGER NOT NULL REFERENCES delimitaciones(id),
    proyecto_id      INTEGER NOT NULL REFERENCES proyectos(id),
    radicado         TEXT NOT NULL,
    pdf_sha256       TEXT,
    PRIMARY KEY (liquidacion_id, delimitacion_id)
);

CREATE TABLE liquidacion_filas (
    liquidacion_id       INTEGER NOT NULL REFERENCES liquidaciones(id),
    autoridad_sigla      TEXT NOT NULL REFERENCES autoridades(sigla),
    s1_ha                TEXT,
    s2_ha                TEXT,
    razon                TEXT NOT NULL,      -- fracción exacta «a/b»
    porcentaje_mostrado  TEXT NOT NULL,      -- 5 decimales, solo presentación
    monto                TEXT NOT NULL,      -- pesos, a centavos
    ajuste_residuo       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (liquidacion_id, autoridad_sigla)
);
