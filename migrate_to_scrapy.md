# Migrate To Scrapy

## Objetivo

Migrar el stage 1 del proyecto desde un crawler monolítico basado en `requests`
hacia un crawler basado en Scrapy, sin romper el contrato de salida que ya
consumen `stats.py` y `web/build.py`.

Contrato a preservar:

- `data/<group>/group.json`
- `data/<group>/matches.json`
- `data/<group>/matches/<id>.json`
- `data/<group>/raw/*.html`

## Trabajo realizado hasta ahora

### 1. Scaffolding de Scrapy embebido

Se añadió un proyecto Scrapy dentro del repo, manteniendo los scripts actuales
en raíz:

- `scrapy.cfg`
- `scraper/__init__.py`
- `scraper/settings.py`
- `scraper/spiders/__init__.py`
- `scraper/spiders/basketaraba.py`
- `scraper/run.py`

### 2. Spider funcional con mismo layout de salida

La spider `basketaraba`:

- resuelve grupo y categoría
- descarga calendario y jornadas
- construye el índice de partidos
- descarga detalles por `partido_id`
- escribe los mismos artefactos que `crawler.py`
- reutiliza la caché HTML existente cuando `--force` no está activo

### 3. Documentación inicial

Se actualizó `README.md` para documentar el nuevo entrypoint Scrapy además del
entrypoint clásico basado en `crawler.py`.

### 4. Extracción del núcleo compartido

Se creó `scraper/common.py` para concentrar la lógica compartida:

- constantes (`BASE`, `USER_AGENT`)
- dataclasses del dominio
- parseo de calendario, jornada y detalle de partido
- serialización a JSON y escritura de HTML raw
- helpers de normalización y slug

Con esto, tanto `crawler.py` como la spider dependen ya de una misma API común.

## Validaciones ya ejecutadas

Validaciones superadas antes de continuar:

- `python -m scrapy list`
- `python -m scraper.run --help`
- `python crawler.py --help`
- imports directos de `scraper.common` y de la spider
- ejecución end-to-end de ambos caminos sobre la caché existente del grupo
  `SENIOR MASCULINA 3ª-GRUPO A`

Resultado observado:

- resolución correcta del grupo
- calendario con 132 partidos en 22 jornadas
- 119 partidos con `partido_id`
- reutilización de caché en ambos caminos
- finalización sin errores

## Trabajo adicional realizado

### 5. Delegación opcional desde `crawler.py`

Se añadió un nuevo parámetro de CLI en `crawler.py`:

- `--engine requests`
- `--engine scrapy`

Comportamiento actual en ese momento:

- `requests` seguía siendo el default para no romper el uso existente
- `scrapy` delega en `scraper.run.main(...)`
- `crawler.py` preserva la forma principal del CLI (`group`, `out`, `sleep`,
  `force`, `verbose`) aunque el backend sea Scrapy

### 6. Compatibilidad básica del runner Scrapy

`scraper/run.py` se amplió para aceptar también:

- `--sleep`
- `-v/--verbose`

Esto permite que la delegación desde `crawler.py` no pierda opciones visibles
del CLI clásico.

## Validaciones adicionales ejecutadas

Tras añadir `--engine`, se validó lo siguiente:

- `python crawler.py --help`
- `python -m scraper.run --help`
- `python crawler.py "SENIOR MASCULINA 3ª-GRUPO A" --engine scrapy`

Evidencias relevantes del último chequeo end-to-end por logs:

- `Calendar: 12 teams, 132 matches across 22 jornadas`
- `Jornada 1 (2025-10-06): 5 matches`
- `Index: 132 matches (4 from calendar-only, no acta)`
- `Fetching 119 matches with partido ids…`
- `[119/119] ... (cached)`
- `finish_reason: 'finished'`

Interpretación:

- la ruta delegada desde `crawler.py` sí recorrió el flujo completo
- la ejecución reutilizó correctamente la caché existente
- el wrapper no se quedó en una validación superficial de CLI; ejecutó de punta
  a punta el crawler Scrapy

## Estado actual

La migración está ahora en una fase útil de convivencia:

- `crawler.py` funciona con engine clásico
- `crawler.py --engine scrapy` funciona como wrapper compatible
- `python -m scraper.run ...` sigue disponible como entrypoint explícito
- ambos caminos comparten parseo y serialización desde `scraper/common.py`

## Próximos pasos propuestos

1. Añadir tests de regresión sobre HTML cacheado para `scraper/common.py`.
2. Decidir si `crawler.py` debe seguir siendo el entrypoint principal o si debe
   pasar a ser un wrapper/documentarse como tal.
3. Aprovechar mejor Scrapy con retry/backoff y métricas más explícitas.

## Trabajo en curso

Se está añadiendo una regresión mínima con `unittest` sobre la caché real del
grupo actual para fijar tres superficies de `scraper/common.py`:

- `parse_calendar(...)`
- `parse_week_jornada(...)`
- `parse_match(...)`

La intención es validar contrato y no solo que el CLI termina sin errores.

Actualización del tramo:

- primer intento: falló por supuestos demasiado rígidos sobre el orden del
  calendario y sobre la grafía normalizada del grupo (`3A` en vez de `3ª`)
- corrección aplicada: las aserciones se ajustaron para validar presencia y
  contrato real de la fixture, no un orden accidental
- el test de `parse_match(...)` se mantuvo para comparar contra un JSON ya
  generado y fijar mejor el contrato del detalle por partido
- resultado final validado: `python -m unittest test.test_scraper_common`
  terminó con `Ran 3 tests ... OK`

Cobertura práctica añadida en este tramo:

- forma básica de temporada desde `parse_calendar(...)`
- filtrado y volumen esperado de una jornada real desde
  `parse_week_jornada(...)`
- contrato del detalle de partido contrastado contra un JSON ya generado desde
  `parse_match(...)`

## Registro vivo

Esta bitácora se seguirá actualizando con cada cambio, comprobación y
validación de la migración.

## Nuevo tramo: retry, backoff y calendar-only

Trabajo realizado en este tramo:

- settings Scrapy endurecidos con `RETRY_ENABLED`, `RETRY_TIMES`,
  `RETRY_HTTP_CODES` y `RETRY_PRIORITY_ADJUST`
- `AUTOTHROTTLE` activado como mecanismo de backoff adaptativo en vez de dejar
  un delay fijo sin respuesta a latencia ni errores
- la spider ahora emite un resumen explícito al cerrar con métricas operativas
  relevantes para este proyecto

Métricas añadidas al cierre de la spider:

- requests de red realmente programadas
- lecturas desde caché de calendario
- lecturas desde caché de jornadas
- lecturas desde caché de partidos
- número de retries observados por Scrapy
- número de partidos `calendar-only`
- respuestas HTTP 200 observadas por el downloader de Scrapy

Cobertura añadida en regresión:

- restauración del test de `parse_match(...)`
- nuevo test sobre `matches.json` para fijar un caso `calendar-only` sin
  `partido_id`

Validación prevista para este tramo:

- `python crawler.py "SENIOR MASCULINA 3ª-GRUPO A" --engine scrapy`
- `python -m unittest test.test_scraper_common`

Resultado del tramo:

- `python -m unittest test.test_scraper_common` terminó con `Ran 4 tests ... OK`
- la validación del engine Scrapy confirmó:
  - `Calendar: 12 teams, 132 matches across 22 jornadas`
  - `Index: 132 matches (4 from calendar-only, no acta)`
  - `Fetching 119 matches with partido ids…`
  - `Scrapy summary: ... cached_calendar=1 cached_weeks=22 cached_matches=119 retries=0 calendar_only=4 ...`

Hallazgo y corrección local durante la validación:

- se detectó una doble finalización del índice cuando las jornadas se leían
  desde caché de forma síncrona
- síntoma observado: logs duplicados de `Index`/`Fetching` y `cached_matches=238`
- corrección aplicada: guard `index_finalized` en la spider para asegurar que
  `_finalize_week_index()` solo corre una vez por ejecución
- validación posterior: el resumen volvió al valor correcto
  `cached_matches=119`

## Nuevo tramo: crawler.py sigue siendo el entrypoint principal

Decisión aplicada:

- `crawler.py` permanece como CLI principal del proyecto
- la transición a Scrapy se prepara por debajo del entrypoint, no cambiando el
  comando que usa la gente

Trabajo realizado en este tramo:

- el engine `requests` ahora emite un resumen comparable al de Scrapy
- `crawler.py` acepta un default de engine controlado por
  `BASKETARABA_DEFAULT_ENGINE`, inicialmente manteniendo `requests` como
  fallback seguro
- se añadió una regresión controlada de retries para Scrapy con un endpoint
  local que responde `503` y luego `200`

Objetivo de estos cambios:

- poder comparar ambos engines con el mismo entrypoint
- probar el middleware de retries sin depender de fallos reales del sitio
- preparar el cambio de default a Scrapy sin romper compatibilidad de CLI

Validación prevista para este tramo:

- `python crawler.py "SENIOR MASCULINA 3ª-GRUPO A"`
- `python crawler.py "SENIOR MASCULINA 3ª-GRUPO A" --engine scrapy`
- `BASKETARABA_DEFAULT_ENGINE=scrapy python crawler.py "SENIOR MASCULINA 3ª-GRUPO A" --help`
- `python -m unittest test.test_scraper_common test.test_scrapy_retry`

Resultado del tramo:

- `python -m unittest test.test_scraper_common test.test_scrapy_retry`
  terminó con `Ran 5 tests ... OK`
- `python crawler.py "SENIOR MASCULINA 3ª-GRUPO A"` produjo un resumen
  comparable para el engine clásico:
  - `Selected crawler engine: requests`
  - `Requests summary: network_requests=3 cached_calendar=1 cached_weeks=22 cached_matches=119 failed_matches=0 calendar_only=4`
- `python crawler.py "SENIOR MASCULINA 3ª-GRUPO A" --engine scrapy`
  confirmó la ruta Scrapy desde el mismo entrypoint:
  - `Selected crawler engine: scrapy`
  - `Scrapy summary: reason=finished scheduled_network_requests=0 cached_calendar=1 cached_weeks=22 cached_matches=119 retries=0 calendar_only=4 http_200=3`
- `BASKETARABA_DEFAULT_ENGINE=scrapy python crawler.py ...` activó Scrapy sin
  cambiar el comando principal, solo el default del engine

Hallazgo y corrección local durante la validación:

- el log `Selected crawler engine: ...` no aparecía al principio porque se
  emitía antes de configurar `logging`
- corrección aplicada: `logging.basicConfig(...)` se movió antes del log de
  selección de engine en `crawler.py`

Conclusión operativa del tramo:

- `crawler.py` sigue siendo el entrypoint principal
- ya se pueden comparar ambos engines con el mismo comando y métricas del mismo
  nivel
- el cambio futuro de default a Scrapy ya se puede ensayar de forma reversible
  con `BASKETARABA_DEFAULT_ENGINE=scrapy`

## Nuevo tramo: comparación automática y ensayo repetido

Trabajo realizado en este tramo:

- `crawler.py` ahora puede ejecutar `--compare-engines` para lanzar ambos
  engines de forma secuencial y devolver una comparación compacta
- ambos engines emiten métricas machine-readable cuando el proceso se ejecuta
  en modo de comparación
- el engine `requests` ahora añade timings por fase (`resolve`, `calendar`,
  `index`, `detail`, `total`)
- el engine `scrapy` añade timings equivalentes de alto nivel
  (`resolve_and_calendar`, `index`, `detail`, `total`)

Objetivo del tramo:

- comparar `requests` y `scrapy` con el mismo entrypoint y sin parsear logs a
  mano
- hacer visible el coste por fase del engine clásico
- ensayar varias veces el default Scrapy controlado por entorno antes de un
  cambio real de default

Trabajo adicional del tramo:

- persistencia opcional de métricas con `--metrics-out`
- en modo compare el JSON escrito incluye `requests`, `scrapy` y `deltas`
- en modo single-engine el JSON escrito contiene solo las métricas de esa
  ejecución

Resultado del tramo:

- `python -m unittest test.test_scraper_common test.test_scrapy_retry`
  terminó con `Ran 5 tests ... OK`
- `python crawler.py "SENIOR MASCULINA 3ª-GRUPO A" --compare-engines`
  produjo una comparación compacta válida, por ejemplo:
  - `requests: network_requests=3 cached_matches=119 calendar_only=4 total_s=2.259`
  - `scrapy: scheduled_network_requests=0 cached_matches=119 calendar_only=4 retries=0 total_s=1.879`
  - `delta(total_s scrapy-requests): -0.38`
- el resumen detallado del engine clásico ya incluye timings por fase:
  - `Requests summary: ... timings_s(resolve=1.922 calendar=0.021 index=0.280 detail=0.009 total=2.232)`
- el resumen detallado de Scrapy también incluye timings de alto nivel:
  - `Scrapy summary: ... timings_s(resolve_and_calendar=1.731 index=0.313 detail=0.013 total=2.058)`
- el ensayo repetido con default Scrapy vía entorno se ejecutó 3 veces seguidas
  sin errores usando el mismo entrypoint `crawler.py`

Observación del ensayo repetido:

- las tres ejecuciones seleccionaron correctamente `scrapy`
- las tres reutilizaron la misma caché (`cached_calendar=1 cached_weeks=22 cached_matches=119`)
- el tiempo total se mantuvo estable en un rango estrecho aproximado de
  `1.82s` a `1.94s`

Trabajo adicional completado después de ese tramo:

- `--compare-engines` ahora imprime también deltas por fase, no solo por total
  (`resolve+calendar`, `index`, `detail`, `total`)
- `--metrics-out` quedó operativo tanto en `requests` como en `scrapy`
  directo, y también en modo compare
- se ejecutó un ensayo real de red con `BASKETARABA_DEFAULT_ENGINE=scrapy` y
  `--force`

Validaciones adicionales ejecutadas:

- `python crawler.py "SENIOR MASCULINA 3ª-GRUPO A" --compare-engines --metrics-out ...`
  escribió correctamente JSON con claves `requests`, `scrapy` y `deltas`
- `python crawler.py "SENIOR MASCULINA 3ª-GRUPO A" --engine scrapy --metrics-out ...`
  escribió correctamente JSON con métricas del engine Scrapy
- `BASKETARABA_DEFAULT_ENGINE=scrapy python crawler.py "SENIOR MASCULINA 3ª-GRUPO A" --force --metrics-out ...`
  completó una ejecución real de red con resumen final válido

Resultado relevante del ensayo `--force` con default Scrapy:

- `Selected crawler engine: scrapy`
- `Index: 132 matches (4 from calendar-only, no acta)`
- `Fetching 120 matches with partido ids…`
- `Scrapy summary: reason=finished scheduled_network_requests=143 cached_calendar=0 cached_weeks=0 cached_matches=0 retries=0 calendar_only=4 http_200=146 timings_s(resolve_and_calendar=2.866 index=11.199 detail=59.361 total=73.426)`

Hallazgos y correcciones locales durante este tramo final:

- la ruta `--force` de Scrapy no llegaba a indexar correctamente cuando una
  jornada compartía el mismo `week=` que otra y Scrapy filtraba la request como
  duplicada
- corrección aplicada: `dont_filter=True` en las requests de jornada dentro de
  la spider
- síntoma previo: faltaba una jornada, no aparecían `Index` ni `Fetching`, y el
  resumen cerraba con `calendar_only=0` y `cached_matches=0`
- validación posterior: la ruta `--force` pasó a procesar 22 jornadas, construir
  el índice y lanzar 120 detalles de partido

- la delegación desde `crawler.py` a Scrapy estaba duplicando cada línea de log
- corrección aplicada: `CrawlerProcess(..., install_root_handler=False)` en
  `scraper/run.py`
- validación posterior: la ruta delegada volvió a emitir cada línea una sola vez

## Cierre de la migración: Scrapy como default efectivo

Decisión aplicada:

- `crawler.py` sigue siendo el entrypoint principal del proyecto
- Scrapy pasa a ser el engine por defecto cuando no se especifica `--engine`
- `requests` permanece disponible como ruta de compatibilidad y escape con
  `--engine requests` o `BASKETARABA_DEFAULT_ENGINE=requests`

Cambio aplicado:

- `_default_engine()` en `crawler.py` ahora usa `scrapy` como fallback real
- la ayuda de `--engine` y el `README` se alinearon con este comportamiento

Validación posterior:

- `python crawler.py --help` mostró `BASKETARABA_DEFAULT_ENGINE or scrapy`
- `python crawler.py "SENIOR MASCULINA 3ª-GRUPO A"` ejecutó la ruta Scrapy por
  defecto
- `python crawler.py "SENIOR MASCULINA 3ª-GRUPO A" --engine requests` mantuvo
  operativa la ruta clásica

Conclusión de migración:

- el cambio de engine quedó absorbido bajo el mismo entrypoint
- el contrato de salida permanece estable para `stats.py` y `web/build.py`
- desde este punto la migración a Scrapy queda cerrada a nivel funcional;
  lo que sigue ya es endurecimiento operativo y observabilidad, no migración

## Trabajo posterior a la migración: observabilidad histórica y endurecimiento

### 1. Snapshots históricos de métricas

Trabajo realizado:

- `crawler.py` y `scraper/run.py` aceptan ahora `--metrics-history-dir`
- el engine `requests`, el engine `scrapy` y el modo `--compare-engines`
  escriben snapshots timestampados bajo una raíz elegida por el usuario
- el layout generado es `<root>/<group>/<YYYY-MM-DD>/<HHMMSS>_<label>.json`

Objetivo:

- conservar una historia de rendimiento por grupo y día sin sobrescribir el
  último JSON de `--metrics-out`
- mantener la misma capacidad tanto en la ruta clásica como en la delegada a
  Scrapy y en el modo compare

Validación prevista:

- `python crawler.py "SENIOR MASCULINA 3ª-GRUPO A" --metrics-history-dir ...`
- `python crawler.py "SENIOR MASCULINA 3ª-GRUPO A" --compare-engines --metrics-history-dir ...`

Resultado del tramo:

- `python crawler.py "SENIOR MASCULINA 3ª-GRUPO A" --metrics-history-dir /tmp/basketaraba-metrics-history`
  escribió un snapshot Scrapy válido en:
  - `/tmp/basketaraba-metrics-history/senior-masculina-3a-grupo-a/2026-05-18/115827_scrapy.json`
- `python crawler.py "SENIOR MASCULINA 3ª-GRUPO A" --compare-engines --metrics-history-dir /tmp/basketaraba-metrics-history`
  escribió un snapshot compare válido en:
  - `/tmp/basketaraba-metrics-history/senior-masculina-3a-grupo-a/2026-05-18/115832_compare.json`

Hallazgo y corrección local durante la validación:

- la primera versión de la ruta Scrapy no incluía el nombre del grupo dentro
  del payload de métricas, así que el snapshot histórico no podía construir la
  ruta por grupo
- corrección aplicada: la spider ahora emite `group: self.group_name` dentro de
  sus métricas finales y usa ese valor para el snapshot

### 2. Endurecimiento adicional de la regresión de red

Trabajo realizado:

- el test de retry existente se refactorizó para reutilizar una sonda común
- se añadió un segundo caso transitorio para `429 Too Many Requests`
- se añadió una regresión de configuración para fijar que Scrapy mantiene
  activados `RETRY_ENABLED`, `AUTOTHROTTLE_ENABLED` y los códigos de retry
  relevantes (`429`, `503`)

Objetivo:

- fijar en tests dos fallos transitorios típicos de red para el crawler
- detectar regresiones de configuración en el endurecimiento de Scrapy aunque
  el crawler siga terminando con éxito sobre caché

Validación ejecutada:

- `python -m unittest test.test_scrapy_retry test.test_scraper_common`

Resultado del tramo:

- la suite terminó con `Ran 7 tests ... OK`
- quedan cubiertos:
  - retry sobre `503`
  - retry sobre `429`
  - regresión de configuración sobre `RETRY_ENABLED`, `RETRY_HTTP_CODES` y
    `AUTOTHROTTLE_ENABLED`