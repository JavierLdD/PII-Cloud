# PII-disocvery-Cloud-LdD

## Documentación

La documentación técnica vive en `docs/`. Incluye
la arquitectura del pipeline, el procesamiento job por job, contratos de
Pub/Sub, persistencia, operación y el inventario de modelos de ML.

Para verla localmente:

```bash
python -m pip install -r requirements-docs.txt
python -m mkdocs serve
```

Para ejecutar la misma validación estricta usada en CI:

```bash
python -m mkdocs build --strict
```

El punto de entrada es [`docs/index.md`](docs/index.md). Los cambios de
comportamiento del pipeline deben actualizar la documentación y, cuando
corresponda, el
[`model-manifest.yaml`](docs/assets/model-manifest.yaml) en el mismo PR.

## Independencia del repositorio principal

Este repositorio contiene copias propias de los módulos que necesitan los jobs:

- `Text_Extract/`
- `Entity_Text_Extract/`
- `Entity_Text_Filter/`
- `Table_Extract/`

## Estructura

- `Cloud/`: jobs, infraestructura SQL y utilidades operacionales.
- `docs/`: documentación canónica de arquitectura, jobs, datos, ML y operación.
- `Text_Extract/`: Extractor de texto.
- `Entity_Text_Extract/`: Extractor de entidades.
- `Entity_Text_Filter/`: Filtro de entidades.
- `Table_Extract/`: Analizador de fuentes tabulares y BBDD.
