#!/usr/bin/env bash
set -euo pipefail

MODEL_PATH="${MODEL_PATH:-/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B}"
PREFILL_HOST="${PREFILL_HOST:-127.0.0.1}"
PREFILL_PORT="${PREFILL_PORT:-8100}"
DECODE_HOST="${DECODE_HOST:-127.0.0.1}"
DECODE_PORT="${DECODE_PORT:-8200}"
PROXY_HOST="${PROXY_HOST:-0.0.0.0}"
PROXY_PORT="${PROXY_PORT:-8000}"

find_proxy_script() {
  if [[ -n "${PROXY_SCRIPT:-}" ]]; then
    echo "${PROXY_SCRIPT}"
    return
  fi

  local candidates=(
    "${VLLM_REPO:-}/tests/v1/kv_connector/nixl_integration/toy_proxy_server.py"
    "/workspace/llm-stack/vllm/tests/v1/kv_connector/nixl_integration/toy_proxy_server.py"
    "/workspace/vllm/tests/v1/kv_connector/nixl_integration/toy_proxy_server.py"
    "/workspace/llm-stack/workspace_HZH/vllm/tests/v1/kv_connector/nixl_integration/toy_proxy_server.py"
  )

  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -n "${candidate}" && -f "${candidate}" ]]; then
      echo "${candidate}"
      return
    fi
  done

  python3 - <<'PY'
from pathlib import Path
try:
    import vllm
except Exception:
    raise SystemExit(1)

roots = [Path(vllm.__file__).resolve().parents[i] for i in range(min(4, len(Path(vllm.__file__).resolve().parents)))]
for root in roots:
    candidate = root / "tests" / "v1" / "kv_connector" / "nixl_integration" / "toy_proxy_server.py"
    if candidate.is_file():
        print(candidate)
        raise SystemExit(0)
raise SystemExit(1)
PY
}

PROXY_SCRIPT_PATH="$(find_proxy_script || true)"
if [[ -z "${PROXY_SCRIPT_PATH}" || ! -f "${PROXY_SCRIPT_PATH}" ]]; then
  cat >&2 <<'EOF'
ERROR: cannot find vLLM NIXL toy proxy.

Set one of these before running:
  export VLLM_REPO=/path/to/vllm
  export PROXY_SCRIPT=/path/to/toy_proxy_server.py

Expected file:
  tests/v1/kv_connector/nixl_integration/toy_proxy_server.py
EOF
  exit 1
fi

echo "Starting NIXL toy proxy"
echo "  PROXY_SCRIPT=${PROXY_SCRIPT_PATH}"
echo "  MODEL_PATH=${MODEL_PATH}"
echo "  Prefill=${PREFILL_HOST}:${PREFILL_PORT}"
echo "  Decode=${DECODE_HOST}:${DECODE_PORT}"
echo "  Proxy=${PROXY_HOST}:${PROXY_PORT}"

python3 "${PROXY_SCRIPT_PATH}" \
  --prefiller-hosts "${PREFILL_HOST}" \
  --prefiller-ports "${PREFILL_PORT}" \
  --decoder-hosts "${DECODE_HOST}" \
  --decoder-ports "${DECODE_PORT}" \
  --host "${PROXY_HOST}" \
  --port "${PROXY_PORT}"
