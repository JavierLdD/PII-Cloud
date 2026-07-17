# Confianza y filtrado de entidades

El filtro de Entity asigna uno de tres niveles a cada entidad aceptada:
`VERY_CONFIDENT`, `CONFIDENT` o `PROBABLE`. No existe un nivel `UNRELIABLE` en
la salida filtrada: un candidato que no supera su ruta de validación se descarta
y no aparece en `accepted_entities` ni en `entity_extraction_entities`.

```mermaid
flowchart TD
    C["Candidato raw"] --> T{"Tipo canónico"}
    T -->|"Base válida"| V["VERY_CONFIDENT"]
    T -->|"Validación local"| L{"Valor válido y gate aplicable"}
    T -->|"Score directo"| M{"score > 0.90"}
    T -->|"Zero-Shot"| G{"score original >= 0.50"}
    L -->|"Sí"| P["PROBABLE"]
    L -->|"No"| D["Descartada"]
    M -->|"Sí"| P
    M -->|"No"| D
    G -->|"No"| D
    G -->|"Sí"| Z{"score Zero-Shot"}
    Z -->|">= 0.85"| F["CONFIDENT"]
    Z -->|">= 0.50 y < 0.85"| P
    Z -->|"< 0.50"| D
```

## Niveles actuales

| Nivel | Regla | `decision_method` | `decision_score` |
|---|---|---|---|
| `VERY_CONFIDENT` | Entidad base que supera su validador determinista | `base_validation` | `1.0` |
| `CONFIDENT` | Resultado Zero-Shot `>= 0.85` | `zero_shot` | Score Zero-Shot |
| `PROBABLE` | Zero-Shot `>= 0.50` y `< 0.85` | `zero_shot` | Score Zero-Shot |
| `PROBABLE` | Validación local superada | `local_validation` | Score original del detector |
| `PROBABLE` | Tipo gobernado por score con score original `> 0.90` | `model_score_threshold` | Score original del detector |

Los bordes son intencionales:

- `0.50` exacto entra al Zero-Shot y un resultado Zero-Shot `0.50` es
  `PROBABLE`.
- `0.85` exacto es `CONFIDENT`.
- Para las rutas gobernadas por `MODEL_SCORE_PROBABLE_THRESHOLD`, `0.90` exacto
  se descarta; debe ser estrictamente mayor que `0.90`.

## Rutas por tipo de entidad

| Ruta | Tipos canónicos | Resultado |
|---|---|---|
| Base determinista | `RUT`, `PHONE`, `EMAIL`, `PAYMENT_CARD`, `LICENSE_PLATE`, `PENSION_SYSTEM`, `HEALTH_SYSTEM`, `GENDER`, `MARITAL_STATUS`, `RELIGION_OR_BELIEF`, `SEXUAL_ORIENTATION`, `POLITICAL_OR_UNION_AFFILIATION` | `VERY_CONFIDENT` si el valor normaliza y valida; de lo contrario se descarta |
| Validación local | `AGE`, `DATE`, `IP_ADDRESS`, `MAC_ADDRESS`, `URL` | `PROBABLE` si valida. `IP_ADDRESS`, `MAC_ADDRESS` y `URL` además exigen score original `> 0.90` |
| Score original | `DOCUMENT_ID`, `CARD_EXPIRY`, `CARD_CVV`, `BANK_ACCOUNT`, `CRYPTO_WALLET`, `ACCOUNT_ID`, `USERNAME`, `PASSWORD`, `SECRET`, `API_KEY`, `ACCESS_TOKEN`, `RECOVERY_CODE`, `BIOMETRIC_OR_BIOLOGICAL` | `PROBABLE` sólo con score original `> 0.90` |
| Zero-Shot | `NAME`, `ORGANIZATION`, `LOCATION`, `ADDRESS`, `MEDICAL_PROBLEM`, `MEDICAL_TEST`, `MEDICAL_TREATMENT` | Gate original `>= 0.50`; después `CONFIDENT`, `PROBABLE` o descarte según score Zero-Shot |

## Campos para consumir o auditar la decisión

| Campo | Significado |
|---|---|
| `confidence_level` | Nivel final: `VERY_CONFIDENT`, `CONFIDENT` o `PROBABLE` |
| `decision_method` | Ruta que aceptó la entidad |
| `decision_score` | Score usado para resolver la decisión y los solapamientos |
| `score` | Score original entregado por el detector |
| `zero_shot_score` | Score del segundo modelo; sólo existe para la ruta Zero-Shot |
| `validation_status` | Resultado técnico de la validación aplicada |
| `evidence` | Candidatos y ubicaciones que respaldan la entidad ganadora |

Estos campos se guardan tanto en el JSON filtrado como en
`entity_extraction_entities` de Cloud SQL.