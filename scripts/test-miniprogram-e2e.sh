#!/bin/bash
set -euo pipefail

repository_root="$(cd "$(dirname "$0")/.." && pwd)"
target="${1:-devtools}"

install_dependencies() {
  npm --prefix "$repository_root/miniprogram" ci
  npm --prefix "$repository_root/e2e/miniprogram" ci
}

case "$target" in
  devtools)
    install_dependencies
    npm --prefix "$repository_root/e2e/miniprogram" run test:devtools
    ;;
  voice)
    install_dependencies
    npm --prefix "$repository_root/e2e/miniprogram" run test:voice
    ;;
  ios)
    install_dependencies
    export PAWTALE_E2E_PROJECT_PATH="${PAWTALE_E2E_PROJECT_PATH:-$repository_root/miniprogram}"
    npm --prefix "$repository_root/e2e/miniprogram" run test:ios
    ;;
  *)
    echo "暂不支持的目标：$target（当前支持：devtools、voice、ios）" >&2
    exit 2
    ;;
esac
