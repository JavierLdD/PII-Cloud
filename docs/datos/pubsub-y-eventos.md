# Pub/Sub y contratos de eventos

Pub/Sub desacopla las etapas, pero no es el orquestador de Cloud Run Jobs. Una
suscripción existente retiene mensajes según su política hasta que un job los
consume o expiran. Publicar en un topic sin suscripciones no conserva el
mensaje para consumidores futuros.

## Topics y eventos

| Topic lógico | Productor | Evento | Consumidor actual |
|---|---|---|---|
| `pii-pdf` | Router | `file.routed` | Text PDF Extract |
| `pii-docs` | Router | `file.routed` | Text Docs Extract |
| `pii-ocr` | Router | `file.routed` | Ninguno; el helper sí crea una suscripción temporal |
| `pii-tables` | Router | `file.routed` | Ninguno; el helper no crea suscripción |
| `pii-unsupported` | Router | `file.routed` | Ninguno; el helper no crea suscripción |
| `pii-entities` | PDF / Docs | `file.chunks_ready` | Entity Text Extract |
| `pii-text-poison` | PDF / Docs | `file.text_extract_poisoned` | Ninguno automático |

Los nombres reales se inyectan por variables y pueden ser rutas completas como
`projects/PROJECT_ID/topics/pii-pdf`.

## Atributos de alcance

Los eventos de esquema `2.0` llevan en atributos, como mínimo, identidad de
schema/evento, `user_id`, `run_id` y `file_id`. `file.routed` agrega
`source_type`, `route_type`, `destination_queue_name` y
`routing_decision_id`.

Las suscripciones por run deben filtrar el mismo `user_id` y `run_id` que se
configuran en el consumidor. El script
`Cloud/Pruebas/crear_suscripciones_pubsub.sh` prepara las suscripciones de
prueba para ese alcance con retención y expiración de un día por defecto. Crea
PDF, Docs, OCR, Entity y poison; no crea Tables ni Unsupported.

## Qué contiene un mensaje

Un evento transporta metadata y referencias, no el documento ni los chunks
completos:

- Router referencia el archivo y su fila `files`.
- PDF/Docs materializan el binario directamente desde la fuente externa.
- `file.chunks_ready` referencia chunks ya persistidos en Cloud SQL.
- Entity lee el texto desde `text_chunks_staging`.

Esto mantiene mensajes pequeños y permite reanudar el procesamiento desde el
estado durable.

## Outbox

Router y extractores registran la ruta normal de eventos en `queue_outbox`
antes de publicar. La fila conserva estado, intentos y error; Router además usa
una clave de idempotencia. Los poison generados directamente por contrato o
alcance inválido no pasan por el outbox. La operación debe detectar y reintentar
filas pendientes.

## Ack, nack y ejecución

- PDF/Docs confirman después del éxito o de persistir un poison permanente.
- Una lease ocupada o materialización diferida genera `nack`.
- Un fallo no controlado deja el mensaje sin confirmar y hace fallar la
  ejecución.
- Entity confirma al terminar su handler; fallos de detector, GCS o DB propagan
  y dejan el mensaje pendiente.

Como las plantillas usan `maxRetries: 0`, un mensaje pendiente no basta para
reiniciar el job: debe ejecutarse nuevamente.

## Compatibilidad

Cambiar un payload, atributo obligatorio o semántica de evento requiere:

1. actualizar productor y consumidor;
2. versionar el schema;
3. actualizar estas tablas y las páginas de ambos jobs;
4. evaluar mensajes todavía retenidos en suscripciones existentes;
5. cambiar `VISOR_PIPELINE_REVISION` si los resultados previos ya no son
   reutilizables.
