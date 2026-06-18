#!/bin/bash

set -e

NETWORK_NAME="screener-mcp-network"
VOLUME_NAME="screener-mcp-data"
CONTAINER_NAME="screener-mcp-server"
IMAGE_NAME="screener-mcp"

echo "Creating network if not exists..."

docker network inspect "$NETWORK_NAME" >/dev/null 2>&1 || \
docker network create "$NETWORK_NAME"

echo "Creating volume if not exists..."

docker volume inspect "$VOLUME_NAME" >/dev/null 2>&1 || \
docker volume create "$VOLUME_NAME"

if [ ! -f ".env" ]; then
    echo ".env not found, creating from .env.example"
    cp .env.example .env
    echo ".env creating from .env.example"
fi

echo "Building image..."

docker build -t "$IMAGE_NAME" .

echo "Removing old container if exists..."

docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

echo "Starting container..."

docker compose up -d --build

echo ""
echo "Container started successfully"
echo ""

docker ps