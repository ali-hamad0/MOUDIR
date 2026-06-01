#!/bin/bash
# Seeds Vault dev mode with placeholder secrets.
# Run this once after `docker compose up` brings vault online.
set -e

export VAULT_ADDR=http://localhost:8200
export VAULT_TOKEN=root

vault kv put secret/modir/llm gemini_api_key="placeholder-rotate-before-prod"
vault kv put secret/modir/minio access_key="modir-access" secret_key="modir-secret-changeme"

echo "Vault seeded with placeholder secrets."
echo "Replace placeholders before production!"
