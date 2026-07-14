# Entity Text Extract

`Entity_Text_Extract` consume archivos con chunks listos desde `Queue-Entity` o
procesa un `file_id` directo. Su responsabilidad es extraer entidades raw desde
`text_chunks_staging`, escribir un JSON raw por archivo, pasar el resultado a
`Entity_Text_Filter` y escribir un JSON filtrado por archivo.

El mensaje de entrada `file.chunks_ready` usa `schema_version=2.0` y preserva la
referencia del archivo (`source_type`, `source_uri`, `external_id`, `mime_type`,
`checksum_sha256`, `content_hash`, `etag`, `size_bytes`) generada al inicio del
pipeline.

La limpieza, validacion y deduplicacion quedan a cargo de `Entity_Text_Filter`.
Ambos JSON se escriben sin masking.

## Entorno

```bash
conda activate PII_entity
python -m pip install -r requirements.txt
python -m spacy download es_core_news_lg
```

Variables requeridas:

```bash
DATABASE_URL=postgresql://postgres:TU_PASSWORD_POSTGRES@localhost:5432/PII_DB
RABBITMQ_URL=amqp://admin:TU_PASSWORD_RABBITMQ@localhost:5672/
PII_ENTITY_OUTPUT_DIR=/tmp/pii-entity-results
TEXT_MATERIALIZE_SCRATCH_DIR=/tmp/pii-text-materialization
```

Variables opcionales:

```bash
PII_ENTITY_SPACY_MODEL=es_core_news_lg
PII_ENTITY_GLINER2_MODEL=fastino/gliner2-privacy-filter-PII-multi
PII_ENTITY_MEDICAL_MODEL=HUMADEX/spanish_medical_ner
PII_ENTITY_MODEL_DEVICE=
PII_ENTITY_ZERO_SHOT_DEVICE=auto
PII_ENTITY_GLINER2_USE_GPU=false
PII_ENTITY_ENABLE_PRESIDIO=true
PII_ENTITY_ENABLE_DETERMINISTIC=true
PII_ENTITY_ENABLE_GLINER2=true
PII_ENTITY_ENABLE_MEDICAL=true
```

`PII_ENTITY_GLINER2_MODEL` y `PII_ENTITY_MEDICAL_MODEL` pueden apuntar a nombres
de Hugging Face o rutas locales. Usa `HF_HOME` o `TRANSFORMERS_CACHE` para
controlar cache de modelos.

## Uso

Procesar un archivo especifico sin RabbitMQ:

```bash
python main.py --file-id UUID_DEL_ARCHIVO
```

Consumir desde `Queue-Entity`:

```bash
python main.py --max-messages 1
```

Usar GPU para GLiNER2, HUMADEX y Zero-Shot del filtro:

```bash
python main.py --gpu
python main.py --device cuda
python main.py --device mps
python main.py --device cpu
```

Leer mensajes y reencolarlos despues de escribir ambos JSON:

```bash
python main.py --dev-mode --max-messages 1
```

`--mask-text` queda disponible solo por compatibilidad, pero no enmascara los
outputs:

```bash
python main.py --file-id UUID_DEL_ARCHIVO --mask-text
```

## Salida

La salida default vive en `/tmp/pii-entity-results`. Se usa
ruta espejo segun `relative_path`: si el archivo original es
`subdir/documento.pdf`, los resultados quedan en:

```text
/tmp/pii-entity-results/subdir/documento.pdf.json
/tmp/pii-entity-results/subdir/documento.pdf_filtrado.json
```

El JSON raw conserva `chunks[]` y todas las entidades detectadas. El JSON
filtrado contiene `accepted_entities[]`, con `source`, `raw_entity_type`,
`score`, `normalized_value`, ubicacion primaria y evidencia completa a
segmentos de `source_map`.

La tabla `entity_extraction_files` registra `started_at`, `completed_at`,
`processing_seconds`, `cpu_user_seconds`, `cpu_system_seconds`,
`cpu_total_seconds`, `peak_memory_mb`, conteos raw/filtrados y paths de ambos
JSON. Esas mismas metricas de entidades tambien se incluyen en el JSON raw y el
JSON filtrado.

La trazabilidad es a nivel de bloque y bbox existente, no bbox exacto por
palabra o entidad.

## Revisar resultados en Jupyter Lab

Abrir `notebooks/review_entity_results.ipynb`. La seccion de resultados
filtrados lee los JSON de `/tmp/pii-entity-results` y
muestra:

- archivos procesados;
- conteos por tipo de entidad;
- conteos por herramienta (`regex`, `presidio`, `gliner2`, `medical_model`,
  `deny_list`);
- tabla de entidades aceptadas con valor detectado, score, chunk, pagina,
  trazabilidad y evidencia.

Las deny-lists custom detectan terminos de AFP/sistema previsional, sistema de
salud, genero, religion/creencia y estado civil. El modulo de filtrado trata
esas ultimas tres categorias como entidades base cuando provienen de documentos
de texto. Para genero tambien existe una regex contextual que captura valores
cortos solo cuando aparecen cerca de etiquetas como `Sexo` o `Genero`.

## Pruebas

```bash
conda activate PII_entity
python -m pytest tests
```
