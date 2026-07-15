# Cloud SQL y GCS

## Cloud SQL como estado durable

El schema central es `Cloud/Database/schema.sql`. Debe aplicarse antes de
desplegar jobs que dependan de tablas nuevas.

| Etapa | Tablas principales | Contenido |
|---|---|---|
| Discovery/Router | `ingestion_runs`, `files`, `routing_decisions`, `file_snapshot_tombstones`, `queue_outbox` | Snapshots, identidad, ruteo y publicaciones |
| Text Extract | `text_extraction_files`, `text_extraction_pages`, `text_chunks_staging`, `text_materialization_leases` | Estado, metadata/métricas de página, texto en chunks y leases |
| Entity Extract | `entity_extraction_files`, `entity_extraction_entities` | Estado, artefactos y entidades aceptadas |
| BBDD | `database_discovery_runs`, `database_discovery_tables`, `database_discovery_findings` | Runs, metadata tabular y hallazgos |

Los jobs de archivos comparten `DATABASE_URL`. BBDD usa una credencial separada,
`BBDD_RESULTS_DATABASE_URL`, para escribir resultados; no debe confundirse con
`connection_uri`, que es la credencial de la fuente inspeccionada.

## Cloud Storage

Entity escribe bajo `PII_ENTITY_GCS_OUTPUT_URI`:

- resultado filtrado, destinado a consumo;
- resultado raw cuando `PII_ENTITY_SAVE_RAW_RESULTS=true`.

BBDD escribe su artefacto de descubrimiento bajo `GCS_OUTPUT_URI` antes de
persistir la proyección consultable en Cloud SQL.

El Zero-Shot de Entity también puede descargarse desde GCS mediante
`PII_ENTITY_ZERO_SHOT_MODEL_URI`. Ese prefijo contiene un artefacto de modelo,
no un resultado de pipeline.

## Datos que no deben persistirse

- secretos y contraseñas;
- tokens de Google Drive;
- `connection_uri` de una BBDD objetivo;
- valores muestreados por BBDD;
- archivos materializados en `/tmp`;
- configuraciones locales `*.local.*`.

Los resultados raw pueden contener PII no filtrada. Deben protegerse con IAM,
retención y acceso más restrictivos que los artefactos filtrados.

## Consistencia y recuperación

No existe una transacción distribuida entre PostgreSQL, Pub/Sub y GCS. El
pipeline usa idempotencia y outbox para reducir ventanas de inconsistencia:

- Router/Text escriben estado y outbox antes de publicar.
- Entity persiste en SQL antes de completar GCS; un fallo de upload puede
  requerir reproceso para reemplazar rutas temporales.
- BBDD considera la run completada sólo después del artefacto GCS y la
  proyección SQL.

La recuperación debe verificar las tres superficies: ejecución del job, filas
en Cloud SQL y objetos esperados en GCS.
