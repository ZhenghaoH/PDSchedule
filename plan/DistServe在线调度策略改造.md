# DistServe 在线调度策略改造计划

## 修改目标

在现有 P/D 网关中增加可启动、可在线切换的 `distserve` 策略，复现 DistServe 论文第 4.3 节中属于**网关在线调度**的机制：

1. 请求按 FCFS 顺序进入调度器。
2. P 网关把请求分配给有效队列最短的在线 Prefill 实例（JSQ）。
3. P 侧按论文的 (L_m) 规则进行 token 组批：短请求按 FCFS 累积到接近 (L_m)，长度不小于 (L_m) 的请求单独发送。
4. D 网关选择有效负载最低的在线 Decode 实例，并在选择时原子预占，避免心跳更新间隔内的热点。
5. PD 请求继续使用 NIXL 的 remote-decode KV 参数链路；验证由 Decode 端消费远端 KV 的既有机制与论文 Pull 语义兼容。
6. `distserve` 运行时不被现有自适应策略切换或抢占逻辑改写，以符合论文“不实现 advanced runtime preemption”的基线行为。

论文原文依据：`DistServe.pdf` 第 8 页 4.3 节。该节还包含 workload profiler 触发 placement algorithm 的周期性 replan；这需要资源/并行度 placement 能力，不属于当前“所有 P 由 P 网关、所有 D 由 D 网关”架构内的在线策略，因此本次明确不实现，后续独立规划。

## 当前状态与范围保护

- 当前分支：`p2p-flexpd`；上游：`origin/p2p-flexpd`；远程：`BJTU-ANT/FlexPD`。
- 当前工作区含 `plan/代码修改前计划确认流程.md` 的用户已有改动，必须保留，不纳入本任务。
- DistServe 旧实现已回滚；本次从当前基线重新实现，不复用此前“仅心跳快照 JSQ”的近似方案。
- 当前部分文件在 Windows CRLF 环境下显示为 `M`，但其 Git 过滤后的 blob 已与索引一致；不得把这些状态缓存标记混入本任务提交。

## 论文机制与本次对应关系

| 论文 4.3 | 本次实现 |
| --- | --- |
| FCFS | 保持 `pending_queue` 到达顺序；只从 FCFS 前缀生成本轮可分发 Prefill batch。 |
| Prefill shortest queue | 用实例心跳的 `running_reqs + waiting_reqs` 与网关未被心跳确认的本地预占量共同形成“有效队列长度”，稳定 tie-break。 |
| Decode least loaded | 使用同一“有效未完成请求数”定义；选择与预占在同一锁内完成。 |
| (L_m) batch | 通过显式可配置 token 阈值表示由 `profile/profile_distserve_lm.py` 离线测得的 (L_m)，短请求累积接近阈值，长请求单发。 |
| Pull KV | 保持 NIXL 的 `do_remote_decode=True` 与 Decode payload 中的 `kv_transfer_params`；不在网关实现自定义 KV push。 |
| 不抢占 | DistServe 模式跳过 adaptive switch / `preempt_heavy_requests`。 |
| 周期性 replan | 不在本次实现；需单独接入 placement algorithm、workload profiler 和部署编排。 |

## 拟修改文件

1. `store/gateway/manager.py`
   - 将 `distserve` 加入 P/D 策略白名单。
   - 新增线程安全的实例有效负载快照、请求预占、释放和心跳确认接口；状态按 role 与 request ID 隔离。
   - 有效负载以心跳的 `running_reqs + waiting_reqs` 为基础，并覆盖心跳尚未反映的本地分发预占，避免连续到达请求重复命中同一实例。

2. `scheduler/gateway/prefill/async_base.py`
   - 提供 P 实例有效队列长度和稳定 JSQ 选择 helper。

3. `scheduler/gateway/prefill/manager.py`
   - 新增 `distserve` 分支：严格按 FCFS 前缀分配 P 实例、按 (L_m) token 阈值生成可分发 batch、为请求写入 `Task.scheduler_metadata["distserve"]` 的批次/预占信息。

4. `proxy/sync.py`
   - 增加 DistServe 专属 dispatch policy：仅分发调度器标记为可发送的 FCFS batch，支持多个 P 实例并行，但不跨越前序未决定请求。
   - 在转发成功、失败、取消和 Prefill 完成的路径上释放或确认预占。
   - DistServe 模式跳过 adaptive switch 与抢占分支。

5. `scheduler/gateway/decode/async_base.py`
   - 新增 Decode 最低有效负载选择，并通过 GatewayStoreManager 完成原子预占。

6. `core/handlers/gateway/decode/proxy.py`
   - 将 Decode 预占纳入实例选择、转发失败和流结束清理路径；避免并发请求在心跳到达前选择同一低负载实例。

7. `core/handlers/gateway/prefill/update.py`、`core/handlers/gateway/decode/update.py`
   - 使实例心跳在有效负载计算中确认或回收过期的网关预占，防止本地计数永久漂移。

8. `config/gatewayP.py`、`entrypoints/gatewayP.py`
   - 增加 `DIST_SERVE_PREFILL_BATCH_TOKENS` 默认配置与 `--distserve-prefill-batch-tokens` 启动参数。
   - 参数代表已 profile 得到的 (L_m)，仅在 `distserve` 模式使用；必须为正整数。

9. `entrypoints/gatewayD.py`
   - 将 `distserve` 加入 Decode 启动参数 choices；不新增 D 侧等待接口。

10. `core/consoles/gateway.py`
    - 将 `distserve` 加入 P/D 在线切换与自动补全词表。

11. `README.md`
    - 写明策略启用方式、(L_m) 参数必须来自目标模型/GPU profile、P/D 的有效负载定义、NIXL 前置条件及“未包含 placement replan”的边界。

12. `test/test_gateway_scheduler_strategy.py`
    - 覆盖策略白名单、CLI 补全、启动参数 choices、FCFS 顺序、JSQ、(L_m) 组批、长请求单发、D 最低有效负载、稳定平局及 DistServe 不触发 adaptive switch。

13. `test/test_runtime_instance_registration.py` 或新增 `test/test_distserve_scheduler.py`
    - 覆盖预占/心跳确认/释放、实例离线与转发异常清理、NIXL payload 保留 `kv_transfer_params`，以及 P→D 流程中的有效负载变化。

## 实施方案

### 1. 策略入口与运行时控制

- P/D 统一使用策略名 `distserve`。
- P 启动示例：`--prefill-scheduler-strategy distserve --distserve-prefill-batch-tokens <Lm>`。
- D 启动示例：`--decode-scheduler-strategy distserve`。
- 在线 CLI 继续使用 `show scheduler` 与 `switch scheduler -m distserve`。
- 在线切换仅重排尚未发出的 P 请求；已发送请求不迁移。D 切换仅影响尚未开始选择实例的请求。

### 2. P：FCFS + 严格 JSQ + (L_m) 组批

- 以 `pending_queue` 的到达顺序为唯一输入顺序；不得依据 SLO、prompt 长度或实例性能重排。
- 对 FCFS 前缀中的每个请求，在锁内计算有效队列长度，选择最短 P 队列；平局按稳定实例地址排序。
- 连续短请求仅在分配到同一 P 实例且总 token 不超过/最接近 (L_m) 时组成 batch；第一个长请求（token 数不小于 (L_m)）单独构成 batch。
- 请求选择后立即写入本地预占并在 metadata 中记录，防止下一轮到达在心跳间隔内重复选择同一实例。
- 分发器按已生成的 DistServe batch dispatch，不把后续 FCFS 请求越过前序未决定 batch。

### 3. D：最低有效负载 + 原子预占

- D 负载定义为在线实例的 `running_reqs + waiting_reqs`，并纳入尚未由下一次心跳体现的本地预占。
- 实例选择与预占在同一 store 锁中完成；失败、取消或流结束后释放。
- 现有 `add_decode_active_request` / `remove_decode_active_request` 继续承担已转发流的生命周期跟踪；新预占仅填补“选择到心跳/流登记”的窗口。
- 保持 D 侧立即返回实例，不增加等待队列接口。

### 4. NIXL Pull 兼容性与边界

- 保持 Prefill 请求的 `do_remote_decode=True`、Prefill 响应返回的 `kv_transfer_params` 和 Decode payload 透传。
- 通过集成测试验证 Decode 请求能够消费相应参数；真正的远端 KV 拉取由 vLLM `NixlConnector` 实现。
- 不实现自定义 KV 传输协议、P/D buffer 分配器或 Decode 就绪后反向 RPC；若实测 NIXL 不具备 consumer pull 语义，本项标记为部署前置条件而非在网关中模拟。

### 5. 论文范围之外的明确限制

- 不接入论文 Algorithm 2 placement、模型并行/资源重新配置、LLM weight reload 或周期性 deployment replan。
- DistServe 模式不执行已有 adaptive switch/preemption；其他策略维持原行为。
- 不修改现有 `least_load`、`round_robin`、`pso_new`、`lowest_generate_throughput`、`slo_tpot_admission` 的语义。

## 风险与控制

1. **预占与心跳双重计数或泄漏**
   - 预占必须带 request ID、role、时间戳；所有正常、异常、取消、离线路径均释放；以 heartbeat 确认和 TTL 清理兜底。
2. **FCFS 与多 P 并行冲突**
   - 保证全局只按 FCFS 前缀作出决定；允许对已决定的不同 P batch 并行发送，不让后到请求越过未决定的前序请求。
3. **(L_m) 必须匹配实际部署环境**
   - 本次仍由显式启动参数提供；使用 `profile/profile_distserve_lm.py` 在目标模型、GPU 和并行配置上离线测量，不能跨环境复用或将固定默认值宣称为论文 profiling 结果。
4. **NIXL 行为依赖部署镜像**
   - 单元测试验证参数链路；真机集成测试需使用支持 `NixlConnector` 的 vLLM 镜像和 P/D 两实例。
5. **在线切换污染已分发请求**
   - 沿用现有 strategy version 与 pending-only 重调度语义；不迁移已发送请求。
6. **混入现有用户改动或 CRLF 状态标记**
   - 提交前按内容 diff 和 Git 过滤 blob 核验；不将 `plan/代码修改前计划确认流程.md` 纳入。

## 验证方式

1. 单元测试：策略白名单、CLI/启动参数 choices、FCFS、JSQ、D 最低有效负载、平局、离线实例和预占生命周期。
2. 异步测试：多请求并发时，连续选择会因本地预占分散，而非等待 heartbeat 后才分散。
3. 组批测试：短请求 token 和接近 (L_m)，长请求独立 batch，且不改变 FCFS 顺序。
4. 回归测试：
   - `test/test_gateway_scheduler_strategy.py`
   - `test/test_decode_pool_load.py`
   - `test/test_runtime_instance_registration.py`
   - `test/test_fixed_gateway_routing.py`
   - `test/test_sync_script.py`
5. NIXL 集成检查：在已有 NIXL TCP fallback 环境验证 P 响应的 `kv_transfer_params` 可被 D 请求消费；缺少 vLLM/NIXL 服务时明确标记为未执行，不伪报通过。
6. 代码质量：`python -m py_compile`、`git diff --check`、策略关键词和限定文件 diff 核对。

## Git 提交范围与建议信息

- 代码完成、验证通过后删除本专项计划文件。
- 拟暂存范围仅限上述实际修改的业务、配置、README 和测试文件；不包含 `plan/代码修改前计划确认流程.md`。
- 建议提交信息：`Add DistServe online gateway scheduling baseline`
- 目标：`origin/p2p-flexpd`。
- 仅在完成后再次获得你的明确确认，才执行精确暂存、提交与推送。

## 待用户确认事项

1. 是否确认本次策略名为 `distserve`，P/D 均通过现有启动参数和在线 CLI 选择？
2. 是否确认本次实现论文 4.3 的网关在线部分，而将周期性 placement replan 明确排除为后续独立任务？
3. 是否确认 (L_m) 使用 P 网关的 `--distserve-prefill-batch-tokens` 显式参数提供，值由你在目标模型/GPU 上 profile 后设置，而本次不实现自动 profiling？
4. 是否确认 D 侧继续立即返回实例，不新增等待接口，并以“心跳负载 + 网关预占”作为最低有效负载定义？
5. 是否确认 NIXL Pull 仅验证既有 `NixlConnector` 的参数链路与部署能力，不在网关中实现自定义 KV 传输协议或 buffer 管理器？
6. 是否确认 DistServe 模式关闭 adaptive switch/preemption，但不改变其他策略的现有行为？
7. 是否确认上述文件范围、风险控制、验证标准、建议提交信息，以及完成后再单独确认提交/推送流程？
