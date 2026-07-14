# Entity_Text_Filter

`Entity_Text_Filter` toma el resultado raw construido por `Entity_Text_Extract`,
filtra entidades aceptadas y conserva la evidencia de origen de cada entidad
final. En el flujo normal recibe el raw en memoria; su CLI sigue disponible para
procesar JSON raw antiguos o de debug. Este modulo no lee Postgres, no consume
RabbitMQ y no guarda resultados en base de datos.

Cuando el JSON raw viene en contrato v2, la salida filtrada preserva
`source_type`, `source_uri`, `external_id`, `extension`, `mime_type`,
`size_bytes`, `checksum_sha256`, `content_hash` y `etag` en la metadata
superior.

## Modo dev

```bash
conda activate PII_entity
python Entity_Text_Filter/main.py \
  --input-json /ruta/a/debug/documento.pdf.entities.json
```

La salida default vive en `/tmp/pii-entity-results`. Si el JSON
raw tiene `relative_path=subdir/documento.pdf`, la salida sera:

```text
/tmp/pii-entity-results/subdir/documento.pdf_filtrado.json
```

`--mask-text` se mantiene solo por compatibilidad; el output se escribe sin
masking:

```bash
python Entity_Text_Filter/main.py \
  --input-json /ruta/a/debug/documento.pdf.entities.json \
  --mask-text
```

## Politica de filtrado

1. Se canonizan tipos de entidad, manteniendo `raw_entity_type`, `source`,
   `trace`, chunk, spans y evidencia.
2. Las entidades base validadas quedan como `VERY_CONFIDENT` y nunca pasan por
   Zero-Shot: `RUT`, telefono chileno, email, tarjeta, patente, sistema
   previsional, sistema de salud, genero, estado civil, religion/creencia,
   orientacion sexual y afiliacion politica/sindical.
3. Las no-base que se solapan con una base validada se descartan como entidad
   aceptada, pero su evidencia se agrega a la base ganadora.
4. Las no-base restantes pasan por una de estas rutas:
   - validacion local: `AGE`, `DATE`, `IP_ADDRESS`, `MAC_ADDRESS`, `URL`;
   - score original sobre `0.9`: identificadores, cuentas, secretos y datos
     biometricos/biologicos;
   - Zero-Shot, solo si el score original es `>= 0.50`: `NAME`,
     `ORGANIZATION`, `LOCATION`, `ADDRESS`, `MEDICAL_PROBLEM`,
     `MEDICAL_TEST`, `MEDICAL_TREATMENT`.
5. Entre no-base solapadas dentro del mismo chunk gana la de mayor
   `decision_score`, sin importar tipo de entidad. Las perdedoras quedan como
   evidencia de la ganadora.
6. Despues se deduplica por `(entity_type, value_key)` para no fusionar textos
   iguales con tipos distintos.

Los niveles de confianza son:

- `VERY_CONFIDENT`: entidad base validada;
- `CONFIDENT`: Zero-Shot `>= 0.85`;
- `PROBABLE`: Zero-Shot `>= 0.50` o regla local/score que sobreviva.

El JSON final solo contiene `accepted_entities`; no escribe una lista separada de
descartadas.

## Zero-Shot local

El filtro carga el modelo de forma lazy, solo si quedan candidatos que requieren
Zero-Shot. Los candidatos Zero-Shot con score original `< 0.50` se descartan
antes de cargar o ejecutar el modelo. El default es:

```text
PII_ENTITY_ZERO_SHOT_MODEL=MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7
PII_ENTITY_ZERO_SHOT_DEVICE=auto
PII_ENTITY_ZERO_SHOT_BATCH_SIZE=8
```

En uso standalone:

```bash
python Entity_Text_Filter/main.py --input-json raw.json --gpu
python Entity_Text_Filter/main.py --input-json raw.json --device cuda
python Entity_Text_Filter/main.py --input-json raw.json --device cpu
```

El modelo se carga con `local_files_only=True`; debe estar descargado en el cache
local o apuntado por `PII_ENTITY_ZERO_SHOT_MODEL`.

## Tests

```bash
conda run -n PII_entity python -m pytest Entity_Text_Filter/tests
```
