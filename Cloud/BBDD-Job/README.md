# Cloud Run Job para PII en BBDD

> Documentación canónica del flujo, persistencia y fallos:
> [`docs/jobs/bbdd.md`](../../docs/jobs/bbdd.md).

Boilerplate para desplegar en Cloud Run Jobs el flujo tabular/BBDD basado en
`Table_Extract`, incluido en la raíz del repositorio. El job recibe un
`ScanRequest`, conecta a una fuente BBDD y ejecuta PII Discovery. Una ejecucion
exitosa publica primero el artifact
JSON en Cloud Storage y despues persiste su proyeccion consultable en Cloud SQL,
en una unica transaccion idempotente.

## Estructura

```text
Cloud/BBDD-Job/
  Dockerfile
  cloudbuild.yaml
  requirements-cloud.txt
  config/
    deploy.env.sample.sh
    env.sample.yaml
    job.yaml.template
  clouddeploy/
    skaffold.yaml
  scripts/
    build.sh
    cloud_deploy_release.sh
    deploy_job.sh
    execute_job.sh
    run.sh
  src/cloud_bbdd_job/
    main.py
    results_repository.py
    scan_request.py
```

## Entrada principal: ScanRequest

La entrada preferida es `SCAN_REQUEST_JSON`. El Visor envia solo identidad de la
run, motor, credencial de la fuente y un alcance explícito por schemas/tablas o
la confirmacion del full scan. Proyecto, region, job, bucket, modelo, device y
otras opciones operativas pertenecen al despliegue, no al usuario.

```json
{
  "run_id": "86ca6e73-ea37-4c1f-812d-7b71dcb771bb",
  "user_id": "ana",
  "run_name": "Clientes produccion Q3",
  "database_type": "postgresql",
  "connection_uri": "postgresql+psycopg://usuario:password@db.example.com:5432/db",
  "include_schemas": ["public", "audit"],
  "include_tables": ["customers", "invoices"],
  "confirm_full_scan": false,
  "allow_full_database_scan": false
}
```

`database_type` acepta `postgresql` y `oracle`; el esquema del connection string
debe coincidir. Debe existir al menos un filtro de schema o tabla cuando
`confirm_full_scan=false`; si se envían ambos se aplican conjuntamente. Para un
full scan explícito, ambas listas deben viajar vacías y
`confirm_full_scan=true` se conserva como scope efectivo. `profile_only` debe
permanecer en `false` para producir resultados consultables. Los `BBDD_*` legacy
quedan solo como fallback de compatibilidad.

## Cloud SQL de resultados

Antes de desplegar, aplicar el schema central cloud:

```bash
psql "$DATABASE_URL" -f Cloud/Database/schema.sql
```

Ese archivo crea de forma aditiva `database_discovery_runs`,
`database_discovery_tables` y `database_discovery_findings`. El repositorio solo
persiste una lista blanca de metadata: nunca guarda `connection_uri`,
`profile.source_uri`, `evidence_summary` ni valores muestreados.

`BBDD_RESULTS_DATABASE_URL` es obligatorio y apunta a esta Cloud SQL. Se entrega
como variable plana mediante un YAML local ignorado, separada de la credencial
de la BBDD objetivo. `GCS_OUTPUT_URI` tambien es obligatorio: si falla GCS o Cloud SQL, el Job termina con error; la fila
`completed` solo existe despues de ambas escrituras.

## Prueba local sin Docker

Una ejecucion completa local tambien debe tener destinos reales de prueba para
GCS y PostgreSQL. Desde la raíz del repositorio:

```bash
export SCAN_REQUEST_JSON='{"run_id":"86ca6e73-ea37-4c1f-812d-7b71dcb771bb","user_id":"ana","run_name":"Clientes local","database_type":"postgresql","connection_uri":"postgresql+psycopg://usuario:password@localhost:5432/db","confirm_full_scan":true}'
export GCS_OUTPUT_URI="gs://bucket-pruebas/database-discovery/"
export BBDD_RESULTS_DATABASE_URL="postgresql://results_user:password@localhost:5432/pii_results"
export BBDD_DISABLE_ZERO_SHOT="true"

PYTHONPATH="$PWD/Table_Extract:$PWD/Cloud/BBDD-Job/src" \
  conda run -n PII_bbdds python -m cloud_bbdd_job.main
```

## Prueba local con Docker

```bash
docker build \
  -f Cloud/BBDD-Job/Dockerfile \
  -t bbdd-pii-job:local \
  .

docker run --rm \
  -e SCAN_REQUEST_JSON='{"run_id":"86ca6e73-ea37-4c1f-812d-7b71dcb771bb","user_id":"ana","run_name":"Clientes Docker","database_type":"postgresql","connection_uri":"postgresql+psycopg://usuario:password@host.docker.internal:5432/db","confirm_full_scan":true}' \
  -e GCS_OUTPUT_URI="gs://bucket-pruebas/database-discovery/" \
  -e BBDD_RESULTS_DATABASE_URL="postgresql://results_user:password@host.docker.internal:5432/pii_results" \
  -e BBDD_DISABLE_ZERO_SHOT="true" \
  -v /private/tmp:/tmp \
  bbdd-pii-job:local
```

## Build

Desde la raíz del repositorio:

```bash
source Cloud/BBDD-Job/config/deploy.env.sample.sh

# Solo si el repositorio aun no existe.
gcloud artifacts repositories create "${AR_REPOSITORY}" \
  --project "${PROJECT_ID}" \
  --repository-format docker \
  --location "${REGION}" \
  --description "Imagenes PII"

Cloud/BBDD-Job/scripts/build.sh
```

El script imprime `IMAGE_URI`; usalo en el despliegue.

Para probar PII Discovery con Zero-Shot en Cloud Run, configura el snapshot
compartido en GCS. El job lo descarga a almacenamiento temporal antes de llamar
a `Table_Extract`; pesos y tokenizer no se incluyen en la imagen:

```bash
export TABLE_EXTRACT_ZERO_SHOT_MODEL_URI="gs://pii-pipeline/Models/zero-shot/mdeberta-xnli/v1"
export TABLE_EXTRACT_ZERO_SHOT_LOCAL_DIR="/tmp/pii-models/zero-shot"
```

La service account necesita `roles/storage.objectViewer` sobre ese snapshot. En
el `SCAN_REQUEST_JSON`, deja:

```json
"profile_only": false,
"disable_zero_shot": false
```

## Deploy

El despliegue no contiene `SCAN_REQUEST_JSON` ni una BBDD objetivo fija. Si los
resultados usan socket Cloud SQL, el Job se asocia solo a esa instancia mediante
`ATTACH_CLOUD_SQL=true`.

```bash
source Cloud/BBDD-Job/config/deploy.env.sample.sh
export PROJECT_ID="tu-proyecto-gcp"
export REGION="us-central1"
export IMAGE_URI="us-central1-docker.pkg.dev/tu-proyecto-gcp/pii/bbdd-pii-job:TAG"
export ENV_VARS_FILE="Cloud/BBDD-Job/config/env.deploy.local.yaml"
export ATTACH_CLOUD_SQL="true"
export CLOUD_SQL_INSTANCE="tu-proyecto-gcp:us-central1:pii-results"

Cloud/BBDD-Job/scripts/deploy_job.sh
```

## Deploy con Cloud Deploy

Cloud Deploy sirve para versionar el despliegue del Job como un release. No
ejecuta el Job automaticamente; despues del release se invoca con
`gcloud run jobs execute`.

Desde la raíz del repositorio:

```bash
source Cloud/BBDD-Job/config/deploy.env.sample.sh

# Si ya tienes una imagen construida, definela aqui.
export IMAGE_URI="us-central1-docker.pkg.dev/ldd-dev/pii/bbdd-pii-job:TAG"

Cloud/BBDD-Job/scripts/cloud_deploy_release.sh
```

Si `IMAGE_URI` no esta definido, el script llama a `scripts/build.sh` antes de
crear el release. Para validar solo los manifests generados, sin tocar GCP:

```bash
CLOUD_DEPLOY_DRY_RUN=1 Cloud/BBDD-Job/scripts/cloud_deploy_release.sh
```

El script genera:

- un `DeliveryPipeline` de Cloud Deploy;
- un `Target` Cloud Run para `PROJECT_ID`/`REGION`;
- un `job.yaml` de Cloud Run Jobs sin `SCAN_REQUEST_JSON`, para no persistir
  credenciales en el despliegue;
- un release Cloud Deploy que reemplaza `bbdd-pii-job-image` por `IMAGE_URI`.

## Ejecutar contra una BBDD

Antes de ejecutar, arma un `SCAN_REQUEST_JSON` con el endpoint de la BBDD de esa
corrida. Para evitar problemas de quoting con `gcloud`, usa la version compacta.

```bash
export SCAN_REQUEST_JSON='{
  "run_id": "86ca6e73-ea37-4c1f-812d-7b71dcb771bb",
  "user_id": "ana",
  "run_name": "Cliente A produccion",
  "database_type": "postgresql",
  "connection_uri": "postgresql+psycopg://usuario:password@db.example.com:5432/db",
  "confirm_full_scan": true,
  "allow_full_database_scan": true
}'

export SCAN_REQUEST_JSON_COMPACT="$(printf '%s' "${SCAN_REQUEST_JSON}" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin), separators=(",",":")))' )"

Cloud/BBDD-Job/scripts/execute_job.sh
```

La URL de resultados es obligatoria en el YAML local de deploy. La credencial
objetivo se mantiene por ejecución dentro de `SCAN_REQUEST_JSON`:

```bash
export ENV_VARS_FILE="Cloud/BBDD-Job/config/env.deploy.local.yaml"
Cloud/BBDD-Job/scripts/deploy_job.sh
```

## Conectividad de BBDD

El `connection_uri` elige la BBDD en runtime, pero Cloud Run igualmente debe
tener red para llegar a ese host.

Para una BBDD externa por TCP:

- usa un host/IP alcanzable desde Cloud Run;
- configura allowlist/firewall/TLS en el lado del cliente;
- si necesitas IP fija de salida, usa VPC egress/NAT;
- si la BBDD esta en red privada, configura VPC.

Si la BBDD esta en una red privada accesible por VPC:

```bash
export VPC_CONNECTOR="projects/tu-proyecto-gcp/locations/us-central1/connectors/pii-connector"
export VPC_EGRESS="private-ranges-only"
Cloud/BBDD-Job/scripts/deploy_job.sh
```

Cloud SQL con socket `/cloudsql/...` se usa para la BBDD de resultados, sin
cambiar la conectividad de la fuente objetivo:

```bash
export ATTACH_CLOUD_SQL="true"
export CLOUD_SQL_INSTANCE="tu-proyecto-gcp:us-central1:tu-instancia"
Cloud/BBDD-Job/scripts/deploy_job.sh
```

Si `CLOUD_SQL_INSTANCE` esta exportado pero `ATTACH_CLOUD_SQL` no es `true`, los
scripts lo ignoran.


## IAM minimo

La service account del job necesita:

- `roles/storage.objectCreator` sobre el bucket de `GCS_OUTPUT_URI`.
- `roles/storage.objectViewer` sobre el snapshot de
  `TABLE_EXTRACT_ZERO_SHOT_MODEL_URI`.
- Permisos de red/firewall/allowlist para llegar a la BBDD externa.
- `roles/cloudsql.client` para la Cloud SQL de resultados cuando se usa socket.
- Permisos de Artifact Registry para leer la imagen al ejecutar el job.

## Notas operativas

- Mantener `TASKS=1` por defecto. Sin sharding, mas tasks repetirian el mismo scan.
- `profile_only` debe ser `false`; para pruebas baratas, usar
  `BBDD_DISABLE_ZERO_SHOT=true`.
- Para usar Zero-Shot, configurar `TABLE_EXTRACT_ZERO_SHOT_MODEL_URI` con el
  prefijo exacto que contiene `config.json`, `tokenizer.json`, `spm.model` y los
  pesos en su raíz.
- El artifact y Cloud SQL no persisten valores crudos de columnas.
