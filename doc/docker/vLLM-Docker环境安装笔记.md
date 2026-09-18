# vLLM Mooncake学习笔记

编写时间：2025.7.30

## 安装环境

### Docker

Docker 是一种轻量级的容器技术，用来解决“应用在本地能运行，但在其他环境出问题”的难题。其核心是**“镜像”**和**“容器”**。

镜像是一个 **只读的“模板”**，包含了应用程序和所有依赖的完整文件系统（比如操作系统、软件、配置等）。类似于“预制菜的配方”，别人按这个配方就能做出一模一样的菜，镜像可以从公开仓库（如 Docker Hub）下载，也可以自己通过 `Dockerfile` 定制。

容器是镜像的 **运行实例**，就像“用配方做出来的菜”，可以启动、停止、删除。每个容器都是独立的，拥有自己的文件系统、网络和进程，不会互相干扰。容器的**挂载**是指将宿主机（运行Docker的机器）的文件或目录与容器内的路径进行关联，实现数据共享或持久化存储。简单来说，就是让容器访问或修改宿主机上的文件。

需要注意的是，在容器中使用`uname -a`依然会看见宿主机的内核版本，要查看容器的操作系统版本可以使用`lsb_release -a`。

#### 安装Docker

````bash
# 修复安装损坏
sudo apt --fix-broken install
# 使用以下指令安装Docker
sudo apt install docker.io
sudo apt install apt-transport-https ca-certificates curl software-properties-common
````

在拉取Docker镜像的时候，可能会遇到如下问题：

````bash
ERROR: failed to solve: nvidia/cuda:11.8.0-devel-ubuntu22.04: pulling from host your-mirror.mirror.aliyuncs.com failed with status code [manifests 11.8.0-devel-ubuntu22.04]: 403 Forbidden
````

这时需要更换镜像源，修改`/etc/docker/daemon.json`，在其中添加新的源，比如：

````json
{

  "registry-mirrors": [

    "https://docker.1panel.live",

    "https://hub.rat.dev",

    "https://your-mirror.mirror.aliyuncs.com",

    "https://registry.docker-cn.com",

    "https://docker.mirrors.ustc.edu.cn"

  ]

}
````

换完源后要使用`systemctl restart docker`指令重启docker服务

除此之外，要使用docker调用nvidia显卡，需要额外安装一个工具：

````bash
# 安装
sudo apt install nvidia-container-runtime
# 安装完要重启docker服务生效
systemctl restart docker
````

这个工具如果安装的时候遇到`Unable to locate package nvidia-container-runtime`的问题，可以考虑用：

````bash
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)   
curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | sudo apt-key add -   
curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | sudo tee /etc/apt/sources.list.d/nvidia-docker.list
sudo apt update
````

#### Dockerfile

Dockerfile是一个用于自动化构建Docker镜像的文本文件，有点类似shell命令。

构建的docker镜像的ubuntu版本不一定要和宿主机的ubuntu版本一致，但cuda版本最好一致。写这个文档时，使用的宿主机环境为ubuntu22.04，cuda12.8 。

要使用Dockerfile构建镜像的话，首先要在一个文件夹中新建一个名为`Dockerfile`的文件，将下面的Dockerfile内容复制进去，然后在控制台中执行`sudo docker build -t vllm-cuda128 .`的命令。

在这条指令中`vllm-cuda128`是对创建的镜像的命名，后面的`.`是指向Dockerfile的路径，因为就在当前文件夹下，所以是一个点。

````dockerfile
ARG CUDA_VERSION=12.8.0
ARG PYTHON_VERSION=3.12

# 使用 Ubuntu 22.04 和 CUDA 12.8 的基础镜像
FROM nvidia/cuda:${CUDA_VERSION}-devel-ubuntu22.04 AS base

# 设置环境变量以避免交互式提示
ENV DEBIAN_FRONTEND=noninteractive

# 安装系统依赖
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    software-properties-common \
    curl \
    git \
    unzip \
    build-essential \
    libgl1 \
    gcc-10 \
    g++-10 \
    net-tools \
    etcd \
    cmake \
    vim \
    wget \
    iperf \
    iputils-ping \
    iproute2 \
    libibverbs-dev \
    libgoogle-glog-dev \
    libgtest-dev \
    libjsoncpp-dev \
    libnuma-dev \
    libcurl4-openssl-dev \
    libhiredis-dev \
    && rm -rf /var/lib/apt/lists/*

# 添加 deadsnakes PPA 以安装 Python 3.12
RUN add-apt-repository ppa:deadsnakes/ppa && \
    apt-get update && \
    apt-get install -y python3.12 python3.12-venv python3.12-dev

# 设置 Python 3.12 为默认版本
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1

# 安装 pip
RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python3

# 设置工作目录
WORKDIR /workspace

CMD ["bash"]
````

#### Docker常用指令

````bash
#查看镜像
sudo docker images
#查看容器
sudo docker ps -a

#根据dockerfile创建镜像 .是dockerfile位置
sudo docker build -t vllm-cuda118 .

#docker镜像添加新标签（重命名）
sudo docker tag <旧镜像>:<旧标签> <新镜像>:<新标签>

#创建容器, --gpus all 表示要用宿主机哪些GPU
sudo docker run --gpus all -it --ipc=host --network host --name vllm_container vllm-cuda118
#创建容器, --network host表示使用宿主机的网络配置，-v表示挂载外部目录，外部目录:docker内的映射目录
sudo docker run --gpus all -it --network host -v /my/path:/docker/path --name mooncake vllm-cuda118
# 创建容器,挂代理，实际上是添加的环境变量
sudo docker run --gpus all -it --ipc=host --network host --env HTTP_PROXY="http://172.18.0.167:7897" --env HTTPS_PROXY="http://172.18.0.167:7897" --name vllm vllm-cuda128:latest

#进入容器
sudo docker exec -it vllm_container bash

#复制外面的目录到容器里
sudo docker cp /home/user/my_folder my_container:/app/data

# 提交容器为新镜像
sudo docker commit vllm_container vllm-cuda118-modified:latest
# 导出镜像
sudo docker save -o vllm-cuda118-modified.tar vllm-cuda118-modified:latest
# 导入镜像
sudo docker load -i /path/to/your_image.tar

#启动容器
sudo docker start vllm_container
#停止容器
sudo docker stop vllm_container
#删除容器
sudo docker rm vllm_container

#删除镜像
sudo docker rmi <image_name>:<tag>
````

### 环境安装步骤

#### Docker基础环境

在安装好Docker的机器中打开控制台，进入放有Dockerfile的文件夹中，执行`sudo docker build -t vllm-cuda128 .`，其中，`vllm-cuda128`是环将要搭建的Docker镜像的名字。

在执行完搭建镜像的命令后，docker中会多一个镜像，之后的安装步骤可以在用这个镜像新建的容器中进行，在安装完其他模块后，再通过commit指令提交成新的镜像。

接下来几个模块的安装需要版本对应才能安装成功，CUDA12.8、LMCache0.3.2、vLLM0.9.2、Mooncake0.3.4、Pytorch2.7.0

#### Pytorch

Pytorch安装版本需要与CUDA版本对应，CUDA安装之前需要先安装显卡驱动

````bash
lsmod | grep nouveau  # 查看nouveau是否禁用
sudo add-apt-repository ppa:graphics-drivers/ppa  # 添加ppa源
sudo apt-get update  # 更新源
sudo apt-get purge nvidia*  # 卸载系统里的NVIDIA低版本显卡驱动
sudo apt-get install nvidia-driver-575  # 通过PPA的方式下载NVIDIA显卡驱动，需要查找与显卡型号对应的版本ubuntu-drivers devices
sudo reboot  # 重启电脑
````

安装完驱动后，如果输入`nvidia-smi`有表格状输出证明安装成功

安装完驱动后要安装CUDA，可以参考[这个链接](https://blog.csdn.net/m0_52583356/article/details/138150039)的教程。

安装CUDA完成后安装Pytorch的流程可以查看[官方的安装流程](https://pytorch.org/get-started/locally/)。

````bash
pip install torch==2.7.0 torchvision==0.22.0 torchaudio==2.7.0 --index-url https://download.pytorch.org/whl/cu128
````

#### vLLM

vLLM安装需要从github拉取项目，需要挂梯子，因此需要为docker容器添加代理。

首先安装[clash verge](https://clash-verge.com/download/)。然后在clash中开启局域网连接和TUN模式

````bash
vim ~/.bashrc  # 打开环境变量
source ~/.bashrc  # 使环境变量修改生效
````

在docker容器中的环境变量中添加如下变量，需要注意的是，这里的IP地址是宿主机默认上网的IP地址，端口号是clash里映射的端口号（一般是7897）

````bash
export HTTP_PROXY="http://172.18.0.167:7897"
export HTTPS_PROXY="https://172.18.0.167:7897"
````

挂好代理就可以开始下载vLLM了

````
git clone --branch v0.9.2 --depth=1 https://github.com/vllm-project/vllm.git
````

在像3090一样的显卡环境中安装vllm按照如下步骤就可以了

````bash
cd vllm
export MAX_JOBS=4
pip install -e . --no-binary vllm
````

但是50系显卡的环境下，安装步骤不同，需要参考[这个链接](https://blog.csdn.net/qq_63455100/article/details/148613949)的教程。

````bash
cd vllm

python use_existing_torch.py  # 使用已安装的torch，即不重新安装torch
pip install -r requirements/build.txt
pip install -r requirements/common.txt
pip install --upgrade pip setuptools setuptools-scm  # 旧版本的 setuptools 可能导致 editable 模式兼容性问题
 
# 默认MAX_JOBS=128，自己系统没那么多CPU也会安装不成功，MAX_JOBS=32时约使用180G内存
MAX_JOBS=8 pip install -e . --no-build-isolation -v # 开始编译

pip install -U nvidia-nccl-cu12 # 先编译vllm后再升级nccl，升级完nccl不需要重新编译
````

最后还要安装其他vllm依赖

````bash
pip install modelscope openai -i https://pypi.tuna.tsinghua.edu.cn/simple/
````

#### Mooncake

同样需要挂梯子

````bash
git clone https://github.com/kvcache-ai/Mooncake.git
cd Mooncake
bash dependencies.sh
mkdir build
cd build
cmake ..
make -j
make install
````

#### LMCache

同样需要挂梯子

````bash
git clone https://github.com/LMCache/LMCache.git
cd LMCache
pip install -e .
pip install nixl -i https://pypi.tuna.tsinghua.edu.cn/simple/
````

安装完环境后，可以使用`sudo docker commit 容器名 镜像名:tag`来提交容器成新镜像环境，也可以用来当做备份

### RDMA

在安装好RDMA的宿主机环境上进行RDMA模式的[切换](https://support.huawei.com/enterprise/zh/doc/EDOC1100228707/686b1095)

````bash
# 1. 切换流程 https://support.huawei.com/enterprise/zh/doc/EDOC1100228707/686b1095
lspci | grep Mell #确认当前环境中有 Mellanox ConnectX 网卡
sudo mst start #启动网卡驱动
sudo mst status 查询当前网卡设备
sudo mlxconfig -d /dev/mst/mt4119_pciconf0 q | grep LINK_TYPE
sudo mlxconfig -y -d /dev/mst/mt4119_pciconf0 s LINK_TYPE_P1=2
reboot #需要重启，配置才能生效
# 两台服务器需要使用同样的命令，进行同样的操作，LINK_TYPE_P1=2 使用 RoCE 模式，之前 LINK_TYPE_P1=1，设置为 IB 模式时，端口一直显示 Down 不能进行通信。

# 2.测试 RDMA 网卡通讯
# 1) 基本命令
ibv_devices
#检查网卡工作状态，ibv_devices 是一个包含在 libibverbs-utils.rpm 包里的工具，通过简洁列表展示本机上的 RDMA 设备。我们的服务器上查询出来的设备有两个，mlx5_0，mlx5_1，使用 mlx5_0。
ibv_devinfo
#获取 InfiniBand 设备的详细信息。
# 2) 用 rdma_server 和 rdma_client 测试网卡的连通情况，在一台服务器上执行 rdma_server，另一台服务器上执行 rdma_client -s 172.18.102.100 -s 指定服务器端的地址，在 client 端出现 rdma_client:start rdma_client:end 0 字符说明 RDMA 连接正常
# 在 server 端输入以下命令，-s 表示服务器，-C 表示 ping 次数，-v 将信息输出
rping -s -C 10 -v
# 在 client 端 -a 172.18.102.100：指定要连接的对端服务器的 IP 地址为 172.18.102.100 。
rping -c -a 172.18.102.100 -C 10 -v
# 3）带宽测试
ib_send_bw -d mlx5_0  # 在 server 端
ib_send_bw -d mlx5_0 172.18102.100  # 在 client 端
````

更重要的是在新建docker容器的时候需要添加很多RDMA相关配置

````bash
sudo docker run \
--gpus all \
-it \
--network host \
-v /home/jtdx/vLLM/workspace:/workspace/workspace \  # 根据实际情况设定
--name container_name \  # 根据实际情况设定
--privileged \
--cap-add=SYS_ADMIN \
--cap-add=NET_RAW \
--cap-add=NET_ADMIN \
--cap-add=IPC_LOCK \
--ulimit memlock=-1 \
--device=/dev/infiniband \
-e NCCL_IB_HCA=mlx5_0 \
-e NCCL_SOCKET_IFNAME=bond0 \
-e NCCL_IB_GID_INDEX=3 \
vllm-cuda128:latest  # 根据实际情况设定
````

### tc指令

````bash
# TC设置延时 10ms lo是网卡名（本地回环）
sudo tc qdisc add dev lo root netem delay 10ms
# TC删除 lo 的规则 后者区别是忽略没有规则时的报错
sudo tc qdisc del dev lo root
sudo tc qdisc del dev lo root 2>/dev/null || true
# 限制接口带宽 lo是接口 tbf令牌桶算法 rate 1mbit限带宽1Mbit burst 32kbit允许短暂的突发流量 latency 400ms最大延迟时间
sudo tc qdisc add dev lo root tbf rate 10mbit burst 32kbit latency 400ms
# 限制带宽
sudo tc qdisc add dev lo root handle 1: htb default 10
sudo tc class add dev lo parent 1: classid 1:10 htb rate 100mbit
# 查看配置是否生效
sudo tc qdisc show dev lo
````

### 启动环境

一般用这个指令创建容器

````bash
sudo docker run --gpus all -it --network host -v 宿主机路径:docker容器中的环境 --name 容器名字 镜像名字:TAG
# 示例如下
sudo docker run --gpus all -it --network host -v /home/jtdx/vLLM/workspace:/workspace/workspace --name test vllm-cuda128:latest
````

一般用这个指令打开容器

````bash
sudo docker exec -it 容器名字 bash
# 示例如下
sudo docker exec -it test /bin/bash
````

有时候会需要限制docker使用的网卡，可以用macvlan

````bash
#创建网络，enp60s0f2是网卡名，my_ray_network是自己创建的网络的名字
sudo docker network create -d macvlan --subnet 192.168.21.0/24 --gateway 192.168.21.117 -o parent=enp60s0f2 my_ray_network

#启动容器
docker run -it --name my_container \
  --network my_ray_network \
  --ip 192.168.21.99 \  # 指定容器的 IP（需与网卡2同网段）
  your_image
  
#查看网络
sudo docker network ls

#删除网络
sudo docker network rm my_ray_network
````

如果要下载模型的话，可以进入docker容器环境中执行这个代码

````python
# model_download.py
from modelscope import snapshot_download
model_dir = snapshot_download('deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B', cache_dir='./models', revision='master')
````



## MooncakeConnector P2P例程

### 官方方案

Metadata server代码http_server.py

````python
from enum import Enum
from time import sleep
from aiohttp import web
import threading
import asyncio

class KVPoll(Enum):
    """状态枚举，表示服务器操作的状态"""
    Failed = 0
    Bootstrapping = 1
    WaitingForInput = 2
    Transferring = 3
    Success = 4

class KVBootstrapServer:
    """
    一个基于 aiohttp 的轻量级键值存储服务，支持 IPv4 和 IPv6。
    可通过构造函数自定义绑定地址和端口。
    """
    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        """
        初始化服务器实例
        - host: 绑定地址，支持 IPv4 (如 0.0.0.0) 和 IPv6 (::)
        - port: 绑定端口
        """
        self.host = host
        self.port = port
        self.app = web.Application()
        self.store = dict()  # 存储键值对
        self.lock = asyncio.Lock()  # 异步锁，用于线程安全
        self._setup_routes()

    def run(self):
        """在后台线程中启动服务器"""
        self.thread = threading.Thread(target=self._run_server, daemon=True)
        print(f"HTTP metadata server is running on {self.host} : {self.port}...")
        self.thread.start()

    def _setup_routes(self):
        """设置路由，所有请求都路由到 /metadata"""
        self.app.router.add_route('*', '/metadata', self._handle_metadata)

    async def _handle_metadata(self, request: web.Request):
        """处理元数据请求（GET/PUT/DELETE）"""
        key = request.query.get('key', '')  # 获取查询参数中的 key

        # 获取客户端 IP 和端口
        client_ip = request.remote  # 直接获取客户端 IP（支持 IPv6）
        transport = request.transport
        client_port = None
        if transport:
            peername = transport.get_extra_info('peername')
            if peername:
                # IPv6 的 peername 是 (host, port, flowinfo, scopeid)
                # IPv4 的 peername 是 (host, port)
                client_ip = peername[0]  # 提取 host
                client_port = peername[1]  # 提取 port

        # 构造发送者标识
        sender = f"{client_ip}:{client_port}" if client_ip and client_port else "Unknown"
        print("SENDER:", sender)

        if request.method == 'GET':
            print("METHOD: GET, key:", key)
            return await self._handle_get(key)
        elif request.method == 'PUT':
            print("METHOD: PUT, key:", key)
            return await self._handle_put(key, request)
        elif request.method == 'DELETE':
            print("METHOD: DELETE, key:", key)
            return await self._handle_delete(key)
        return web.Response(text='Method not allowed', status=405,
                            content_type='application/json')

    async def _handle_get(self, key):
        """处理 GET 请求，返回指定 key 的值"""
        async with self.lock:
            value = self.store.get(key)
        if value is None:
            return web.Response(text='metadata not found', status=404,
                                content_type='application/json')
        return web.Response(body=value, status=200,
                            content_type='application/json')

    async def _handle_put(self, key, request):
        """处理 PUT 请求，存储 key-value"""
        data = await request.read()
        async with self.lock:
            self.store[key] = data
            print("    put data:", data.decode("utf-8"))
        return web.Response(text='metadata updated', status=200,
                            content_type='application/json')

    async def _handle_delete(self, key):
        """处理 DELETE 请求，删除指定 key"""
        async with self.lock:
            if key not in self.store:
                return web.Response(text='metadata not found', status=404,
                                    content_type='application/json')
            del self.store[key]
        return web.Response(text='metadata deleted', status=200,
                            content_type='application/json')

    def _run_server(self):
        """启动 aiohttp 服务器（异步线程）"""
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            self._runner = web.AppRunner(self.app)
            self._loop.run_until_complete(self._runner.setup())

            # 使用传入的 host 和 port 启动服务
            site = web.TCPSite(self._runner, host=self.host, port=self.port)
            self._loop.run_until_complete(site.start())
            self._loop.run_forever()
        except Exception as e:
            print(f"Server error: {str(e)}")
        finally:
            # 清理资源
            self._loop.run_until_complete(self._runner.cleanup())
            self._loop.close()

    def close(self):
        """关闭服务器"""
        if self._loop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
            print("Stopping server loop...")
        if self.thread.is_alive():
            self.thread.join(timeout=2)
            print("Server thread stopped")

    def poll(self) -> KVPoll: ...
        # 实现 poll 方法（当前为占位符）

if __name__ == '__main__':
    # 示例：启用 IPv6 支持，绑定地址为 ::，端口 8080
    # 要改回 IPv4，可将 host 参数设为 "0.0.0.0",ipv6:"2004:1::167"
    server = KVBootstrapServer(host="172.18.0.167", port=8080)
    server.run()
    try:
        threading.Event().wait()  # 保持主线程运行
    except KeyboardInterrupt:
        server.close()
````

用户代理代码disagg_proxy_demo.py

````python
# SPDX-License-Identifier: Apache-2.0
"""
This file provides a disaggregated prefilling proxy demo to demonstrate an
example usage of XpYd disaggregated prefilling.
We can launch multiple vllm instances (2 for prefill and 2 for decode), and
launch this proxy demo through:
  python3 examples/online_serving/disagg_examples/disagg_proxy_demo.py  \
       --model $model_name  \
       --prefill localhost:8100 localhost:8101   \
       --decode localhost:8200 localhost:8201   \
       --port 8000

Note: This demo will be removed once the PDController implemented in PR 15343
(https://github.com/vllm-project/vllm/pull/15343) supports XpYd.
"""
import argparse
import ipaddress
import itertools
import json
import logging
import os
import sys
from abc import ABC, abstractmethod
from typing import Callable, Optional

import aiohttp
import requests
import uvicorn
from fastapi import (APIRouter, Depends, FastAPI, Header, HTTPException,
                     Request, status)
from fastapi.responses import JSONResponse, StreamingResponse

import re

AIOHTTP_TIMEOUT = aiohttp.ClientTimeout(total=6 * 60 * 60)
logger = logging.getLogger()
logging.basicConfig(level=logging.INFO)


class SchedulingPolicy(ABC):

    @abstractmethod
    def schedule(self, cycler: itertools.cycle):
        raise NotImplementedError("Scheduling Proxy is not set.")


class Proxy:

    def __init__(
        self,
        prefill_instances: list[str],
        decode_instances: list[str],
        model: str,
        scheduling_policy: SchedulingPolicy,
        custom_create_completion: Optional[Callable[[Request],
                                                    StreamingResponse]] = None,
        custom_create_chat_completion: Optional[Callable[
            [Request], StreamingResponse]] = None,
    ):
        self.prefill_instances = prefill_instances
        self.decode_instances = decode_instances
        self.prefill_cycler = itertools.cycle(prefill_instances)
        self.decode_cycler = itertools.cycle(decode_instances)
        self.model = model
        self.scheduling_policy = scheduling_policy
        self.custom_create_completion = custom_create_completion
        self.custom_create_chat_completion = custom_create_chat_completion
        self.router = APIRouter()
        self.setup_routes()

    def setup_routes(self):
        self.router.post(
            "/v1/completions",
            dependencies=[
                Depends(self.validate_json_request)
            ])(self.custom_create_completion if self.
               custom_create_completion else self.create_completion)
        self.router.post(
            "/v1/chat/completions",
            dependencies=[
                Depends(self.validate_json_request)
            ])(self.custom_create_chat_completion if self.
               custom_create_chat_completion else self.create_chat_completion)
        self.router.get("/status",
                        response_class=JSONResponse)(self.get_status)
        self.router.post("/instances/add",
                         dependencies=[Depends(self.api_key_authenticate)
                                       ])(self.add_instance_endpoint)

    async def validate_json_request(self, raw_request: Request):
        content_type = raw_request.headers.get("content-type", "").lower()
        if content_type != "application/json":
            raise HTTPException(
                status_code=415,
                detail=
                "Unsupported Media Type: Only 'application/json' is allowed",
            )

    def api_key_authenticate(self, x_api_key: str = Header(...)):
        expected_api_key = os.environ.get("ADMIN_API_KEY")
        if not expected_api_key:
            logger.error("ADMIN_API_KEY is not set in the environment.")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Server configuration error.",
            )
        if x_api_key != expected_api_key:
            logger.warning("Unauthorized access attempt with API Key: %s",
                           x_api_key)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: Invalid API Key.",
            )

    async def validate_instance(self, instance: str) -> bool:
        url = f"http://{instance}/v1/models"
        try:
            async with aiohttp.ClientSession(
                    timeout=AIOHTTP_TIMEOUT) as client:
                logger.info("Verifying %s ...", instance)
                async with client.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        if "data" in data and len(data["data"]) > 0:
                            model_cur = data["data"][0].get("id", "")
                            if model_cur == self.model:
                                logger.info("Instance: %s could be added.",
                                            instance)
                                return True
                            else:
                                logger.warning("Mismatch model %s : %s != %s",
                                               instance, model_cur, self.model)
                                return False
                        else:
                            return False
                    else:
                        return False
        except aiohttp.ClientError as e:
            logger.error(str(e))
            return False
        except Exception as e:
            logger.error(str(e))
            return False

    async def add_instance_endpoint(self, request: Request):
        try:
            data = await request.json()
            logger.warning(str(data))
            instance_type = data.get("type")
            instance = data.get("instance")
            if instance_type not in ["prefill", "decode"]:
                raise HTTPException(status_code=400,
                                    detail="Invalid instance type.")
            if not instance or ":" not in instance:
                raise HTTPException(status_code=400,
                                    detail="Invalid instance format.")
            host, port_str = instance.split(":")
            try:
                if host != "localhost":
                    ipaddress.ip_address(host)
                port = int(port_str)
                if not (0 < port < 65536):
                    raise HTTPException(status_code=400,
                                        detail="Invalid port number.")
            except Exception as e:
                raise HTTPException(status_code=400,
                                    detail="Invalid instance address.") from e

            is_valid = await self.validate_instance(instance)
            if not is_valid:
                raise HTTPException(status_code=400,
                                    detail="Instance validation failed.")

            if instance_type == "prefill":
                if instance not in self.prefill_instances:
                    self.prefill_instances.append(instance)
                    self.prefill_cycler = itertools.cycle(
                        self.prefill_instances)
                else:
                    raise HTTPException(status_code=400,
                                        detail="Instance already exists.")
            else:
                if instance not in self.decode_instances:
                    self.decode_instances.append(instance)
                    self.decode_cycler = itertools.cycle(self.decode_instances)
                else:
                    raise HTTPException(status_code=400,
                                        detail="Instance already exists.")

            return JSONResponse(content={
                "message":
                f"Added {instance} to {instance_type}_instances."
            })
        except HTTPException as http_exc:
            raise http_exc
        except Exception as e:
            logger.error("Error in add_instance_endpoint: %s", str(e))
            raise HTTPException(status_code=500, detail=str(e)) from e

    async def forward_request(self, url, data, use_chunked=True):
        async with aiohttp.ClientSession(timeout=AIOHTTP_TIMEOUT) as session:
            headers = {
                "Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY')}"
            }
            try:
                async with session.post(url=url, json=data,
                                        headers=headers) as response:
                    if 200 <= response.status < 300 or 400 <= response.status < 500:  # noqa: E501
                        if use_chunked:
                            async for chunk_bytes in response.content.iter_chunked(  # noqa: E501
                                    1024):
                                yield chunk_bytes
                        else:
                            content = await response.read()
                            yield content
                    else:
                        error_content = await response.text()
                        try:
                            error_content = json.loads(error_content)
                        except json.JSONDecodeError:
                            error_content = error_content
                        logger.error("Request failed with status %s: %s",
                                     response.status, error_content)
                        raise HTTPException(
                            status_code=response.status,
                            detail=
                            f"Request failed with status {response.status}: "
                            f"{error_content}",
                        )
            except aiohttp.ClientError as e:
                logger.error("ClientError occurred: %s", str(e))
                raise HTTPException(
                    status_code=502,
                    detail=
                    "Bad Gateway: Error communicating with upstream server.",
                ) from e
            except Exception as e:
                logger.error("Unexpected error: %s", str(e))
                raise HTTPException(status_code=500, detail=str(e)) from e

    def schedule(self, cycler: itertools.cycle) -> str:
        return self.scheduling_policy.schedule(cycler)

    async def get_status(self):
        status = {
            "prefill_node_count": len(self.prefill_instances),
            "decode_node_count": len(self.decode_instances),
            "prefill_nodes": self.prefill_instances,
            "decode_nodes": self.decode_instances,
        }
        return status

    async def create_completion(self, raw_request: Request):
        try:
            request = await raw_request.json()

            kv_prepare_request = request.copy()
            kv_prepare_request["max_tokens"] = 1

            prefill_instance = self.schedule(self.prefill_cycler)
            try:
                async for _ in self.forward_request(
                        f"http://{prefill_instance}/v1/completions",
                        kv_prepare_request):
                    continue
            except HTTPException as http_exc:
                self.remove_instance_endpoint("prefill", prefill_instance)
                raise http_exc

            # Perform kv recv and decoding stage
            decode_instance = self.schedule(self.decode_cycler)

            try:
                generator = self.forward_request(
                    f"http://{decode_instance}/v1/completions", request)
            except HTTPException as http_exc:
                self.remove_instance_endpoint("decode", decode_instance)
                raise http_exc
            response = StreamingResponse(generator)
            return response
        except Exception:
            import sys

            exc_info = sys.exc_info()
            print("Error occurred in disagg proxy server")
            print(exc_info)

    async def create_chat_completion(self, raw_request: Request):
        try:
            request = await raw_request.json()

            # add params to request
            kv_prepare_request = request.copy()
            kv_prepare_request["max_tokens"] = 1

            # prefill stage
            prefill_instance = self.schedule(self.prefill_cycler)
            try:
                async for _ in self.forward_request(
                        f"http://{prefill_instance}/v1/chat/completions",
                        kv_prepare_request):
                    continue
            except HTTPException as http_exc:
                self.remove_instance_endpoint("prefill", prefill_instance)
                raise http_exc
            # Perform kv recv and decoding stage
            decode_instance = self.schedule(self.decode_cycler)

            try:
                generator = self.forward_request(
                    "http://" + decode_instance + "/v1/chat/completions",
                    request)
            except HTTPException as http_exc:
                self.remove_instance_endpoint("decode", decode_instance)
                raise http_exc
            response = StreamingResponse(content=generator)
            return response
        except Exception:
            exc_info = sys.exc_info()
            error_messages = [str(e) for e in exc_info if e]
            print("Error occurred in disagg proxy server")
            print(error_messages)
            return StreamingResponse(content=iter(error_messages),
                                     media_type="text/event-stream")

    def remove_instance_endpoint(self, instance_type, instance):
        if (instance_type == "decode" and instance in self.decode_instances):
            self.decode_instances.remove(instance)
            self.decode_cycler = itertools.cycle(self.decode_instances)
        if (instance_type == "prefill" and instance in self.decode_instances):
            self.prefill_instances.remove(instance)
            self.prefill_cycler = itertools.cycle(self.decode_instances)


class RoundRobinSchedulingPolicy(SchedulingPolicy):

    def __init__(self):
        super().__init__()

    def schedule(self, cycler: itertools.cycle) -> str:
        return next(cycler)


class ProxyServer:

    def __init__(
        self,
        args: argparse.Namespace,
        scheduling_policy: Optional[SchedulingPolicy] = None,
        create_completion: Optional[Callable[[Request],
                                             StreamingResponse]] = None,
        create_chat_completion: Optional[Callable[[Request],
                                                  StreamingResponse]] = None,
    ):
        self.validate_parsed_serve_args(args)
        self.host = args.host
        self.port = args.port
        self.proxy_instance = Proxy(
            prefill_instances=[] if args.prefill is None else args.prefill,
            decode_instances=[] if args.decode is None else args.decode,
            model=args.model,
            scheduling_policy=(scheduling_policy if scheduling_policy
                               is not None else RoundRobinSchedulingPolicy()),
            custom_create_completion=create_completion,
            custom_create_chat_completion=create_chat_completion,
        )

    def validate_parsed_serve_args(self, args: argparse.Namespace):
        if not args.prefill:
            raise ValueError("Please specify at least one prefill node.")
        if not args.decode:
            raise ValueError("Please specify at least one decode node.")
        self.validate_instances(args.prefill)
        self.validate_instances(args.decode)
        self.verify_model_config(args.prefill, args.model)
        self.verify_model_config(args.decode, args.model)

    def validate_instances(self, instances: list):
        for instance in instances:
            # 判断是否为 IPv6 格式 [IPv6]:port
            if "]:" in instance:
                # 分割 IPv6 地址和端口
                ipv6_part, port_part = instance.split("]:")
                host = ipv6_part[1:]  # 去掉开头的 "["
                port = int(port_part)
            elif ":" in instance and not instance.startswith("["):
                # 传统 host:port 格式（IPv4 或 hostname）
                host, port = instance.split(":", 1)
                port = int(port)
            else:
                raise ValueError(f"Invalid instance format: {instance}. Use [IPv6]:port or host:port")

            # 验证端口号
            if not (0 < port < 65536):
                raise ValueError(f"Invalid port number in instance: {instance}")

            # 验证 IP 地址（跳过 localhost）
            if host != "localhost":
                try:
                    ipaddress.ip_address(host)
                except ValueError as e:
                    raise ValueError(f"Invalid host in instance {instance}: {str(e)}") from e

    def verify_model_config(self, instances: list, model: str) -> None:
        model_suffix = model.split("/")[-1]
        for instance in instances:
            try:
                response = requests.get(f"http://{instance}/v1/models")
                if response.status_code == 200:
                    model_cur = response.json()["data"][0]["id"]
                    model_cur_suffix = model_cur.split("/")[-1]
                    if model_cur_suffix != model_suffix:
                        raise ValueError(
                            f"{instance} serves a different model: "
                            f"{model_cur} != {model}")
                else:
                    raise ValueError(f"Cannot get model id from {instance}!")
            except requests.RequestException as e:
                raise ValueError(
                    f"Error communicating with {instance}: {str(e)}") from e

    def run_server(self):
        app = FastAPI()
        app.include_router(self.proxy_instance.router)
        config = uvicorn.Config(app, host=self.host, port=self.port, loop="uvloop")
        server = uvicorn.Server(config)
        server.run()


if __name__ == "__main__":
    # Todo: allow more config
    parser = argparse.ArgumentParser("vLLM disaggregated proxy server.")
    parser.add_argument("--model",
                        "-m",
                        type=str,
                        required=True,
                        help="Model name")

    parser.add_argument(
        "--prefill",
        "-p",
        type=str,
        nargs="+",
        help="List of prefill node URLs (host:port)",
    )

    parser.add_argument(
        "--decode",
        "-d",
        type=str,
        nargs="+",
        help="List of decode node URLs (host:port)",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Server port number",
    )

    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="The IP address for the proxy server to listen on. Supports IPv4 and IPv6."
    )
    args = parser.parse_args()
    proxy_server = ProxyServer(args=args)
    proxy_server.run_server()

````

假设两台机器的IP分别为`172.18.0.167`和`172.18.0.166`，MetadataServer放在166上。

配置文件mooncake2.json

````json
{
  "prefill_url": "172.18.0.166:13003",
  "decode_url": "172.18.0.167:13006",
  "metadata_server": "http://172.18.0.166:8080/metadata",
  "protocol": "tcp",
  "device_name": ""
}
````

操作指令示例

````bash
# 自己写的代码和配置默认都放在/home/jtdx/vLLM/workspace中用于映射
# 启动环境
sudo docker run --gpus all -it --network host -v /home/jtdx/vLLM/workspace:/workspace/workspace --name test vllm-cuda128:latest
sudo docker exec -it test /bin/bash

# 在166启动metadata服务，需要在代码中修改监听的IP地址为172.18.0.167
python3 /workspace/workspace/http_server.py

# 在166上启动P实例
CUDA_VISIBLE_DEVICES=0 VLLM_USE_V1=0 MOONCAKE_CONFIG_PATH=/workspace/workspace/mooncake2.json VLLM_USE_MODELSCOPE=True python3 -m vllm.entrypoints.openai.api_server --model /workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B --port 8100 --max-model-len 10000 --gpu-memory-utilization 0.8 --kv-transfer-config '{"kv_connector":"MooncakeConnector","kv_role":"kv_producer","kv_rank":0,"kv_parallel_size":2,"kv_buffer_size":2e9}'

# 在167上启动D实例
CUDA_VISIBLE_DEVICES=1 VLLM_USE_V1=0 MOONCAKE_CONFIG_PATH=/workspace/workspace/mooncake2.json VLLM_USE_MODELSCOPE=True python3 -m vllm.entrypoints.openai.api_server --model /workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B --port 8101 --max-model-len 10000 --gpu-memory-utilization 0.8 --kv-transfer-config '{"kv_connector":"MooncakeConnector","kv_role":"kv_consumer","kv_rank":1,"kv_parallel_size":2,"kv_buffer_size":2e9}'

# 启动代理 假设代理放在167上
python3 /workspace/workspace/disagg_proxy_demo.py --model /workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B --prefill 172.18.0.166:8100 --decode 172.18.0.167:8101 --port 8000 --host 172.18.0.167

# 用户发送请求
curl http://172.18.0.167:8000/v1/chat/completions -H "Content-Type: application/json" -d '{"model": "/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B","messages": [{"role": "system", "content": "You are a helpful assistant."},{"role": "user", "content": "Who are you?"}]}'

curl -s http://172.18.0.167:8000/v1/completions -H "Content-Type: application/json" -d '{"model": "/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B","prompt": "What is DeepSeek?","max_tokens": 1000}'
````



### IPv6

旧版通过仓库内 `tcp_transport.cpp` 覆盖 Mooncake 源码的实验方法已移除。需要 IPv6 时应以所用 Mooncake 版本的上游实现和文档为准。

配置文件mooncake2.json

````json
{
  "prefill_url": "[2004:1::166]:13003",
  "decode_url": "[2004:1::167]:13006",
  "metadata_server": "http://[2004:1::166]:8080/metadata",
  "protocol": "tcp",
  "device_name": ""
}
````

操作指令示例

````bash
# 自己写的代码和配置默认都放在/home/jtdx/vLLM/workspace中用于映射
# 启动环境
sudo docker run --gpus all -it --network host -v /home/jtdx/vLLM/workspace:/workspace/workspace --name test vllm-cuda128:latest
sudo docker exec -it test /bin/bash

# 在166启动metadata服务，需要在代码中修改监听的IP地址为2004:1::167
python3 /workspace/workspace/http_server.py

# 在166上启动P实例
MC_USE_IPV6=1 CUDA_VISIBLE_DEVICES=0 VLLM_USE_V1=0 MOONCAKE_CONFIG_PATH=/workspace/workspace/mooncake2.json VLLM_USE_MODELSCOPE=True python3 -m vllm.entrypoints.openai.api_server --model /workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B --port 8100 --max-model-len 10000 --gpu-memory-utilization 0.8 --kv-transfer-config '{"kv_connector":"MooncakeConnector","kv_role":"kv_producer","kv_rank":0,"kv_parallel_size":2,"kv_buffer_size":2e9}' --host 2004:1::166

# 在167上启动D实例
MC_USE_IPV6=1 CUDA_VISIBLE_DEVICES=1 VLLM_USE_V1=0 MOONCAKE_CONFIG_PATH=/workspace/workspace/mooncake2.json VLLM_USE_MODELSCOPE=True python3 -m vllm.entrypoints.openai.api_server --model /workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B --port 8101 --max-model-len 10000 --gpu-memory-utilization 0.8 --kv-transfer-config '{"kv_connector":"MooncakeConnector","kv_role":"kv_consumer","kv_rank":1,"kv_parallel_size":2,"kv_buffer_size":2e9}' --host 2004:1::167

# 启动代理 假设代理放在167上
python3 /workspace/workspace/disagg_proxy_demo.py --model /workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B --prefill [2004:1::166]:8100 --decode [2004:1::167]:8101 --port 8000 --host 2004:1::167

# 用户发送请求
curl http://[2004:1::167]:8000/v1/chat/completions -H "Content-Type: application/json" -d '{"model": "/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B","messages": [{"role": "system", "content": "You are a helpful assistant."},{"role": "user", "content": "Who are you?"}]}'

curl -s http://[2004:1::167]:8000/v1/completions -H "Content-Type: application/json" -d '{"model": "/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B","prompt": "What is DeepSeek?","max_tokens": 1000}'
````

### RDMA

RDMA目前只支持IPv4，只支持有RDMA网卡的机器，启动容器时必须设置好参数。

配置文件mooncake2.json

````json
{
  "prefill_url": "172.18.102.100:13003",
  "decode_url": "172.18.102.101:13006",
  "metadata_server": "http://172.18.102.100:8080/metadata",
  "protocol": "rdma",
  "device_name": "mlx5_0"
}
````

操作指令示例

````bash
# 自己写的代码和配置默认都放在/home/jtdx/vLLM/workspace中用于映射
# 启动环境
sudo docker run --gpus all -it --network host -v /home/jtdx/vLLM/workspace:/workspace/workspace --name rdma --privileged --cap-add=SYS_ADMIN --cap-add=NET_RAW --cap-add=NET_ADMIN --cap-add=IPC_LOCK --ulimit memlock=-1 --device=/dev/infiniband -e NCCL_IB_HCA=mlx5_0 -e NCCL_SOCKET_IFNAME=bond0 -e NCCL_IB_GID_INDEX=3 vllm-cuda128:latest
sudo docker exec -it rdma /bin/bash

# 在166启动metadata服务，需要在代码中修改监听的IP地址为172.18.102.100
python3 /workspace/workspace/http_server.py

# 在166上启动P实例
CUDA_VISIBLE_DEVICES=0 VLLM_USE_V1=0 MOONCAKE_CONFIG_PATH=/workspace/workspace/mooncake2.json VLLM_USE_MODELSCOPE=True python3 -m vllm.entrypoints.openai.api_server --model /workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B --port 8100 --max-model-len 10000 --gpu-memory-utilization 0.8 --kv-transfer-config '{"kv_connector":"MooncakeConnector","kv_role":"kv_producer","kv_rank":0,"kv_parallel_size":2,"kv_buffer_size":2e9}' --host 172.18.0.100

# 启动D实例
CUDA_VISIBLE_DEVICES=1 VLLM_USE_V1=0 MOONCAKE_CONFIG_PATH=/workspace/workspace/mooncake2.json VLLM_USE_MODELSCOPE=True python3 -m vllm.entrypoints.openai.api_server --model /workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B --port 8101 --max-model-len 10000 --gpu-memory-utilization 0.8 --kv-transfer-config '{"kv_connector":"MooncakeConnector","kv_role":"kv_consumer","kv_rank":1,"kv_parallel_size":2,"kv_buffer_size":2e9}' --host 172.18.102.101

# 启动代理 假设代理放在167上
python3 /workspace/workspace/disagg_proxy_demo.py --model /workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B --prefill 172.18.102.100:8100 --decode 172.18.102.101:8101 --port 8000 --host 172.18.102.101

# 用户发送请求
curl http://172.18.102.101:8000/v1/chat/completions -H "Content-Type: application/json" -d '{"model": "/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B","messages": [{"role": "system", "content": "You are a helpful assistant."},{"role": "user", "content": "Who are you?"}]}'

curl -s http://172.18.102.101:8000/v1/completions -H "Content-Type: application/json" -d '{"model": "/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B","prompt": "What is DeepSeek?","max_tokens": 1000}'
````



### 调度

加上调度功能的本质是把读取mooncake.json的功能放到了一个HTTP服务里面，只有收到对应请求的时候才能加载配置。以下以IPv4为例，如果设置为IPv6和上书设置IPv6方法需要修改的地方一致。

修改代码

mooncake_pipe.py 

````python
# SPDX-License-Identifier: Apache-2.0

import json
import os
import struct
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional, Union

import torch
import zmq
from safetensors.torch import load as safetensors_load
from safetensors.torch import save as safetensors_save

from vllm.config import KVTransferConfig
from vllm.distributed.kv_transfer.kv_pipe.base import KVPipeBase
from vllm.logger import init_logger

import time
import socketserver
import http.server
import threading
import concurrent.futures
import socket


logger = init_logger(__name__)
NONE_INT = -150886311


def is_port_available(host, port):
    """
    检查指定主机和端口是否可用（使用标准 socket 模块）。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except socket.error:
            return False


@dataclass
class MooncakeTransferEngineConfig:
    prefill_url: str
    decode_url: str
    metadata_backend: Union[str, None]
    metadata_server: str
    protocol: str
    device_name: str

    @staticmethod
    def from_file(file_path: str) -> 'MooncakeTransferEngineConfig':
        """Load the config from a JSON file."""
        with open(file_path) as fin:
            config = json.load(fin)
        return MooncakeTransferEngineConfig(
            prefill_url=config.get("prefill_url"),
            decode_url=config.get("decode_url"),
            metadata_backend=config.get("metadata_backend", None),
            metadata_server=config.get("metadata_server"),
            protocol=config.get("protocol", "tcp"),
            device_name=config.get("device_name", ""),
        )

    @staticmethod
    def load_from_env() -> 'MooncakeTransferEngineConfig':
        """Load config from a file specified in the environment variable."""
        config_file_path = os.getenv('MOONCAKE_CONFIG_PATH')
        if config_file_path is None:
            raise ValueError(
                "The environment variable 'MOONCAKE_CONFIG_PATH' is not set.")
        return MooncakeTransferEngineConfig.from_file(config_file_path)

    @staticmethod
    def from_dict(config_dict: dict[str, any]) -> 'MooncakeTransferEngineConfig':
        """
        Create a new config instance from a dictionary.
        Mimics the logic of `from_file`.
        """
        return MooncakeTransferEngineConfig(
            prefill_url=config_dict.get("prefill_url"),
            decode_url=config_dict.get("decode_url"),
            metadata_backend=config_dict.get("metadata_backend", None),
            metadata_server=config_dict.get("metadata_server"),
            protocol=config_dict.get("protocol", "tcp"),
            device_name=config_dict.get("device_name", ""),
        )

    def merge(self, config_data: dict[str, any]) -> 'MooncakeTransferEngineConfig':
        """
        Merge the current configuration with the provided dictionary.
        Fields are updated one by one, with explicit handling of defaults.
        """
        return MooncakeTransferEngineConfig(
            prefill_url=config_data.get("prefill_url", self.prefill_url),
            decode_url=config_data.get("decode_url", self.decode_url),
            metadata_backend=config_data.get("metadata_backend", None),
            metadata_server=config_data.get("metadata_server", self.metadata_server),
            protocol=config_data.get("protocol", self.protocol),
            device_name=config_data.get("device_name", self.device_name),
        )


class MooncakeTransferEngine:
    """Handles the transfer of data using mooncake_vllm_adaptor and ZeroMQ."""

    def __init__(self, mooncake_config: MooncakeTransferEngineConfig, kv_rank: int, local_rank: int):
        try:
            from mooncake.engine import TransferEngine
        except ImportError as e:
            raise ImportError(
                "Please install mooncake by following the instructions at "
                "https://github.com/kvcache-ai/Mooncake/blob/main/doc/en/build.md "  # noqa: E501
                "to run vLLM with MooncakeConnector.") from e

        self.engine = TransferEngine()
        self.local_rank = local_rank

        self.config = mooncake_config

        def extract_ip_and_port(s):
            if '[' in s:
                ip_part = s.split(']:')[0]  # 提取IP部分并去掉中括号
                ip = ip_part.strip('[]')
                port = s.split(':')[-1]  # 提取端口号
            else:
                ip = s.split(':')[0]
                port = s.split(':')[-1]
            return ip, port

        if '[' in self.config.prefill_url:
            prefill_host, base_prefill_port = extract_ip_and_port(self.config.prefill_url)
            decode_host, base_decode_port = extract_ip_and_port(self.config.decode_url)
            prefill_host = '['+prefill_host+']'
            decode_host = '['+decode_host+']'
        else:
            prefill_host, base_prefill_port = extract_ip_and_port(self.config.prefill_url)
            decode_host, base_decode_port = extract_ip_and_port(self.config.decode_url)

        # Avoid ports conflict when running prefill and decode on the same node
        if prefill_host == decode_host and \
                base_prefill_port == base_decode_port:
            base_decode_port = str(int(base_decode_port) + 100)

        prefill_port = int(base_prefill_port) + self.local_rank
        decode_port = int(base_decode_port) + self.local_rank
        self.prefill_url = ':'.join([prefill_host, str(prefill_port)])
        self.decode_url = ':'.join([decode_host, str(decode_port)])

        self.initialize(self.prefill_url if kv_rank == 0 else self.decode_url,
                        self.config.metadata_server, self.config.protocol,
                        self.config.device_name, self.config.metadata_backend)

        self.remote_url = (self.decode_url
                           if kv_rank == 0 else self.prefill_url)

        # Initialize ZeroMQ context and sockets
        self.context = zmq.Context()  # type: ignore[attr-defined]
        if '[' in prefill_host:
            self.context.setsockopt(zmq.IPV6, 1)
        self.sender_socket = self.context.socket(zmq.constants.PUSH)
        self.receiver_socket = self.context.socket(zmq.constants.PULL)
        self.sender_ack = self.context.socket(zmq.constants.PULL)
        self.receiver_ack = self.context.socket(zmq.constants.PUSH)

        self.buffer_cleaner = ThreadPoolExecutor(max_workers=1)

        self._setup_metadata_sockets(kv_rank, prefill_host, base_prefill_port,
                                     decode_host, base_decode_port)

    def _setup_metadata_sockets(self, kv_rank: int, p_host: str, p_port: str,
                                d_host: str, d_port: str) -> None:
        """Set up ZeroMQ sockets for sending and receiving data."""
        # Offsets < 8 are left for initialization in case tp and pp are enabled
        p_rank_offset = int(p_port) + 8 + self.local_rank * 2
        d_rank_offset = int(d_port) + 8 + self.local_rank * 2
        if kv_rank == 0:
            self.sender_socket.bind(f"tcp://{p_host}:{p_rank_offset + 1}")
            self.receiver_socket.connect(f"tcp://{d_host}:{d_rank_offset + 1}")
            self.sender_ack.connect(f"tcp://{d_host}:{d_rank_offset + 2}")
            self.receiver_ack.bind(f"tcp://{p_host}:{p_rank_offset + 2}")
        else:
            self.receiver_socket.connect(f"tcp://{p_host}:{p_rank_offset + 1}")
            self.sender_socket.bind(f"tcp://{d_host}:{d_rank_offset + 1}")
            self.receiver_ack.bind(f"tcp://{d_host}:{d_rank_offset + 2}")
            self.sender_ack.connect(f"tcp://{p_host}:{p_rank_offset + 2}")

    def initialize(self, local_hostname: str, metadata_server: str,
                   protocol: str, device_name: str,
                   metadata_backend: Union[str, None]) -> None:
        """Initialize the mooncake instance."""
        if metadata_backend is None:
            self.engine.initialize(local_hostname, metadata_server, protocol,
                                   device_name)
        else:
            supported_backend = ["etcd", "redis"]
            metadata_backend = metadata_backend.lower()
            if metadata_backend not in supported_backend:
                raise ValueError(
                    "Mooncake Configuration error. `metadata_backend`"
                    f" should be one of {supported_backend}.")

            self.engine.initialize_ext(local_hostname, metadata_server,
                                       protocol, device_name, metadata_backend)

    def allocate_managed_buffer(self, length: int) -> int:
        """Allocate a managed buffer of the specified length."""
        ret = self.engine.allocate_managed_buffer(length)
        if ret <= 0:
            logger.error("Allocation Return Error")
            raise Exception("Allocation Return Error")
        return ret

    def free_managed_buffer(self, buffer: int, length: int) -> int:
        """Free a previously allocated managed buffer."""
        return self.engine.free_managed_buffer(buffer, length)

    def transfer_sync(self, buffer: int, peer_buffer_address: int,
                      length: int) -> int:
        """Synchronously transfer data to the specified address."""
        ret = self.engine.transfer_sync_read(self.remote_url, buffer,
                                             peer_buffer_address, length)
        if ret < 0:
            logger.error("Transfer Return Error")
            raise Exception("Transfer Return Error")
        return ret

    def write_bytes_to_buffer(self, buffer: int, user_data: bytes,
                              length: int) -> int:
        """Write bytes to the allocated buffer."""
        return self.engine.write_bytes_to_buffer(buffer, user_data, length)

    def read_bytes_from_buffer(self, buffer: int, length: int) -> bytes:
        """Read bytes from the allocated buffer."""
        return self.engine.read_bytes_from_buffer(buffer, length)

    def wait_for_ack(self, src_ptr: int, length: int) -> None:
        """Asynchronously wait for ACK from the receiver."""
        ack = self.sender_ack.recv()
        if ack != b'ACK':
            logger.error("Failed to receive ACK from the receiver")

        self.free_managed_buffer(src_ptr, length)

    def send_bytes(self, user_data: bytes) -> None:
        """Send bytes to the remote process."""
        length = len(user_data)
        src_ptr = self.allocate_managed_buffer(length)
        self.write_bytes_to_buffer(src_ptr, user_data, length)
        self.sender_socket.send_multipart(
            [struct.pack("!Q", src_ptr),
             struct.pack("!Q", length)])
        self.buffer_cleaner.submit(self.wait_for_ack, src_ptr, length)

    def recv_bytes(self) -> bytes:
        """Receive bytes from the remote process."""
        data = self.receiver_socket.recv_multipart()
        src_ptr = struct.unpack("!Q", data[0])[0]
        length = struct.unpack("!Q", data[1])[0]
        dst_ptr = self.allocate_managed_buffer(length)
        self.transfer_sync(dst_ptr, src_ptr, length)
        ret = self.read_bytes_from_buffer(dst_ptr, length)

        # Buffer cleanup
        self.receiver_ack.send(b'ACK')
        self.free_managed_buffer(dst_ptr, length)

        return ret

    def close(self, force=False) -> None:
        self.sender_socket.close()
        self.receiver_socket.close()
        self.sender_ack.close()
        self.receiver_ack.close()
        self.context.term()
        self.buffer_cleaner.shutdown(wait=not force)
        logger.info("Stopped transfer engine.")


class ConfigUpdateHandler(http.server.BaseHTTPRequestHandler):
    def __init__(self, *args, pipe=None, **kwargs):
        self.pipe = pipe
        super().__init__(*args, **kwargs)

    def do_POST(self):
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length == 0:
                self.send_error(400, "No content in request")
                return

            post_data = self.rfile.read(content_length)
            data = json.loads(post_data)

            if self.path == '/update':
                result = self._process_update(data)
            elif self.path == '/close':
                result = self._process_close(data)
            else:
                self.send_error(404, f"Path {self.path} not found")
                return

            if result is True:
                self.send_response(200)
                self.end_headers()
            else:
                self.send_error(500, "Internal Server Error")

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON format: {e}")
            self.send_error(400, f"Invalid JSON format: {e}")
        except Exception as e:
            logger.error(f"Error during config update: {e}")
            self.send_error(500, f"Internal server error: {e}")

    def _process_update(self, config_data):
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(self.pipe.update_config, config_data)
            try:
                result = future.result()
                logger.info("Scheduler updated config.")
                return True
            except Exception as e:
                return False

    def _process_close(self, data):
        force_stop = data.get('force_stop', None)
        if force_stop is None:
            force_stop = self.pipe.force_stop_on_update
        else:
            if isinstance(force_stop, str):
                force_stop = force_stop.lower() in ("1", "true", "yes")
            elif not isinstance(force_stop, bool):
                raise ValueError("force_stop must be a boolean")

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(self.pipe.close_engine, force_stop)
            try:
                result = future.result()
                return True
            except Exception as e:
                return False


class MooncakePipe(KVPipeBase):
    """MooncakeTransferEngine based Pipe implementation."""

    def __init__(self,
                 local_rank: int,
                 config: KVTransferConfig,
                 device: Optional[str] = None):
        """Initialize the mooncake pipe and set related parameters."""
        self.config = config
        self.local_rank = local_rank
        self.kv_rank = self.config.kv_rank
        if device is None:
            self.device = self._select_device(self.config.kv_buffer_device)
        else:
            self.device = self._select_device(device)

        self.transfer_engine: Optional[MooncakeTransferEngine] = None
        self.transport_thread: Optional[ThreadPoolExecutor] = None
        self.none_tensor = torch.tensor([NONE_INT], device=self.device)
        self.mc_config: Optional[MooncakeTransferEngineConfig] = None

        try:
            self.mc_config = MooncakeTransferEngineConfig.load_from_env()
            logger.info("Mooncake Configuration loaded successfully.")
        except ValueError as e:
            logger.error(e)
        except Exception as exc:
            logger.error(
                "An error occurred while loading the configuration: %s", exc)
        # try:
        #     # 根据配置文件初始化一个连接
        #     self.transfer_engine = MooncakeTransferEngine(self.mc_config, self.kv_rank, self.local_rank)
        # except Exception as exc:
        #     logger.error(
        #         "An error occurred while creating the base transfer engine: %s", exc)

        # Start HTTP server for config updates
        self.http_ip = os.getenv("HTTP_IP", "0.0.0.0:8080")  # 监听调度器端口
        self.force_stop_on_update = os.getenv("FORCE_STOP_ON_UPDATE", "0").lower() in ("1", "true", "yes")  # 控制是否强制中断未完成的传输

        self.httpd = None
        self.server_thread = None
        self.start_http_server()

        self.simple_buffer = None  # 用于关闭上一级类 Simple Buffer的引用

    def start_http_server(self):
        try:
            server_address = (self.http_ip.split(":")[0], int(self.http_ip.split(":")[1]))
            handler_class = lambda *args: ConfigUpdateHandler(*args, pipe=self)
            self.httpd = socketserver.TCPServer(server_address, handler_class)
            self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
            self.server_thread.start()
            logger.info(f"Started Scheduler HTTP server on {self.http_ip}")
        except Exception as e:
            logger.error(f"Failed to start Scheduler HTTP server: {e}")
            raise

    def close_engine(self, force=False):
        engine = self.transfer_engine
        try:
            if self.simple_buffer is not None and engine is not None:
                if self.kv_rank == 0 and self.simple_buffer.request_handling_thread is None:
                    pass
                else:
                    if force:
                        self.simple_buffer.force_close()
                    else:
                        self.simple_buffer.close()
            if self.transport_thread is not None:
                self.transport_thread.shutdown(wait=not force)
                self.transport_thread = None
                logger.info("Stopped transport_thread.")
            if engine is not None:
                engine.close(force)
                self.transfer_engine = None
                logger.info("Closed old transfer engine.")
        except Exception as e:
            logger.error(f"Failed to close old transfer engine: {e}")
            self.transfer_engine = engine

    def update_config(self, config_data: dict):
        try:
            if self.transfer_engine:
                old_config = self.transfer_engine.config
                new_config = old_config.merge(config_data)
            else:
                new_config = MooncakeTransferEngineConfig.from_dict(config_data)
            new_engine = MooncakeTransferEngine(new_config, self.kv_rank, self.local_rank)
        except Exception as e:
            logger.error(f"Failed to create new transfer engine: {e}")
        else:
            if self.transfer_engine:
                self.close_engine(self.force_stop_on_update)
            if self.transfer_engine is None:
                self.transfer_engine = new_engine
                logger.info("Created new transfer engine.")

    def _select_device(self, device: str) -> torch.device:
        """Select available device (CUDA or CPU)."""
        logger.info("Selecting device: %s", device)
        if device == "cuda":
            return torch.device(f"cuda:{self.local_rank}")
        else:
            return torch.device("cpu")

    def tensor_hash(self, tensor: torch.Tensor) -> int:
        """Calculate the hash value of the tensor."""
        return hash(tensor.data_ptr())

    def _send_impl(self, tensor: torch.Tensor) -> None:
        """Implement the tensor sending logic using safetensors."""
        if self.transfer_engine is not None:
            try:
                # with open('DEBUG.txt', 'a', buffering=65536) as f:
                #         #logger.info("L DEBUG P1:",time.time())
                #         print("L DEBUG P1:",time.time(),file=f)
                self.transfer_engine.send_bytes(safetensors_save({"tensor": tensor}))
                # with open('DEBUG.txt', 'a', buffering=65536) as f:
                #         #logger.info("L DEBUG P2:",time.time())
                #         print("L DEBUG P2:",time.time(),file=f)
            except Exception as e:
                logger.error(f"Failed to _send_impl: {e}")
        else:
            logger.error("Sending tensors failed: No transfer engine.")

    def _recv_impl(self) -> torch.Tensor:
        """Implement the tensor receiving logic using safetensors."""
        if self.transfer_engine is not None:
            try:
                # with open('DEBUG.txt', 'a', buffering=65536) as f:
                #         #logger.info("L DEBUG D1:",time.time())
                #         print("L DEBUG D1:",time.time(),file=f)
                data = self.transfer_engine.recv_bytes()
                # with open('DEBUG.txt', 'a', buffering=65536) as f:
                #         #logger.info("L DEBUG D2:",time.time())
                #         print("L DEBUG D2:",time.time(),file=f)
                return safetensors_load(data)["tensor"].to(self.device)
            except Exception as e:
                logger.error(f"Failed to _recv_impl: {e}")
                return None
        else:
            logger.error("Receiving tensors failed: No transfer engine.")
            return None

    def send_tensor(self, tensor: Optional[torch.Tensor]) -> None:
        """Send tensor to the target process."""
        if self.transport_thread is None:
            self.transport_thread = ThreadPoolExecutor(max_workers=1)
            logger.info("No transport_thread, new transport_thread created.")
        tensor = tensor if tensor is not None else self.none_tensor
        assert (len(tensor.shape) > 0)
        self.transport_thread.submit(self._send_impl, tensor)

    def recv_tensor(self) -> Optional[torch.Tensor]:
        """Receive tensor from other processes."""
        if self.transport_thread is None:
            self.transport_thread = ThreadPoolExecutor(max_workers=1)
            logger.info("No transport_thread, new transport_thread created.")
        tensor = self.transport_thread.submit(self._recv_impl).result()
        if tensor is not None and tensor.numel() == 1 and tensor.item() == NONE_INT:
            return None
        else:
            return tensor

    def close(self) -> None:
        """Cleanup logic when closing the pipe."""
        if self.httpd:
            try:
                self.httpd.shutdown()
                self.server_thread.join(timeout=5)
                logger.info("Stopping HTTP server.")
            except Exception as e:
                logger.error(f"Failed to shutdown HTTP server: {e}")
        if self.transfer_engine is not None:
            self.transfer_engine.close()
            del self.transfer_engine
            logger.info("Closed the transfer engine and cleaned up resources.")
        else:
            logger.info("Transfer engine closed failed: No transfer engine.")

````

simple_buffer.py

````python
# SPDX-License-Identifier: Apache-2.0
"""
    Implements a distributed key-value (KV) cache transfer mechanism.

    Key Features:
    - Distributed KV cache transmission using PyNccl pipes.
    - Non-blocking `insert`, blocking `drop_select`.
    - Use CPU signal pipe to avoid racing condition
    - Handles buffer size constraints and provide backpressure mechanism to
      stop the prefill instance when the decode instance is slow.
"""
import threading
from collections import deque
from typing import Deque, List, Optional, Union

import torch

from vllm.distributed.kv_transfer.kv_lookup_buffer.base import (
    KVLookupBufferBase)
from vllm.distributed.kv_transfer.kv_pipe.base import KVPipeBase
from vllm.logger import init_logger

logger = init_logger(__name__)


class SimpleBuffer(KVLookupBufferBase):

    def __init__(self, signal_pipe: KVPipeBase, data_pipe: KVPipeBase,
                 buffer_size_thresh: float):
        """
        signal_pipe: on CPU

        NOTE: on-device recv will block all threads in the process, making the
        KV cache producer unable to listen to new request while transmitting
        KV cache. Luckily CPU recv only blocks the current thread so we use
        CPU recv to listen to new request.

        data_pipe: on device (e.g. GPU)
        """

        self.buffer: Deque[List[torch.Tensor]] = deque()

        self.buffer_size = 0
        self.buffer_size_threshold = buffer_size_thresh
        self.buffer_cv = threading.Condition()
        self.signal_pipe = signal_pipe
        self.data_pipe = data_pipe
        self.request_handling_thread: Optional[threading.Thread] = None

        self.normal_signal = torch.tensor([0], device="cpu")
        self.end_signal = None

        self.signal_pipe.simple_buffer = self

    def _matches(self, tokens_roi_sender: List[torch.Tensor],
                 tokens_roi_recver: List[torch.Tensor]):

        # tokens_roi_sender: tokens and roi of the producer (in the buffer)
        # tokens_roi_recver: tokens and roi of the consumer (query)

        tokens_sender = tokens_roi_sender[0]
        tokens_recver = tokens_roi_recver[0]
        roi_sender = tokens_roi_sender[1]
        roi_recver = tokens_roi_recver[1]

        if tokens_recver is None:
            # consumer sends an empty request
            # semantics: DROP SELECT * LIMIT 1
            # so any of the data in the buffer can be drop-selected
            return True

        # Assuming that roi is a binary mask on tokens
        tokens_sender = tokens_sender[roi_sender]
        tokens_recver = tokens_recver[roi_recver]

        # simple common prefix matching
        min_length = min(len(tokens_sender), len(tokens_recver))
        if torch.allclose(tokens_sender[:min_length],
                          tokens_recver[:min_length]):
            return min_length

        return 0

    def _send_tensor_and_dec_size(self,
                                  tensor: Optional[torch.Tensor]) -> None:

        assert tensor is not None, "Use self.data_pipe.send(None) instead"
        self.buffer_size -= tensor.element_size() * tensor.numel()
        if tensor.dtype == torch.bool:
            tensor = tensor.float()
        self.data_pipe.send_tensor(tensor)

    def _get_element_size(self, data: Optional[Union[List, torch.Tensor]]):

        if isinstance(data, torch.Tensor):
            return data.element_size() * data.numel()
        if not data:
            # cannot perform `not data` on a tensor
            # so this check needs to go after the check above
            return 0

        raise AssertionError(f"Unknown data type {type(data)}")

    def _add_to_buffer(self, input_tokens: torch.Tensor, roi: torch.Tensor,
                       key: torch.Tensor, value: torch.Tensor,
                       hidden: torch.Tensor):

        if isinstance(input_tokens, torch.Tensor):
            input_tokens = input_tokens.clone()
        if isinstance(roi, torch.Tensor):
            roi = roi.clone()
        if isinstance(key, torch.Tensor):
            key = key.clone()
        if isinstance(value, torch.Tensor):
            value = value.clone()
        if isinstance(hidden, torch.Tensor):
            hidden = hidden.clone()

        buffer_item = [input_tokens, roi, key, value, hidden]
        data_size = sum([self._get_element_size(data) for data in buffer_item])

        with self.buffer_cv:
            if self.buffer_size + data_size > self.buffer_size_threshold:
                # log outside the while loop to avoid this message being logged
                # repeatedly.
                logger.debug("KV transfer buffer is full. Handling...")
                while self.buffer_size + data_size > self.buffer_size_threshold:
                    self.buffer_cv.wait()

            self.buffer_size += data_size
            self.buffer.append(buffer_item)
            self.buffer_cv.notify()

    def _is_end_signal(self, signal):
        return signal is None

    def drop_select_handler(self):

        try:

            while True:
                logger.info("debug simple buffer loop")
                signal = self.signal_pipe.recv_tensor()
                logger.info("debug simple buffer signal")
                if self._is_end_signal(signal):
                    logger.info("Received end signal!")
                    break
                input_tokens = self.data_pipe.recv_tensor()

                roi = self.data_pipe.recv_tensor()
                assert roi is not None, "Please provide the roi when sending "\
                    "drop-select request"
                roi = (roi > 0.5)
                tokens_roi_recver = [input_tokens, roi]

                def is_buffer_available(
                    tokens_roi_recver: List[torch.Tensor], ) -> bool:
                    # perform input tokens and roi matching
                    # FIXME: this matching is O(n), ideally it should be O(1)
                    # but this buffer size won't (and shouldn't) be too large so
                    # the fix is not urgent.
                    for _ in range(len(self.buffer)):
                        if self._matches(self.buffer[0],
                                         tokens_roi_recver) > 0:
                            return True
                        # rotate the element we just accessed to the end
                        self.buffer.rotate(-1)
                    return False

                with self.buffer_cv:
                    while not is_buffer_available(tokens_roi_recver):
                        logger.debug(
                            "KV transfer buffer is not available. Waiting...")
                        self.buffer_cv.wait()
                    # need to clone the tensor
                    # in case the tensor is freed before sending finishes
                    matched_item = self.buffer.popleft()
                    for tensor in matched_item:
                        self._send_tensor_and_dec_size(tensor)
                    self.buffer_cv.notify()

        except RuntimeError as e:
            if 'Connection closed by peer' not in str(e):
                raise e

        logger.debug("Closing drop_select_handler")

    def drop_select(
            self, input_tokens: Optional[torch.Tensor],
            roi: Optional[torch.Tensor]) -> List[Optional[torch.Tensor]]:

        assert self.request_handling_thread is None, \
            "drop_select should be called by the KV cache consumer "\
            "(e.g. the decode vLLM instance)"

        if isinstance(input_tokens, torch.Tensor):
            input_tokens = input_tokens.clone()
        if isinstance(roi, torch.Tensor):
            roi = roi.clone().float()

        self.signal_pipe.send_tensor(self.normal_signal)
        self.data_pipe.send_tensor(input_tokens)
        self.data_pipe.send_tensor(roi)

        input_tokens = self.data_pipe.recv_tensor()
        roi = self.data_pipe.recv_tensor()
        if roi is not None:
            # convert from float tensor to bool tensor
            # as PyNccl does not support sending bool tensor
            roi = (roi > 0.5)
        key = self.data_pipe.recv_tensor()
        value = self.data_pipe.recv_tensor()
        hidden = self.data_pipe.recv_tensor()

        return [input_tokens, roi, key, value, hidden]

    def insert(self, input_tokens: torch.Tensor, roi: torch.Tensor,
               key: torch.Tensor, value: torch.Tensor,
               hidden: torch.Tensor) -> None:

        self._add_to_buffer(input_tokens, roi, key, value, hidden)

        # when calling the insert, the current process is a sender
        # need to launch the request handler and start listening to request.
        if self.request_handling_thread is None:
            self.request_handling_thread = threading.Thread(
                target=self.drop_select_handler)
            self.request_handling_thread.start()

    def close(self):

        if hasattr(self, "request_handling_thread"
                   ) and self.request_handling_thread is not None:
            logger.info("Waiting for end signal to stop request_handling_thread...")
            self.request_handling_thread.join()
            self.request_handling_thread = None
            logger.info("Stopped request_handling_thread.")

        else:
            # TODO: have a explicit close signal and have a explicit way to
            # check if it's requester
            logger.info("Sending end signal to close...")
            self.signal_pipe.send_tensor(self.end_signal)
            logger.info("End signal has been sent.")
            pass

    def force_close(self):
        self.request_handling_thread = None
        logger.info("Closed request_handling_thread.")

````

执行指令示例

````bash
# 自己写的代码和配置默认都放在/home/jtdx/vLLM/workspace中用于映射
# 启动环境
sudo docker run --gpus all -it --network host -v /home/jtdx/vLLM/workspace:/workspace/workspace --name test vllm-cuda128:latest
sudo docker exec -it test /bin/bash

# 旧版通过本地 mooncake_pipe.py 和 simple_buffer.py 覆盖 vLLM 源码的步骤已移除
# 请使用所安装 vLLM 版本正式提供的 Connector 实现

# 在166启动metadata服务，需要在代码中修改监听的IP地址为172.18.0.167
python3 /workspace/workspace/http_server.py

# 在166上启动P实例
FORCE_STOP_ON_UPDATE=0 HTTP_IP=172.18.0.166:8081 CUDA_VISIBLE_DEVICES=0 VLLM_USE_V1=0 MOONCAKE_CONFIG_PATH=/workspace/workspace/mooncake2.json VLLM_USE_MODELSCOPE=True python3 -m vllm.entrypoints.openai.api_server --model /workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B --port 8100 --max-model-len 10000 --gpu-memory-utilization 0.8 --kv-transfer-config '{"kv_connector":"MooncakeConnector","kv_role":"kv_producer","kv_rank":0,"kv_parallel_size":2,"kv_buffer_size":2e9}'

# 在167上启动D实例
FORCE_STOP_ON_UPDATE=0 HTTP_IP=172.18.0.167:8082 CUDA_VISIBLE_DEVICES=1 VLLM_USE_V1=0 MOONCAKE_CONFIG_PATH=/workspace/workspace/mooncake2.json VLLM_USE_MODELSCOPE=True python3 -m vllm.entrypoints.openai.api_server --model /workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B --port 8101 --max-model-len 10000 --gpu-memory-utilization 0.8 --kv-transfer-config '{"kv_connector":"MooncakeConnector","kv_role":"kv_consumer","kv_rank":1,"kv_parallel_size":2,"kv_buffer_size":2e9}'

# 下发配对指令
curl -X POST http://172.18.0.166:8081/update -H "Content-Type: application/json" -d '{"prefill_url": "172.18.0.166:12013","decode_url": "172.18.0.167:13016","metadata_server": "http://172.18.0.167:8080/metadata","protocol": "tcp","device_name": ""}'
curl -X POST http://172.18.0.167:8082/update -H "Content-Type: application/json" -d '{"prefill_url": "172.18.0.166:12013","decode_url": "172.18.0.167:13016","metadata_server": "http://172.18.0.167:8080/metadata","protocol": "tcp","device_name": ""}'


# 启动代理 假设代理放在167上
python3 /workspace/workspace/disagg_proxy_demo.py --model /workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B --prefill 172.18.0.166:8100 --decode 172.18.0.167:8101 --port 8000 --host 172.18.0.167

# 用户发送请求
curl http://172.18.0.167:8000/v1/chat/completions -H "Content-Type: application/json" -d '{"model": "/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B","messages": [{"role": "system", "content": "You are a helpful assistant."},{"role": "user", "content": "Who are you?"}]}'

curl -s http://172.18.0.167:8000/v1/completions -H "Content-Type: application/json" -d '{"model": "/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B","prompt": "What is DeepSeek?","max_tokens": 1000}'

# 断开配对
curl -X POST http://172.18.0.167:8082/close -H "Content-Type: application/json" -d '{"force_stop": "0"}'
curl -X POST http://172.18.0.166:8081/close -H "Content-Type: application/json" -d '{"force_stop": "1"}'
````



## MooncakeStoreConnector例程

假设为单机，ip地址为`172.18.0.250`，http server的代码和proxy的代码与MooncakeConnector的例程相同：

````bash
# 启动metadata服务
python3 /workspace/workspace/http_server.py

# 启动mooncake_master
mooncake_master --port 50001

# 启动p阶段vllm服务
MOONCAKE_CONFIG_PATH=/workspace/workspace/mooncake.json VLLM_USE_V1=0 python3 -m vllm.entrypoints.openai.api_server --model /workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B --port 8100 --max-model-len 10000 --gpu-memory-utilization 0.8 --kv-transfer-config '{"kv_connector":"MooncakeStoreConnector","kv_role":"kv_producer"}'

# 启动d阶段vllm服务
CUDA_VISIBLE_DEVICES=1 MOONCAKE_CONFIG_PATH=/workspace/workspace/mooncake.json VLLM_USE_V1=0 python3 -m vllm.entrypoints.openai.api_server --model /workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B --port 8101 --max-model-len 10000 --gpu-memory-utilization 0.8 --kv-transfer-config '{"kv_connector":"MooncakeStoreConnector","kv_role":"kv_consumer"}'

# 启动代理，管理pd两阶段任务
python3 /workspace/workspace/disagg_proxy_demo.py --model /workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B --prefill localhost:8100 --decode localhost:8101 --port 8000

# 发送请求
curl -s http://localhost:8000/v1/completions -H "Content-Type: application/json" -d '{"model": "/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B","prompt": "What is DeepSeek?","max_tokens": 1000}'

curl http://localhost:8000/v1/chat/completions -H "Content-Type: application/json" -d '{"model": "/workspace/workspace/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B","messages": [{"role": "system", "content": "You are a helpful assistant."},{"role": "user", "content": "Who are you?"}]}'
````

mooncake.json

````json
{
    "local_hostname": "172.18.0.167",
    "metadata_server": "http://172.18.0.167:8080/metadata",
    "protocol": "tcp",
    "device_name": "",
    "master_server_address": "172.18.0.167:50001"
}
````



## 常见问题

**OOM out of memory问题**

显存爆了，如果是之前在这台机器可以运行这个模型，现在不行了，可能是之前运行的vLLM进程没关，可以找出来关掉，不行可以试着重启电脑。还有一种可能是多个vLLM实例选择的显卡号冲突，可以检查一下 CUDA_VISIBLE_DEVICES 参数。

**启动vLLM实例时无法有效选择是P还是D的问题**

检查 `kv_role`、`kv_rank` 以及 `mooncake.json`中的参数是否设置正确。
