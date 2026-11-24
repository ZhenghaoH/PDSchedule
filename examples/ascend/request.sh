#!/usr/bin/env bash
# Send one real generation request through the Controller.
#
#   bash examples/ascend/request.sh ["prompt"] [max_tokens]
#
# The Controller is the only entry point: no Gateway or vLLM address is used
# here, so a successful answer means the whole chain was exercised.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${ASCEND_CONFIG:-$HERE/config.env}"

if [ ! -f "$CONFIG" ]; then
    echo "config not found: $CONFIG" >&2
    echo "cp $HERE/config.example.env $CONFIG   # then edit it" >&2
    exit 2
fi

# shellcheck disable=SC1090
. "$CONFIG"

PROMPT="${1:-Introduce yourself in one sentence.}"
MAX_TOKENS="${2:-64}"

if [ -z "${CONTROLLER_HOST:-}" ] || [ -z "${CONTROLLER_PORT:-}" ]; then
    echo "CONTROLLER_HOST / CONTROLLER_PORT are required in config" >&2
    exit 2
fi

PAYLOAD="$(python3 - "$MODEL_NAME" "$PROMPT" "$MAX_TOKENS" <<'PY'
import json
import sys

model, prompt, max_tokens = sys.argv[1], sys.argv[2], int(sys.argv[3])
print(json.dumps({
    "model": model,
    "messages": [{"role": "user", "content": prompt}],
    "max_tokens": max_tokens,
    "stream": True,
}))
PY
)"

echo ">>> POST http://${CONTROLLER_HOST}:${CONTROLLER_PORT}/v1/chat/completions"
curl -sS -N "http://${CONTROLLER_HOST}:${CONTROLLER_PORT}/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d "$PAYLOAD"
echo
