# Text PDF Extract Job

Nombre por defecto: `text-pdf-extract-job`.

## Objetivo

Consumir PDFs enrutados, materializarlos desde fuentes externas, extraer el texto embebido
y dejar páginas y chunks listos en Cloud SQL. La versión cloud actual no ejecuta
OCR.

## Entrada

Lee eventos `file.routed` de una suscripción asociada al topic `pii-pdf`. Exige
esquema `2.0`, `route_type=pdf` y coincidencia con `EXPECTED_USER_ID` y
`EXPECTED_RUN_ID`.

El job drena la suscripción de uno en uno hasta llegar a `MAX_MESSAGES` o hasta
que transcurre `PUBSUB_IDLE_TIMEOUT_SECONDS` sin trabajo. `MAX_MESSAGES=0`
significa sin límite por cantidad, no ejecución infinita.

## Procesamiento

1. Valida el contrato y el alcance del mensaje.
2. Adquiere una lease de materialización y descarga el PDF a un directorio
   temporal.
3. Abre el archivo con PyMuPDF y clasifica cada página mediante reglas
   deterministas:

    - texto suficiente: al menos 80 caracteres o 12 palabras, salvo dominio de
      imagen;
    - OCR probable: una imagen ocupa al menos 55 % de la página, o el total de
      imágenes al menos 70 %, o el texto es insuficiente.

4. Si todas las páginas son procesables, divide el texto por página con objetivo
   1.500 caracteres, máximo 2.500, mínimo 400 y solapamiento 200.
5. Guarda archivo, páginas, chunks y evento de outbox dentro de una transacción.
6. Libera la lease y elimina el archivo temporal.
7. Publica `file.chunks_ready` en `pii-entities`.
8. Confirma el mensaje de entrada al terminar correctamente el handler.

## Persistencia y salida

Escribe en `text_extraction_files`, `text_extraction_pages`,
`text_chunks_staging` y `queue_outbox`. El evento `file.chunks_ready` sólo
transporta referencias y metadata; Entity lee el texto desde PostgreSQL.

## PDFs que requieren OCR


Si una página requiere OCR, el PDF completo queda fallido. El job elimina
    sus chunks y publica `file.text_extract_poisoned` con
    `reason=ocr_required` en `pii-text-poison`.

No existe un Cloud OCR Job conectado que recupere ese mensaje. El operador debe
inspeccionarlo o implementar el consumidor correspondiente.

## Confirmación y reintentos

- Éxito o poison permanente persistido: `ack`.
- Materialización temporalmente diferida: `nack`.
- Excepción no controlada: sin `ack`; la ejecución falla.
- JSON bien formado pero contrato/alcance inválido: poison y `ack`.
- JSON malformado antes del handler: fallo sin evento de poison.

Las plantillas del Cloud Run Job usan `maxRetries: 0`; una nueva ejecución debe
ser decidida por la operación u orquestación.

## Variables esenciales

`SUBSCRIPTION_ID`, `DATABASE_URL`, `EXPECTED_USER_ID`, `EXPECTED_RUN_ID`,
`TOPIC_PII_ENTITIES`, `TOPIC_TEXT_POISON`, `TEXT_MATERIALIZE_SCRATCH_DIR`,
`TEXT_EXTRACT_MAX_FILE_BYTES`, `PER_FILE_TIMEOUT_SECONDS`,
`PUBSUB_IDLE_TIMEOUT_SECONDS`, `PUBSUB_PULL_TIMEOUT_SECONDS` y `MAX_MESSAGES`.

## Código fuente

- `Cloud/Text-PDF-Extract-Job/src/cloud_text_pdf_extract_job/`
- `Text_Extract/`
- `Cloud/Database/schema.sql`
