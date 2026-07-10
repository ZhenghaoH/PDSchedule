#!/bin/bash

CONTAINER="new_env"

echo ">>> 正在强制清理容器 $CONTAINER ..."

sudo docker exec $CONTAINER /bin/bash -c '
    # 忽略错误继续执行 (set +e)
    set +e

    echo "1. 强制停止 Python 进程 (vLLM/Proxy/Scheduler)..."
    # 直接杀掉所有 python3 进程，使用 -9 强制信号
    pkill -9 -f python3

    # 也可以指定关键词
    pkill -9 -f vllm
    pkill -9 -f main.py

    echo "2. 清理显卡占用 (fuser)..."
    # 尝试使用 fuser 清理设备占用 (如果容器内有这个工具)
    if command -v fuser &> /dev/null; then
        fuser -k -9 /dev/nvidia0
        fuser -k -9 /dev/nvidia1
    else
        echo "提示: 容器内未找到 fuser 命令，跳过设备文件清理。"
    fi

    echo ">>> 清理完毕。"
'

echo "-----------------------------------------------------"
echo "检查 GPU 显存释放情况:"
sudo docker exec $CONTAINER nvidia-smi
