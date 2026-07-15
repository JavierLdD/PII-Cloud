# Build, deploy y ejecución

## Prerrequisitos

- proyecto, región e identidades de servicio en GCP;
- Artifact Registry para las imágenes;
- Cloud SQL/PostgreSQL con `Cloud/Database/schema.sql` aplicado;
- topics y suscripciones Pub/Sub;
- acceso de lectura a Google Drive;
- buckets GCS y permisos de lectura/escritura;
- secretos para las URLs de base de datos;
- conectividad de red para Cloud SQL y las fuentes BBDD.

Las credenciales reales deben vivir en Secret Manager o archivos
`*.local.*` ignorados; no en los YAML versionados.

## Construcción

Todos los Dockerfiles usan la raíz del repositorio como contexto. Ejecute desde
esa raíz:

```bash
Cloud/File-Discovery-Router-Job/scripts/build.sh
Cloud/Text-PDF-Extract-Job/scripts/build.sh
Cloud/Text-Docs-Extract-Job/scripts/build.sh
Cloud/Entity-Text-Extract-Job/scripts/build.sh
Cloud/BBDD-Job/scripts/build.sh
```

Router, PDF, Docs y Entity requieren `IMAGE_URI`; esa URI ya codifica proyecto,
región, repositorio y tag. El build de BBDD requiere `PROJECT_ID` y puede derivar
`REGION`/`IMAGE_URI` desde sus defaults. BBDD necesita un build especial con
`PRELOAD_ZERO_SHOT_MODEL=true` si se desea usar Zero-Shot.

## Despliegue

Cada directorio tiene `scripts/deploy_job.sh` y
`config/job.yaml.template`. Los nombres por defecto son:

| Job | Nombre |
|---|---|
| Router | `file-discovery-router-job` |
| PDF | `text-pdf-extract-job` |
| Docs | `text-docs-extract-job` |
| Entity | `entity-text-extract-job` |
| BBDD | `bbdd-pii-job` |

El despliegue instala configuración estable. El JSON de una run, la
suscripción por alcance y las credenciales de la fuente BBDD se inyectan en la
ejecución, no deben quedar fijados globalmente en el job.

## Secuencia recomendada para archivos

1. Crear suscripciones para el `user_id` y `run_id`:

    ```bash
    Cloud/Pruebas/crear_suscripciones_pubsub.sh
    ```

2. Ejecutar Router con `DISCOVERY_ROUTER_REQUEST_JSON`.
3. Ejecutar Text PDF Extract para drenar PDFs.
4. Ejecutar Text Docs Extract para drenar documentos.
5. Inspeccionar `pii-text-poison` y reconocer que `pii-ocr` y `pii-tables` no
   tienen consumidor cloud.
6. Ejecutar Entity Text Extract después de que PDF y Docs hayan publicado sus
   `file.chunks_ready`.
7. Verificar estados y conteos en Cloud SQL, objetos JSON en GCS y logs de cada
   ejecución.

Scripts de ejecución:

```bash
Cloud/File-Discovery-Router-Job/scripts/execute_job.sh
Cloud/Text-PDF-Extract-Job/scripts/execute_job.sh
Cloud/Text-Docs-Extract-Job/scripts/execute_job.sh
Cloud/Entity-Text-Extract-Job/scripts/execute_job.sh
```

Los pasos 3 y 4 pueden correr en paralelo una vez finalizado el Router. Entity
debe ejecutarse cuando ambos productores ya hayan terminado o volver a
ejecutarse para drenar mensajes posteriores.

## Ejecución BBDD

BBDD no participa en la secuencia anterior:

```bash
Cloud/BBDD-Job/scripts/execute_job.sh
```

Antes de invocarlo se debe definir un `SCAN_REQUEST_JSON` compacto, un UUID
`run_id`, `GCS_OUTPUT_URI` y la credencial de resultados montada como
`BBDD_RESULTS_DATABASE_URL`.

## Validación operacional

Para cada job, comprobar:

1. estado de la ejecución de Cloud Run;
2. logs filtrados por job y execution;
3. mensajes pendientes o poison;
4. filas esperadas y estados en Cloud SQL;
5. objetos esperados en GCS;
6. ausencia de rutas temporales o outbox pendiente después del éxito.

!!! warning "Sin reintentos administrados"
    Las plantillas actuales fijan `maxRetries: 0`. Un error exige decidir si es
    seguro corregir y volver a ejecutar; no asuma que Cloud Run lo hará solo.

## Cambios de pipeline

Si una modificación invalida resultados previos, cambie
`VISOR_PIPELINE_REVISION`. Si cambia contratos, persistencia o modelos,
actualice la documentación correspondiente en el mismo commit.
