#!/bin/bash
set -euo pipefail

repository_root="$(cd "$(dirname "$0")/.." && pwd)"
target="${1:-devtools}"

case "$target" in
  devtools)
    npm --prefix "$repository_root/miniprogram" ci
    npm --prefix "$repository_root/e2e/miniprogram" ci
    npm --prefix "$repository_root/e2e/miniprogram" run test:devtools
    ;;
  *)
    echo "暂不支持的目标：$target（当前支持：devtools）" >&2
    exit 2
    ;;
esac
