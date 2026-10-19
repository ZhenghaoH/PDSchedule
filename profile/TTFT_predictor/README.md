# TTFT Predictor

本目录只发布测量、拟合、预测和本地持久化代码，不附带任何实验机器的已拟合模型。
`artifacts/` 中的生成物及 profile 下的 pickle/joblib 文件由 Git 忽略。

## 文件与输入

- [prefill_regressor.py](prefill_regressor.py)：采集 warmup 样本，拟合线性回归器，保存和加载本地状态。
- [prefill_predictor.py](prefill_predictor.py)：异步预测、详细 warmup 和本地模型加载/保存入口。
- [request_generator.py](request_generator.py)：按 tokenizer 构造请求并测量 TTFT。
- [prefill_prediction_server.py](prefill_prediction_server.py)：提供 HTTP 预测服务。
- [validate_prefill_predictor.py](validate_prefill_predictor.py)：在真实部署中核对预测结果。

当前拟合形式为：

```text
total_tokens = max(0, batch_size) * max(0, prompt_length)
TTFT_seconds = tokens_coefficient * total_tokens + intercept_seconds
```

回归器对总 token 数做标准化后拟合，再还原系数。详细 warmup 会采集真实请求；
批次聚类与测量过滤由回归器实现。参数和结果只适用于相应模型、GPU、TP/PP、
vLLM 配置和测量路径，不能直接当作其他部署的校准值。

## 冷启动预测

从项目根目录进入 `profile/` 后使用：

```python
import asyncio
from TTFT_predictor.prefill_predictor import predict_ttft

print(asyncio.run(predict_ttft(batch_size=1, prompt_length=128)))
```

返回值单位为秒。没有本地模型时，代码使用内置合成样本初始化或粗略估计。
这仅用于冷启动/功能检查，不代表已在当前硬件测量或具有实测精度。

## 在本地测量、拟合和保存

准备兼容环境、真实 vLLM 服务与本地 tokenizer 后，在 `profile/` 下执行：

```python
import asyncio
from TTFT_predictor.prefill_predictor import perform_detailed_warmup

config = {
    "host": "127.0.0.1",
    "port": 8001,
    "model_id": "your-served-model",
    "tokenizer_path": "/path/to/your/model",
}

asyncio.run(perform_detailed_warmup(
    vllm_config=config,
    repeats=3,
    model_file="TTFT_predictor/artifacts/prefill_ttft_regressor.pkl",
))
```

替换模型标识与 tokenizer 路径，检查目标服务、批大小和 prompt 长度范围。
warmup 会产生真实推理请求。输出文件只保存在本地；不传 `model_file` 时不会保存。
有足够有效样本并完成拟合后，检查日志/系数和验证脚本结果，不能只依据函数返回判断校准成功。

## 加载与 Gateway 集成

`load_ttft_model(path)` 在文件缺失时返回 `False`，`save_ttft_model(path)` 用于保存
本地已拟合状态。只加载自己生成或已核验的可信 pickle；不从请求中接受任意模型文件。

P Gateway 在启用 TTFT warmup 后先尝试加载本地文件；未命中时按配置目标进行真实
warmup。目标不可用或 warmup 未完成时，应检查 predictor readiness 和所选策略的
fallback，而不是假设已有实测预测器。

Controller 使用独立的本地 profile 路径；Gateway 模型不会自动分发给 Controller。
参见 [Controller 本地模型说明](../controller/README.md)。
