# 真实部署：NIXL 1P/1D

本流程使用真实模型和真实 NIXL KV 传输。先完成[环境准备](environment.md)，
然后在同一 `pdschedule-nixl` 容器内打开七个独立服务终端。
所有 Python 命令均从 `/workspace/llm-stack/PDSchedule` 执行。

## 1. 拓扑与执行顺序

| 终端 | 进程/入口 | 地址 | 上级或下游 | GPU / TP |
| --- | --- | --- | --- | --- |
| 1 | Prefill vLLM API | 127.0.0.1:8001 | NIXL side-channel 127.0.0.1:5557 | 物理 GPU 0 / TP=1 |
| 2 | Decode vLLM API | 127.0.0.1:8003 | NIXL side-channel 127.0.0.1:5559 | 物理 GPU 1 / TP=1 |
| 3 | `entrypoints/controller.py` | 127.0.0.1:5000 | P/D Gateway | 不分配 GPU |
| 4 | `entrypoints/gatewayP.py` | 127.0.0.1:8081 | Controller5000，P Instance7001，D Gateway8082 | 不分配 GPU |
| 5 | `entrypoints/gatewayD.py` | 127.0.0.1:8082 | Controller5000，D Instance7003 | 不分配 GPU |
| 6 | `entrypoints/instanceP.py` | 127.0.0.1:7001 | Gateway8081，P vLLM8001 | 监测物理 GPU 0 |
| 7 | `entrypoints/instanceD.py` | 127.0.0.1:7003 | Gateway8082，D vLLM8003 | 监测物理 GPU 1 |

按终端 1～7 启动。每个服务保持前台运行，不需要 GNOME Terminal 或外部启动脚本。
另开一个检查/请求终端，进入同一个容器：

```bash
docker exec -it pdschedule-nixl /bin/bash
```

请求经 Controller → P Gateway → P Instance → P vLLM 完成 Prefill，
P Gateway 获取 `kv_transfer_params` 后转到 D Gateway → D Instance → D vLLM。
Decode 响应沿代理链路返回。KV 数据由 D vLLM 通过 NIXL 从 P vLLM 拉取，不经过 Controller。

本例每个 Gateway 下只有一个 Instance；两个 Gateway 仍须向 Controller 动态注册。
恰好 1P/1D Pool 时，全局选择唯一兼容组合，不需要固定地址调度模式，也不要求额外网络记录。

## 2. 终端 1：Prefill vLLM

```bash
cd /workspace/llm-stack/PDSchedule
export CUDA_VISIBLE_DEVICES=0
export PYTHONHASHSEED=0
export VLLM_USE_FLASHINFER_SAMPLER=0
export UCX_TLS=tcp,cuda_copy
export UCX_NET_DEVICES=all
export VLLM_NIXL_SIDE_CHANNEL_HOST=127.0.0.1
export VLLM_NIXL_SIDE_CHANNEL_PORT=5557
python3 -m vllm.entrypoints.openai.api_server \
  --model /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --host 127.0.0.1 --port 8001 \
  --tensor-parallel-size 1 --dtype half \
  --gpu-memory-utilization 0.8 --max-model-len 4096 \
  --max-num-batched-tokens 4096 \
  --enforce-eager --attention-backend TRITON_ATTN \
  --no-enable-prefix-caching --disable-hybrid-kv-cache-manager \
  --kv-cache-metrics \
  --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_both","kv_load_failure_policy":"fail","kv_connector_extra_config":{"backends":["UCX"]}}'
```

## 3. 终端 2：Decode vLLM

```bash
cd /workspace/llm-stack/PDSchedule
export CUDA_VISIBLE_DEVICES=1
export PYTHONHASHSEED=0
export VLLM_USE_FLASHINFER_SAMPLER=0
export UCX_TLS=tcp,cuda_copy
export UCX_NET_DEVICES=all
export VLLM_NIXL_SIDE_CHANNEL_HOST=127.0.0.1
export VLLM_NIXL_SIDE_CHANNEL_PORT=5559
python3 -m vllm.entrypoints.openai.api_server \
  --model /workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B \
  --host 127.0.0.1 --port 8003 \
  --tensor-parallel-size 1 --dtype half \
  --gpu-memory-utilization 0.8 --max-model-len 4096 \
  --max-num-batched-tokens 4096 \
  --enforce-eager --attention-backend TRITON_ATTN \
  --no-enable-prefix-caching --disable-hybrid-kv-cache-manager \
  --kv-cache-metrics \
  --kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_both","kv_load_failure_policy":"fail","kv_connector_extra_config":{"backends":["UCX"]}}'
```

vLLM 的 `kv_both` 允许 NIXL 处理发送和接收；本例的 P/D 职责由 PDSchedule 的入口和请求上下文决定。
P/D 使用相同模型、dtype、attention backend 和 TP，避免首次部署混入布局差异。
`fail` 禁止在 KV 加载失败时静默重算。`--enforce-eager` 使用 eager 执行，
关闭 prefix caching 便于观察每次 PD 请求的 KV 传输；这不是性能调优配置。

### 检查两个 vLLM 已就绪

在检查终端执行，两个 `/v1/models` 均成功后再继续，不用固定倒计时：

```bash
python3 - <<'PY'
import json
import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
expected = "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B"
for port in (8001, 8003):
    url = f"http://127.0.0.1:{port}/v1/models"
    with opener.open(url, timeout=10) as response:
        models = json.load(response)
    ids = [item["id"] for item in models["data"]]
    assert expected in ids, (url, ids)
    print(f"vLLM {port}: ready; model={expected}")
PY
```

预期两行 `vLLM 8001: ready; ...`、`vLLM 8003: ready; ...`。模型加载期间连接失败或
服务尚未提供模型时，先检查对应 vLLM 终端，完成加载后重试。

## 4. 终端 3：Controller

```bash
cd /workspace/llm-stack/PDSchedule
export CUDA_VISIBLE_DEVICES=""
python3 entrypoints/controller.py --host 127.0.0.1 --port 5000
```

保留这个交互终端。Controller 当前没有 `--no-cli` 参数，不将其输入重定向为 EOF。
启动后提供 `scheduler-cli>` 命令行，页面为 `http://127.0.0.1:5000/requests`。

## 5. 终端 4：Prefill Gateway

```bash
cd /workspace/llm-stack/PDSchedule
export CUDA_VISIBLE_DEVICES=""
python3 entrypoints/gatewayP.py \
  --host 127.0.0.1 --port 8081 \
  --controller 127.0.0.1:5000 \
  --prefill 127.0.0.1:7001 --decode 127.0.0.1:8082 \
  --prefill-scheduler-strategy jsq \
  --connector-name nixl --connector-version 1 --connector-metadata '{}' \
  --no-ttft-warmup --no-cli
```

## 6. 终端 5：Decode Gateway

```bash
cd /workspace/llm-stack/PDSchedule
export CUDA_VISIBLE_DEVICES=""
python3 entrypoints/gatewayD.py \
  --host 127.0.0.1 --port 8082 \
  --controller 127.0.0.1:5000 --decode 127.0.0.1:7003 \
  --decode-scheduler-strategy round_robin \
  --connector-name nixl --connector-version 1 --connector-metadata '{}' \
  --no-tpot-warmup --no-cli
```

Gateway 的 NIXL metadata 可以为空；Instance 上报的 side-channel 信息必须完整。
`--prefill` 和 `--decode` 配置成员/下游位置，并不免除实际注册。
jsq/round_robin 不需要预先拟合模型，两个 warmup 也显式关闭。

## 7. 终端 6：Prefill Instance Proxy

```bash
cd /workspace/llm-stack/PDSchedule
unset CUDA_VISIBLE_DEVICES
python3 entrypoints/instanceP.py \
  --host 127.0.0.1 --port 7001 --vllm_port 8001 \
  --gateway 127.0.0.1:8081 \
  --connector-name nixl --connector-version 1 \
  --connector-metadata '{"side_channel_host":"127.0.0.1","side_channel_port":5557,"tp_size":1}' \
  --gpu-indices 0
```

## 8. 终端 7：Decode Instance Proxy

```bash
cd /workspace/llm-stack/PDSchedule
unset CUDA_VISIBLE_DEVICES
python3 entrypoints/instanceD.py \
  --host 127.0.0.1 --port 7003 --vllm_port 8003 \
  --gateway 127.0.0.1:8082 \
  --connector-name nixl --connector-version 1 \
  --connector-metadata '{"side_channel_host":"127.0.0.1","side_channel_port":5559,"tp_size":1}' \
  --gpu-indices 1
```

Instance Proxy 采集 GPU 指标但不加载模型。`--gpu-indices` 是 NVML 可见的物理索引，
不同于 vLLM 在 `CUDA_VISIBLE_DEVICES` 筛选后看到的局部编号；不要把 D Proxy 的物理 GPU 1 写成 0。
Instance 的 `--host` 同时用于连接同主机 vLLM，必须是可访问的实际地址。

## 9. 检查注册与 Connector

在检查终端执行：

```bash
python3 - <<'PY'
import json
import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for port in (8081, 8082, 7001, 7003):
    with opener.open(f"http://127.0.0.1:{port}/connectors", timeout=10) as response:
        body = json.load(response)
    assert body["connector"]["name"] == "nixl", body
    assert body["connector"]["version"] == "1", body
    print(f"{port}: connector=nixl version=1 mode={body['mode']}")
PY
```

预期四行 `connector=nixl version=1 mode=request_scoped`。
该接口只显示组件 Connector 配置，不代表 Instance 已注册或底层 KV 已传输。

在 Controller 终端的 `scheduler-cli>` 提示符下依次输入：

```text
show pool_id
show scheduler
```

预期池表包含 Prefill `127.0.0.1:8081` 和 Decode `127.0.0.1:8082`；池 ID 不固定。
路由状态应包含 `single_gateway_direct`，不是 `resource_wait`。
两个 Instance 终端应完成启动，Gateway 不应持续出现实例注册失败；
稍后请求日志还必须显示实际选择了 `7001` 和 `7003`，不能仅凭池表判定 Instance 已可用。

## 10. 经 Controller 发出真实请求

请求终端同样位于容器内。通过 loopback 访问，客户端来源为 127.0.0.1。
当前源码按来源 IP 映射是否启用 PD，10.0.* / 192.168.* 来源可能走非 PD；
不要改成从其他机器直发后仍把响应当成本流程的 PD 验收。

下面使用标准库，发送非流式请求并打印状态与完整 JSON：

```bash
cd /workspace/llm-stack/PDSchedule
python3 - <<'PY'
import json
import urllib.error
import urllib.request
payload = {
    "model": "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
    "messages": [{"role": "user", "content": "Explain the difference between prefill and decode in two sentences."}],
    "max_tokens": 128,
    "temperature": 0.7,
    "top_p": 1.0,
    "stream": False,
}
request = urllib.request.Request(
    "http://127.0.0.1:5000/v1/chat/completions",
    data=json.dumps(payload).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(request, timeout=300) as response:
        status = response.status
        body = json.load(response)
except urllib.error.HTTPError as exc:
    raise SystemExit(f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}")
assert status == 200, status
assert body.get("choices"), body
assert not body.get("error"), body
assert body["choices"][0].get("finish_reason") != "error", body
print("HTTP", status)
print(json.dumps(body, ensure_ascii=False, indent=2))
PY
```

预期输出结构如下；模型生成文本、ID、token 数和结束原因可能不同：

```json
{
  "id": "chatcmpl-<request-id>",
  "object": "chat.completion",
  "model": "/workspace/llm-stack/models/deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
  "choices": [
    {"index": 0, "message": {"role": "assistant", "content": "<model-generated text>"}, "finish_reason": "stop"}
  ],
  "usage": {"prompt_tokens": 32, "completion_tokens": 64, "total_tokens": 96}
}
```

JSON 前有一行 `HTTP 200`。上述 usage 数字只是字段示意，不用于逐字比较；
推理模型可能先输出 reasoning，或在预算耗尽时以 `length` 结束。

### 检查请求真正经过 P/D

同一次请求的日志应能关联这些片段（请求 ID、session ID 动态变化）：

```text
PD分离:  True
[Controller-Connector] ... connector=nixl session_id=nixl-...
[Prefill Schedule] Selected instance: 127.0.0.1:7001
[Decode-Connector] validation succeeded request_id=... connector=nixl prefill_instance=127.0.0.1:7001 decode_instance=127.0.0.1:7003 session_id=...
```

若只收到正常文字但没有 PD 日志，不算通过动态配对路径验证。
`validation succeeded` 仅证明选中的端点配置兼容，不能单独证明 NIXL 数据传输。

### 检查 NIXL 实际传输指标

在发送请求前、请求后各执行一次下列命令，对比 D vLLM 的指标：

```bash
python3 - <<'PY'
import urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
prefixes = (
    "vllm:nixl_bytes_transferred_sum",
    "vllm:nixl_bytes_transferred_count",
    "vllm:nixl_num_failed_transfers_total",
)
with opener.open("http://127.0.0.1:8003/metrics", timeout=10) as response:
    text = response.read().decode("utf-8")
for line in text.splitlines():
    if line.startswith(prefixes):
        print(line)
PY
```

预期 `vllm:nixl_bytes_transferred_sum` 与 `_count` 相对请求前增加，
`vllm:nixl_num_failed_transfers_total` 不增加；labels 和具体数值由运行实例决定。
指标刷新可能滞后，请等待 vLLM 下一个统计周期后重新读取。
若没有这些样本，检查版本、`--kv-cache-metrics` 和 D vLLM NIXL 日志；
不能用“没有报错”或通用 KV offloading 日志代替传输证据。

完成条件分为三层：模型/服务就绪 → Controller PD 请求成功 → NIXL 传输指标增加且无失败增长。

## 11. 页面、日志与停止

- 请求记录页面：`http://127.0.0.1:5000/requests`。
- 项目根目录 `global_schedule.log`：全局选择与请求耗时。
- `gateway_logs/prefill_gateway_schedule.log`、`decode_gateway_schedule.log`：实例选择。
- `gateway_logs/prefill_gateway_timing.log`、`decode_gateway_timing.log`：请求生命周期。
- `gateway_logs/prefill_gateway_resource.log`、`decode_gateway_resource.log`：资源采样。

这些是不同层次的观测，不把 `observed_network_transfer_ms` 当成纯 NIXL 网络耗时。
远程浏览页面时应使用实际可达主机地址与监听配置；本例只监听 loopback。

停止时先结束请求，两个 Instance Proxy、两个 Gateway、两个 vLLM 前台进程分别按 Ctrl+C；
最后在 Controller CLI 输入 `quit`。不要直接关闭全部终端而留下孤立 vLLM 进程。
确认 GPU 进程已结束后，如需停止整个专用容器，在宿主机执行：

```bash
docker stop pdschedule-nixl
```

这会停止该容器内所有服务；不用于与其他任务共享的容器。

## 12. 更换模型、TP 和主机

- 模型路径在两个 vLLM 与请求中一起更换；Controller 可访问同一路径的 tokenizer。
- TP>1 时给每个 vLLM 分配独立 GPU 组，修改 `--tensor-parallel-size`、Proxy 的
  `--gpu-indices` 及 metadata 的 `tp_size`。本例同 TP，不限制 NIXL 原有 TP 不对称能力。
- 多机时修改注册/访问地址和 `VLLM_NIXL_SIDE_CHANNEL_HOST`，不能向对端通告 127.0.0.1。
  vLLM 与对应 Instance Proxy 同机；监听地址与通告地址区分，不将 0.0.0.0 用作远端目标。
- `UCX_NET_DEVICES=all` 可改为真实存在的网卡；不复制实验主机的固定 eno5 名称。
- 不同 Gateway Pool 可以使用不同 Connector，但一次 PD 不能跨 Connector桥接。
  本指南只展开 NIXL，不要求安装 LMCache/Redis。

## 13. 常见问题

| 现象 | 检查位置 |
| --- | --- |
| `Unknown kv connector` / 导入 nixl 失败 | vLLM/NIXL 版本、CUDA 12实现包及共享库 |
| UCX transport/device 或 agent 初始化错误 | UCX 的 tcp/cuda_copy支持、驱动、网卡与CUDA库 |
| `resource_wait` 或缺少实例 | Controller/Gateway/Instance注册顺序，端口、角色与metadata |
| `Prefill response does not contain kv_transfer_params` | 请求是否走NIXL Prefill、vLLM版本和参数；不能靠手工空字段绕过 |
| NIXL请求失败或传输失败计数增长 | side-channel 地址/端口、模型/布局、GPU资源和D vLLM日志 |
| `Address already in use` / GPU OOM | 遗留进程、端口冲突、模型显存、TP和上下文设置 |

底层协议/参数参考 [vLLM 0.13.0 NIXL 指南](https://docs.vllm.ai/en/v0.13.0/features/nixl_connector_usage/)。
本项目负责完整 Controller/Gateway/Instance 链路，不另外启动上游 toy proxy。
