#!/bin/bash
# Seeds Vault dev mode with placeholder secrets.
# Run this once after `docker compose up` brings vault online.
set -e

export VAULT_ADDR=http://localhost:8200
export VAULT_TOKEN=root

vault kv put secret/modir/llm \
  gemini_api_key="placeholder-rotate-before-prod" \
  langsmith_api_key="dev-langsmith-key-rotate-before-prod"
vault kv put secret/modir/minio access_key="modir-access" secret_key="modir-secret-changeme"
vault kv put secret/modir/auth jwt_secret="dev-jwt-secret-rotate-before-prod"
vault kv put secret/modir/mail api_key="dev-mail-key-rotate-before-prod"
# OCR (Phase 5): GCP service-account JSON. Placeholder in dev (stub mode ignores it);
# provide a real service-account JSON only when ocr_mode=cloud_vision.
vault kv put secret/modir/ocr service_account_json="{}"

echo "Vault seeded with placeholder secrets."
echo "Replace placeholders before production!"
