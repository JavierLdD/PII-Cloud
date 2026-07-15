# File Discovery + Router Job

Nombre por defecto: `file-discovery-router-job`.

## Objetivo

Descubrir archivos en Google Drive sin descargar su contenido, registrar un
snapshot de metadata y publicar un evento `file.routed` por cada archivo que
debe procesarse. No extrae texto, no ejecuta OCR y no detecta PII.

## Entrada

El job se ejecuta con `DISCOVERY_ROUTER_REQUEST_JSON`:

```json
{
  "user_id": "user-001",
  "run_id": "86ca6e73-ea37-4c1f-812d-7b71dcb771bb",
  "source_type": "drive",
  "drive_folder_id": "DRIVE_FOLDER_ID",
  "source_name": "carpeta-clientes",
  "force_enqueue": false,
  "dry_run": false
}
```

`source_type` sólo admite `drive`. `max_files` existe para diagnóstico: una
ejecución limitada no se considera snapshot completo, no genera tombstones y
no sirve como padre incremental.

## Procesamiento

1. Valida la solicitud y abre o reanuda `ingestion_runs`.
2. Enumera recursivamente la carpeta de Drive y obtiene metadata. La URI
   persistida tiene forma `drive://file/{drive_file_id}`.
3. Compara cada elemento con snapshots previos y lo clasifica como nuevo,
   modificado, reutilizable o reprocesado.
4. Sólo reutiliza un resultado cuando la revisión compatible del pipeline tiene
   resultados previos completos y la identidad del archivo es fiable.
5. Decide la ruta y crea la decisión más su registro de outbox.
6. Publica los eventos pendientes en Pub/Sub.
7. Si la enumeración fue completa y no falló ninguna publicación, registra
   tombstones para archivos desaparecidos.

## Ruteo

| Tipo | `route_type` | Topic configurado por |
|---|---|---|
| PDF | `pdf` | `TOPIC_PDF` |
| Imagen | `ocr` | `TOPIC_OCR` |
| TXT, DOCX, Docs, Slides | `doc` | `TOPIC_DOC` |
| CSV, XLSX, XLSM, Sheets | `table` | `TOPIC_TABLES` |
| XLS, DOC, desconocido | `unsupported` | `TOPIC_UNSUPPORTED` |

## Persistencia y salida

Escribe principalmente en:

- `ingestion_runs`;
- `files`;
- `routing_decisions`;
- `queue_outbox`;
- `file_snapshot_tombstones`.

El evento de salida es `file.routed`, esquema `2.0`. Pub/Sub recibe identidad,
metadata y atributos de ruteo; el binario del archivo no viaja en el mensaje.
Los atributos incluyen `user_id`, `run_id`, `file_id`, `route_type`,
`destination_queue_name` y `routing_decision_id`.

## Idempotencia y fallos

Cada publicación usa una clave de idempotencia. Si Pub/Sub falla, el outbox
conserva el estado y el número de intentos, y la run termina `partial_failed`.
No se calculan eliminaciones sobre una enumeración incompleta o con
publicaciones fallidas.

`dry_run=true` valida Drive, Cloud SQL y topics y muestra la clasificación, pero
no escribe inventario ni publica eventos.

## Variables esenciales

`DATABASE_URL`, `TOPIC_PDF`, `TOPIC_OCR`, `TOPIC_DOC`, `TOPIC_TABLES`,
`TOPIC_UNSUPPORTED`, `VISOR_PIPELINE_REVISION` y `LOG_LEVEL`.

## Código fuente

- `Cloud/File-Discovery-Router-Job/src/cloud_file_router_job/request.py`
- `Cloud/File-Discovery-Router-Job/src/cloud_file_router_job/drive_source.py`
- `Cloud/File-Discovery-Router-Job/src/cloud_file_router_job/routing.py`
- `Cloud/File-Discovery-Router-Job/src/cloud_file_router_job/repository.py`
- `Cloud/File-Discovery-Router-Job/src/cloud_file_router_job/pubsub.py`
- `Cloud/Database/schema.sql`
