# Cloud Database Schema

`schema.sql` es el punto central para aplicar el schema de Cloud SQL/Postgres del
pipeline desplegado. Incluye las tablas base, routing, outbox, `Text_Extract`,
`Table_Extract`, entidades cloud y resultados consultables de BBDD Discovery.

Aplicar desde la raiz de `PII-Cloud`:

```bash
psql "$DATABASE_URL" -f Cloud/Database/schema.sql
```

El archivo es idempotente: puede correr sobre una BBDD vacia o sobre una BBDD que
ya tenga los schemas locales anteriores. A medida que se agreguen nuevos jobs o
workers cloud, este archivo debe actualizarse con las nuevas tablas, columnas e
indices necesarios.

## Reset total de una BBDD dedicada

`reset_database.sql` elimina de forma destructiva todos los objetos del schema
`public`, lo recrea y conserva la base, los roles y sus membresias. Tambien
revoca los privilegios de `PUBLIC` y configura privilegios actuales y futuros
para el rol runtime. No elimina otros schemas, large objects ni configuracion a
nivel de instancia, por lo que debe usarse solamente sobre una BBDD dedicada al
pipeline.

El reset exige cuatro guardas: nombre esperado de la BBDD, rol migrador, rol
runtime y la confirmacion literal `RESET_PII_PIPELINE_DATABASE`. Tambien se
niega a operar sobre BBDD de sistema o mientras existan otras sesiones cliente.

La forma recomendada ejecuta el reset y el schema maestro en una unica
transaccion. Si `schema.sql` falla, PostgreSQL revierte tambien la limpieza:

```bash
psql -X "$MIGRATOR_DATABASE_URL" \
  --set=ON_ERROR_STOP=1 \
  --single-transaction \
  --set=external_transaction=1 \
  --set=expected_database=pii_pipeline_db \
  --set=expected_role=pii_migrator \
  --set=runtime_role=pii_app \
  --set=confirm_reset=RESET_PII_PIPELINE_DATABASE \
  --file=Cloud/Database/reset_database.sql \
  --file=Cloud/Database/schema.sql
```

Antes de ejecutarlo se deben detener Visor y los Jobs, comprobar que no haya
runs activas y crear un backup recuperable de Cloud SQL. El connection string
usado en `MIGRATOR_DATABASE_URL` debe pertenecer a `pii_migrator`, nunca a
`pii_app`, al usuario de lectura del Visor ni a la BBDD objetivo de un cliente.

La migracion de snapshots elimina la unicidad global de `files` y la reemplaza
por `(run_id, source_type, source_uri)`. Antes de aplicarla en un entorno real se
debe respaldar la base. El backfill conserva las filas existentes como
`snapshot_state = 'legacy'`, calcula solo revisiones disponibles y no intenta
inventar historiales que no fueron persistidos por el esquema anterior.

Despues de esta migracion, las nuevas ingestas cloud deben ejecutarse con
`Cloud/File-Discovery-Router-Job`. El adapter legacy de `File_Discovery` supone
una fila global por URI y no es compatible con snapshots inmutables.

## Orden de despliegue

La activacion debe hacerse en este orden para no mezclar el modelo legacy con
los snapshots inmutables:

1. Respaldar Cloud SQL y comprobar que el respaldo puede restaurarse.
2. Aplicar `Cloud/Database/schema.sql` y verificar las nuevas columnas, indices
   y `file_snapshot_tombstones`.
3. Construir y desplegar `Cloud/File-Discovery-Router-Job` con una
   `VISOR_PIPELINE_REVISION` explicita. No volver a ejecutar el adapter legacy
   contra la base migrada.
4. Configurar el Visor con los nombres de jobs, topics, region, proyecto y
   `VISOR_RESULTS_DATABASE_URL` exclusivamente mediante variables de entorno.
5. Ejecutar dos runs consecutivas del mismo usuario y carpeta. La segunda debe
   procesar cero archivos sin cambios, conservar el mismo resultado acumulado y
   dejar inmutable el primer snapshot.

La migracion incluida en el repositorio no se aplica automaticamente a ninguna
instancia de GCP.

## Resultados de BBDD Discovery

La migracion aditiva crea:

- `database_discovery_runs`: propietario, motor, artifact GCS, metricas y KPI;
- `database_discovery_tables`: schema, tabla/vista, filas y contadores;
- `database_discovery_findings`: columna, tipo PII, confianza, metodo y conteos.

Estas tablas no contienen el connection string objetivo, `source_uri`, evidencia
textual ni valores muestreados. `bbdd-pii-job` usa
`BBDD_RESULTS_DATABASE_URL` como variable plana en un YAML local ignorado, sube primero el artifact GCS
y despues reemplaza idempotentemente las filas de la run en una sola transaccion.
Un fallo en cualquiera de las dos escrituras hace fallar el Job.

Para activar BBDD Discovery:

1. Respaldar Cloud SQL y aplicar `Cloud/Database/schema.sql`.
2. Desplegar la imagen actualizada de `Cloud/BBDD-Job`.
3. Configurar `BBDD_RESULTS_DATABASE_URL` y `GCS_OUTPUT_URI` en el YAML local
   de variables internas del Job.
4. Validar una run PostgreSQL y confirmar que el artifact y las tres tablas
   tienen el mismo `run_id` antes de habilitarla desde el Visor.
