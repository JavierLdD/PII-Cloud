# Cloud Run Job Text Docs Extract

Job finito para drenar una subscription Pub/Sub de documentos ya ruteados,
materializar cada archivo temporalmente, extraer texto y publicar
`file.chunks_ready` hacia `TOPIC_PII_ENTITIES`.

Soporta el contrato actual de `Text_Extract`: `.txt`, `.docx`, Google Docs y
Google Slides materializados desde Drive.

## Contrato

Variables requeridas:

```text
SUBSCRIPTION_ID
DATABASE_URL
TOPIC_PII_ENTITIES
TOPIC_TEXT_POISON
EXPECTED_USER_ID
EXPECTED_RUN_ID
```

La subscription debe contener mensajes `file.routed` de `Queue-Doc` para un solo
`user_id + run_id`. El job valida esos valores usando attributes y payload.

## Runtime

Valores iniciales recomendados:

```text
TASKS=1
PARALLELISM=1
MAX_RETRIES=0
TASK_TIMEOUT=600s
PER_FILE_TIMEOUT_SECONDS=540
PUBSUB_IDLE_TIMEOUT_SECONDS=60
TEXT_MATERIALIZE_SCRATCH_DIR=/tmp/pii-text
TEXT_EXTRACT_MAX_FILE_BYTES=104857600
```

El archivo descargado/exportado se borra antes de ackear el mensaje y antes de
leer el siguiente mensaje.

## Base de datos

Antes de ejecutar, aplicar:

```bash
psql "$DATABASE_URL" -f Cloud/Database/schema.sql
```

`Cloud/Database/schema.sql` es el punto central para cloud e incluye las tablas
base, routing, outbox, `Text_Extract`, `Table_Extract` y entidades cloud.

## Build y deploy

```bash
export IMAGE_URI="REGION-docker.pkg.dev/PROJECT_ID/pii/text-docs-extract-job:TAG"
export ENV_VARS_FILE="Cloud/Text-Docs-Extract-Job/config/env.deploy.yaml"
Cloud/Text-Docs-Extract-Job/scripts/build.sh
Cloud/Text-Docs-Extract-Job/scripts/deploy_job.sh
```

`SUBSCRIPTION_ID`, `EXPECTED_USER_ID` y `EXPECTED_RUN_ID` quedan vacios en
`config/env.deploy.yaml` porque son valores de corrida. Pasalos al ejecutar:

```bash
export UPDATE_ENV_VARS="SUBSCRIPTION_ID=projects/PROJECT_ID/subscriptions/SUBSCRIPTION_NAME,EXPECTED_USER_ID=USER_ID,EXPECTED_RUN_ID=RUN_ID"
Cloud/Text-Docs-Extract-Job/scripts/execute_job.sh
```

Para Cloud SQL nativo:

```bash
export ATTACH_CLOUD_SQL=true
export CLOUD_SQL_INSTANCE="PROJECT_ID:REGION:INSTANCE_NAME"
export ENV_VARS_FILE="Cloud/Text-Docs-Extract-Job/config/env.deploy.yaml"
unset DATABASE_URL_SECRET
unset DATABASE_URL
Cloud/Text-Docs-Extract-Job/scripts/deploy_job.sh
```

En este modo, `DATABASE_URL` va dentro de `config/env.deploy.yaml`.
