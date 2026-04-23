# 允许 Prefill/Decode 实例池为空并支持按索引读取实例配置的最小修改计划

## 修改目标

在尽量少改代码的前提下，同时满足两个需求：

1. `CONFIG_INSTANCE_P_POOL` 或 `CONFIG_INSTANCE_D_POOL` 允许为空，导入配置时不报 `IndexError`。
2. `config/instanceP.py` 和 `config/instanceD.py` 直接从对应池中读取实例配置，而不是读取旧单实例常量。

不增加 `name` 字段，不引入复杂配置类，不重构现有启动流程。

## 当前问题

当前 `config/ADDRESS_CONFIG.py` 无条件读取池中第一个元素：

```python
CONFIG_INSTANCE_D_IP = CONFIG_INSTANCE_D_POOL[0]["ip"]
```

当 D 池为空时，启动 `gatewayP.py` 也会在导入阶段失败。

同时，`config/instanceP.py` 和 `config/instanceD.py` 当前读取的是旧单实例常量。后续应改为直接读取 `CONFIG_INSTANCE_P_POOL` / `CONFIG_INSTANCE_D_POOL`。

## 最小修改范围

只修改：

1. `config/ADDRESS_CONFIG.py`
2. `config/instanceP.py`
3. `config/instanceD.py`

不修改：

1. `entrypoints/instanceP.py`
2. `entrypoints/instanceD.py`
3. `config/gatewayP.py`
4. `config/gatewayD.py`

原因：

- `config/instanceP.py` / `config/instanceD.py` 继续提供入口需要的 `DEFAULT_HOST`、`DEFAULT_PORT`、`VLLM_PORT`，但这些值直接来自实例池。
- `entrypoints/instanceP.py` / `entrypoints/instanceD.py` 已经只读取 config 层默认值，不需要改。
- Gateway 已经使用 `CONFIG_INSTANCE_P_ADDRS` / `CONFIG_INSTANCE_D_ADDRS`，空池自然得到 `[]`。

## 方案细节

### 1. `ADDRESS_CONFIG.py` 避免空池取 `[0]`

将旧单实例兼容常量改成条件表达式。

Prefill：

```python
CONFIG_INSTANCE_P_IP = CONFIG_INSTANCE_P_POOL[0]["ip"] if CONFIG_INSTANCE_P_POOL else None
CONFIG_INSTANCE_P_PORT = CONFIG_INSTANCE_P_POOL[0]["port"] if CONFIG_INSTANCE_P_POOL else None
CONFIG_VLLM_P_PORT = CONFIG_INSTANCE_P_POOL[0]["vllm_port"] if CONFIG_INSTANCE_P_POOL else None
```

Decode：

```python
CONFIG_INSTANCE_D_IP = CONFIG_INSTANCE_D_POOL[0]["ip"] if CONFIG_INSTANCE_D_POOL else None
CONFIG_INSTANCE_D_PORT = CONFIG_INSTANCE_D_POOL[0]["port"] if CONFIG_INSTANCE_D_POOL else None
CONFIG_VLLM_D_PORT = CONFIG_INSTANCE_D_POOL[0]["vllm_port"] if CONFIG_INSTANCE_D_POOL else None
```

这样：

- 非空池行为不变，仍取第一个实例。
- 空池时旧兼容常量为 `None`，不会导入时报错。
- `CONFIG_INSTANCE_P_ADDRS` / `CONFIG_INSTANCE_D_ADDRS` 保持现状。

### 2. `config/instanceP.py` 直接读取 P 池

不修改入口参数。只在 `config/instanceP.py` 内定义默认实例索引：

```python
INSTANCE_INDEX = 0
```

然后直接从 `CONFIG_INSTANCE_P_POOL` 读取默认配置：

```python
INSTANCE_CONFIG = CONFIG_INSTANCE_P_POOL[INSTANCE_INDEX] if len(CONFIG_INSTANCE_P_POOL) > INSTANCE_INDEX else None

DEFAULT_HOST = INSTANCE_CONFIG["ip"] if INSTANCE_CONFIG else None
DEFAULT_PORT = INSTANCE_CONFIG["port"] if INSTANCE_CONFIG else None
VLLM_PORT = INSTANCE_CONFIG["vllm_port"] if INSTANCE_CONFIG else None
```

语义：

- 默认读取 `CONFIG_INSTANCE_P_POOL[0]`。
- 如需让某个运行副本默认读取第 2 个 P 配置，将 `INSTANCE_INDEX` 改为 `1`。
- 如果 P 池为空或索引越界，默认值为 `None`，入口无参数启动时会失败；也可以继续用命令行手动传 `--host`、`--port`、`--vllm_port`。

### 3. `config/instanceD.py` 直接读取 D 池

Decode 侧同理，使用 `INSTANCE_INDEX` 从 `CONFIG_INSTANCE_D_POOL` 读取 `DEFAULT_HOST`、`DEFAULT_PORT`、`VLLM_PORT`。

## 启动示例

使用不同配置副本启动多个 Prefill instance：

```bash
python3 instanceP.py
python3 instanceP.py --port 7002 --vllm_port 8002
```

使用不同配置副本启动多个 Decode instance：

```bash
python3 instanceD.py
python3 instanceD.py --port 7004 --vllm_port 8004
```

保留原手动覆盖方式：

```bash
python3 instanceP.py --host 172.18.0.169 --port 7010 --vllm_port 8010
python3 instanceD.py --host 172.18.0.169 --port 7011 --vllm_port 8011
```

## 取舍说明

这版计划刻意不做以下事情：

1. 不新增 `get_instance_config()`、`get_prefill_instance_config()`、`get_decode_instance_config()`。
2. 不修改 `entrypoints/instanceP.py` / `entrypoints/instanceD.py`。
3. 不把 `--host`、`--port`、`--vllm_port` 默认值改成 `None`。
4. 不新增 `--instance_index`。

最小语义是：

- 入口仍沿用旧逻辑。
- config 层默认值直接来自实例池。

这样改动少，行为也更容易理解。

## 风险点

1. 空池时，Gateway 可以正常导入和启动，但真实请求可能没有可用实例。
2. 空池时，无参数启动对应 `instanceP.py` / `instanceD.py` 仍可能因为默认 `host/port/vllm_port` 为 `None` 而启动失败；这是合理的，因为没有实例配置可用。
3. 多个进程使用相同默认配置会导致端口冲突，需要启动脚本或命令行参数避免。
4. 如果需要同一份代码同时无参数启动多个不同实例，仅修改 config 层无法做到，需要不同配置副本或命令行覆盖端口。

## 验证方式

1. 当前非空池验证：

```bash
python -c "from config.gatewayP import DEFAULT_PREFILL; from config.gatewayD import DEFAULT_DECODE; print(DEFAULT_PREFILL); print(DEFAULT_DECODE)"
```

2. 空池导入验证：

临时将某个池设为 `[]`，验证：

```bash
python -c "from config.ADDRESS_CONFIG import CONFIG_INSTANCE_P_ADDRS, CONFIG_INSTANCE_D_ADDRS; print(CONFIG_INSTANCE_P_ADDRS); print(CONFIG_INSTANCE_D_ADDRS)"
```

3. instance 配置读取验证：

```bash
python -c "from config.instanceP import DEFAULT_HOST, DEFAULT_PORT, VLLM_PORT; print(DEFAULT_HOST, DEFAULT_PORT, VLLM_PORT)"
python -c "from config.instanceD import DEFAULT_HOST, DEFAULT_PORT, VLLM_PORT; print(DEFAULT_HOST, DEFAULT_PORT, VLLM_PORT)"
```

## 待用户确认

请确认是否采用这个最小方案：

1. 只改 `ADDRESS_CONFIG.py`、`config/instanceP.py`、`config/instanceD.py`。
2. 不新增配置读取函数。
3. 不修改 `entrypoints/instanceP.py` / `entrypoints/instanceD.py`。
4. 不新增 `--instance_index`。

确认后再执行代码修改。
