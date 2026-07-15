# BBDD Job

Nombre por defecto: `bbdd-pii-job`.

## Objetivo

Descubrir PII en tablas de una base PostgreSQL u Oracle mediante introspección,
muestreo y reglas de clasificación. Es un flujo independiente del pipeline de
Drive: no consume Pub/Sub ni el topic `pii-tables`.

## Entrada

La entrada preferida es `SCAN_REQUEST_JSON`:

```json
{
  "run_id": "86ca6e73-ea37-4c1f-812d-7b71dcb771bb",
  "user_id": "ana",
  "run_name": "Clientes producción Q3",
  "database_type": "postgresql",
  "connection_uri": "postgresql+psycopg://usuario:password@db.example.com:5432/db",
  "include_schemas": ["public"],
  "include_tables": ["customers"],
  "confirm_full_scan": false,
  "allow_full_database_scan": false,
  "profile_only": false
}
```

`database_type` acepta `postgresql` u `oracle`. Sin confirmación de full scan,
debe existir un filtro explícito de schema o tabla. La credencial de la fuente
es distinta de `BBDD_RESULTS_DATABASE_URL`, que apunta a la Cloud SQL de
resultados.

## Procesamiento

1. Valida identidad, motor, URI y alcance.
2. Convierte la solicitud a los argumentos de `Table_Extract`.
3. Usa SQLAlchemy para introspección de schemas, tablas, columnas, llaves y
   relaciones.
4. Obtiene muestras en memoria y perfila nombres/tipos de columna.
5. Aplica detectores deterministas y heurísticos; opcionalmente aplica
   mDeBERTa Zero-Shot a nombres, apellidos, nombres completos y direcciones.
6. Propaga evidencia compatible por nombres y relaciones FK.
7. Escribe el artefacto JSON en GCS.
8. Persiste una proyección idempotente en Cloud SQL.

Los valores de muestra y `connection_uri` no se guardan en las tablas de
resultados.

## Persistencia y salida

- `database_discovery_runs`;
- `database_discovery_tables`;
- `database_discovery_findings`;
- JSON en `GCS_OUTPUT_URI`.

No hay topic de salida. La run sólo queda `completed` después de las escrituras
en GCS y Cloud SQL.

## Zero-Shot

El modelo esperado es
`MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7`. Los pesos y archivos
del tokenizer viven en GCS. Cuando `disable_zero_shot=false`, el wrapper copia
`TABLE_EXTRACT_ZERO_SHOT_MODEL_URI` a
`TABLE_EXTRACT_ZERO_SHOT_LOCAL_DIR` y pasa esa ruta local a `Table_Extract`, que
carga con `local_files_only=True`. La imagen conserva las dependencias de
runtime, incluido `sentencepiece`, pero no incluye el snapshot del modelo.

## Idempotencia y restricciones

El mismo `run_id` UUID puede reintentarse cuando conserva metadata inmutable;
una combinación incompatible falla para no mezclar resultados. Se recomienda
enviar siempre un UUID explícito: el fallback `scan-...` no satisface el
contrato UUID de persistencia.

`profile_only=true` produce un perfil de `Table_Extract`, pero el wrapper cloud
no lo acepta como descubrimiento persistible. Para resultados consultables debe
ser `false`.

## Variables esenciales

`SCAN_REQUEST_JSON`, `BBDD_RESULTS_DATABASE_URL`, `GCS_OUTPUT_URI`,
`BBDD_DISABLE_ZERO_SHOT`, `TABLE_EXTRACT_ZERO_SHOT_MODEL_URI`,
`TABLE_EXTRACT_ZERO_SHOT_LOCAL_DIR`,
`TABLE_EXTRACT_ZERO_SHOT_DEVICE`, `TABLE_EXTRACT_ZERO_SHOT_BATCH_SIZE` y
`LOG_LEVEL`.

## Código fuente

- `Cloud/BBDD-Job/src/cloud_bbdd_job/`
- `Table_Extract/`
- `Cloud/Database/schema.sql`
