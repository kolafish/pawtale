#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."
if [[ "$#" -gt 1 || ( "$#" -eq 1 && "$1" != "--colima" ) ]]; then
    echo "Usage: $0 [--colima]" >&2
    exit 2
fi

pdf_python=(uv run --quiet --no-project --with-requirements e2e/memoir/requirements.txt python)
if [[ -n "${PAWTALE_E2E_PDF_PYTHON:-}" ]]; then
    pdf_python=("$PAWTALE_E2E_PDF_PYTHON")
fi
"${pdf_python[@]}" -c 'import pymupdf'

artifact_root="${PAWTALE_E2E_ARTIFACT_ROOT:-$PWD/output/pdf/memoir-e2e}"
mkdir -p "$artifact_root"
artifact_dir="$(mktemp -d "$artifact_root/run-$(date +%Y%m%d-%H%M%S)-XXXXXX")"
artifact_dir="$(cd "$artifact_dir" && pwd)"
echo "Artifacts: $artifact_dir"

test_args=(tests/manual/memoir_print_journey.py -x -s --tb=short)
status=0
if [[ "${1:-}" == "--colima" ]]; then
    helper="${PAWTALE_LOCAL_TEST_HELPER:-$HOME/.codex/skills/pawtale-local-backend-tests/scripts/run_tests.py}"
    remote_temp="/tmp/pawtale-memoir-${artifact_dir##*/}"
    python3 "$helper" run --repo "$PWD" -- "${test_args[@]}" "--basetemp=$remote_temp" 2>&1 | tee "$artifact_dir/backend.log" || status=$?
    if [[ "$status" != 75 && "$status" != 130 ]]; then
        docker=(docker --context colima-pawtale-test)
        if "${docker[@]}" exec pawtale-test-runner test -d "$remote_temp/artifacts"; then
            "${docker[@]}" cp "pawtale-test-runner:$remote_temp/artifacts/." "$artifact_dir/"
        fi
        "${docker[@]}" exec pawtale-test-runner rm -rf -- "$remote_temp"
    fi
else
    : "${TEST_POSTGRES_DATABASE_URL:?Set TEST_POSTGRES_DATABASE_URL to an isolated ParadeDB test database}"
    local_temp="$(mktemp -d)"
    trap 'rm -rf -- "$local_temp"' EXIT
    bash scripts/test-backend.sh "${test_args[@]}" "--basetemp=$local_temp/pytest" 2>&1 | tee "$artifact_dir/backend.log" || status=$?
    if [[ -d "$local_temp/pytest/artifacts" ]]; then
        cp -R "$local_temp/pytest/artifacts/." "$artifact_dir/"
    fi
fi

audit_status=0
if [[ "$status" -eq 0 || -f "$artifact_dir/before-correction.pdf" || -f "$artifact_dir/final.pdf" ]]; then
    "${pdf_python[@]}" e2e/memoir/check_print_pdf.py "$artifact_dir" || audit_status=$?
fi
echo "Artifacts: $artifact_dir"
if [[ "$status" -ne 0 ]]; then
    exit "$status"
fi
exit "$audit_status"
