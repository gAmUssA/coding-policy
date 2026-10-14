#!/usr/bin/env bash
set -euo pipefail

ENVIRONMENT="${1:-staging}"
VERSION="${2:-latest}"

echo "Deploying version $VERSION to $ENVIRONMENT..."

if [ "$ENVIRONMENT" = "production" ]; then
    echo "Running pre-flight checks..."
    ./scripts/preflight.sh
fi

echo "Pulling image: app:$VERSION"
docker pull "app:$VERSION"

echo "Restarting service..."
systemctl restart app-service

echo "Deploy complete."
