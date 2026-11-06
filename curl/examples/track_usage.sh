#!/usr/bin/env bash
set -euo pipefail

curl -X POST "${DRIP_API_BASE:-https://api.drippay.dev/v1}/usage/internal" \
  -H "Authorization: Bearer ${DRIP_API_KEY:?set DRIP_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "customerId": "cus_123",
    "usageType": "tokens",
    "quantity": 1847,
    "idempotencyKey": "track_tokens_1847"
  }'
