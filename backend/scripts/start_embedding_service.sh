#!/usr/bin/env bash
set -euo pipefail

backend_dir="$(cd "$(dirname "$0")/.." && pwd)"
runtime_dir="${PAWTALE_EMBEDDING_RUNTIME_DIR:-/opt/pawtale/embedding-service}"
dependencies_dir="${PAWTALE_EMBEDDING_DEPENDENCIES_DIR:-${runtime_dir}/.pythondeps}"

export PYTHONPATH="${dependencies_dir}:${backend_dir}"
export PAWTALE_EMBEDDING_MODEL_PATH="${PAWTALE_EMBEDDING_MODEL_PATH:-${runtime_dir}/models/bge-small-zh-v1.5}"

exec python3 -m uvicorn embedding_service.app:app \
  --host 127.0.0.1 \
  --port "${PAWTALE_EMBEDDING_PORT:-8102}"
