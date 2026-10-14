#!/usr/bin/env bash
set -euo pipefail

SERVICE="${1:-}"
if [ -z "$SERVICE" ]; then
    echo "Usage: $0 <service-name>" >&2
    exit 1
fi

echo "Rotating API keys for $SERVICE..."

NEW_KEY=$(openssl rand -hex 32)

# Store the new key
vault kv put "secrets/$SERVICE" api_key="$NEW_KEY"

if [ "$SERVICE" = "payment-gateway" ]; then
    echo "Notifying payment team of rotation..."
    ./scripts/notify-team.sh payment "$NEW_KEY"

echo "Key rotation complete for $SERVICE."
