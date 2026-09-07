#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/../backend"

venv/bin/ruff check app embedding_service tests scripts
if [ "$#" -eq 0 ]; then
    set -- tests
fi
PYTHONPATH=. venv/bin/python -m pytest -q "$@"
