#!/usr/bin/env bash
set -euo pipefail

MODEL_PATH="${MODEL_PATH:-/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B}"
HOST_IP="${HOST_IP:-0.0.0.0}"
DECODE_PORT="${DECODE_PORT:-8200}"
DECODE_SIDE_CHANNEL_HOST="${DECODE_SIDE_CHANNEL_HOST:-${HOST_IP}}"
DECODE_SIDE_CHANNEL_PORT="${DECODE_SIDE_CHANNEL_PORT:-5558}"
GPU="${GPU:-1}"
TP_SIZE="${TP_SIZE:-1}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-4096}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.8}"
VLLM_API_MODULE="${VLLM_API_MODULE:-vllm.entrypoints.openai.api_server}"

export CUDA_VISIBLE_DEVICES="${GPU}"
export VLLM_USE_V1="${VLLM_USE_V1:-1}"
export PYTHONHASHSEED="${PYTHONHASHSEED:-0}"
export VLLM_ENABLE_V1_MULTIPROCESSING="${VLLM_ENABLE_V1_MULTIPROCESSING:-1}"
export VLLM_WORKER_MULTIPROC_METHOD="${VLLM_WORKER_MULTIPROC_METHOD:-spawn}"
export UCX_TLS="${UCX_TLS:-tcp,cuda_copy}"
export VLLM_NIXL_SIDE_CHANNEL_HOST="${DECODE_SIDE_CHANNEL_HOST}"
export VLLM_NIXL_SIDE_CHANNEL_PORT="${DECODE_SIDE_CHANNEL_PORT}"

KV_TRANSFER_CONFIG="${KV_TRANSFER_CONFIG:-{\"kv_connector\":\"NixlConnector\",\"kv_role\":\"kv_both\",\"kv_connector_extra_config\":{\"backends\":[\"UCX\"]}}}"

echo "Starting Decode vLLM"
echo "  MODEL_PATH=${MODEL_PATH}"
echo "  HOST_IP=${HOST_IP}"
echo "  DECODE_PORT=${DECODE_PORT}"
echo "  CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES}"
echo "  UCX_TLS=${UCX_TLS}"
echo "  VLLM_NIXL_SIDE_CHANNEL_HOST=${VLLM_NIXL_SIDE_CHANNEL_HOST}"
echo "  VLLM_NIXL_SIDE_CHANNEL_PORT=${VLLM_NIXL_SIDE_CHANNEL_PORT}"
echo "  KV_TRANSFER_CONFIG=${KV_TRANSFER_CONFIG}"

python3 -m "${VLLM_API_MODULE}" \
  --model "${MODEL_PATH}" \
  --host "${HOST_IP}" \
  --port "${DECODE_PORT}" \
  --kv-transfer-config "${KV_TRANSFER_CONFIG}" \
  --tensor-parallel-size "${TP_SIZE}" \
  --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
  --max-model-len "${MAX_MODEL_LEN}" \
  --max-num-batched-tokens "${MAX_MODEL_LEN}" \
  --dtype auto \
  --enforce-eager \
  --disable-uvicorn-access-log
