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
        echo -e "\033[33m=== [0/11] Start Metadata Server ===\033[0m"
        mooncake_master --rpc_address 172.18.0.169 --rpc_port 50051 --metrics_port 9002 --enable_http_metadata_server=true --http_metadata_server_host=172.18.0.169 --http_metadata_server_port=8080
        ;;
    "p1")
        echo -e "\033[32m=== [1/11] Start Prefill vLLM 1 (GPU 0) ===\033[0m"
        cd /workspace/llm-stack/PDSchedule
        export CUDA_VISIBLE_DEVICES=0
        export PYTHONHASHSEED=0
        export VLLM_USE_FLASHINFER_SAMPLER=0
        export UCX_TLS=tcp,cuda_copy
        export UCX_NET_DEVICES=eno5
        export VLLM_NIXL_SIDE_CHANNEL_HOST=172.18.0.169
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
        --tensor_parallel_size=1 \
        --disable-uvicorn-access-log \
        --no-enable-prefix-caching \
        --attention-backend TRITON_ATTN
        ;;
    "p2")
        echo -e "\033[32m=== [2/11] Start Prefill vLLM 2 (GPU 1) ===\033[0m"
        cd /workspace/llm-stack/PDSchedule
        export CUDA_VISIBLE_DEVICES=1
        export PYTHONHASHSEED=0
        export VLLM_USE_FLASHINFER_SAMPLER=0
        export UCX_TLS=tcp,cuda_copy
        export UCX_NET_DEVICES=eno5
        export VLLM_NIXL_SIDE_CHANNEL_HOST=172.18.0.169
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
        --tensor_parallel_size=1 \
        --disable-uvicorn-access-log \
        --no-enable-prefix-caching \
        --attention-backend TRITON_ATTN
        ;;
    "d1")
        echo -e "\033[34m=== [3/11] Start Decode vLLM 1 (GPU 2) ===\033[0m"
        cd /workspace/llm-stack/PDSchedule
        export CUDA_VISIBLE_DEVICES=3
        export PYTHONHASHSEED=0
        export VLLM_USE_FLASHINFER_SAMPLER=0
        export UCX_TLS=tcp,cuda_copy
        export UCX_NET_DEVICES=eno5
        export VLLM_NIXL_SIDE_CHANNEL_HOST=172.18.0.169
        export VLLM_NIXL_SIDE_CHANNEL_PORT=5559
        python3 -m vllm.entrypoints.openai.api_server \
        --model $MODEL_PATH \
        --host 0.0.0.0 --port 8003 \
        --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_consumer","kv_load_failure_policy":"fail","kv_connector_extra_config":{"backends":["UCX"],"kv_lease_duration":120}}' \
        --gpu-memory-utilization 0.8 \
        --max-model-len 20000 \
        --dtype auto \
        --max-num-batched-tokens 4096 \
        --disable-hybrid-kv-cache-manager \
        --kv-cache-metrics \
        --tensor_parallel_size=1 \
        --disable-uvicorn-access-log \
        --attention-backend TRITON_ATTN
        ;;
    "d2")
        echo -e "\033[34m=== [4/11] Start Decode vLLM 2 (GPU 3) ===\033[0m"
        cd /workspace/llm-stack/PDSchedule
        export CUDA_VISIBLE_DEVICES=4
        export PYTHONHASHSEED=0
        export VLLM_USE_FLASHINFER_SAMPLER=0
        export UCX_TLS=tcp,cuda_copy
        export UCX_NET_DEVICES=eno5
        export VLLM_NIXL_SIDE_CHANNEL_HOST=172.18.0.169
        export VLLM_NIXL_SIDE_CHANNEL_PORT=5560
        python3 -m vllm.entrypoints.openai.api_server \
        --model $MODEL_PATH \
        --host 0.0.0.0 --port 8004 \
        --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_consumer","kv_load_failure_policy":"fail","kv_connector_extra_config":{"backends":["UCX"],"kv_lease_duration":120}}' \
        --gpu-memory-utilization 0.8 \
        --max-model-len 20000 \
        --dtype auto \
        --max-num-batched-tokens 4096 \
        --disable-hybrid-kv-cache-manager \
        --kv-cache-metrics \
        --tensor_parallel_size=1 \
        --disable-uvicorn-access-log \
        --attention-backend TRITON_ATTN
        ;;
    "s")
        wait_for_loading 10 "Scheduler"

        echo -e "\033[31m=== [5/11] Start Scheduler ===\033[0m"
        cd /workspace/llm-stack/PDSchedule/entrypoints
        python3 controller.py --host 172.18.0.170 --port 5000
        ;;
    "pd")
        wait_for_loading 50 "Decode Proxy"
        export MY_MEM_BANDWIDTH=1792
        export MY_MAX_DECODE_REQ_NUM=128
        echo -e "\033[35m=== [6/11] Start Decode Proxy ===\033[0m"
        cd /workspace/llm-stack/PDSchedule/entrypoints
        python3 gatewayD.py --host 172.18.0.169 --port 8082 --controller 172.18.0.170:5000 --decode 172.18.0.169:7003 172.18.0.169:7004 --decode-scheduler-strategy slo_aware_decode
        ;;
    "pp")
        wait_for_loading 52 "Prefill Proxy"

        echo -e "\033[36m=== [7/11] Start Prefill Proxy ===\033[0m"
        cd /workspace/llm-stack/PDSchedule/entrypoints
        export MY_MAX_PROMPT_THROUGHPUT=24976.1
        export MY_FLOPS=104.8
        python3 gatewayP.py --host 172.18.0.169 --port 8081 --controller 172.18.0.170:5000 --prefill 172.18.0.169:7001 172.18.0.169:7002 --prefill-scheduler-strategy slo_aware_prefill
        ;;
    "id1")
        wait_for_loading 55 "Decode Instance 1"

        echo -e "\033[36m=== [8/11] Start Decode Instance Proxy 1 ===\033[0m"
        cd /workspace/llm-stack/PDSchedule/entrypoints
        python3 instanceD.py --host 172.18.0.169 --port 7003 --vllm_port 8003 --gateway 172.18.0.169:8082 --side-channel-host 172.18.0.169 --side-channel-port 5559 --tp-size 1
        ;;
    "id2")
        wait_for_loading 55 "Decode Instance 2"

        echo -e "\033[36m=== [9/11] Start Decode Instance Proxy 2 ===\033[0m"
        cd /workspace/llm-stack/PDSchedule/entrypoints
        python3 instanceD.py --host 172.18.0.169 --port 7004 --vllm_port 8004 --gateway 172.18.0.169:8082 --side-channel-host 172.18.0.169 --side-channel-port 5560 --tp-size 1
        ;;
    "ip1")
        wait_for_loading 57 "Prefill Instance 1"

        echo -e "\033[36m=== [10/11] Start Prefill Instance Proxy 1 ===\033[0m"
        cd /workspace/llm-stack/PDSchedule/entrypoints
        python3 instanceP.py --host 172.18.0.169 --port 7001 --vllm_port 8001 --gateway 172.18.0.169:8081 --side-channel-host 172.18.0.169 --side-channel-port 5557 --tp-size 1
        ;;
    "ip2")
        wait_for_loading 57 "Prefill Instance 2"

        echo -e "\033[36m=== [11/11] Start Prefill Instance Proxy 2 ===\033[0m"
        cd /workspace/llm-stack/PDSchedule/entrypoints
        python3 instanceP.py --host 172.18.0.169 --port 7002 --vllm_port 8002 --gateway 172.18.0.169:8081 --side-channel-host 172.18.0.169 --side-channel-port 5558 --tp-size 1
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

launch_tab "3.Decode-vLLM-1" "d1"
sleep 0.5

launch_tab "4.Decode-vLLM-2" "d2"
sleep 0.5

# launch_tab "5.Controller" "s"
# sleep 0.5

launch_tab "6.Gateway-D" "pd"
sleep 0.5

launch_tab "7.Gateway-P" "pp"
sleep 0.5

launch_tab "8.Instance-D-1" "id1"
sleep 0.5

launch_tab "9.Instance-D-2" "id2"
sleep 0.5

launch_tab "10.Instance-P-1" "ip1"
sleep 0.5

launch_tab "11.Instance-P-2" "ip2"
sleep 0.5

echo ">>> All service tabs have been opened."
unset SUDO_PASS
