# FlexPD simulator

## 当前归类

- `controller/`：控制器视角的 P/D 池选择、带宽利用率分析，以及 controller + gateway + instance 联合仿真入口。
- `gateway/`：池内网关调度、队列管理、Prefill PSO 调度策略，以及联合仿真使用的 SLO-aware 实例选择器。
- `instance/`：模拟 vLLM 运行过程的离散事件仿真器、在线执行时间计算器、TTFT 模型加载器和默认 TTFT 回归模型文件。
- `dataset/`：BurstGPT 数据集和请求工作负载生成逻辑。
- `experiment/`：原单池 Prefill/vLLM 对比实验流水线和 CLI 入口。
- `result/`：仿真 JSON、调度日志、SVG 可视化结果，以及结果渲染脚本。
- `profile/`：不同机器/实例的 TTFT、TPOT profile 文件。
- `test/`：simulator 相关单元测试和集成测试。

`simulator/` 是 FlexPD 的离线仿真策略对比子项目，用于比较不同 Prefill 策略进入 vLLM V1 chunked prefill 调度后的 goodput 表现。

## 统一流水线

主入口是 `experiment/pipeline.py`：

```python
from simulator import run_comparison

result = run_comparison(seed=42, strategy="both")
```

CLI 入口是：

```powershell
python .\simulator\experiment\run_prefill_vllm_simulation.py --seed 42 --strategy both
```

默认请求到达模式为 `qps`，默认 `qps=10`，即每 1 秒窗口到达 10 个请求，窗口内到达间隔随机但 seed 固定时可复现。

默认 prompt token 来源为 `BurstGPT_1.csv` 的 `Request tokens` 列。`--num-requests N` 会生成 N 个请求，并只从 CSV 中读取前 N 条有效 `Request tokens > 0`，不会扫描完整 CSV。

默认 prefill 计算时间从 `simulator/instance/prefill_ttft_regressor.pkl` 读取，使用线性公式：

```text
duration_ms = (a * total_tokens + b) * 1000
```

其中 pkl 中的 `a` 和 `b` 单位为秒，simulator 内部统一转换为毫秒。

默认会将完整仿真结果写入：

```text
simulator/result/latest_result.json
```

每次运行会覆盖旧文件，只保留最近一次仿真结果。终端默认只打印摘要；如果需要完整 JSON 输出到终端，可加 `--print-json`。如果只想运行不保存文件，可加 `--no-save`。也可以用 `--output <path>` 指定保存路径。

如果需要额外生成图形结果，可加 `--save-figure`：

```powershell
python .\simulator\experiment\run_prefill_vllm_simulation.py --seed 42 --strategy both --save-figure
```

第一阶段只输出 SVG。默认会展示最多前 20 个请求，并生成两张图：

```text
simulator/result/latest_result.svg
simulator/result/latest_result_direct.svg
```

其中 `latest_result.svg` 是 pso 策略图，`latest_result_direct.svg` 是 direct 策略图。每次运行会覆盖旧图。图中包含执行时间线，以及 `request ID`、`prompt tokens`、`SLO`、`actual latency`、`SLO met?` 表格。

默认会额外生成人类可读的调度过程日志：

```powershell
python .\simulator\experiment\run_prefill_vllm_simulation.py --seed 42 --strategy both
```

默认输出：

```text
simulator/result/latest_schedule_log.md
```

如果不想保存调度日志，可加 `--no-save-schedule-log`。也可以用 `--schedule-log-output <path>` 指定保存路径，用 `--schedule-log-strategy direct|pso` 指定日志策略。

## 策略

- `direct`：请求到达后立即转发给 vLLM V1 仿真器，不在 QueueManager 中等待调度；`dispatch_time_ms = original_arrival_ms`。
- `pso`：request-level SLO-aware PSO。PSO 只决定完整请求的转发顺序，粒子评估阶段调用 `VllmV1SchedulerSimulator` 估算该顺序下的 chunked prefill 完成时间；QueueManager 按完整请求 dispatch，vLLM 计算器负责长请求跨 chunk。
- `both`：同时运行 direct 和 pso，并输出对比 summary。

## 请求到达模式

- `all_at_once`：所有请求在 `t=0` 时刻全部到达，适合观察调度器在完整待调度集合上的调度能力。
- `qps`：按照 QPS 到达。例如 `--qps 10` 表示每 1 秒窗口到达 10 个请求；窗口内到达时间随机生成并排序，固定 seed 时可复现。

示例：

```powershell
python .\simulator\experiment\run_prefill_vllm_simulation.py --arrival-mode all_at_once --num-requests 10 --save-figure
python .\simulator\experiment\run_prefill_vllm_simulation.py --arrival-mode qps --qps 10 --num-requests 20 --save-figure
```

goodput deadline 始终基于原始到达时间：

```text
deadline_ms = original_arrival_ms + slo_ttft_ms
```

PSO 路径里的 `scheduled_arrival_ms` 只用于控制 vLLM 仿真的执行顺序，不改变 SLO 计算口径。

request-level PSO 规则：

- PSO 只接收 QueueManager 当前 pending queue 中已到达的请求。
- PSO 不拆分请求，只搜索完整请求的转发顺序。
- 粒子评估时调用 vLLM 计算器估算该完整请求顺序下的 chunked prefill 行为。
- QueueManager 使用 rolling dispatch：维护一个正在执行的 running dispatch，并把 vLLM pending queue 作为可继续增长的 waiting 水位。
- running 执行期间，如果 waiting token 数还没达到 `max_num_batched_tokens`，ready 请求会继续 dispatch 到 vLLM waiting 中，而不是把第一个 waiting dispatch 封箱。
- PSO 每次输出一个完整请求 batch，尽量接近 `max_num_batched_tokens`，但不拆请求；如果 vLLM 空闲且 ready 请求不足，也会立即 dispatch 不足额 batch。
- PSO 粒子评估会读取 QueueManager 内部在线 vLLM 执行器的 rolling snapshot，感知当前 running 结束时间以及已经 dispatch 到 vLLM pending queue 的 waiting 请求。
- PSO 粒子评估不只预测本次 dispatch，还会按当前 pending 的粒子顺序模拟后续 rolling dispatch future，再用 vLLM FCFS + chunked prefill 规则计算完成时间并评分。
- 单个长请求是否跨 chunk，由 `VllmV1SchedulerSimulator` 负责。

## 目录结构

- `queue_manager.py`：对齐 `proxy/sync.py` 的离线队列管理与 dispatch 闭环，记录 pending 请求如何被策略转发。
- `scheduling_strategies.py`：统一 direct 与 pso 策略接口，将策略输出转换为 batch order / planned finish 等计划字段。
- `experiment/pipeline.py`：统一策略对比流水线，包含策略运行、goodput 统计和 summary 对比。
- `run_prefill_vllm_simulation.py`：统一 CLI wrapper。
- `visualize_result.py`：将最近一次仿真结果渲染为 SVG 时间线和 SLO 表格。
- `request_workload.py`：可复现 workload 生成器，默认生成 50 个请求。
- `ttft_model.py`：读取 `prefill_ttft_regressor.pkl`，提供 `a * total_tokens + b` 的 prefill 耗时模型。
- `vllm_execution_calculator.py`：在线 vLLM 执行器，让 QueueManager 的 dispatch 计划和 vLLM chunked prefill 执行队列共享同一条时间线。
- `vllm_v1_scheduler.py`：vLLM V1 chunked prefill 离散事件仿真器。
- `prefill_pso_scheduler.py`：Prefill 侧同步 PSO 调度仿真器。
- `test/`：测试目录。
- `test/test_vllm_v1_scheduler.py`：vLLM 调度规则单元测试。
- `test/test_prefill_pso_scheduler.py`：PSO 调度器单元测试。
- `test/test_request_workload_experiment/pipeline.py`：workload 与统一 pipeline 集成测试。

## 常用命令

生成 workload：

```powershell
python .\simulator\dataset\request_workload.py --seed 42
```

运行 direct 与 pso 对比：

```powershell
python .\simulator\experiment\run_prefill_vllm_simulation.py --seed 42 --strategy both
```

只运行 direct：

```powershell
python .\simulator\experiment\run_prefill_vllm_simulation.py --seed 42 --strategy direct
```

只运行 pso：

```powershell
python .\simulator\experiment\run_prefill_vllm_simulation.py --seed 42 --strategy pso
```

常用参数：

- `--seed`
- `--num-requests`
- `--arrival-mode all_at_once|qps`
- `--qps`
- `--prompt-token-source burstgpt|synthetic`
- `--burstgpt-csv`
- `--min-slo-ttft-ms`
- `--max-slo-ttft-ms`
- `--strategy direct|pso|both`
- `--max-num-batched-tokens`
- `--arrival-window-ms`
- `--idle-batching first|same_time|window`
- `--token-time-ms`
- `--batch-overhead-ms`
- `--ttft-model`
- `--pso-particles`
- `--pso-iterations`
- `--output`
- `--no-save`
- `--print-json`
- `--save-figure`
- `--figure-output`
- `--figure-strategy direct|pso|both`
- `--max-figure-requests`
- `--no-save-schedule-log`
- `--schedule-log-output`
- `--schedule-log-strategy direct|pso`

## Controller + Gateway + Instance 联合仿真

联合事件仿真支持 `pso_slo`、`round_robin`、`least_load`、`adaptive_variance`、
`distserve_batched` 和 `distserve`。两个 DistServe baseline 都使用一个中心 P 网关和
一个中心 D 网关，并在 Prefill 完成后由 D 侧按唯一的 active + waiting 请求数选择
全局负载最低的 D 实例。

旧 `distserve_batched` 在 P 网关按 FCFS 从队首形成不超过 `L_m` 的逻辑 batch
（队首请求大于等于 `L_m` 时独占一批），再把整批预约到请求数最少的 P 实例；实例
内部继续使用 `--max-num-batched-tokens`，默认 4096：

```powershell
python .\simulator\controller\joint_controller_instance_simulation.py --policy distserve_batched --distserve-prefill-batch-tokens 2048 --max-num-batched-tokens 4096
```

新 `distserve` 不在 P 网关组批。每个请求到达后立即根据全部 P 实例 queued 与
running 的唯一未完成请求数执行 JSQ，并直接进入选中的实例；实例内部 vLLM
`max_num_batched_tokens` 由代码常量控制，长 prompt 由实例执行 chunked prefill：

```powershell
python .\simulator\controller\joint_controller_instance_simulation.py --policy distserve
```

运行全部 baseline 对比（输出 JSON 和 PNG）：

```powershell
python .\simulator\controller\joint_controller_instance_simulation.py --compare-all
```

联合仿真结果包含以下 DistServe 和 SLO 验证数据：

- `slo_attainment.all`：TTFT、TPOT、E2E 三项同时达标的请求比例。
- `goodput_requests_per_second`：固定工作负载中三项 SLO 同时达标的请求吞吐。
- `distserve.mode`：`gateway_fcfs_batching` 或 `direct_jsq_vllm_batching`。
- `distserve.prefill_max_num_batched_tokens`：该次运行实际使用的 P 实例 token budget。

goodput 的计算公式为：

```text
goodput_requests_per_second
= 同时满足 TTFT、TPOT、E2E 三项 SLO 的请求数
  /（最后一个请求完成时间 - 第一个请求到达时间，单位为秒）
```

它表示固定 workload 的“每秒达标请求数”，不是输入 QPS、token/s、模型原始吞吐量，
也不是仅满足其中一项 SLO 的请求吞吐。仿真内部仍计算有效时长以作为 goodput 和网络
平均带宽的分母，但不把 duration 或 P/Gateway batch 统计写入单策略及多策略结果。

`--distserve-prefill-batch-tokens` 只影响 `distserve_batched`。新 `distserve` 的实例内部
vLLM 每轮可调度 token 上限当前在
`controller/joint_controller_instance_simulation.py` 的
`DIST_SERVE_DIRECT_PREFILL_BATCH_TOKENS` 常量中调整（当前工作区值为 4096）；
`--max-num-batched-tokens` 只会影响旧 `distserve_batched` 和其他策略。两种 DistServe
的性能差异同时包含“是否在网关组批”和“实例 token budget”两项因素，不能单独归因于其中一项。

## 输出

最近一次完整结果默认保存在：

```powershell
Get-Content .\simulator\result\latest_result.json -Encoding UTF8
```

`runs.<strategy>.summary` 包含：

- `total_requests`
- `met_slo_requests`
- `missed_slo_requests`
- `slo_attainment_rate`
- `total_prompt_tokens`
- `goodput_tokens`
- `goodput_ratio`
- `avg_ttft_ms`
- `max_ttft_ms`

`runs.<strategy>.queue_result` 用于解释小闭环行为：

- `dispatch_records`：每次向 vLLM 计算器转发了哪些请求；direct 中每个请求到达即转发，pso 中由 QueueManager 按策略转发。
- `dispatch_records[*].planned_*`：外部策略评估时估算的开始/完成时间。
- `dispatch_records[*].actual_*`：在线 vLLM 执行器实际执行该 dispatch 中请求的开始/完成时间。
- `request_states`：每个请求的原始到达时间、计划批次、计划完成时间和实际 dispatch 时间。
- `vllm_requests`：真正送入 `VllmV1SchedulerSimulator` 的请求，`arrival_ms` 等于 dispatch time。
- `vllm_result`：QueueManager 内部在线 vLLM 执行器产出的实际执行时间线；pso 的 goodput 统计直接使用该结果，不再二次重放。

`runs.<strategy>.requests` 中：

- `original_arrival_ms`：workload 原始到达时间。
- `dispatch_time_ms`：队列管理器转发给 vLLM 的时间。
- `scheduled_arrival_ms`：兼容旧结果字段，当前等价于 `dispatch_time_ms`。

当同时运行 direct 和 pso 时，`comparison` 包含：

- `goodput_tokens_delta`
- `goodput_ratio_delta`
- `met_slo_requests_delta`
- `slo_attainment_rate_delta`
- `avg_ttft_ms_delta`

delta 方向均为：

```text
pso - direct
```

最近一次图形结果默认保存在：

```powershell
Get-Content .\simulator\result\latest_result.svg -Encoding UTF8
```

也可以从已有 JSON 单独生成 SVG：

```powershell
python .\simulator\result\visualize_result.py --strategy both
```

## 测试

```powershell
python -m unittest discover -s .\simulator\test -p "test_*.py"
```

## 扩展新策略

新增策略时优先在 `experiment/pipeline.py` 中补充独立的 `run_<strategy>_strategy()`，并让 `run_comparison()` 负责统一调度和输出。策略内部可以改写请求到达顺序或仿真前处理，但 goodput 统计应继续通过 `calculate_goodput()` 统一完成。
