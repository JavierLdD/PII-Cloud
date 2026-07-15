# Modelos de ML y licencias

Inventario verificado contra el código del repositorio y las fuentes upstream
el **15 de julio de 2026**. La licencia indicada es la declarada por el
proveedor; no sustituye una revisión legal de datasets, pesos, dependencias
transitivas ni forma de distribución.

El inventario legible por herramientas se mantiene en
[`model-manifest.yaml`](../assets/model-manifest.yaml).

## Resumen

| Modelo | Job | Uso | Estado | Licencia declarada |
|---|---|---|---|---|
| `es_core_news_lg` | Entity | NER general en español | Activo por defecto | GPL-3.0 |
| `fastino/gliner2-privacy-filter-PII-multi` | Entity | NER orientado a PII | Activo por defecto | Apache-2.0 |
| `HUMADEX/spanish_medical_ner` | Entity | NER médico en español | Activo por defecto | Apache-2.0 en la model card |
| Artefacto GCS esperado: `MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7` | Entity | Filtro Zero-Shot | Activo por defecto; identidad desplegada no verificable desde Git | Desconocida para los bytes GCS; el upstream esperado declara MIT |
| `MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7` | BBDD | Clasificar columnas ambiguas | Feature activa en código; template la desactiva y la imagen base no precarga pesos | MIT |

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

La imagen descarga el modelo con `python -m spacy download es_core_news_lg` sin
fijar una revisión. Como los pesos se incorporan a la imagen, la distribución de
esa imagen merece una revisión específica de obligaciones GPL.

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
declara **Apache-2.0** en la ficha del modelo. No se observó un archivo `LICENSE`
independiente en ese repositorio, por lo que el manifiesto conserva esa
salvedad. Entity lo usa para detectar entidades médicas cuando
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

El repositorio contiene un cliente en `Text_Extract/ocr` que puede llamar a una
API MinerU, pero ningún Cloud Run Job actual lo utiliza. Por eso **no hay un
modelo MinerU desplegado cuya identidad o licencia pueda afirmarse desde este
repo**.

El código upstream de MinerU usa su
[MinerU Open Source License](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md),
basada en Apache-2.0 con condiciones adicionales. El texto actual exige una
licencia comercial separada al superar 100 millones de usuarios activos
mensuales o USD 20 millones de ingresos mensuales, y exige atribución al ofrecer
un servicio online a terceros. Estas condiciones corresponden al código
upstream, no prueban la licencia de los pesos desplegados.

Algunos bundles, como
[`opendatalab/PDF-Extract-Kit-1.0`](https://huggingface.co/opendatalab/PDF-Extract-Kit-1.0),
declaran AGPL-3.0; eso sólo sería aplicable si el manifiesto del servicio
demuestra que ese bundle se desplegó. No debe inferirse por el nombre MinerU.

## Reproducibilidad pendiente

Hoy varios downloads no fijan `revision` o hash. Para que una imagen sea
reconstruible y auditable se debe registrar, por modelo:

1. identificador y revisión inmutable;
2. SHA-256 del artefacto;
3. origen y fecha de adquisición;
4. job e imagen que lo consumen;
5. licencia de pesos, código y dataset por separado;
6. resultado de la revisión de seguridad/licencia.

Cuando se cambie un modelo, debe actualizarse
`docs/assets/model-manifest.yaml`, esta página y la revisión del pipeline en el
mismo PR.
