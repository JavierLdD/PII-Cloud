# Text Extract

`Text_Extract` contiene workers independientes para extraer texto y crear
chunks temporales. Esta version implementa los workers de `Queue-PDF`,
`Queue-OCR-Urgente`, `Queue-OCR` y `Queue-Doc`.

Postgres es la fuente de verdad. RabbitMQ transporta avisos de trabajo y
`queue_outbox` mantiene publicaciones pendientes hacia `Queue-OCR-Urgente`,
`Queue-OCR` y `Queue-Entity`.

Los workers consumen y publican mensajes JSON `schema_version=2.0`. La
referencia durable del archivo viaja como `source_type + source_uri` junto a
`external_id`, `mime_type`, `checksum_sha256`, `content_hash`, `etag` y
`size_bytes` cuando existan. `original_path` ya no es una columna del schema; se
deriva solo para fuentes locales `local://...`.

## Submodulos

Cada extractor vive en su propia carpeta y tiene su propio `main.py`. La raiz
del modulo queda reservada para infraestructura compartida: `common`,
`chunking`, `staging`, `messaging`, `schema.sql` y herramientas de revision.

Submodulos previstos:

- `pdf`: consume `Queue-PDF`.
- `ocr`: consume primero `Queue-OCR-Urgente` y luego `Queue-OCR`.
- `docs`: consume `Queue-Doc`.
- `tables`: se definira mas adelante.

## Entorno

```bash
conda activate PII_base
python -m pip install -r requirements.txt
```

El worker OCR usa un entorno separado porque depende de MinerU:

```bash
conda activate PII_ocr
python -m pip install -r ocr/requirements.txt
```

`ocr/requirements.txt` instala solo el cliente liviano del worker. MinerU,
Torch, CUDA y los modelos locales deben vivir en el entorno donde esta levantado
`mineru-api`. El worker sube el archivo materializado por HTTP, espera la tarea
asincrona y luego parsea los artefactos devueltos por MinerU.

Variables requeridas:

```bash
DATABASE_URL=postgresql://postgres:TU_PASSWORD_POSTGRES@localhost:5432/PII_DB
RABBITMQ_URL=amqp://admin:TU_PASSWORD_RABBITMQ@localhost:5672/
TEXT_EXTRACT_RABBITMQ_HEARTBEAT_SECONDS=1800
TEXT_EXTRACT_RABBITMQ_BLOCKED_CONNECTION_TIMEOUT_SECONDS=1800
TEXT_CHUNK_TTL_HOURS=24
TEXT_MATERIALIZE_SCRATCH_DIR=/tmp/pii-text-materialization
TEXT_MATERIALIZE_SMALL_LIMIT_BYTES=104857600
TEXT_MATERIALIZE_GLOBAL_LIMIT_BYTES=524288000
TEXT_MATERIALIZE_LEASE_TTL_SECONDS=7200
TEXT_MATERIALIZE_REQUEUE_DELAY_SECONDS=5
GOOGLE_CLIENT_SECRETS_FILE=/ruta/a/google_client_secret.json
GOOGLE_TOKEN_FILE=/ruta/a/google_drive_token.json
MINERU_API_URL=http://127.0.0.1:8000
MINERU_API_POLL_INTERVAL_SECONDS=1
MINERU_API_SUBMIT_TIMEOUT_SECONDS=300
MINERU_API_RESULT_TIMEOUT_SECONDS=600
```

Las variables de Google solo son necesarias cuando llegan archivos
`source_type=drive`. El scope usado por `Text_Extract` es `drive.readonly`, por
lo que un token creado solo para metadata puede requerir reautorizacion.

Si aparece `Transport indicated EOF` durante PDFs u OCR pesados, normalmente el
broker cerro la conexion mientras el worker estaba ocupado. Mantener
`TEXT_EXTRACT_RABBITMQ_HEARTBEAT_SECONDS` y
`TEXT_EXTRACT_RABBITMQ_BLOCKED_CONNECTION_TIMEOUT_SECONDS` en valores altos
ayuda a evitar que el cierre del transporte tape el archivo problematico real.

El servicio MinerU se levanta fuera del worker, por ejemplo:

```bash
mineru-api --host 0.0.0.0 --port 8000
```

La seleccion de GPU/backend se controla en ese servicio, no en el worker OCR. En
cada arranque el worker loggea `api_url`, timeouts y frecuencia de polling.

## Base de datos

Primero deben existir los schemas de `File_Discovery` y `Router`. Luego aplicar:

```bash
psql "$DATABASE_URL" -f schema.sql
```

Si usas Postgres en Docker y no tienes `psql` local:

```bash
docker exec -i NOMBRE_CONTENEDOR psql -U postgres -d PII_DB < schema.sql
```

## Materializacion temporal

Los mensajes siguen usando `source_type + source_uri` como referencia durable.
Si el archivo ya es `local://...`, los workers leen ese path y no borran nada.
Si el archivo viene desde Drive, `Text_Extract` lo descarga o exporta a
`TEXT_MATERIALIZE_SCRATCH_DIR` solo mientras se extrae texto.

La concurrencia de descargas se coordina con Postgres:

- archivos de hasta `100 MB` compiten por
  `TEXT_MATERIALIZE_SMALL_LIMIT_BYTES`;
- archivos sobre `100 MB` no bloquean ese presupuesto pequeno, pero cuentan
  contra `TEXT_MATERIALIZE_GLOBAL_LIMIT_BYTES`;
- si el presupuesto no alcanza, el mensaje se reencola y no se marca como
  `text_extraction_failed`;
- los temporales se eliminan cuando el archivo llega a
  `text_extraction_completed` o `text_extraction_failed`.

Para PDFs remotos mixtos, el temporal se conserva mientras el archivo queda
`waiting_ocr`; las paginas OCR reutilizan el mismo archivo y lo liberan al
cerrar el estado final.

## Flujo PDF

- Consume mensajes `file.routed` desde `Queue-PDF`.
- Decide pagina por pagina si usa `pymupdf` u `ocr`.
- Extrae bloques PyMuPDF y los convierte en chunks temporales.
- Para paginas OCR, crea un mensaje batch `pdf.ocr_batch_requested` hacia
  `Queue-OCR-Urgente` con la lista de paginas pendientes. Los mensajes antiguos
  `pdf.page_ocr_requested` siguen siendo aceptados por compatibilidad.
- Si todas las paginas quedan listas, crea un mensaje `file.chunks_ready` hacia
  `Queue-Entity`.
- Si alguna pagina queda pendiente de OCR, el archivo queda `waiting_ocr`.
- `text_extraction_files.started_at`, `completed_at` y `processing_seconds`
  registran cuanto tarda el archivo en completar la extraccion de texto. En
  PDFs mixtos, `completed_at` y `processing_seconds` quedan vacios hasta que el
  worker OCR cierre las paginas pendientes.
- Para PDFs mixtos tambien se guardan `embedded_text_seconds`,
  `ocr_queue_wait_seconds`, `ocr_processing_seconds` y
  `ocr_processing_wall_seconds`; a nivel pagina se guardan los timestamps y
  duraciones de PyMuPDF/OCR en `text_extraction_pages`.
- Las metricas confiables del batch OCR, incluyendo tiempo wall, transporte API,
  cantidad de requests MinerU y nivel de fallback, quedan en `text_ocr_batches`.
- Para dimensionamiento tambien se guardan `cpu_user_seconds`,
  `cpu_system_seconds`, `cpu_total_seconds` y `peak_memory_mb` en
  `text_extraction_files` y `text_extraction_pages`. El CPU y memoria observados
  corresponden al proceso worker cliente; el uso GPU vive en el servicio MinerU.

## Flujo OCR

- Consume mensajes `pdf.ocr_batch_requested` desde `Queue-OCR-Urgente` para
  paginas de PDF enviadas por el worker PDF, y mantiene compatibilidad con
  mensajes legacy `pdf.page_ocr_requested`.
- Consume mensajes `file.routed` desde `Queue-OCR` para imagenes enviadas
  directamente por el Router.
- Envia el archivo materializado a `mineru-api` con backend `pipeline` y lenguaje
  `latin`. Para PDFs batch solicita rangos consecutivos de paginas, luego divide
  el rango si falla y termina intentando pagina individual si es necesario.
- Guarda chunks temporales con `method=ocr` y metadata de `mineru` en
  `source_map`.
- Borra los artefactos MinerU al terminar correctamente. Para debug se puede
  usar `--keep-artifacts`.
- Si la API de MinerU falla, el worker imprime `error=...` y persiste el detalle en
  `text_extraction_pages.error`.
- Cuando no quedan paginas OCR pendientes para el archivo, publica un unico
  `file.chunks_ready` hacia `Queue-Entity`.

## Flujo Doc

- Consume mensajes `file.routed` desde `Queue-Doc`.
- Soporta archivos locales `.txt` y `.docx`.
- Usa una pagina logica `page_number=1` para mantener el mismo esquema de
  revision que PDF/OCR.
- Extrae bloques de texto, parrafos y tablas, y los convierte en chunks
  temporales.
- Publica un mensaje `file.chunks_ready` hacia `Queue-Entity` al terminar.
- Google Docs/Slides se exportan temporalmente a texto plano antes de extraer.
- Archivos Drive no nativos, como PDFs o DOCX subidos a Drive, se descargan
  temporalmente antes de que estos workers los abran.

El servicio MinerU debe levantarse en el entorno con GPU/modelos:

```bash
conda activate PII_ocr
mineru-api --host 0.0.0.0 --port 8000
```

El worker OCR se ejecuta desde el entorno liviano de `Text_Extract`:

```bash
export MINERU_API_URL=http://127.0.0.1:8000
python -m pip install -r ocr/requirements.txt
python -m ocr.main --max-messages 1
# o tambien:
python ocr/main.py --max-messages 1
```

`mineru-models-download` y `MINERU_MODEL_SOURCE=local` pertenecen al entorno del
servicio `mineru-api`, no al worker cliente.

## Modo dev

Leer N mensajes sin retirarlos de la cola ni publicar downstream:

```bash
python -m pdf.main --dev-mode --max-messages 3
# o tambien:
python pdf/main.py --dev-mode --max-messages 3

python -m ocr.main --dev-mode --max-messages 3
# o tambien:
python ocr/main.py --dev-mode --max-messages 3

python -m docs.main --dev-mode --max-messages 3
# o tambien:
python docs/main.py --dev-mode --max-messages 3
```

Publicar solo outbox pendiente:

```bash
python -m pdf.main --publish-pending-only
python -m ocr.main --publish-pending-only
python -m docs.main --publish-pending-only
```

## Revisar chunks en Jupyter Lab

Abrir `notebooks/review_chunks.ipynb` desde Jupyter Lab. El notebook es
read-only: solo ejecuta consultas `SELECT` sobre Postgres.

Por defecto `SHOW_TEXT = False`, por lo que no trae el texto de los chunks. Si
necesitas revisar contenido, cambia explicitamente:

```python
SHOW_TEXT = True
MAX_TEXT_CHARS = 800
```

La herramienta permite revisar:

- archivos procesados y tiempos de extraccion;
- decisiones por pagina (`pymupdf` u `ocr`);
- chunks temporales y `source_map`;
- mensajes pendientes en `queue_outbox` hacia `Queue-OCR-Urgente`,
  `Queue-OCR` o `Queue-Entity`.

## Pruebas

```bash
conda activate PII_base
python -m pytest tests
```
