# Cobertura y limitaciones

Esta página evita confundir código disponible, rutas configuradas y capacidades
realmente desplegables.

## Cobertura actual

| Capacidad | Estado |
|---|---|
| Descubrimiento recursivo en Google Drive | Implementada |
| Detección de archivos nuevos, modificados, reutilizados y eliminados | Implementada |
| Extracción de PDF con texto embebido | Implementada |
| Extracción de TXT, DOCX, Google Docs y Slides | Implementada |
| Detección y filtrado de entidades PII en texto | Implementada |
| Escaneo directo de PostgreSQL y Oracle | Implementado como flujo independiente |
| OCR cloud para imágenes o PDF escaneado | No implementado |
| Consumo cloud de CSV/Excel/Google Sheets | No implementado |
| Consumo de `pii-unsupported` | No implementado |
| Consumidor automático de poison | No implementado |
| Orquestación automática Pub/Sub → Cloud Run Job | No implementada |

## Implicancias operacionales

- Un mensaje publicado no implica que su job haya comenzado.
- Un PDF mixto se envía completo a poison si una sola página requiere OCR.
- Las rutas sin consumidor deben monitorearse para evitar que parezcan trabajo
  completado. Tables y Unsupported ni siquiera quedan retenidas si no se crea
  una suscripción antes de publicar.
- Las plantillas actuales usan `maxRetries: 0`; un fallo de ejecución no obtiene
  reintentos administrados por Cloud Run.
- El script de suscripciones crea PDF, Docs, OCR, Entity y poison con retención
  y expiración de un día por defecto; no crea suscripciones ni consumidores de
  Tables o Unsupported.

## Criterio para declarar una capacidad lista

Una ruta sólo debe marcarse como operativa cuando existan y se hayan validado:

1. imagen construida;
2. Cloud Run Job desplegado;
3. IAM, secretos y conectividad;
4. topic y suscripción con el alcance correcto;
5. ejecución end-to-end observada en logs, Cloud SQL y GCS;
6. tratamiento de errores y reintentos definido.
