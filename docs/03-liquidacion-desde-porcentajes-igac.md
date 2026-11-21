# 03 — Liquidación a partir del oficio de delimitación del IGAC

**Este es el núcleo del sistema.** Reemplaza a `03-formulas-decreto-800.md`
(archivado el 2026-08-28 en `docs/archivo/`), que especificaba que la aplicación
calculara las áreas a partir de la cartografía.

> **Corregido el 2026-08-28, después de leer un oficio real del IGAC**
> (`insumos/igac-distribucion/EJEMPLO Dec 644 El eden PCH San Bartolome.pdf`).
> El oficio **no trae porcentajes de CAR/Parques: trae hectáreas.** Eso obliga a
> matizar la regla: la aplicación no calcula áreas a partir de geometría, pero sí
> hace la **aritmética del Decreto sobre las hectáreas que el IGAC declara**.

## 1. La regla, con precisión

**La aplicación no mide; divide.**

- **No** cruza polígonos, no calcula intersecciones, no deriva hectáreas de la
  cartografía. Eso lo hace el IGAC y lo certifica en su oficio.
- **Sí** toma las hectáreas del oficio y aplica la razón del Decreto para obtener
  el porcentaje de cada autoridad, incluida la mezcla con α cuando hay páramo.

Consecuencia práctica sobre la decisión de archivar el motor: se archiva
`geo_service.py` (la geometría), **no** `tse_calculator.py` (la aritmética). Ver
`docs/14-plan-ajuste-codigo.md` §2.

## 2. Qué trae el oficio del IGAC

Verificado contra el oficio real de EL EDÉN (Radicado 2420SGEO-2023-0003417-EE-001,
26-07-2023, Decreto 644 de 2021). El oficio declara, en hectáreas:

| Concepto del oficio | Símbolo | Ejemplo El Edén |
|---|---|---|
| Área de influencia del proyecto en jurisdicción de la autoridad i | AIJAA_i | CORPOCALDAS 47.605,34 · PNN Selva de Florencia 2.220,93 |
| Área de la cuenca hidrográfica en jurisdicción de la autoridad i | ACHAA_i | CORPOCALDAS 4.690,39 |
| Área total de influencia del proyecto | ATI | 49.826,27 |
| Área total de la cuenca | ATCH | 4.690,39 |
| Intersección del área total de influencia y la cuenca | ATI ∩ ATCH | 2.900,92 |
| **Intersección AIJAA_i ∩ ACHAA_i por autoridad** | **S1_i** | CORPOCALDAS 2.900,92 |

También trae los porcentajes de participación de los **municipios** en la cuenca.
**Se ignoran** (decisión del 2026-08-28): el 3% de municipios y distritos está
fuera del alcance y la extracción no los lee.

## 3. Lo que falta en el oficio del Decreto 644 y hay que exigir en los del 800

El oficio de 2023 **no trae el páramo**. Le falta la tercera variable, y con ella
todo lo que necesita la Fórmula 2:

- **APJAA_i** — área de páramo delimitado en jurisdicción de la autoridad i.
- **S2_i** — área de convergencia de criterios en jurisdicción i.

> ### ⚠️ S2_i debe venir calculado del IGAC. No se puede deducir.
>
> S2_i es una **unión de intersecciones**:
> `S2_i = (AIJAA_i ∩ ACHAA_i) ∪ (AIJAA_i ∩ APJAA_i) ∪ (ACHAA_i ∩ APJAA_i)`
>
> Con solo las hectáreas totales de AIJAA_i, ACHAA_i y APJAA_i **es imposible**
> obtener S2_i: los tres pedazos se traslapan y sumarlos cuenta doble el traslape.
> En El Edén, según la cartografía procesada, S1_i = 4.629,93 ha y el páramo
> aporta 2.599,82 ha; la suma daría 7.229,75 ha, pero la unión real es
> 6.920,10 ha — **309,65 ha de más, un 4,5% de sobreestimación** en una sola
> autoridad. Aplicado al reparto, eso mueve dinero.
>
> **Lo que el MADS debe pedirle al IGAC** (decisión del 2026-08-28) es que el
> oficio traiga, por cada autoridad ambiental, **una fila con S2_i en hectáreas
> ya calculada**, además de S1_i. Con esas dos columnas la aplicación liquida sin
> tocar geometría. Sin S2_i, los proyectos con páramo no se pueden liquidar.

Esto es el **Bloque 0 de `TAREAS-JORGE.md`** y es lo que bloquea la Fórmula 2.

## 4. Cadena de liquidación

```
(1)  ventas_brutas   = generación_kWh × TVB_$/kWh
(2)  TSE_total       = 6%  × ventas_brutas
(3)  bolsa_CAR_PNN   = 3%  × ventas_brutas
(4)  pct_i           = razón del Decreto sobre las hectáreas del oficio  (§5)
(5)  monto_i         = bolsa_CAR_PNN × pct_i
```

- `generación_kWh`: reporte mensual de XM/ASIC por central.
- `TVB`: Tarifa de Venta en Bloque de la CREG ($/kWh).
- El 3% restante corresponde a municipios y distritos y está fuera de alcance.

## 5. Cómo se obtiene pct_i

Las razones son las del Decreto 800 de 2026, alimentadas con las hectáreas
**declaradas por el IGAC**, no medidas por la aplicación.

**Sin páramo en el oficio (Fórmula 1):**

```
pct_i = S1_i / S1_T          con S1_T = Σ_i S1_i
```

**Con páramo, si el oficio trae S2_i (Fórmula 2):**

```
pct_i = (1 − α) × (S1_i / S1_T)  +  α × (S2_i / S2_T)
        con α = 0,5228 · S2_T = Σ_i S2_i
```

α es un parámetro del Decreto, centralizado y versionado en configuración, nunca
disperso en el código: puede cambiar por acto administrativo futuro.

**Qué fórmula se aplica lo decide el oficio, no la aplicación**: si el oficio trae
columna S2_i, es Fórmula 2; si no la trae, es Fórmula 1. La aplicación **no
decide** si hay páramo — no tiene con qué, y no debe inventarlo.

## 6. Cómo entra el oficio: extracción con revisión humana

Decisión del 2026-08-28. Tres pasos, y **ninguno deja que la máquina publique un
valor sola**:

```
1. CARGUE      La empresa o la autoridad sube el PDF y selecciona el proyecto.
               El sistema guarda el archivo íntegro y su hash SHA-256.

2. EXTRACCIÓN  El sistema intenta leer las tablas del oficio (autoridades, S1_i,
               S2_i si está) y precarga un formulario. Cada valor queda marcado
               con su origen: `extraido` o `digitado`, con el fragmento del PDF
               del que salió. Si no logra leer nada, el formulario sale vacío —
               no inventa, no estima, no completa.

3. REVISIÓN    Un revisor (administrador MADS/ONVS) compara el formulario contra
               el PDF, corrige lo que haga falta y aprueba. Solo entonces el
               oficio queda VIGENTE y el proyecto empieza a liquidar.
```

Estados de un oficio de delimitación:

| Estado | Significa | ¿Liquida? |
|---|---|---|
| `cargado` | El PDF está guardado, sin extraer todavía | No |
| `en_revision` | Hay valores propuestos, esperando aprobación | No |
| `vigente` | Aprobado; es el que manda para ese proyecto | **Sí** |
| `reemplazado` | Existe un oficio posterior vigente | No |
| `rechazado` | El revisor lo devolvió, con motivo escrito | No |

Un proyecto tiene **como máximo un oficio `vigente` a la vez**. Aprobar uno nuevo
pasa el anterior a `reemplazado` — nunca lo borra.

**Buena noticia sobre el formato:** el oficio de ejemplo es PDF nativo con tablas
legibles (generado con Aspose, no escaneado), aunque viene con restricción de
copia. `pdftotext -layout` y `pdfplumber` lo leen sin problema. La extracción
automática es viable; la captura manual sigue siendo el respaldo.

## 7. Validaciones obligatorias

Antes de que un oficio pueda pasar a `vigente`:

1. **Coherencia interna del oficio.** Σ AIJAA_i = ATI y Σ S1_i = ATI ∩ ATCH,
   dentro de una tolerancia declarada. Si el propio oficio no cuadra, se avisa al
   revisor con la diferencia a la vista — no se corrige solo.
2. **Autoridades conocidas.** Cada autoridad debe corresponder a una CAR o Parque
   del maestro. Un nombre que no cruce bloquea la aprobación.
3. **Rango.** Hectáreas > 0. Una autoridad con S1_i = 0 no es beneficiaria por
   Fórmula 1 y se registra como tal, explícitamente (en El Edén, PNN Selva de
   Florencia tiene AIJAA_i = 2.220,93 pero ACHAA_i = 0, luego S1_i = 0).
4. **Consistencia de fórmula.** Si hay columna S2_i, debe estar para *todas* las
   autoridades del oficio, no para algunas.
5. **S2_i ≥ S1_i** para cada autoridad (la convergencia contiene la intersección).
   Si no se cumple, el oficio tiene un error y no se aprueba.
6. **Proyecto identificado** en el maestro de centrales de XM.
7. **Vigencia temporal** declarada: desde cuándo aplica.
8. **Suma de porcentajes resultante** = 100,00000% tras el reparto.

## 8. Precisión y unidades

- **Superficies** en hectáreas, tal como las declara el oficio, con los decimales
  que traiga.
- **Razones de reparto** con **5 decimales** (parágrafo 2 del Decreto).
- **Montos** en pesos, cuantizados **una sola vez a centavos** al final, con
  `ROUND_HALF_UP`. Los 5 decimales acotan la razón, no el producto ya
  multiplicado por el 3% (hallazgo A1 de la auditoría del 2026-08-26).
- **Residuo de redondeo:** la diferencia se suma al beneficiario de mayor
  participación y la liquidación registra que el ajuste se aplicó y a quién.
  *(Pendiente de confirmación con ONVS.)*
- Toda la cadena usa `decimal.Decimal`. Nunca `float`.

## 9. Vigencia temporal y recálculos

- Cada liquidación guarda **cuál oficio usó** (identificador + hash del PDF), las
  hectáreas aplicadas y el porcentaje resultante.
- Un oficio nuevo **no sobrescribe** liquidaciones emitidas: se versionan, con
  motivo, y se notifica a las autoridades afectadas.
- Un oficio nuevo aplica **hacia adelante** desde su fecha de vigencia. El
  recálculo retroactivo es una acción explícita del administrador, con motivo
  escrito — nunca automática.

## 10. Casos que el sistema debe manejar (pruebas mínimas)

1. **Proyecto sin oficio vigente** → no se liquida; aparece como «sin
   delimitación certificada». **Nunca reparto por partes iguales ni estimación.**
2. **Un solo beneficiario** → pct = 100%; monto = bolsa completa.
3. **Autoridad con S1_i = 0** (caso PNN Selva de Florencia en El Edén) → no
   recibe por Fórmula 1; queda registrada como presente en el oficio pero sin
   participación, y el comprobante lo dice.
4. **Oficio con páramo pero sin columna S2_i** → no se liquida por Fórmula 2; se
   marca como oficio incompleto y se solicita la información al IGAC.
5. **S2_T = 0 con S2_i declarados** → degrada a Fórmula 1, con alerta explícita.
6. **Mes sin reporte de XM** → solo estimación marcada como tal (CU6).
7. **Oficio reemplazado a mitad de año** → los meses anteriores conservan las
   hectáreas viejas; los posteriores usan las nuevas. El histórico muestra los
   dos tramos.
8. **Empresa con potencia nominal instalada total ≤ 10.000 kW** → excluida
   (art. 45 Ley 99/1993; umbral por empresa, no por central).
9. **Residuo de redondeo** → Σ monto_i = bolsa exacta, con el ajuste registrado.
10. **PDF ilegible** → formulario vacío, captura manual. Camino previsto, no falla.

## 11. Explicabilidad

Toda liquidación debe poder desplegarse paso a paso:

```
Generación XM (kWh)  →  TVB CREG ($/kWh)  →  Ventas brutas ($)
  →  TSE 6% ($)  →  Bolsa CAR/PNN 3% ($)
  →  S1_i / S1_T  [y S2_i / S2_T con α, si aplica]   ← hectáreas del oficio
  →  % de la autoridad  →  Monto ($)
```

Con enlace de descarga al **PDF del IGAC** y las hectáreas declaradas a la vista.
Sin ese enlace la liquidación no es verificable y no cumple el requisito.

## 12. Fundamento normativo

- **6% de las ventas brutas** y umbral por empresa: art. 45 Ley 99 de 1993,
  modificado por el art. 24 de la Ley 1930 de 2018.
- **Mitad para CAR / Parques** y razones de reparto: art. 2.2.9.2.1.5 del Decreto
  1076 de 2015, sustituido por el **Decreto 800 de 2026**.
- **Delimitación de áreas por el IGAC**: art. 2.2.9.2.1.3 del Decreto 1076 de 2015
  — es el artículo que el oficio de ejemplo cita como fundamento.
- **Recursos de PNN vía FONAM**: art. 11 Ley 1955 de 2019.
