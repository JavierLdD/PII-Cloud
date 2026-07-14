# Cloud Run Job File Discovery + Router

Job finito para descubrir archivos desde Google Drive, registrar metadata en
Cloud SQL/Postgres y publicar eventos `file.routed` hacia Pub/Sub.

La v1 soporta `source_type = "drive"` solamente. El job no extrae texto, no hace
OCR y no detecta PII; deja esos pasos para los workers downstream.

## Contrato de ejecucion

La app web o backend debe ejecutar el job con `DISCOVERY_ROUTER_REQUEST_JSON`:

```json
{
  "user_id": "user-001",
  "run_id": "run-001",
  "source_type": "drive",
  "drive_folder_id": "DRIVE_FOLDER_ID",
  "source_name": "carpeta-clientes",
  "force_enqueue": false,
  "dry_run": false
}
```

`user_id` y `run_id` viajan en los attributes de Pub/Sub para permitir filtros
futuros.

`max_files` se reserva para diagnostico. Cuando se usa, la run no se marca como
snapshot completo, no registra eliminaciones y no se usa como padre incremental.

## Variables del template

Config estable:

```text
DATABASE_URL
TOPIC_PDF
TOPIC_OCR
TOPIC_DOC
TOPIC_TABLES
TOPIC_UNSUPPORTED
VISOR_PIPELINE_REVISION
LOG_LEVEL
```

`VISOR_PIPELINE_REVISION` identifica una version compatible de todo el pipeline, no
solo del Router. Debe cambiar cuando una nueva version requiera reprocesar los
archivos aunque su contenido no haya cambiado.

Por compatibilidad, el job tambien acepta `PIPELINE_REVISION` cuando la variable
con prefijo `VISOR_` no esta definida.

`DATABASE_URL` debe configurarse con Secret Manager en despliegues reales.
Para pruebas rapidas, el script tambien acepta `DATABASE_URL` como variable de
entorno plana si `DATABASE_URL_SECRET` no esta configurada.

## Build

Desde la raiz de `PII-Cloud`:

```bash
export IMAGE_URI="REGION-docker.pkg.dev/PROJECT_ID/pii/file-discovery-router-job:TAG"
Cloud/File-Discovery-Router-Job/scripts/build.sh
```

El script construye `linux/amd64` por defecto, que es la plataforma esperada por
Cloud Run. Para cambiarla explicitamente:

```bash
export DOCKER_PLATFORM="linux/amd64"
Cloud/File-Discovery-Router-Job/scripts/build.sh
```

## Deploy

```bash
export IMAGE_URI="REGION-docker.pkg.dev/PROJECT_ID/pii/file-discovery-router-job:TAG"
export PROJECT_ID="PROJECT_ID"
export REGION="us-central1"
export SERVICE_ACCOUNT="file-discovery-router-job@PROJECT_ID.iam.gserviceaccount.com"
export DATABASE_URL_SECRET="pii-database-url"
Cloud/File-Discovery-Router-Job/scripts/deploy_job.sh
```

Para una prueba sin Secret Manager:

```bash
unset DATABASE_URL_SECRET
export DATABASE_URL="postgresql://pii_app:PASSWORD@/PII_DB?host=/cloudsql/PROJECT_ID:REGION:INSTANCE_NAME"
Cloud/File-Discovery-Router-Job/scripts/deploy_job.sh
```

Si se usa Cloud SQL nativo:

```bash
export ATTACH_CLOUD_SQL=true
export CLOUD_SQL_INSTANCE="PROJECT_ID:REGION:INSTANCE_NAME"
Cloud/File-Discovery-Router-Job/scripts/deploy_job.sh
```

## Execute

```bash
export DISCOVERY_ROUTER_REQUEST_JSON='{
  "user_id": "user-001",
  "run_id": "run-001",
  "source_type": "drive",
  "drive_folder_id": "DRIVE_FOLDER_ID"
}'

Cloud/File-Discovery-Router-Job/scripts/execute_job.sh
```

## Dry run

`dry_run=true` valida Drive, Cloud SQL y topics, lista y clasifica archivos, pero
no escribe `files`, `routing_decisions` ni `queue_outbox`, y no publica mensajes.

## Pub/Sub

El job publica al topic correspondiente:

| Ruta | Variable |
| --- | --- |
| PDF | `TOPIC_PDF` |
| OCR | `TOPIC_OCR` |
| Docs | `TOPIC_DOC` |
| Tables | `TOPIC_TABLES` |
| Unsupported | `TOPIC_UNSUPPORTED` |

Attributes obligatorios publicados:

```text
schema_version
event_type
user_id
run_id
file_id
source_type
route_type
destination_queue_name
routing_decision_id
```

## Base de datos

Requiere aplicar el schema central cloud del pipeline:

```bash
psql "$DATABASE_URL" -f Cloud/Database/schema.sql
```

Ese archivo crea las tablas base de `File_Discovery`, `Router` y agrega columnas
cloud-only como `user_id`, `execution_id`, `idempotency_key`,
`pubsub_message_id` y `pubsub_attributes`.

Cada run completada es un snapshot inmutable. El Router crea una fila nueva de
`files` por run, enlaza archivos sin cambios mediante `reused_from_file_id` y
publica solamente archivos nuevos, modificados o que requieren reproceso. Una
revision se considera fiable solo si Drive entrega checksum, content hash,
version o etag; en caso contrario el archivo se procesa otra vez.

Para Drive, `source_scope_key` guarda el folder ID crudo. `source_type` se
mantiene como columna separada para que el mismo identificador textual en otro
proveedor no comparta historial ni bloqueo.

Los archivos eliminados se registran en `file_snapshot_tombstones` unicamente
despues de enumerar la carpeta completa y publicar todos los mensajes. Una run
con `max_files`, una excepcion o publicaciones fallidas no genera tombstones ni
se convierte en base para el siguiente snapshot.

El primer snapshot posterior a la migracion reprocesa conservadoramente los
archivos legacy. No se reconstruyen asociaciones historicas que el esquema
global anterior ya hubiera perdido.

No se debe ejecutar simultaneamente el adapter legacy de `File_Discovery`
contra esta base: aquel adapter actualiza una fila global y no implementa el
contrato de snapshots por run.

`Cloud/File-Discovery-Router-Job/schema.sql` queda como referencia aditiva del
job, pero el punto central para despliegues nuevos es `Cloud/Database/schema.sql`.
