# Text Docs Extract Job

Nombre por defecto: `text-docs-extract-job`.

## Objetivo

Materializar documentos de texto desde fuentes externas, normalizar su contenido y crear
chunks para el job de entidades. Comparte con PDF el contrato de persistencia,
outbox, leases y consumo acotado de Pub/Sub.

## Entrada

Consume `file.routed` desde una suscripción del topic `pii-docs`. Valida esquema
`2.0`, `route_type=doc` y el alcance `user_id`/`run_id` esperado.

## Formatos y extracción

| Formato | Tratamiento |
|---|---|
| TXT | Decodificación UTF-8 con BOM; fallback Latin-1 |
| DOCX | Lectura de párrafos y tablas en orden documental |
| Google Docs | Exportación de Drive a `text/plain` |
| Google Slides | Exportación de Drive a `text/plain` |

El contenido forma una página lógica y usa el mismo chunker que PDF: objetivo
1.500 caracteres, máximo 2.500, mínimo 400 y solapamiento 200. Un documento
vacío es un resultado válido con cero chunks.

## Procesamiento

1. Hace pull de un mensaje y valida su alcance.
2. Adquiere la lease y materializa o exporta el documento en `/tmp`.
3. Extrae y normaliza el texto.
4. Genera los chunks.
5. Persiste archivo, página, chunks y outbox transaccionalmente.
6. Libera la lease y limpia temporales.
7. Publica `file.chunks_ready` en `pii-entities`.
8. Confirma el mensaje al terminar correctamente el handler.

## Persistencia y salida

Escribe en `text_extraction_files`, `text_extraction_pages`,
`text_chunks_staging` y `queue_outbox`. Entity obtiene los chunks desde la base,
no desde Pub/Sub.

## Fallos y reintentos

Los archivos inválidos, demasiado grandes, agotados por timeout o con errores
permanentes de extracción se registran como fallidos y generan poison de forma
idempotente. Una materialización diferida produce `nack`; una excepción no
controlada deja el mensaje sin confirmar y hace fallar el job.

Al igual que PDF, la plantilla usa `maxRetries: 0` y el job termina tras el
timeout de inactividad configurado, que por defecto es 60 segundos.

## Variables esenciales

`SUBSCRIPTION_ID`, `DATABASE_URL`, `EXPECTED_USER_ID`, `EXPECTED_RUN_ID`,
`TOPIC_PII_ENTITIES`, `TOPIC_TEXT_POISON`, `TEXT_MATERIALIZE_SCRATCH_DIR`,
`TEXT_EXTRACT_MAX_FILE_BYTES`, `PER_FILE_TIMEOUT_SECONDS`,
`PUBSUB_IDLE_TIMEOUT_SECONDS`, `PUBSUB_PULL_TIMEOUT_SECONDS` y `MAX_MESSAGES`.

## Código fuente

- `Cloud/Text-Docs-Extract-Job/src/cloud_text_docs_extract_job/`
- `Text_Extract/`
- `Cloud/Database/schema.sql`
