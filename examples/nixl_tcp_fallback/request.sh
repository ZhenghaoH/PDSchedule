#!/usr/bin/env bash
set -euo pipefail

PROXY_HOST="${PROXY_HOST:-127.0.0.1}"
PROXY_PORT="${PROXY_PORT:-8000}"
MODEL_NAME="${MODEL_NAME:-${MODEL_PATH:-/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B}}"
PROMPT="${PROMPT:-Explain disaggregated prefilling in one short paragraph.}"
MAX_TOKENS="${MAX_TOKENS:-64}"

echo "Sending request to http://${PROXY_HOST}:${PROXY_PORT}/v1/completions"

curl -sS "http://${PROXY_HOST}:${PROXY_PORT}/v1/completions" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"${MODEL_NAME}\",
    \"prompt\": \"${PROMPT}\",
    \"max_tokens\": ${MAX_TOKENS},
    \"temperature\": 0
  }"

echo
