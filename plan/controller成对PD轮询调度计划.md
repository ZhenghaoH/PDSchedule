# controller 成对 PD 轮询调度计划

## 目标

在 controller 调度层新增一种“成对 PD 轮询”方法：不再分别对 Prefill 池和 Decode 池各自轮询，而是将已经配对的 `(P, D)` 作为一个调度单元轮询。

## 需求理解

- 如果 `P1` 和 `D1` 已经配对，则轮询选中 `P1` 时必须同时选中 `D1`。
- 调度粒度从单独的 PrefillPool / DecodePool 改为 Pair。
- 现有调度器接口应尽量保持返回 `(p_ip, p_port, d_ip, d_port)`。

## 检查范围

- `scheduler/controller/scheduler_RoundRobin.py`
- `scheduler/controller/global_scheduler.py`
- `store/controller/pool_manager.py`
- `store/controller/network.py`
- controller 注册和选择调度器的位置

## 输出

- 明确新增调度器类或修改点。
- 明确配对关系来源。
- 明确回退行为和测试方式。

## 实现决策

- 新增策略名：`paired_round_robin`。
- 新增类：`PairedRoundRobin_Scheduler`。
- 保留原 `round_robin` 行为不变，它继续轮询所有 P-D 笛卡尔积。
- `paired_round_robin` 的配对关系来源为 controller 当前活跃池列表：
  - Prefill 池按 `pool_id` 排序。
  - Decode 池按 `pool_id` 排序。
  - 相同位置组成固定 pair。
- 当 P/D 数量不一致时，只轮询前 `min(len(P), len(D))` 对，多出的池暂不参与，并打印 warning。
- 策略返回值保持 `(p_ip, p_port, d_ip, d_port)`，不改变 `GlobalScheduler.schedule()` 对外结构。
