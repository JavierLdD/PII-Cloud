export PROJECT_ID="ldd-dev"
export USER_ID="Kerial"
export RUN_ID="$(uuidgen | tr 'A-Z' 'a-z')"

gcloud pubsub subscriptions create "pii-route-pdf-${USER_ID}-${RUN_ID}" \
  --project "$PROJECT_ID" \
  --topic "projects/$PROJECT_ID/topics/pii-pdf" \
  --message-filter "attributes.user_id=\"${USER_ID}\" AND attributes.run_id=\"${RUN_ID}\"" \
  --ack-deadline 600 \
  --message-retention-duration 1d \
  --expiration-period 1d

gcloud pubsub subscriptions create "pii-route-doc-${USER_ID}-${RUN_ID}" \
  --project "$PROJECT_ID" \
  --topic "projects/$PROJECT_ID/topics/pii-docs" \
  --message-filter "attributes.user_id=\"${USER_ID}\" AND attributes.run_id=\"${RUN_ID}\"" \
  --ack-deadline 600 \
  --message-retention-duration 1d \
  --expiration-period 1d

gcloud pubsub subscriptions create "pii-route-ocr-${USER_ID}-${RUN_ID}" \
  --project "$PROJECT_ID" \
  --topic "projects/$PROJECT_ID/topics/pii-ocr" \
  --message-filter "attributes.user_id=\"${USER_ID}\" AND attributes.run_id=\"${RUN_ID}\"" \
  --ack-deadline 600 \
  --message-retention-duration 1d \
  --expiration-period 1d

gcloud pubsub subscriptions create "pii-entity-${USER_ID}-${RUN_ID}" \
  --project "$PROJECT_ID" \
  --topic "projects/$PROJECT_ID/topics/pii-entities" \
  --message-filter "attributes.user_id=\"${USER_ID}\" AND attributes.run_id=\"${RUN_ID}\"" \
  --ack-deadline 600 \
  --message-retention-duration 1d \
  --expiration-period 1d

gcloud pubsub subscriptions create "pii-text-poison-${USER_ID}-${RUN_ID}" \
  --project "$PROJECT_ID" \
  --topic "projects/$PROJECT_ID/topics/pii-text-poison" \
  --message-filter "attributes.user_id=\"${USER_ID}\" AND attributes.run_id=\"${RUN_ID}\"" \
  --ack-deadline 600 \
  --message-retention-duration 1d \
  --expiration-period 1d
