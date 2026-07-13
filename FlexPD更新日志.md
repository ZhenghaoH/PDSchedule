# 更新日志

本项目的所有重大变更都将记录在此文件中。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

## [未发布] - 2026-03-09

### 修改

- 修改 `store/gateway/manager.py`：Decode 池负载统计改为汇总所有在线 Decode 实例的 `running_reqs + waiting_reqs`，并继续通过现有 `running_reqs` 字段上报，使全局 Least Load 调度能够感知等待中的请求
- 修改 `test/test_affinity_scheduler.py`：为 TTFT benchmark 增加按 round 自动切换的问题池，baseline、warmup 和 affinity hit 每轮都使用新的问题文本，避免重复提问；当轮数超过预置问题数时自动生成新问题
- 修改 `scheduler/controller/scheduler_RoundRobin.py`：将 RoundRobin 调度从“Prefill 池独立轮询 + Decode 池独立轮询”改为“所有 P-D 配对整体轮询”；调度器内部改用单一配对索引，并新增当前选中配对的调度日志
- 修改 `instance/perception/profile_throughput.py`：调整吞吐统计口径；`prompt_throughput` 改为“并发输入 token 总数 / 最长 TTFT”，`gen_throughput` 改为按流式增量文本逐秒累计生成 token 后取峰值；结果中新增最长 TTFT 展示
- 修改 `instance/perception/profile_throughput.py`：将吞吐测试拆分为 `prefill` 与 `decode` 两个固定阶段；`prefill` 使用 `1024 -> 10`，`decode` 使用 `10 -> 1024`；继续真实向 vLLM 发送流式请求，并增加实际生成 token 总量与基于流式增量文本的 token 计数统计
- 修改 `instance/perception/profile_throughput.py`：将最大生成吞吐量测试改为按默认并发列表逐组向 vLLM 发送真实请求；保留真实流式请求压测方式，逐组输出吞吐结果，并在末尾汇总 `BEST_CONCURRENCY` 和 `MAX_GEN_TPS`
- 修改 `store/controller/pool_manager.py`、`scheduler/controller/global_scheduler.py`、`core/handlers/controller/proxy.py`：将 Decode 负载释放方案进一步收敛为“按 D 代理地址反查池并释放”，不再依赖 `request_id -> pool_id` 映射，也不再要求修改 `request.py`
- 修改 `store/controller/pool_base.py`、`store/controller/pool_manager.py`、`scheduler/controller/global_scheduler.py`、`core/schemas/request.py`、`core/handlers/controller/proxy.py`、`core/handlers/controller/update.py`：将 Decode `current_load` 切换为调度侧请求账本维护；在调度时按 `prompt_length / mem_bandwidth` 立即增加负载，在请求完成时于控制器代理层精确释放；同时注释保留旧的 Decode 心跳写负载逻辑
- 修改 `coord/instance/up_store.py`、`instance/perception/metric.py`、`store/gateway/instance.py`、`store/gateway/manager.py`、`core/handlers/gateway/decode/update.py`：删除 `iteration_tokens_total / GPU_MemBand` 相关的 Decode 负载感知与定义代码，保留 GPU 带宽占用率测试日志但不再参与 `current_load`
- 修改 `store/controller/pool_base.py`、`store/controller/pool_manager.py`、`scheduler/controller/global_scheduler.py`：引入 Decode 预约负载（shadow load）机制；调度器选中 Decode 池后立即增加一笔带 TTL 的影子负载，Decode 池有效负载改为 `max(observed_load, reservation_load)`，用于避免冷启动阶段被误判为空载
- 修改 `coord/instance/up_store.py`：在 Instance 代理测试日志中补充 `running_reqs` 与 `waiting_reqs` 打印，便于和 GPU `memory_util_percent` 对照观察
- 修改 `instance/perception/metric.py`、`coord/instance/up_store.py`：新增 GPU 带宽占用率测试能力；实例侧通过 NVML 读取 `memory utilization` 与 `gpu utilization`，仅在 Instance 代理日志中打印观测值，不接入当前上报 payload
- 修改 `instance/perception/metric.py`、`coord/instance/up_store.py`：将 `iteration_tokens_total` 的提取与速率计算逻辑下沉到 `metric.py`，统一管理 Histogram 解析和差分状态，使 `up_store.py` 仅负责实例指标上报
- 修改 `instance/perception/metric.py`、`coord/instance/up_store.py`：修正 `vllm:iteration_tokens_total` 的读取方式；Prometheus 解析结果新增 `sample.name`，实例侧优先提取 Histogram 的 `_sum`（回退 `_count`）来计算 `iteration_tokens_total_rate`，避免 Decode 负载长期为 `0.0`
- 修改 `coord/instance/up_store.py`、`core/handlers/gateway/decode/update.py`、`store/gateway/instance.py`、`store/gateway/manager.py`：将 Decode 池 `current_load` 的计算口径改为 `iteration_tokens_total_rate / mem_bandwidth`；实例侧从 vLLM Prometheus 的 `vllm:iteration_tokens_total` 计算近期速率并上报，网关侧聚合速率后再做归一化
- 修改 `core/consoles/controller.py`：新增 CLI 命令 `switch scheduler -m <mode>`，可在运行时切换全局调度策略；同时补充命令帮助信息和补全词
- 修改 `scheduler/controller/scheduler_LeastLoad.py`：增加 Least Load 调度日志，在每次调度时打印 Prefill/Decode 池负载列表及最终选中的最低负载池，便于核对负载更新与调度结果
- 修改 `utils/network_metric.py`：新增 Top N 活跃接口调试能力，支持按采样窗口列出流量最高的接口及其类型提示；单独运行时可周期性打印 Top 接口列表，帮助定位真实承载流量的网卡、bridge 或 veth
- 修改 `utils/network_metric.py`：增强为调试版，在保持 `get_interface_metrics()` 接口和原字段兼容的前提下，追加目标网卡理论速率与全局总流量调试字段；单独运行时同时展示目标网卡和全局总带宽，便于与 Ubuntu 性能监视器对照
- 修改 `utils/pcie_metric.py`：移除人工配置作为 PCIe 最大带宽主基线，改为直接使用运行时探测到的当前链路速度或最大链路速度；仅在探测失败时回退到默认值
- 修改 `utils/pcie_metric.py`：按第一版保守估算策略重构 PCIe 可用带宽计算，最大带宽改为配置优先；当前占用带宽取 `max(TX, RX)`；增加基于 `gpu_index` 的 EMA 平滑和安全系数折扣，最终上报更平滑的保守可用带宽
- 修改 `utils/pcie_metric.py`：将 PCIe `bandwidth` 的语义调整为“剩余可用带宽”，按“最大带宽减去实时吞吐”计算；增加短窗口多次采样、带宽基线和 `MY_PCIE_BANDWIDTH_FLOOR` 保底逻辑
- 修改 `utils/pcie_metric.py`：新增基于 NVML 的 PCIe 实时探测逻辑，优先读取 GPU 的 PCIe 吞吐与链路速度；当缺少 Python 绑定或探测失败时，回退到环境变量和默认值，并保留单文件持续监控入口
- 修改 `core/handlers/controller/update.py`：`/network/update` 支持可选的 `pcie_bandwidth` 和 `pcie_rtt` 上报；按链路逐条更新带宽，使用 `proxy_ip_address` 相同判定同机链路，并将 PCIe 指标直接写入现有 `bandwidth`
- 修改 `coord/gateway/up_store.py`：网络上报时同时采集并上报 NIC 带宽和 PCIe 指标，供控制器区分同机与跨机链路使用
- 重构 `core/lifespans/controller.py`：将 `PoolManager` 和 `NetworkManager` 实例存储到 `app.state`，实现全局共享
- 重构 `core/handlers/controller/update.py`：所有处理函数改为使用 `request.app.state` 获取全局实例，替代原有的独立创建方式；适配新的网络上报格式，实现根据网关类型自动更新对应连接；更新 register_prefill_pool 和 register_decode_pool 函数，使其调用 register_Pool 并接收返回的 ID
- 修改 `coord/gateway/up_store.py`：网络上报架构调整，网关不再上报 peer 信息，仅上报自己的 IP、端口和带宽
- 修改 `store/controller/network.py`：添加自动 PD 池连接创建，实现负载基于的带宽计算
- 修改 `store/controller/pool_manager.py`：重构 register_Pool 函数，使其负责 ID 分配和防重复检查，当池的类型、IP 和端口都匹配时，直接使用之前的 ID
- 修改 `scheduler/controller/global_scheduler.py`：集成 RoundRobin 调度算法，使其成为可选的调度策略之一；集成 Least Load 调度算法，使其成为可选的调度策略之一
- 添加 `scheduler/controller/scheduler_Least_Load.py`：实现最小负载调度算法，选择负载最低的 Prefill 和 Decode 节点

### 文件改动

- `utils/pcie_metric.py`
- `core/handlers/controller/update.py`
- `coord/gateway/up_store.py`
- `utils/pcie_metric.py`
- `core/lifespans/controller.py`
- `core/handlers/controller/update.py`
- `coord/gateway/up_store.py`
- `store/controller/network.py`
- `store/controller/pool_manager.py`
- `scheduler/controller/global_scheduler.py`
- `scheduler/controller/scheduler_Least_Load.py`

---

## [v0.1.0] - 初始版本

### 添加

- 初始版本发布
- PD 分离推理调度系统核心功能
- 支持 Prefill/Decode 池管理
- 支持网络链路管理
- 集成 LMCache KV Cache 缓存
- 支持 KDN 知识网络
