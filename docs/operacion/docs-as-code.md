# Docs as code


## Uso local

```bash
python -m pip install -r requirements-docs.txt
python -m mkdocs serve
```

MkDocs mostrará una URL local y recompilará al guardar cambios.

Antes de abrir un PR:

```bash
python -m mkdocs build --strict
```

El modo estricto falla ante advertencias como enlaces internos no reconocidos o
páginas omitidas de la navegación.

## Cuándo actualizar cada página

| Cambio | Documentos mínimos |
|---|---|
| Nuevo job o etapa | Arquitectura, flujo por tipo, página del job y operación |
| Cambio de topic/evento | Productor, consumidor y Pub/Sub |
| Cambio de schema SQL | Job afectado y Cloud SQL/GCS |
| Nuevo formato soportado | Router, flujo por tipo y job consumidor |
| Modelo o revisión nueva | Página ML y `docs/assets/model-manifest.yaml` |
| Umbral o política de confianza | Confianza y filtrado, Entity y tests del resolver |
| Cambio de licencia upstream | Página ML, manifiesto y fecha de verificación |
| Nueva variable operativa | Job y build/deploy/ejecución |

## Definition of done documental

Un cambio de pipeline está completo cuando:

1. el comportamiento documentado coincide con el código y plantillas;
2. capacidades pendientes siguen marcadas como pendientes;
3. ejemplos no incluyen secretos reales;
4. modelos tienen revisión, procedencia y licencia registradas;
5. `mkdocs build --strict` termina correctamente;
6. el reviewer puede seguir el flujo de entrada a resultado sin leer el código.

## Publicación

El workflow actual valida el sitio, pero no lo publica: el repositorio local
aún no tiene remoto configurado y GitHub Pages requiere una decisión de rama,
dominio y permisos. Una vez conectado el remoto se puede agregar un job de
deploy a Pages sin cambiar el contenido ni la estructura.
