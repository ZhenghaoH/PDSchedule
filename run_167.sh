#!/bin/bash

# ================= Config =================
CONTAINER="new_env"
# ==========================================

echo ">>> Please enter sudo password:"
read -s SUDO_PASS
echo ">>> Password captured."

if ! echo "$SUDO_PASS" | sudo -S -k true 2>/dev/null; then
    echo "ERROR: wrong sudo password, exiting."
    exit 1
fi

echo ">>> Initializing container runtime script..."

cat << 'EOF' > .internal_script.sh
#!/bin/bash
MODE=$1
MODEL_PATH="/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"

wait_for_loading() {
    SECONDS=$1
    NAME=$2
    echo -e "\033[33m>>> [$NAME] waiting for upstream services..."
    echo -e ">>> Sleeping $SECONDS seconds before starting service.\033[0m"

    for ((i=SECONDS; i>0; i--)); do
        echo -ne "Countdown: $i seconds  \r"
        sleep 1
    done
    echo -e "\n>>> Wait finished, starting service."
}

case "$MODE" in
    "meta")
        echo -e "\033[33m=== [0/6] Start Metadata Server ===\033[0m"
        mooncake_master --rpc_address 172.18.0.169 --rpc_port 50051 --metrics_port 9002 --enable_http_metadata_server=true --http_metadata_server_host=172.18.0.169 --http_metadata_server_port=8080
        ;;
    "p1")
        echo -e "\033[32m=== [1/6] Start Prefill vLLM 1 (GPU 0) ===\033[0m"
        cd /workspace/llm-stack/P2P_FlexPD
        export CUDA_VISIBLE_DEVICES=0
        export PYTHONHASHSEED=0
        export VLLM_USE_FLASHINFER_SAMPLER=0
        export UCX_TLS=tcp,cuda_copy
        export UCX_NET_DEVICES=eno2
        export VLLM_NIXL_SIDE_CHANNEL_HOST=172.18.0.167
        export VLLM_NIXL_SIDE_CHANNEL_PORT=5557
        python3 -m vllm.entrypoints.openai.api_server \
        --model $MODEL_PATH \
        --host 0.0.0.0 --port 8001 \
        --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_producer","kv_load_failure_policy":"fail","kv_connector_extra_config":{"backends":["UCX"],"kv_lease_duration":120}}' \
        --gpu-memory-utilization 0.8 \
        --max-model-len 20000 \
        --dtype auto \
        --max-num-batched-tokens 4096 \
        --disable-hybrid-kv-cache-manager \
        --kv-cache-metrics \
        --no-enable-prefix-caching \
        --tensor_parallel_size=1 \
        --disable-uvicorn-access-log \
        --attention-backend TRITON_ATTN
        ;;
    "p2")
        echo -e "\033[32m=== [2/6] Start Prefill vLLM 2 (GPU 1) ===\033[0m"
        cd /workspace/llm-stack/P2P_FlexPD
        export CUDA_VISIBLE_DEVICES=1
        export PYTHONHASHSEED=0
        export VLLM_USE_FLASHINFER_SAMPLER=0
        export UCX_TLS=tcp,cuda_copy
        export UCX_NET_DEVICES=eno2
        export VLLM_NIXL_SIDE_CHANNEL_HOST=172.18.0.167
        export VLLM_NIXL_SIDE_CHANNEL_PORT=5558
        python3 -m vllm.entrypoints.openai.api_server \
        --model $MODEL_PATH \
        --host 0.0.0.0 --port 8002 \
        --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_producer","kv_load_failure_policy":"fail","kv_connector_extra_config":{"backends":["UCX"],"kv_lease_duration":120}}' \
        --gpu-memory-utilization 0.8 \
        --max-model-len 20000 \
        --dtype auto \
        --max-num-batched-tokens 4096 \
        --disable-hybrid-kv-cache-manager \
        --kv-cache-metrics \
        --no-enable-prefix-caching \
        --tensor_parallel_size=1 \
        --disable-uvicorn-access-log \
        --attention-backend TRITON_ATTN
        ;;
    "s")
        wait_for_loading 10 "Scheduler"

        echo -e "\033[31m=== [3/6] Start Scheduler ===\033[0m"
        cd /workspace/llm-stack/P2P_FlexPD/entrypoints
        python3 controller.py --host 172.18.0.170 --port 5000
        ;;
    "pp")
        wait_for_loading 52 "Prefill Proxy"

        echo -e "\033[36m=== [4/6] Start Prefill Proxy ===\033[0m"
        cd /workspace/llm-stack/P2P_FlexPD/entrypoints
        export MY_MAX_PROMPT_THROUGHPUT=5646.747
        export MY_FLOPS=35.58
        python3 gatewayP.py --host 172.18.0.167 --port 8081 --controller 172.18.0.170:5000 --prefill 172.18.0.167:7001 172.18.0.167:7002 --prefill-scheduler-strategy pso_new
        ;;
    "ip1")
        wait_for_loading 57 "Prefill Instance 1"

        echo -e "\033[36m=== [5/6] Start Prefill Instance Proxy 1 ===\033[0m"
        cd /workspace/llm-stack/P2P_FlexPD/entrypoints
        python3 instanceP.py --host 172.18.0.167 --port 7001 --vllm_port 8001 --gateway 172.18.0.167:8081 --side-channel-host 172.18.0.167 --side-channel-port 5557 --tp-size 1
        ;;
    "ip2")
        wait_for_loading 57 "Prefill Instance 2"

        echo -e "\033[36m=== [6/6] Start Prefill Instance Proxy 2 ===\033[0m"
        cd /workspace/llm-stack/P2P_FlexPD/entrypoints
        python3 instanceP.py --host 172.18.0.167 --port 7002 --vllm_port 8002 --gateway 172.18.0.167:8081 --side-channel-host 172.18.0.167 --side-channel-port 5558 --tp-size 1
        ;;
esac
echo "--------------------------------------------------------"
exec /bin/bash
EOF

chmod +x .internal_script.sh
echo "$SUDO_PASS" | sudo -S cp .internal_script.sh temp_script.sh 2>/dev/null
echo "$SUDO_PASS" | sudo -S docker cp .internal_script.sh $CONTAINER:/usr/local/bin/run_flexpd
rm .internal_script.sh

launch_tab() {
    TITLE=$1
    ARG=$2
    echo "Opening tab: $TITLE ..."

    CMD="echo '$SUDO_PASS' | sudo -S bash -c \"docker exec -it $CONTAINER /bin/bash -c '/usr/local/bin/run_flexpd $ARG' < /dev/tty\"; exec bash"

    gnome-terminal --tab --title="$TITLE" -- bash -c "$CMD"
}

# launch_tab "0.Meta" "meta"
sleep 1

launch_tab "1.Prefill-vLLM-1" "p1"
sleep 0.5

launch_tab "2.Prefill-vLLM-2" "p2"
sleep 0.5

# launch_tab "3.Controller" "s"
# sleep 0.5

launch_tab "4.Gateway-P" "pp"
sleep 0.5

launch_tab "5.Instance-P-1" "ip1"
sleep 0.5

launch_tab "6.Instance-P-2" "ip2"
sleep 0.5

echo ">>> All Prefill-only service tabs have been opened."
unset SUDO_PASS
