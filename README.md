![img](doc/images/logo.png)

# 简介

**FlexPD** 是一种基于**vLLM**、**LMCache**和**Mooncake**开发的LLM推理任务调度平台，专注于实现 Prefill 阶段与 Decode 阶段的算力分离与灵活配对。在大语言模型推理过程中，Prefill 阶段需要高并行计算能力且耗时较短，而 Decode 阶段则需持续占用计算资源但算力需求相对较低；传统静态调度方式常导致两阶段资源争抢或利用率不足，难以有效利用分布式的零散算力。为此，FlexPD 设计了一套轻量级调度算法，能够实时感知可用的 Prefill 与 Decode 算力资源，并根据任务队列长度、模型结构、输入输出长度等特征，动态将两阶段计算任务匹配至合适的算力节点。该系统通过解耦并灵活配对 Prefill 与 Decode 算力，显著提升异构算力资源的整体利用率，支持在分布式环境下实现更低延迟、更高吞吐的大语言模型推理服务。

# 架构

项目结构图如下所示，点击 [这里](./doc/项目代码结构说明.md) 查看关于项目代码结构的具体说明。

![img](doc/images/struct.png)

- 由用户请求发送模块发起推理任务，发送prompt给全局控制器做全局资源池选择。
- 全局控制器收到请求后解析请求和标识信息并构造推理任务，通过embed模型分析prompt并与知识库中条目比对，通过调度策略选择合适的Prefill与Decode推理算力池，并转发推理任务请求。
- 算力池网关接收任务请求后通过调度策略选择池内的推理实例并转发请求。
- 实例代理接收请求并转发给vLLM实例，并实时感知实例状态。
- vLLM实例完成推理任务，并在KV Cache传输时提供压缩功能。

# 环境说明

项目当前版本使用Docker环境，点击 [这里](./doc/docker/vLLM-Docker环境安装笔记.md) 参考查看环境安装相关笔记：

- Ubuntu 22.04
- vLLM 0.13.0
- LMCache 3.11
- Pytorch 2.9.1
- Mooncake 0.3.8 (可选)

此外还需要一个Redis容器环境，点击 [这里](./doc/docker/Redis安装笔记.md) 查看环境安装相关记录：

- Redis 7

# 快速开始

此项目在 vLLM PD分离方案的基础上进行推理池和推理实例的选择与动态配对，点击 [这里](./doc/PD分离案例.md) 查看PD分离基础案例。

此项目的旧版本使用基于MooncakeP2PConnector的PD分离方案，点击 [这里](./doc/Mooncake版本.md) 查看旧版本快速开始说明。

当前版本使用基于LMCacheConnectorV1的PD分离方案，以下指令为在 **实验室8卡5090机器** 上的启动指令：

### 1 启动环境

启动 vLLM 容器环境

```bash
# 新建容器
sudo docker run --gpus all --shm-size=16g -it --network host -v /llm-stack:/workspace/llm-stack --name test cacheroute:vllm0.13-lmcache3.11-pytorch2.9.1

sudo docker run --shm-size=16g -it --network host -v /llm-stack:/workspace/llm-stack --name test-proxyp cacheroute:vllm0.13-lmcache3.11-pytorch2.9.1

sudo docker run --shm-size=16g -it --network host -v /llm-stack:/workspace/llm-stack --name test-proxyd cacheroute:vllm0.13-lmcache3.11-pytorch2.9.1

# 启动容器
sudo docker start test
# 打开容器
sudo docker exec -it test /bin/bash
# 停止容器
sudo docker stop test
# 关闭容器
sudo docker rm test
```

启动 Redis 容器环境

```bash
# 新建容器
sudo docker run -d --name flexpd-redis -p 6378:6379 redis:7 redis-server --save "" --appendonly no --maxmemory 200gb --maxmemory-policy allkeys-lru
# 打开容器
sudo docker exec -it flexpd-redis /bin/bash
# 清除 Redis 缓存
redis-cli flushall
# 查看 Redis 缓存大小
redis-cli info memory | grep used_memory_human
```

### 2 启动系统

`config/ADDRESS_CONFIG.py` 只保存公共默认端口，不保存机器拓扑。各模块的 IP、上级服务地址、实例列表和 NIXL 参数通过启动参数或 `FLEXPD_*` / `VLLM_NIXL_*` 环境变量提供。实例启动后会向所属网关动态注册；部署新机器时无需复制或修改 Python 地址配置。

主要启动参数包括 `--host`、`--port`、`--controller`、`--gateway`、`--prefill`、`--decode`、`--side-channel-host`、`--side-channel-port` 和 `--tp-size`。可参考 `run_166.sh`、`run_167.sh`、`run_169.sh`、`run_170.sh`。

Prefill 网关可通过 `--prefill-scheduler-strategy` 选择 `least_load`、`round_robin`、`pso_new` 或 `distserve`；Decode 网关可通过 `--decode-scheduler-strategy` 选择 `round_robin`、`lowest_generate_throughput`、`slo_tpot_admission` 或 `distserve`。网关默认启动交互式 CLI，运行期间可使用 `show scheduler` 查看策略，使用 `switch scheduler -m <mode>` 在线切换策略。systemd、nohup 或无 TTY 容器部署时请增加 `--no-cli`。

#### DistServe 在线调度基线

`distserve` 复现 DistServe 4.3 的网关在线调度部分。P 网关不等待或按 token 形成逻辑 batch：每个请求到达后，以网关维护的各实例“未完成输入 token 总数”独立执行 token-weighted JSQ，并立即转发到负载最低的 P 实例。每个请求使用 `Prompt.token_length` 作为权重，不包含主要属于 Decode 阶段的输出预算 `max_tokens`；预约会立即影响下一个请求的选择，并在请求成功、失败或取消后释放。P heartbeat 上报的是请求数，不能与 token 负载混加，因此不会覆盖 P 网关维护的 token 预约；异常泄漏保护 TTL 为 240 秒。D 网关继续以原有请求数有效负载定义立即选择最低负载实例。并列时按实例地址稳定选择。策略保留既有 NIXL Pull 参数链路，并关闭自适应策略切换和 Prefill 抢占。

```bash
python entrypoints/gatewayP.py \
  --prefill-scheduler-strategy distserve \
  --distserve-prefill-batch-tokens 2048 \
  --no-ttft-warmup

python entrypoints/gatewayD.py \
  --decode-scheduler-strategy distserve \
  --no-tpot-warmup
```

`--distserve-prefill-batch-tokens` 仅作为旧部署命令的兼容参数保留，当前 request-level direct-JSQ P 调度会忽略它；vLLM 的 continuous batching 由实例自身的 `max_num_batched_tokens` 等参数控制。该基线不包含论文的 placement algorithm、周期性 workload replan、模型迁移或资源重配置。四机中心网关部署参见 `DistServe启动脚本/README.md`。

DistServe P 侧未完成 token 预约保存在网关进程内存中；网关重启后不能从仅包含请求数的实例心跳恢复原 token 负载。正常请求结束会立即释放预约，异常情况下预约最多保留 240 秒。

#### 网关调度、时间追踪与资源日志

P/D 网关会在项目根目录自动创建 `gateway_logs/`，并将三类日志严格分开：

- `prefill_gateway_schedule.log`、`decode_gateway_schedule.log`：策略、候选实例、选择结果、选择来源和选择理由。
- `prefill_gateway_timing.log`、`decode_gateway_timing.log`：请求生命周期事件及 Prefill 排队/执行、D 网关排队、首响应等待、Decode 流式生成和端到端耗时。
- `prefill_gateway_resource.log`、`decode_gateway_resource.log`：实例逐卡 GPU 利用率、真实显存容量占用率和实例网卡利用率。

日志为 UTF-8 JSON Lines，每行可独立解析。可通过 `FLEXPD_GATEWAY_LOG_DIR` 修改统一日志目录，通过 `FLEXPD_RESOURCE_LOG_INTERVAL_SEC` 修改资源日志最小写入间隔（默认 1 秒），通过 `FLEXPD_RESOURCE_ZERO_PAUSE_SEC` 修改连续全零静默阈值（默认 30 秒）。资源组全零达到阈值后仅写一次 `logging_paused`，后台继续采样；恢复非零时写 `logging_resumed` 并自动恢复记录。

实例代理使用 `--gpu-indices` 声明其对应的物理 GPU，TP 实例使用逗号分隔，例如：

```bash
python entrypoints/instanceP.py ... --tp-size 2 --gpu-indices 0,1
python entrypoints/instanceD.py ... --tp-size 1 --gpu-indices 2
```

时间日志中的 `observed_network_transfer_ms` 是 D 网关从向 D 实例发出请求到收到首个响应 chunk 的可观测时间，包含 NIXL KV 拉取、vLLM 接纳/内部排队和首 token 计算，并非纯链路传输时间。网关排队时间只统计网关可直接观测的等待，不伪造 vLLM 内部阶段。

交互式网关 CLI 支持上下方向键查看历史，并支持 Tab 自动补全命令和当前网关可用的调度策略。

#### 单 P/D 网关的固定路由模式

论文调度实验可以使用一个 P 网关管理全部 Prefill 实例、一个 D 网关管理全部 Decode 实例。Controller 使用 `fixed_gateway` 后只写入这两个固定网关，不再执行 P/D 池选择；实例级策略仍由现有 P/D 网关调度器执行。

```bash
python entrypoints/controller.py \
  --host 0.0.0.0 \
  --port 5000 \
  --routing-mode fixed_gateway \
  --fixed-prefill-gateway 172.18.0.170:8081 \
  --fixed-decode-gateway 172.18.0.166:8082
```

也可以使用环境变量：

```bash
export FLEXPD_CONTROLLER_ROUTING_MODE=fixed_gateway
export FLEXPD_FIXED_PREFILL_GATEWAY=172.18.0.170:8081
export FLEXPD_FIXED_DECODE_GATEWAY=172.18.0.166:8082
```

固定地址缺失或格式不是 `host:port` 时 Controller 会快速报错。原有 `global` 路由模式仍然保留。Controller CLI 可使用 `show scheduler` 核对当前路由模式和目标网关。

#### 后续直接增加网关策略的检查表

本项目继续使用现有策略管理器，不使用额外 registry 或 plugins 目录。增加策略时按以下位置逐项核对：

- P 策略：在 `scheduler/gateway/prefill/` 增加实现，并在 `scheduler/gateway/prefill/manager.py` 中导入、初始化和增加 `schedule_batch()` 分支。
- D 策略：在 `scheduler/gateway/decode/` 增加实现或选择函数，并在 `scheduler/gateway/decode/async_base.py` 的 `schedule_async()` 中增加即时选择分支。D 策略必须直接返回实例，不支持等待队列。
- 将策略名加入 `store/gateway/manager.py` 对应的 P/D 白名单。
- 将策略名加入 `entrypoints/gatewayP.py` 或 `entrypoints/gatewayD.py` 的启动参数合法值。
- 将策略名加入 `core/consoles/gateway.py` 的对应角色列表，使在线切换和 Tab 自动补全一致。
- 增加策略单元测试，并核对未知策略、无在线实例和在线切换边界。

`Task.scheduler_metadata` 是 P→D 内部使用的空扩展容器。后续策略如需跨阶段传递少量 JSON 数据，应使用策略名作为一级命名空间；该字段不会进入发送给 vLLM 实例的标准或 NIXL payload。

在 Ubuntu 部署机上可使用根目录的 `sync.sh` 同步代码并修复 Shell 脚本执行权限：

```bash
# 默认同步 origin/p2p-flexpd
./sync.sh

# 覆盖远端名和分支名
./sync.sh origin p2p-flexpd
```

脚本会先把 staged、unstaged 和未跟踪的本地改动保存到临时 stash，再获取远端代码。只有本地独有路径的改动会以普通未提交工作区改动恢复；同一路径只要本地和远端都发生修改，即使 Git 能自动合并，当前工作区也采用远端完整版本，原本地内容保留在脚本输出的 stash 中供人工检查。此时脚本返回状态码 `2`，可使用 `git stash show -p <stash-ref>` 查看备份。脚本不会提交恢复后的本地改动，也不会自动推送代码。

```bash
# 进入项目所在目录
cd /workspace/llm-stack/workspace_LLL/FlexPD
# 启动脚本 (脚本需777)
./run.sh
```

### 3 注册知识（可选）

启动多窗口脚本后，在 “9.KDN-CLI” 窗口输入指令进行知识注入

注册文本，选择包含知识的文本文件路径

```bash
# 关于LLM的知识
:file /workspace/llm-stack/workspace_LLL/FlexPD/kdn_server/prompts/req1.txt
```

注册缓存，选择包含知识的文本文件路径、计算缓存的vLLM接口、计算缓存的模型型号

```bash
# 关于LMCache的知识
:buildkv_file /workspace/llm-stack/workspace_LLL/FlexPD/kdn_server/prompts/req2.txt --api-url http://172.18.0.169:8001/v1/chat/completions --model /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B
```

### 4 网页交互

系统启动后，可以通过访问 "3.Controller" 提供的IP地址和端口来查看系统管理页面

除此之外，还可以通过用户侧网页脚本发送请求，试验全流程

```bash
# 进入目录
cd /workspace/llm-stack/workspace_LLL/FlexPD/examples/clientUI
# 启动脚本，在脚本提供的网页中发送请求
python3 client.py
```

# 常见问题

```bash
# 使用脚本 多窗口同时启动 时遇到这个问题
# Error constructing proxy for org.gnome.Terminal:/org/gnome/Terminal/Factory0: Failed to execute child process “dbus-launch” (No such file or directory)
sudo apt install dbus-x11
```

