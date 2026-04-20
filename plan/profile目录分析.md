# profile目录分析

## 目录定位

`profile/` 当前主要用于对推理性能做离线或在线 profiling，并提供两个预测器：

- `TTFT_predictor`
  - 关注 Prefill 阶段
  - 预测首 token 延迟，即 TTFT
- `TPOT_predictor`
  - 关注 Decode / Generation 阶段
  - 预测每 token 生成时间，即 TPOT，以及完整 generation time

两者整体设计相似：

1. 通过真实 vLLM 请求采样性能数据
2. 用简单回归模型拟合性能函数
3. 暴露 Python 异步接口和 FastAPI HTTP 服务
4. 服务启动时做初始化或 warmup

## TTFT_predictor

### 文件职责

- `prefill_prediction_server.py`
  - FastAPI 服务入口
  - 暴露 `GET /` 和 `POST /predict`
  - 服务启动时先做一次 dummy init，再后台启动详细 warmup
- `prefill_predictor.py`
  - TTFT 预测器的单例管理层
  - 提供 `predict_ttft(batch_size, prompt_length)`
  - 提供 `perform_detailed_warmup(...)`
  - 首次初始化时使用少量 dummy data 让模型快速可用
- `prefill_regressor.py`
  - 核心回归模型
  - 收集真实 TTFT 样本
  - 使用 `LinearRegression + StandardScaler` 拟合
  - 特征为：
    - `batch_size * prompt_length`
    - `prompt_length`
    - `batch_size`
- `request_generator.py`
  - 根据目标 token 数生成 prompt
  - 给 prompt 添加 UUID 前缀，降低 KV Cache 前缀命中影响
  - 向 vLLM 发送流式请求并测量 TTFT
- `README.md`
  - 说明了 TTFT predictor 的目标、接口和使用方式
  - 但部分路径仍指向旧工程路径，需要后续清理

### 核心模型

TTFT 近似为：

```text
TTFT ~= a * (batch_size * prompt_length)
      + b * prompt_length
      + c * batch_size
      + d
```

优点：

- 推理速度快，适合作为调度器实时估计
- 特征可解释
- warmup 数据量不需要很大就能给出可用估计

局限：

- 只建模 `batch_size` 和 `prompt_length`
- 没显式考虑 GPU 型号、当前队列、KV Cache 命中、并发干扰、网络传输等因素
- dummy data 启动阶段的预测只适合兜底，不适合作为最终精确结果

## TPOT_predictor

### 文件职责

- `TPOT_prediction_server.py`
  - FastAPI 服务入口
  - 暴露：
    - `POST /predict_tpot`
    - `POST /predict_generation_time`
  - 启动时调用 `predict_tpot(1, 1)` 触发模型初始化和 warmup
- `TPOT_predictor.py`
  - TPOT 预测器的单例管理层
  - 提供：
    - `predict_tpot(batch_size, context_length)`
    - `predict_generation_time(batch_size, prompt_length, generation_length)`
- `TPOT_regressor.py`
  - 核心 TPOT 回归模型
  - 使用完整生成任务的总 generation time 拟合“分步 TPOT 求和模型”
  - 支持 `SGDRegressor` 做在线更新
- `TPOT_measure.py`
  - 发送真实 vLLM 流式请求
  - 测量 TTFT、输出 token 数、TPOT、总耗时
- `TPOT_warmup.py`
  - 定义 TPOT warmup 配置
  - 覆盖 batch size、prompt length、generation length 三个维度
  - 调用真实请求进行采样并初始化回归模型

### 核心模型

TPOT 使用更偏 Decode 的建模方式。

它把完整 generation time 看成每一步 TPOT 的求和，并用以下特征拟合：

```text
sum_lengths = G * P + G * (G - 1) / 2

feature1 = batch_size * sum_lengths
feature2 = batch_size * generation_length
feature3 = sum_lengths
feature4 = generation_length
```

最终：

```text
generation_time ~= d1 * feature1
                 + d2 * feature2
                 + d3 * feature3
                 + d4 * feature4
```

优点：

- 比只用平均 TPOT 更细，能体现上下文长度随生成过程增长
- 可以直接预测完整 generation time
- 支持在线更新

局限：

- 当前模型仍是线性假设
- 没显式考虑 batching 动态变化、PagedAttention/KV Cache 压力、GPU memory bandwidth、调度等待、网络/PCIe 传输
- warmup 组合较多，启动成本可能明显高于 TTFT predictor

## 当前可运行性

已对以下文件做过 `py_compile` 语法检查，均通过：

- `profile/TTFT_predictor/prefill_prediction_server.py`
- `profile/TTFT_predictor/prefill_predictor.py`
- `profile/TTFT_predictor/prefill_regressor.py`
- `profile/TTFT_predictor/request_generator.py`
- `profile/TPOT_predictor/TPOT_prediction_server.py`
- `profile/TPOT_predictor/TPOT_predictor.py`
- `profile/TPOT_predictor/TPOT_regressor.py`
- `profile/TPOT_predictor/TPOT_measure.py`
- `profile/TPOT_predictor/TPOT_warmup.py`

这说明当前文件语法层面可用，但不代表线上配置和真实 vLLM 链路已经完全可用。

## 主要问题与风险

### 1. 硬编码地址和模型路径较多

例如：

- TTFT 默认 vLLM 地址：`172.18.0.250:8000`
- TTFT 预测服务地址：`172.18.0.250:9000`
- TPOT 默认 vLLM 地址：`172.18.0.250:8000`
- TPOT 预测服务地址：`0.0.0.0:9001`
- 多处模型路径写死为 `/workspace/...` 或 `/home/dell/...`

影响：

- 换机器、换容器或换模型时容易失效
- 不利于集成到现有控制器配置体系

建议：

- 后续统一改为环境变量或配置文件

### 2. TTFT 和 TPOT 的模块风格不完全统一

TTFT 侧已经支持包内相对导入和直接脚本运行的兼容逻辑。

TPOT 侧当前大量使用直接导入，例如：

```python
from TPOT_predictor import ...
from TPOT_regressor import ...
from TPOT_measure import ...
```

影响：

- 从不同工作目录启动时更容易遇到 import path 问题
- 不利于作为包被其他模块引用

建议：

- 统一改成类似 TTFT 的 `if __package__` 兼容导入方式

### 3. README 与当前路径不完全一致

`TTFT_predictor/README.md` 中仍有旧工程路径，例如 `BurstGPT/example/scheduler/...`。

影响：

- 新人按文档操作可能找错目录

建议：

- 后续单独整理 profile 文档，把 TTFT/TPOT 两套服务的启动方式、接口和配置项合并说明

### 4. 预测器还没有明显接入主调度链路

从 `profile/` 目录本身看，它更像独立 profiling / predictor 服务。

当前分析中没有看到它直接被主调度器稳定调用的路径。

影响：

- 预测能力目前可能还停留在实验或旁路服务阶段
- 如果要用于 PSO、LeastLoad 或 Affinity 调度，需要补充调用链设计

### 5. 模型特征仍偏简单

TTFT 只用：

- batch size
- prompt length

TPOT 只用：

- batch size
- prompt length
- generation length / context length

它们没有使用当前项目里已经逐步引入的：

- 网络/PCIe 带宽
- Decode 池预约负载
- GPU memory utilization
- KV Cache 命中或迁移状态

建议：

- 第一阶段可以保留轻量模型
- 第二阶段再考虑把这些系统状态作为特征引入

## 建议的后续演进顺序

1. 先清理配置
   - 把 vLLM 地址、模型路径、服务 host/port 抽到环境变量或配置文件
2. 再统一导入风格
   - 让 TTFT/TPOT 都能包内导入和脚本运行
3. 再整理文档
   - 修正旧路径
   - 给出两个 predictor 的启动命令和 API 示例
4. 最后考虑调度集成
   - Prefill 调度可使用 TTFT 预测
   - Decode 调度可使用 TPOT / generation time 预测
   - 与当前 Decode `current_load` 账本机制结合
