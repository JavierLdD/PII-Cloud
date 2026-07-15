#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Crea las subscriptions Pub/Sub para una corrida de prueba del pipeline de archivos.

Requerido:
  RUN_ID      Identificador de corrida esperado por los Cloud Run jobs.

Opcional:
  PROJECT_ID Proyecto GCP. Por defecto usa `gcloud config get-value project`.
  USER_ID    Identificador de usuario esperado por los jobs. Default: user-001

Ejemplo:
  PROJECT_ID=ldd-dev USER_ID=user-001 \
    RUN_ID=86ca6e73-ea37-4c1f-812d-7b71dcb771bb \
    bash Cloud/Pruebas/crear_suscripciones_pubsub.sh

Subscriptions creadas:
  pii-route-pdf-${USER_ID}-${RUN_ID} -> TOPIC_PDF / pii-pdf
  pii-route-doc-${USER_ID}-${RUN_ID} -> TOPIC_DOC / pii-docs
  pii-route-ocr-${USER_ID}-${RUN_ID} -> TOPIC_OCR / pii-ocr
  pii-entity-${USER_ID}-${RUN_ID}    -> TOPIC_PII_ENTITIES / pii-entities
  pii-text-poison-${USER_ID}-${RUN_ID} -> TOPIC_TEXT_POISON / pii-text-poison
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "ERROR: gcloud es requerido y no se encontro en PATH." >&2
  exit 127
fi

PROJECT_ID="${PROJECT_ID:-}"
if [[ -z "${PROJECT_ID}" ]]; then
  PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
fi

if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
  echo "ERROR: define PROJECT_ID o configura un proyecto gcloud por defecto." >&2
  usage >&2
  exit 2
fi

USER_ID="${USER_ID:-user-001}"
RUN_ID="${RUN_ID:-}"

if [[ -z "${RUN_ID}" ]]; then
  echo "ERROR: define RUN_ID para esta corrida de prueba." >&2
  usage >&2
  exit 2
fi

ACK_DEADLINE_SECONDS="${ACK_DEADLINE_SECONDS:-600}"
MESSAGE_RETENTION_DURATION="${MESSAGE_RETENTION_DURATION:-1d}"
EXPIRATION_PERIOD="${EXPIRATION_PERIOD:-1d}"

TOPIC_PDF="${TOPIC_PDF:-projects/${PROJECT_ID}/topics/pii-pdf}"
TOPIC_DOC="${TOPIC_DOC:-projects/${PROJECT_ID}/topics/pii-docs}"
TOPIC_OCR="${TOPIC_OCR:-projects/${PROJECT_ID}/topics/pii-ocr}"
TOPIC_PII_ENTITIES="${TOPIC_PII_ENTITIES:-projects/${PROJECT_ID}/topics/pii-entities}"
TOPIC_TEXT_POISON="${TOPIC_TEXT_POISON:-projects/${PROJECT_ID}/topics/pii-text-poison}"

sanitize_resource_part() {
  local raw="$1"
  local sanitized
  sanitized="$(printf '%s' "${raw}" | LC_ALL=C tr -c 'A-Za-z0-9._~+%-' '-')"
  sanitized="${sanitized##-}"
  sanitized="${sanitized%%-}"
  if [[ -z "${sanitized}" ]]; then
    sanitized="value"
  fi
  printf '%s' "${sanitized}"
}

escape_filter_value() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '%s' "${value}"
}

USER_SUFFIX="${SUBSCRIPTION_USER_SUFFIX:-$(sanitize_resource_part "${USER_ID}")}"
RUN_SUFFIX="${SUBSCRIPTION_RUN_SUFFIX:-$(sanitize_resource_part "${RUN_ID}")}"

PDF_SUBSCRIPTION_NAME="${PDF_SUBSCRIPTION_NAME:-pii-route-pdf-${USER_SUFFIX}-${RUN_SUFFIX}}"
DOC_SUBSCRIPTION_NAME="${DOC_SUBSCRIPTION_NAME:-pii-route-doc-${USER_SUFFIX}-${RUN_SUFFIX}}"
OCR_SUBSCRIPTION_NAME="${OCR_SUBSCRIPTION_NAME:-pii-route-ocr-${USER_SUFFIX}-${RUN_SUFFIX}}"
ENTITY_SUBSCRIPTION_NAME="${ENTITY_SUBSCRIPTION_NAME:-pii-entity-${USER_SUFFIX}-${RUN_SUFFIX}}"
TEXT_POISON_SUBSCRIPTION_NAME="${TEXT_POISON_SUBSCRIPTION_NAME:-pii-text-poison-${USER_SUFFIX}-${RUN_SUFFIX}}"

USER_FILTER_VALUE="$(escape_filter_value "${USER_ID}")"
RUN_FILTER_VALUE="$(escape_filter_value "${RUN_ID}")"
MESSAGE_FILTER="attributes.user_id=\"${USER_FILTER_VALUE}\" AND attributes.run_id=\"${RUN_FILTER_VALUE}\""

create_subscription() {
  local subscription_name="$1"
  local topic_name="$2"
  local subscription_id="projects/${PROJECT_ID}/subscriptions/${subscription_name}"

  if gcloud pubsub subscriptions describe "${subscription_name}" \
    --project "${PROJECT_ID}" >/dev/null 2>&1; then
    echo "Ya existe: ${subscription_id}"
    return 0
  fi

  echo "Creando: ${subscription_id}"
  gcloud pubsub subscriptions create "${subscription_name}" \
    --project "${PROJECT_ID}" \
    --topic "${topic_name}" \
    --message-filter "${MESSAGE_FILTER}" \
    --ack-deadline "${ACK_DEADLINE_SECONDS}" \
    --message-retention-duration "${MESSAGE_RETENTION_DURATION}" \
    --expiration-period "${EXPIRATION_PERIOD}"
}

echo "Proyecto: ${PROJECT_ID}"
echo "Usuario: ${USER_ID}"
echo "Run: ${RUN_ID}"
echo "Filtro: ${MESSAGE_FILTER}"
echo

create_subscription "${PDF_SUBSCRIPTION_NAME}" "${TOPIC_PDF}"
create_subscription "${DOC_SUBSCRIPTION_NAME}" "${TOPIC_DOC}"
create_subscription "${OCR_SUBSCRIPTION_NAME}" "${TOPIC_OCR}"
create_subscription "${ENTITY_SUBSCRIPTION_NAME}" "${TOPIC_PII_ENTITIES}"
create_subscription "${TEXT_POISON_SUBSCRIPTION_NAME}" "${TOPIC_TEXT_POISON}"

PDF_SUBSCRIPTION_ID="projects/${PROJECT_ID}/subscriptions/${PDF_SUBSCRIPTION_NAME}"
DOC_SUBSCRIPTION_ID="projects/${PROJECT_ID}/subscriptions/${DOC_SUBSCRIPTION_NAME}"
OCR_SUBSCRIPTION_ID="projects/${PROJECT_ID}/subscriptions/${OCR_SUBSCRIPTION_NAME}"
ENTITY_SUBSCRIPTION_ID="projects/${PROJECT_ID}/subscriptions/${ENTITY_SUBSCRIPTION_NAME}"
TEXT_POISON_SUBSCRIPTION_ID="projects/${PROJECT_ID}/subscriptions/${TEXT_POISON_SUBSCRIPTION_NAME}"

cat <<EOF

Subscriptions listas:
  PDF:         ${PDF_SUBSCRIPTION_ID}
  Docs:        ${DOC_SUBSCRIPTION_ID}
  OCR:         ${OCR_SUBSCRIPTION_ID}
  Entity:      ${ENTITY_SUBSCRIPTION_ID}
  Text poison: ${TEXT_POISON_SUBSCRIPTION_ID}

Env vars para los siguientes pasos:
  PDF:
    export UPDATE_ENV_VARS="SUBSCRIPTION_ID=${PDF_SUBSCRIPTION_ID},EXPECTED_USER_ID=${USER_ID},EXPECTED_RUN_ID=${RUN_ID}"
  Docs:
    export UPDATE_ENV_VARS="SUBSCRIPTION_ID=${DOC_SUBSCRIPTION_ID},EXPECTED_USER_ID=${USER_ID},EXPECTED_RUN_ID=${RUN_ID}"
  OCR:
    export UPDATE_ENV_VARS="SUBSCRIPTION_ID=${OCR_SUBSCRIPTION_ID},EXPECTED_USER_ID=${USER_ID},EXPECTED_RUN_ID=${RUN_ID}"
  Entity:
    export UPDATE_ENV_VARS="SUBSCRIPTION_ID=${ENTITY_SUBSCRIPTION_ID},EXPECTED_USER_ID=${USER_ID},EXPECTED_RUN_ID=${RUN_ID}"
  Text poison:
    # Sin job consumidor en esta version; Visor usa esta sub para monitorear poison de la run.
    export TEXT_POISON_SUBSCRIPTION_ID="${TEXT_POISON_SUBSCRIPTION_ID}"
EOF
