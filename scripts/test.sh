#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/../backend"
source venv/bin/activate
PYTHONPATH=. pytest -p no:capture -v "$@"
