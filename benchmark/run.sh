#!/usr/bin/env bash
# Run the Relora load test.
# Ensures the stack is up with the benchmark rate-limit override before testing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "Starting stack with benchmark override..."
docker-compose \
  -f "$ROOT/docker-compose.yml" \
  -f "$ROOT/docker-compose.benchmark.yml" \
  up -d --wait

echo "Stack healthy. Running benchmark..."
python "$SCRIPT_DIR/loadtest.py" "$@"
