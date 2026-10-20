<p align="center">
  <img src="doc/images/logo.png" alt="PDSchedule logo" width="680">
</p>

# 简介

**PDSchedule** 是一种基于 **vLLM** 的 LLM Prefill/Decode 分离推理调度平台。系统实时感知 P/D Gateway Pool 与池内实例状态，通过分层调度动态选择 Prefill 和 Decode 资源，并由统一 Connector 协议负责底层 KV 传输协商；首期运行时 Connector 仅支持 **NIXL** 与 **LMCache**。

# 架构

项目结构图如下所示，点击 [这里](./doc/项目代码结构说明.md) 查看关于项目代码结构的具体说明。

![img](doc/images/struct.png)

- 由用户请求发送模块发起推理任务，发送prompt给全局控制器做全局资源池选择。
- 全局控制器收到请求后解析请求和标识信息并构造推理任务，通过调度策略选择合适的 Prefill 与 Decode 推理算力池，并转发推理任务请求。
- 算力池网关接收任务请求后通过调度策略选择池内的推理实例并转发请求。
- 实例代理接收请求并转发给vLLM实例，并实时感知实例状态。
- vLLM实例完成推理任务，并在KV Cache传输时提供压缩功能。

# 环境说明

TTFT/TPOT 仅发布测量、拟合和预测代码，不附带实验机器的已拟合模型。部署时在目标
环境自行校准并将模型保存在本地；缺少 Controller 模型时 `demand_aware` 使用已有
fallback。合成冷启动结果不代表实测精度，详见 [预测器说明](./profile/README.md)。

项目当前版本使用 Docker 环境。旧版环境学习笔记已移出发布树，新的环境安装指南将在后续编写。当前环境记录如下，版本兼容性仍需在发布前统一验证：

- Ubuntu 22.04
- vLLM 0.13.0
- LMCache 3.11
- Pytorch 2.9.1

此外还需要一个Redis容器环境，点击 [这里](./doc/docker/Redis安装笔记.md) 查看环境安装相关记录：

- Redis 7

# 快速开始

此项目在 vLLM PD分离方案的基础上进行推理池和推理实例的选择与动态配对，点击 [这里](./doc/PD分离案例.md) 查看PD分离基础案例。

当前运行时配对协议仅支持 NIXL 与 LMCache。文档中的 Mooncake Store 仅作为 LMCache 存储后端的部署材料，不代表支持其他 P2P Connector。以下指令为在 **实验室8卡5090机器** 上的启动指令：

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

主要启动参数包括 `--host`、`--port`、`--controller`、`--gateway`、`--prefill`、`--decode`、`--connector-name`、`--connector-version` 和 `--connector-metadata`。可参考 `run_166.sh`、`run_167.sh`、`run_169.sh`、`run_170.sh`。

Prefill 网关可通过 `--prefill-scheduler-strategy` 选择 `slo_aware_prefill`、`jsq` 或 `round_robin`；Decode 网关可通过 `--decode-scheduler-strategy` 选择 `round_robin`、`lowest_generation_throughput` 或 `slo_aware_decode`。网关默认启动交互式 CLI，运行期间可使用 `show scheduler` 查看策略，使用 `switch scheduler -m <mode>` 在线切换策略。systemd、nohup 或无 TTY 容器部署时请增加 `--no-cli`。

#### P 网关 JSQ 与 D 网关吞吐量调度

P 网关的 `jsq` 不等待或按 token 形成逻辑 batch：每个请求到达后，以网关维护的各实例“未完成输入 token 总数”独立执行 token-weighted JSQ，并立即转发到负载最低的 P 实例。每个请求使用 `Prompt.token_length` 作为权重，不包含主要属于 Decode 阶段的输出预算 `max_tokens`；预约会立即影响下一个请求的选择，并在请求成功、失败或取消后释放。P heartbeat 上报的是请求数，不能与 token 负载混加，因此不会覆盖 P 网关维护的 token 预约；异常泄漏保护 TTL 为 240 秒。D 网关的 `lowest_generation_throughput` 会立即选择当前生成吞吐量最低的在线 D 实例；`slo_aware_decode` 则按 TPOT 预测优先选择不会违反 SLO 的候选实例。策略保留既有 NIXL Pull 参数链路；P 网关的 `jsq` 关闭自适应策略切换和 Prefill 抢占。

```bash
python entrypoints/gatewayP.py \
  --prefill-scheduler-strategy jsq \
  --no-ttft-warmup

python entrypoints/gatewayD.py \
  --decode-scheduler-strategy lowest_generation_throughput \
  --no-tpot-warmup
```

vLLM 的 continuous batching 由实例自身的 `max_num_batched_tokens` 等参数控制，P 网关 `jsq` 不提供逻辑 batch 大小参数。该基线不包含论文的 placement algorithm、周期性 workload replan、模型迁移或资源重配置。

P 侧 JSQ 未完成 token 预约保存在网关进程内存中；网关重启后不能从仅包含请求数的实例心跳恢复原 token 负载。正常请求结束会立即释放预约，异常情况下预约最多保留 240 秒。

#### 网关调度、时间追踪与资源日志

P/D 网关会在项目根目录自动创建 `gateway_logs/`，并将三类日志严格分开：

- `prefill_gateway_schedule.log`、`decode_gateway_schedule.log`：策略、候选实例、选择结果、选择来源和选择理由。
- `prefill_gateway_timing.log`、`decode_gateway_timing.log`：请求生命周期事件及 Prefill 排队/执行、D 网关排队、首响应等待、Decode 流式生成和端到端耗时。
- `prefill_gateway_resource.log`、`decode_gateway_resource.log`：实例逐卡 GPU 利用率、真实显存容量占用率和实例网卡利用率。

日志为 UTF-8 JSON Lines，每行可独立解析。可通过 `FLEXPD_GATEWAY_LOG_DIR` 修改统一日志目录，通过 `FLEXPD_RESOURCE_LOG_INTERVAL_SEC` 修改资源日志最小写入间隔（默认 1 秒），通过 `FLEXPD_RESOURCE_ZERO_PAUSE_SEC` 修改连续全零静默阈值（默认 30 秒）。资源组全零达到阈值后仅写一次 `logging_paused`，后台继续采样；恢复非零时写 `logging_resumed` 并自动恢复记录。

实例代理使用 `--gpu-indices` 声明其对应的物理 GPU，TP 实例使用逗号分隔，例如：

```bash
python entrypoints/instanceP.py ... --connector-name nixl \
  --connector-metadata '{"side_channel_host":"10.0.0.1","side_channel_port":5557,"tp_size":2}' \
  --gpu-indices 0,1
python entrypoints/instanceD.py ... --connector-name nixl \
  --connector-metadata '{"side_channel_host":"10.0.0.2","side_channel_port":5558,"tp_size":1}' \
  --gpu-indices 2
```

时间日志中的 `observed_network_transfer_ms` 是 D 网关从向 D 实例发出请求到收到首个响应 chunk 的可观测时间，包含 NIXL KV 拉取、vLLM 接纳/内部排队和首 token 计算，并非纯链路传输时间。网关排队时间只统计网关可直接观测的等待，不伪造 vLLM 内部阶段。

交互式网关 CLI 支持上下方向键查看历史，并支持 Tab 自动补全命令和当前网关可用的调度策略。

#### Connector 协议与 Pool 约束

每个 P Gateway 或 D Gateway Pool 只能配置一个 Connector，所属实例必须使用同名、同版本 Connector；LMCache 实例还必须使用相同的 `backend`、`namespace` 和 `config_fingerprint`。不同 Pool 可以分别使用 NIXL 或 LMCache。Controller 先构造 Connector 兼容的 P/D Pool 候选，再运行全局调度策略；同一次 PD 会话不支持跨 Connector 桥接。

NIXL 使用 `request_scoped` 会话并在通用 `connector_context` 中携带请求 ID 与 `kv_transfer_params`。LMCache 使用 `shared_backend` 逻辑会话，只校验共享后端配置，不发起远程配对 RPC。服务可通过 `GET /connectors` 查看能力，Gateway 还提供 `/connector/sessions` 会话管理接口。旧 `/pd/pair` 与 `/pd/close` 已删除。

四个 Gateway/Instance 启动入口的 `--connector-name` 仅接受 `nixl` 或 `lmcache`，默认 `nixl`；未支持的 Connector 名称会被拒绝。NIXL 的兼容性校验不要求 P/D 使用相同 TP 数量，具体可运行的不对称 TP 组合仍取决于所安装 vLLM/NIXL 的能力与实测结果。

#### 单 P/D 网关自动直连

论文调度实验可以使用一个 P 网关管理全部 Prefill 实例、一个 D 网关管理全部 Decode 实例。P/D Gateway 完成动态注册后，Controller 若检测到活跃资源恰好为一个 P Pool 和一个 D Pool，会直接选择唯一组合，不运行 PSO 等全局配对算法，也不要求额外的 P→D 网络记录。实例级策略仍由现有 P/D Gateway 调度器执行。

```bash
python entrypoints/controller.py \
  --host 0.0.0.0 \
  --port 5000
```

该快速路径完全由当前活跃 Pool 数量决定，不需要路由模式或固定地址参数。拓扑扩展为多个 P 或多个 D Gateway 后，Controller 会自动恢复当前全局调度策略；若 P 或 D 尚未注册，则继续报告资源未就绪。Controller CLI 可使用 `show scheduler` 查看当前拓扑与调度策略。

#### 后续直接增加网关策略的检查表

P/D 网关均使用统一策略协议和 manager 注册表，不再按 sync/async 拆分策略文件。增加策略时按以下位置逐项核对：

- P 策略：在 `scheduler/gateway/prefill/` 增加与公开策略同名的文件，实现 `strategy.py` 定义的统一异步接口、能力声明及必要生命周期钩子，再将策略类加入 `manager.py` 注册表。manager 不包含具体算法分支。
- D 策略：在 `scheduler/gateway/decode/` 增加与公开策略同名的文件，实现 `strategy.py` 定义的统一异步接口，再将策略类加入 `manager.py` 注册表。D 策略必须直接返回实例，不支持等待队列。
- 将策略名加入 `store/gateway/manager.py` 对应的 P/D 白名单。
- 将策略名加入 `entrypoints/gatewayP.py` 或 `entrypoints/gatewayD.py` 的启动参数合法值。
- 将策略名加入 `core/consoles/gateway.py` 的对应角色列表，使在线切换和 Tab 自动补全一致。
- 增加策略单元测试，并核对未知策略、无在线实例、能力驱动的队列行为、资源释放和在线切换边界。

`Task.scheduler_metadata` 是 P→D 内部使用的空扩展容器。后续策略如需跨阶段传递少量 JSON 数据，应使用策略名作为一级命名空间；该字段不会进入发送给 vLLM 实例的标准或 NIXL payload。

```bash
# 进入项目所在目录
cd /workspace/llm-stack/workspace_LLL/FlexPD
# 启动脚本 (脚本需777)
./run.sh
```

### 3 网页交互

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

# 许可证

本项目采用 [Apache License 2.0](./LICENSE)，作者信息暂保留占位值。

