# Modelos de ML y licencias

El inventario legible por herramientas se mantiene en
[`model-manifest.yaml`](../assets/model-manifest.yaml).

## Resumen

| Modelo | Job | Uso | Estado | Licencia declarada |
|---|---|---|---|---|
| `es_core_news_lg` | Entity | NER general en español | Activo por defecto | GPL-3.0 |
| `fastino/gliner2-privacy-filter-PII-multi` | Entity | NER orientado a PII | Activo por defecto | Apache-2.0 |
| `HUMADEX/spanish_medical_ner` | Entity | NER médico en español | Activo por defecto | Apache-2.0 en la model card |
| `MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7` | BBDD / Entity | Clasificar columnas ambiguas | Activo por defecto. Se debe cargar desde GCP | MIT |

Router, Text PDF Extract y Text Docs Extract no usan modelos de ML. PDF usa
PyMuPDF y heurísticas deterministas; Docs usa parsers y decodificadores.

## spaCy: `es_core_news_lg`

Entity lo usa a través de spaCy/Presidio para NER en español. El release 3.8.0
del modelo declara **GPL-3.0** en [spaCy Models](https://github.com/explosion/spacy-models/releases/tag/es_core_news_lg-3.8.0),
y la ficha funcional está en [spaCy: Spanish pipelines](https://spacy.io/models/es#es_core_news_lg).

Es importante separar artefactos:

- la biblioteca [spaCy](https://github.com/explosion/spaCy) declara MIT;
- [Presidio](https://github.com/data-privacy-stack/presidio) declara MIT;
- los **pesos `es_core_news_lg`** declaran GPL-3.0.

## GLiNER2 Privacy Filter

[`fastino/gliner2-privacy-filter-PII-multi`](https://huggingface.co/fastino/gliner2-privacy-filter-PII-multi)
declara **Apache-2.0**. El runtime
[`fastino-ai/GLiNER2`](https://github.com/fastino-ai/GLiNER2) también declara
Apache-2.0.

Se usa para etiquetas flexibles de PII, entre ellas nombres, documentos,
direcciones, cuentas, credenciales y datos financieros. El identificador puede
reemplazarse por `PII_ENTITY_GLINER2_MODEL`.

## HUMADEX Spanish Medical NER

[`HUMADEX/spanish_medical_ner`](https://huggingface.co/HUMADEX/spanish_medical_ner)
declara **Apache-2.0** en la ficha del modelo. Entity lo usa para detectar entidades médicas cuando
`PII_ENTITY_ENABLE_MEDICAL=true`.

El modelo se ejecuta mediante Transformers; la biblioteca
[Hugging Face Transformers](https://github.com/huggingface/transformers)
declara Apache-2.0.

## mDeBERTa Zero-Shot

[`MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7`](https://huggingface.co/MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7)
incluye una licencia [MIT](https://huggingface.co/MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7/blob/main/LICENSE).

- Entity espera usarlo para resolver candidatos ambiguos después de los
  detectores, pero el wrapper reemplaza el identificador por una ruta local
  descargada desde GCS.
- BBDD puede usarlo para nombres, apellidos, nombres completos y direcciones.
- La feature BBDD está habilitada por defecto en el código; la plantilla la
  desactiva con `BBDD_DISABLE_ZERO_SHOT=true` y la imagen base no precarga los
  pesos.
- BBDD carga con `local_files_only=True`. Sin pesos, falla de forma diferida
  cuando una columna candidata intenta ejecutar Zero-Shot, no necesariamente al
  iniciar el job.

En Entity el job descarga desde `PII_ENTITY_ZERO_SHOT_MODEL_URI` hacia una ruta
local. Git no puede comprobar que esos bytes sean el modelo nombrado ni aplicar
automáticamente su licencia MIT; la identidad, revisión, hash y licencia del
artefacto desplegado permanecen desconocidas hasta auditar el bucket.

## MinerU: presente, pero no activo

El código upstream de MinerU usa su
[MinerU Open Source License](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md),
basada en Apache-2.0 con condiciones adicionales.
