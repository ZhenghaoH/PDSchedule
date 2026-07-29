#!/bin/bash


#  ⚠️目前测试这个脚本会不能在同时运行3个以上vllm实例⚠️


# ================= 配置 =================
CONTAINER="test"
# =======================================

# 0. 提前询问 sudo 密码
echo ">>> 请输入 sudo 密码（输入时不可见，回车确认）："
read -s SUDO_PASS
echo ">>> 密码已捕获。"

# 简单验证密码
if ! echo "$SUDO_PASS" | sudo -S -k true 2>/dev/null; then
    echo "❌ 密码错误，退出。"
    exit 1
fi

echo ">>> 正在初始化容器环境..."

# 1. 生成容器内部的统一启动器
cat << 'EOF' > .internal_script.sh
#!/bin/bash
MODE=$1
CONFIG_PATH="/workspace/llm-stack/workspace_HZH/FlexPD/lmcache.yaml"
MODEL_PATH="/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"
REDIS_URL="redis://127.0.0.1:6378"

# 定义一个通用的倒计时函数，方便复用
wait_for_loading() {
    SECONDS=$1
    NAME=$2
    echo -e "\033[33m>>> [$NAME] 检测到前序服务正在启动..."
    echo -e ">>> 为了等待 P/D 实例加载模型，将暂停 $SECONDS 秒...\033[0m"

    for ((i=SECONDS; i>0; i--)); do
        # -ne 不换行，\r 回到行首，实现原地刷新倒计时效果
        echo -ne "正在倒计时: $i 秒   \r"
        sleep 1
    done
    echo -e "\n>>> 等待结束，开始启动服务！"
}

case "$MODE" in
    "meta")
        echo -e "\033[33m=== [0/11] 启动 Metadata Server ===\033[0m"
        mooncake_master --rpc_address 172.18.0.170 --rpc_port 50051 --metrics_port 9002 --enable_http_metadata_server=true --http_metadata_server_host=172.18.0.170 --http_metadata_server_port=8080
        ;;
    "p1")
        echo -e "\033[32m=== [1/11] 启动 Prefill 实例 (GPU 0) ===\033[0m"
        cd /workspace/llm-stack/workspace_HZH/FlexPD
        export VLLM_LOGGING_LEVEL=DEBUG
        export CUDA_VISIBLE_DEVICES=0,1
        export PYTHONHASHSEED=0
        export LMCACHE_REMOTE_URL=$REDIS_URL
        export LMCACHE_REMOTE_SERDE="cachegen"
        export LMCACHE_CONFIG_FILE=$CONFIG_PATH
        python3 -m vllm.entrypoints.openai.api_server \
        --model $MODEL_PATH \
        --host 0.0.0.0 --port 8001 \
        --kv-transfer-config '{"kv_connector":"LMCacheConnectorV1","kv_role":"kv_producer"}' \
        --gpu-memory-utilization 0.8 \
        --max-model-len 4096 \
        --max-num-seqs 8 \
        --dtype auto \
        --max-num-batched-tokens 8192 \
        --kv-offloading-backend lmcache \
        --kv-offloading-size 16 \
        --disable-hybrid-kv-cache-manager \
        --kv-cache-metrics \
        --disable-uvicorn-access-log

        ;;
    "p2")
        echo -e "\033[32m=== [2/11] 启动 Prefill 实例 (GPU 1) ===\033[0m"
        cd /workspace/llm-stack/workspace_HZH/FlexPD
        export VLLM_LOGGING_LEVEL=DEBUG
        export CUDA_VISIBLE_DEVICES=2,3
        export PYTHONHASHSEED=0
        export LMCACHE_REMOTE_URL=$REDIS_URL
        export LMCACHE_REMOTE_SERDE="cachegen"
        export LMCACHE_CONFIG_FILE=$CONFIG_PATH
        python3 -m vllm.entrypoints.openai.api_server \
        --model $MODEL_PATH \
        --host 0.0.0.0 --port 8002 \
        --kv-transfer-config '{"kv_connector":"LMCacheConnectorV1","kv_role":"kv_producer"}' \
        --gpu-memory-utilization 0.8 \
        --max-model-len 4096 \
        --max-num-seqs 8 \
        --dtype auto \
        --max-num-batched-tokens 8192 \
        --kv-offloading-backend lmcache \
        --kv-offloading-size 16 \
        --disable-hybrid-kv-cache-manager \
        --kv-cache-metrics \
        --disable-uvicorn-access-log

        ;;
    "d1")
        echo -e "\033[34m=== [3/11] 启动 Decode 实例 (GPU 2) ===\033[0m"
        cd /workspace/llm-stack/workspace_HZH/FlexPD
        export VLLM_LOGGING_LEVEL=DEBUG
        export CUDA_VISIBLE_DEVICES=4,5
        export PYTHONHASHSEED=0
        export LMCACHE_REMOTE_URL=$REDIS_URL
        export LMCACHE_REMOTE_SERDE="cachegen"
        export LMCACHE_CONFIG_FILE=$CONFIG_PATH
        python3 -m vllm.entrypoints.openai.api_server \
        --model $MODEL_PATH \
        --host 0.0.0.0 --port 8003 \
        --kv-transfer-config '{"kv_connector":"LMCacheConnectorV1","kv_role":"kv_consumer"}' \
        --gpu-memory-utilization 0.8 \
        --max-model-len 4096 \
        --dtype auto \
        --max-num-batched-tokens 8192 \
        --kv-offloading-backend lmcache \
        --kv-offloading-size 16 \
        --disable-hybrid-kv-cache-manager \
        --kv-cache-metrics \
	--disable-uvicorn-access-log
	
        ;;
     "d2")
         echo -e "\033[34m=== [4/11] 启动 Decode 实例 (GPU 3) ===\033[0m"
         cd /workspace/llm-stack/workspace_HZH/FlexPD
         export VLLM_LOGGING_LEVEL=DEBUG
         export CUDA_VISIBLE_DEVICES=3
         export PYTHONHASHSEED=0
         export LMCACHE_REMOTE_URL=$REDIS_URL
         export LMCACHE_REMOTE_SERDE="cachegen"
         export LMCACHE_CONFIG_FILE=$CONFIG_PATH
         python3 -m vllm.entrypoints.openai.api_server \
         --model $MODEL_PATH \
         --host 0.0.0.0 --port 8004 \
         --kv-transfer-config '{"kv_connector":"LMCacheConnectorV1","kv_role":"kv_consumer"}' \
         --gpu-memory-utilization 0.8 \
         --max-model-len 4096 \
         --dtype auto \
         --max-num-batched-tokens 8192 \
         --kv-offloading-backend lmcache \
         --kv-offloading-size 16 \
         --disable-hybrid-kv-cache-manager \
	 --disable-uvicorn-access-log
         ;;
    "s")
        wait_for_loading 10 "Scheduler"

        echo -e "\033[31m=== [5/11] 启动 Scheduler ===\033[0m"
        cd /workspace/llm-stack/workspace_HZH/FlexPD/entrypoints
        export CUDA_VISIBLE_DEVICES=6
        python3 controller.py
        ;;
    "pd")
        wait_for_loading 40 "Decode Proxy"

        echo -e "\033[35m=== [6/11] 启动 Decode Proxy ===\033[0m"
        cd /workspace/llm-stack/workspace_HZH/FlexPD/entrypoints
        python3 gatewayD.py --decode 172.18.0.170:7003 172.18.0.170:7004
        ;;
    "pp")
        wait_for_loading 42 "Prefill Proxy"

        echo -e "\033[36m=== [7/11] 启动 Prefill Proxy ===\033[0m"
        cd /workspace/llm-stack/workspace_HZH/FlexPD/entrypoints
        python3 gatewayP.py --prefill 172.18.0.170:7001 172.18.0.170:7002
        ;;
    "id1")
        wait_for_loading 45 "Decode Instance"

        echo -e "\033[36m=== [8/11] 启动 Decode Instance ===\033[0m"
        cd /workspace/llm-stack/workspace_HZH/FlexPD/entrypoints
        python3 instanceD.py --port 7003 --vllm_port 8003
        ;;
    "id2")
        wait_for_loading 45 "Decode Instance"

        echo -e "\033[36m=== [9/11] 启动 Decode Instance ===\033[0m"
        cd /workspace/llm-stack/workspace_HZH/FlexPD/entrypoints
        python3 instanceD.py --port 7004 --vllm_port 8004
        ;;
    "ip1")
        wait_for_loading 47 "Prefill Instance"

        echo -e "\033[36m=== [10/11] 启动 Prefill Instance ===\033[0m"
        cd /workspace/llm-stack/workspace_HZH/FlexPD/entrypoints
        python3 instanceP.py --port 7001 --vllm_port 8001
        ;;
    "ip2")
        wait_for_loading 47 "Prefill Instance"

        echo -e "\033[36m=== [11/11] 启动 Prefill Instance ===\033[0m"
        cd /workspace/llm-stack/workspace_HZH/FlexPD/entrypoints
        python3 instanceP.py --port 7002 --vllm_port 8002
        ;;
esac
echo "--------------------------------------------------------"
exec /bin/bash
EOF

# 2. 注入脚本到容器
chmod +x .internal_script.sh
echo "$SUDO_PASS" | sudo -S cp .internal_script.sh temp_script.sh 2>/dev/null
echo "$SUDO_PASS" | sudo -S docker cp .internal_script.sh $CONTAINER:/usr/local/bin/run_flexpd
rm .internal_script.sh

# 3. 逐个启动终端标签页
launch_tab() {
    TITLE=$1
    ARG=$2
    echo "正在打开标签页: $TITLE ..."

    # 强制 Docker 使用 TTY 解决 input device is not a TTY 报错
    CMD="echo '$SUDO_PASS' | sudo -S bash -c \"docker exec -it $CONTAINER /bin/bash -c '/usr/local/bin/run_flexpd $ARG' < /dev/tty\"; exec bash"

    gnome-terminal --tab --title="$TITLE" -- bash -c "$CMD"
}

# --- 快速顺序打开所有窗口 ---
# 宿主机的 sleep 只是为了让 GNOME Terminal 标签页排序整齐，不需要很久

#launch_tab "0.Meta" "meta"
sleep 1

launch_tab "1A.Prefill" "p1"
sleep 0.5

launch_tab "1B.Prefill" "p2"
sleep 0.5

launch_tab "2A.Decode" "d1"
sleep 0.5

launch_tab "2B.Decode" "d2"
sleep 0.5

launch_tab "3.Controller" "s"
sleep 0.5

launch_tab "4.Gateway-D" "pd"
sleep 0.5

launch_tab "5.Gateway-P" "pp"
sleep 0.5

launch_tab "6A.Instance-P" "ip1"
sleep 0.5

launch_tab "6B.Instance-P" "ip2"
sleep 0.5

launch_tab "7A.Instance-D" "id1"
sleep 0.5

launch_tab "7B.Instance-D" "id2"
sleep 0.5

echo ">>> 所有服务窗口已打开。"
echo ">>> P/D/Meta 正在启动，Scheduler 和 Proxy 将在 90 秒后自动启动。"
unset SUDO_PASS
