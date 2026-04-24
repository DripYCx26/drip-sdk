#!/usr/bin/env bash
set -euo pipefail

curl -X POST "${DRIP_API_BASE:-https://api.drippay.dev/v1}/customers" \
  -H "Authorization: Bearer ${DRIP_API_KEY:?set DRIP_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "externalCustomerId": "user_123"
  }'
