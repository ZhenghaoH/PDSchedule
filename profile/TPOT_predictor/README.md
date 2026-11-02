# TPOT Predictor

本目录只发布测量、拟合、预测和本地持久化代码，不附带预训练或实验机器模型。
生成文件保存在本地 `artifacts/`，不进入 Git 发布树。

完整的真实 NIXL 多池校准、验证命令及 Gateway 启用步骤见
[SLO 使用流程](../../docs/slo.md)。该流程使用 `pdschedule.profiling` 导入，
并显式指定真实 vLLM API 端口；下文保留原有 profile 目录下的调用方式。

## 文件与模型

- [TPOT_regressor.py](TPOT_regressor.py)：基于完整生成时间拟合四项系数，并提供在线更新与持久化。
- [TPOT_predictor.py](TPOT_predictor.py)：异步 TPOT/生成时间预测、详细 warmup、模型加载与保存。
- [TPOT_measure.py](TPOT_measure.py)：对真实 vLLM 请求测量生成性能。
- [TPOT_warmup.py](TPOT_warmup.py)：兼容旧 warmup 调用的包装入口。
- [TPOT_prediction_server.py](TPOT_prediction_server.py)：HTTP 预测服务。
- [validate_decode_predictor.py](validate_decode_predictor.py)：真实部署的预测验证工具。

单步模型与完整生成时间模型：

```text
step_seconds = d1 * batch_size * context_length
             + d2 * batch_size + d3 * context_length + d4

sum_context = generation_length * prompt_length
            + generation_length * (generation_length - 1) / 2
generation_seconds = d1 * batch_size * sum_context
                   + d2 * batch_size * generation_length
                   + d3 * sum_context + d4 * generation_length
```

真实测量样本包含 batch_size、prompt token 数、实际生成 token 数和生成时间。
更换模型、GPU、并行拓扑、运行时或批处理配置后，应重新测量和验证。

## 冷启动预测

从项目根目录进入 `profile/` 后使用：

```python
import asyncio
from TPOT_predictor.TPOT_predictor import predict_tpot, predict_generation_time

async def main():
    print(await predict_tpot(batch_size=1, context_length=128))
    print(await predict_generation_time(
        batch_size=1, prompt_length=128, generation_length=32,
    ))

asyncio.run(main())
```

返回值单位为秒。初始化包含合成样本，未完成实测校准时只能用于功能检查，
不能把输出当作当前部署的性能承诺。

## 在本地测量、拟合和保存

准备真实 Decode vLLM 服务与 tokenizer 后，在 `profile/` 下执行：

```python
import asyncio
from TPOT_predictor.TPOT_predictor import perform_detailed_warmup

config = {
    "host": "127.0.0.1",
    "port": 8002,
    "model_id": "your-served-model",
    "tokenizer_path": "/path/to/your/model",
    "prompt": "Write a short story.",
}

asyncio.run(perform_detailed_warmup(
    vllm_config=config,
    repeats=2,
    model_file="TPOT_predictor/artifacts/decode_tpot_regressor.pkl",
))
```

替换模型标识和本地 tokenizer 路径，检查批大小/生成长度范围。该过程会发送真实
推理请求；有有效样本并拟合后才保存状态。检查测量日志和验证结果，不用合成冷启动
预测充当校准证据。不传 `model_file` 时只保留进程内拟合状态。

## 加载与 Decode Gateway 集成

`load_tpot_model(path)` 在文件缺失时返回 `False`；
`save_tpot_model(path)` 保存本地已拟合状态。pickle 只从可信本地来源加载。

D Gateway 在启用 TPOT warmup 后先尝试本地加载，再按配置目标进行真实 warmup。
缺少目标或校准尚未完成时，以实际 predictor readiness 和策略 fallback 为准。
Controller 的模型需独立放置，参见 [Controller 本地模型说明](../controller/README.md)。

注意：`--no-tpot-warmup` 同时跳过加载和拟合；仅传 `--tpot-model-file` 不会在该模式下加载。
`--tpot-warmup` 先尝试加载，再在失败时进行真实 warmup；评估前应核对
`[TPOT Predictor] Using persisted model: ...` 的实际文件路径。
模型替换需重启 Gateway，不假设自动热加载。预测不可用时，`slo_aware_decode`
退回最低生成吞吐策略；没有安全候选时仍会选择预测违约较少的候选，不进行硬性拒绝。
