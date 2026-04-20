#!/bin/bash

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
export FORCE_STOP_ON_UPDATE=0
export VLLM_USE_V1=0
export VLLM_USE_MODELSCOPE=True
CONFIG_PATH="/workspace/workspace/mooncake/mooncake.json"
MODEL_PATH="/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"
LOCAL_IP="172.18.0.168"

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
    "da")
        echo -e "\033[34m=== [1/5] 启动 Decode 实例 (GPU 0) ===\033[0m"
        cd /workspace/vllm
        export HTTP_IP=172.18.0.168:8105
        export CUDA_VISIBLE_DEVICES=0
        export MOONCAKE_CONFIG_PATH=$CONFIG_PATH
        python3 -m vllm.entrypoints.openai.api_server --host $LOCAL_IP --port 8005 --model $MODEL_PATH --max-model-len 10000 --gpu-memory-utilization 0.8 --kv-transfer-config '{"kv_connector":"MooncakeConnector","kv_role":"kv_consumer","kv_rank":1,"kv_parallel_size":2,"kv_buffer_size":2e9}'
        ;;
    "db")
        echo -e "\033[34m=== [2/5] 启动 Decode 实例 (GPU 1) ===\033[0m"
        cd /workspace/vllm
        export HTTP_IP=172.18.0.168:8106
        export CUDA_VISIBLE_DEVICES=1
        export MOONCAKE_CONFIG_PATH=$CONFIG_PATH
        python3 -m vllm.entrypoints.openai.api_server --host $LOCAL_IP --port 8006 --model $MODEL_PATH --max-model-len 10000 --gpu-memory-utilization 0.8 --kv-transfer-config '{"kv_connector":"MooncakeConnector","kv_role":"kv_consumer","kv_rank":1,"kv_parallel_size":2,"kv_buffer_size":2e9}'
        ;;
    "pd1")
        # === Decode Proxy 加入 等待 ===
        wait_for_loading 40 "Decode Proxy"

        echo -e "\033[35m=== [3/5] 启动 Decode Proxy ===\033[0m"
        cd /workspace/workspace/FlexPD/entrypoints
        export MY_FLOPS=256.0
        python3 gatewayD.py --host $LOCAL_IP --port 8083 --controller 172.18.0.169:5000 --decode 172.18.0.168:7005 172.18.0.168:7006
        ;;
    "ida")
        # === Decode Instance 加入 等待 ===
        wait_for_loading 50 "Decode Instance"

        echo -e "\033[36m=== [4/5] 启动 Decode Instance ===\033[0m"
        cd /workspace/workspace/FlexPD/entrypoints
        python3 instanceD.py --host $LOCAL_IP --port 7005 --vllm_port 8005 --gateway 172.18.0.168:8083
        ;;
    "idb")
        # === Decode Instance 加入 等待 ===
        wait_for_loading 50 "Decode Instance"

        echo -e "\033[36m=== [5/5] 启动 Decode Instance ===\033[0m"
        cd /workspace/workspace/FlexPD/entrypoints
        python3 instanceD.py --host $LOCAL_IP --port 7006 --vllm_port 8006 --gateway 172.18.0.168:8083
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

launch_tab "1.DecodeA" "da"
sleep 0.5

launch_tab "2.DecodeB" "db"
sleep 0.5

launch_tab "3.GatewayD1" "pd1"
sleep 0.5

launch_tab "4.InstanceDA" "ida"
sleep 0.5

launch_tab "5.InstanceDB" "idb"

echo ">>> 所有服务窗口已打开。"
unset SUDO_PASS
