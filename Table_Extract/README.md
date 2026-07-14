# Table_Extract

`Table_Extract` es el nuevo modulo base para analizar fuentes tabulares y BBDD
desde cero. La etapa actual define estructura, objetos internos, contratos, el
runtime de archivos y el primer perfilamiento estructural: escucha
`Queue-Tables`, valida mensajes del Router, materializa archivos remotos con
leasing propio y construye `DataSourceProfile` para CSV/Excel. Tambien puede
perfilar metadata estructural de BBDD via SQLAlchemy y ORDS REST Enabled SQL.

Todavia no implementa discovery de PII. El sampling de archivos y PostgreSQL
existe como evidencia temporal; PostgreSQL, Oracle directo y ORDS tienen
sampling bajo demanda.

## Entorno

Desde la raíz de `PII-Cloud`:

```bash
conda activate PII_bbdds
python -m pip install -r Table_Extract/requirements.txt
```

Variables principales:

```bash
TABLE_EXTRACT_DATABASE_URL=postgresql://postgres:TU_PASSWORD@localhost:5432/PII_DB
TABLE_EXTRACT_RABBITMQ_URL=amqp://admin:TU_PASSWORD@localhost:5672/%2F
TABLE_EXTRACT_RABBITMQ_HEARTBEAT_SECONDS=1800
TABLE_EXTRACT_RABBITMQ_BLOCKED_CONNECTION_TIMEOUT_SECONDS=1800
TABLE_EXTRACT_OUTPUT_DIR=/tmp/pii-table-results
TABLE_EXTRACT_ZERO_SHOT_MODEL=MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7
TABLE_EXTRACT_ZERO_SHOT_DEVICE=cpu
TABLE_EXTRACT_ZERO_SHOT_BATCH_SIZE=8
```

Si no existen, se usan `DATABASE_URL` y `RABBITMQ_URL` como fallback.
Los tiempos RabbitMQ se elevan por defecto porque una tabla grande o un
Zero-Shot tabular puede tardar mas que el heartbeat normal del broker.

Variables de materializacion:

```bash
TABLE_EXTRACT_MATERIALIZE_SCRATCH_DIR=/tmp/pii-table-materialization
TABLE_EXTRACT_MATERIALIZE_SMALL_LIMIT_BYTES=104857600
TABLE_EXTRACT_MATERIALIZE_GLOBAL_LIMIT_BYTES=524288000
TABLE_EXTRACT_MATERIALIZE_LEASE_TTL_SECONDS=7200
```

Para Drive/Google Sheets tambien se leen:

```bash
GOOGLE_CLIENT_SECRETS_FILE=/ruta/google_client_secret.json
GOOGLE_TOKEN_FILE=/ruta/google_drive_token.json
```

Para BBDD se usa SQLAlchemy; `DatabaseScanRequest.connection_uri` recibe una URI
compatible:

```bash
postgresql+psycopg://usuario:password@localhost:5432/db
oracle+oracledb://usuario:password@host:1521/?service_name=FREEPDB1
```

Para ORDS REST Enabled SQL, `OrdsScanRequest.rest_sql_url` recibe el endpoint
`_/sql` completo:

```bash
https://host/ords/schema_alias/_/sql
```

## Contratos principales

- `DataSourceProfile`: metadata de la fuente completa.
- `TableProfile`: metadata de una tabla, vista, sheet o CSV virtual.
- `ColumnProfile`: metadata de columna, sin valores crudos.
- `ColumnSample`: muestra temporal de valores de una columna.
- `DiscoveredPII`: resultado limpio del analisis.
- `SourceAdapter`: interfaz comun para fuentes BBDD/archivo futuras.
- `TableRoutedMessage`: contrato de entrada desde `Queue-Tables`.
- `StoredFile`: metadata durable recuperada desde `files`.
- `FileScanContext`: handoff hacia perfilamiento, con path local listo.
- `DatabaseScanRequest`: entrada para perfilamiento estructural de BBDD.
- `OrdsScanRequest`: entrada para perfilamiento via ORDS REST Enabled SQL.
- `profile_table_source(...)`: orquestador comun para archivo materializado,
  BBDD y ORDS.
- `create_scan_session(...)`: prepara el contrato canonico para PII Discovery.
- Artifact JSON `table_extract.profile`: salida versionada del CLI para
  guardar/ver inventario estructural.

Los samples no se guardan dentro de `ColumnProfile`. El flujo esperado es:

```text
SourceAdapter -> create_scan_session(...) -> ScanSession -> discover_pii(...)
```

`ScanSession` contiene `source`, `profile` y `config`. El adapter debe quedar
abierto mientras corre PII Discovery, porque `discover_pii(session)` podra pedir
samples bajo demanda con `source.get_column_sample(...)`.

Para entrada comun de alto nivel:

```text
FileProfileRequest | DatabaseProfileRequest | OrdsProfileRequest
  -> profile_table_source(...)
  -> DataSourceProfile
```

Para archivos ruteados, el flujo previo es:

```text
Queue-Tables -> TableRoutedMessage -> StoredFile -> materialization -> FileScanContext
```

El perfilamiento de archivos opera sobre el contexto:

```text
FileScanContext -> FileSourceAdapter -> DataSourceProfile
```

## Artifact JSON

Los modos directos del CLI (`--database-url` y `--ords-url`) emiten un artifact
JSON versionado, no un `DataSourceProfile` plano. El formato V1 incluye:

- `artifact_type: "table_extract.profile"`;
- `schema_version: "1.0"`;
- `generated_at` en UTC;
- `summary` con `source_name`, `source_type`, `dialect`, cantidad de tablas,
  vistas y columnas;
- `profile` con el `DataSourceProfile` serializado.

Este artifact es solo inventario estructural: fuente, dialecto, tablas, vistas,
columnas y `row_count` cuando exista. No contiene samples, valores crudos ni
resultados de discovery PII.

## Perfilamiento

CSV:

- usa la primera fila como header;
- representa el archivo como una sola tabla;
- cuenta filas de datos;
- no guarda valores.

Excel/XLSX/XLSM/Google Sheets materializado:

- usa `openpyxl` con `read_only=True` y `data_only=True`;
- representa cada sheet visible como una tabla;
- ignora sheets ocultas;
- usa la primera fila de cada sheet como header;
- cuenta filas de datos;
- no guarda valores.

Headers vacios se renombran como `column_1`, `column_2`, etc. Los tipos de
columna quedan como `unknown`; la inferencia queda para etapas posteriores.
Los samples se obtienen bajo demanda con `get_column_sample(...)` y no se
guardan dentro del perfil.

## BBDD

La entrada BBDD usa `DatabaseScanRequest` y `build_database_source_adapter(...)`
para crear un adapter SQLAlchemy. El adapter detecta dialecto, lista
schemas/tablas/vistas/columnas y mantiene la salida comun:

```text
DatabaseScanRequest -> DatabaseSourceAdapter -> DataSourceProfile
```

En PostgreSQL, `row_count` usa estimaciones baratas desde catalogos y no ejecuta
`COUNT(*)`. Tambien implementa `get_column_sample(...)` con `LIMIT`, sin
persistir ni loggear valores.

En Oracle, el adapter directo usa `ALL_TABLES`, `ALL_VIEWS` y
`ALL_TAB_COLUMNS`; trata `OWNER` como `schema_name`, excluye owners de sistema
por defecto y usa `ALL_TABLES.NUM_ROWS` cuando existen estadisticas. Tambien
implementa `get_column_sample(...)` con `FETCH FIRST`, sin persistir ni loggear
valores. Otros dialectos SQLAlchemy mantienen `row_count=None`, pero tienen
sampling generico de una columna con `LIMIT`, sin persistir ni loggear valores.

La integracion PostgreSQL real de pruebas es opcional y se activa con:

```bash
TABLE_EXTRACT_POSTGRES_TEST_URL=postgresql+psycopg://usuario:password@localhost:5432/db \
  python -m pytest tests/test_database_source.py
```

La integracion Oracle real de pruebas tambien es opcional:

```bash
TABLE_EXTRACT_ORACLE_TEST_URL=oracle+oracledb://usuario:password@host:1521/?service_name=FREEPDB1 \
  python -m pytest tests/test_database_source.py
```

## ORDS REST Enabled SQL

La entrada ORDS usa `OrdsScanRequest` y `build_ords_source_adapter(...)`.
Es una fuente HTTP separada de `DatabaseSourceAdapter`, pero mantiene la misma
salida comun:

```text
OrdsScanRequest -> OrdsSourceAdapter -> DataSourceProfile
```

ORDS V1 solo soporta REST Enabled SQL, no endpoints ORDS de negocio. El adapter
envia SQL por `POST` con JSON (`statementText`, `limit`, `offset`), pagina con
`hasMore`, y usa `ALL_TABLES`, `ALL_VIEWS` y `ALL_TAB_COLUMNS` para metadata.
Soporta auth `none`, `basic` y `bearer`; el flujo OAuth completo queda fuera.

El sampling usa una consulta de una sola columna con `FETCH FIRST`, omite
valores vacios, trunca segun `max_value_length` y no persiste ni loggea valores.
Los timeouts, errores HTTP/auth y respuestas ORDS invalidas levantan errores
tipados (`OrdsTimeoutError`, `OrdsAuthError`, `OrdsHttpError`,
`OrdsResponseError`).

La integracion ORDS real de pruebas es opcional:

```bash
TABLE_EXTRACT_ORDS_TEST_URL=https://host/ords/schema_alias/_/sql \
TABLE_EXTRACT_ORDS_AUTH_MODE=basic \
TABLE_EXTRACT_ORDS_USERNAME=SCHEMA \
TABLE_EXTRACT_ORDS_PASSWORD=password \
  python -m pytest tests/test_ords_source.py
```

## Manejo operativo de errores

`Table_Extract` emite logs operativos JSONL a stderr para errores de Drive,
BBDD, ORDS y RabbitMQ. Stdout queda reservado para artifacts JSON en los modos
directos del CLI.

Los errores operativos se clasifican con `component`, `category`, `retryable`,
`message` y `safe_context`. Los mensajes y contextos se sanean para no exponer
passwords, bearer tokens, tokens en query strings, headers de auth, valores
sampleados ni celdas.

En `Queue-Tables`, RabbitMQ mantiene la politica:

- exito: `basic_ack`;
- error retryable: `basic_nack(requeue=True)`;
- error permanente: `basic_nack(requeue=False)`;
- `--dev-mode`: los exitos se reencolan y se loggean como `dev_requeued`.

Drive distingue credenciales/token/permisos/archivo no encontrado como errores
permanentes, y timeouts/429/5xx como retryable. ORDS distingue auth, timeouts,
HTTP, paginacion y respuestas invalidas. BBDD distingue conexion, permisos e
introspeccion, manteniendo URIs saneadas.

## Schema

El modulo usa leasing propio en Postgres:

```bash
scripts/apply_table_extract_schema.sh
```

El script usa `POSTGRES_DOCKER_CONTAINER` si esta definido; si no, intenta
detectar un contenedor Postgres en ejecucion. `POSTGRES_USER` y `POSTGRES_DB`
permiten cambiar usuario y base de datos.

Las tablas creadas son:

- `table_materialization_leases`: leasing temporal propio; no se usa
  `text_materialization_leases`.
- `table_extraction_files`: estado y metricas por archivo tabular procesado,
  incluyendo `processing_seconds`, CPU, memoria peak, conteos y paths de
  artifacts.

## Uso

Preparar contexto de un archivo puntual sin RabbitMQ:

```bash
python main.py --file-id UUID_DEL_ARCHIVO
```

Consumir desde `Queue-Tables`:

```bash
python main.py --max-messages 1
```

Usar GPU para Zero-Shot tabular:

```bash
python main.py --gpu
python main.py --device cuda
python main.py --device mps
python main.py --device cpu
```

En modo discovery por cola, `python main.py` usa `TABLE_EXTRACT_OUTPUT_DIR`.
Si esa variable no existe, entregar `--output-dir` explicitamente:

```bash
python main.py --max-messages 1 --output-dir /ruta/a/resultados_tabulares
```

Los artifacts `table_extract.profile` y `table_extract.discovery` incluyen
metricas top-level: `table_started_at`, `table_completed_at`,
`table_processing_seconds`, `cpu_user_seconds`, `cpu_system_seconds`,
`cpu_total_seconds` y `peak_memory_mb`. En flujo por archivo, esas mismas
metricas se guardan en `table_extraction_files`.

Leer mensajes y reencolarlos despues de preparar contexto:

```bash
python main.py --dev-mode --max-messages 1
```

Perfilar una BBDD fuente directamente y emitir un artifact JSON versionado:

```bash
python main.py --database-url postgresql+psycopg://usuario:password@localhost:5432/db
```

Perfilar ORDS REST Enabled SQL directamente:

```bash
python main.py --ords-url https://host/ords/schema_alias/_/sql
```

Guardar el artifact en archivo:

```bash
python main.py --database-url sqlite:///fuente.db --output profile.json
```

Filtrar schemas/tablas:

```bash
python main.py --database-url oracle+oracledb://usuario:password@host:1521/?service_name=FREEPDB1 \
  --include-schema APP \
  --include-table CONTACTS \
  --exclude-views
```

En fuentes directas, `--dev-mode` imprime un resumen a stderr y el artifact JSON
indentado:

```bash
python main.py --ords-url https://host/ords/schema_alias/_/sql --dev-mode
```

`--database-url` es la BBDD fuente a perfilar. No reemplaza
`TABLE_EXTRACT_DATABASE_URL`, que sigue siendo la BBDD operacional usada por
archivos, leases y `Queue-Tables`.

## Pruebas

Desde la raíz de `PII-Cloud`:

```bash
PYTHONPATH="$PWD/Table_Extract" conda run -n PII_bbdds \
  python -m pytest Table_Extract/tests
```
