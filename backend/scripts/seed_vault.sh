#!/bin/bash
# Seeds Vault dev mode with placeholder secrets.
# Run this once after `docker compose up` brings vault online.
set -e

export VAULT_ADDR=http://localhost:8200
export VAULT_TOKEN=root

vault kv put secret/modir/llm \
  gemini_api_key="${GEMINI_API_KEY:-placeholder-rotate-before-prod}" \
  langsmith_api_key="dev-langsmith-key-rotate-before-prod"
vault kv put secret/modir/minio access_key="modir-access" secret_key="modir-secret-changeme"
vault kv put secret/modir/auth jwt_secret="dev-jwt-secret-rotate-before-prod"
vault kv put secret/modir/mail api_key="dev-mail-key-rotate-before-prod"
# OCR (Phase 5): GCP service-account JSON. Placeholder in dev (stub mode ignores it);
# provide a real service-account JSON only when ocr_mode=cloud_vision.
vault kv put secret/modir/ocr service_account_json="{}"
# WhatsApp Business API (Phase 10). In dev mode the api_token is never used for real
# API calls; verify_token is checked only by the GET webhook handler challenge.
vault kv put secret/modir/whatsapp \
  api_token="${WHATSAPP_API_TOKEN:-placeholder-rotate-before-prod}" \
  verify_token="${WHATSAPP_VERIFY_TOKEN:-dev-verify-token}"
# Whish Pay merchant credentials (Phase 11). Placeholders are fine while
# WHISH_PAY_MODE=dev (simulated checkout); inject the real channel + secret
# issued by Whish (apps.whish.money) before flipping to live.
vault kv put secret/modir/whish \
  channel="${WHISH_PAY_CHANNEL:-placeholder-rotate-before-prod}" \
  secret="${WHISH_PAY_SECRET:-placeholder-rotate-before-prod}"

echo "Vault seeded."
echo "Set GEMINI_API_KEY, WHATSAPP_API_TOKEN, WHATSAPP_VERIFY_TOKEN env vars before running to inject real values."
echo "Replace remaining placeholders before production!"
