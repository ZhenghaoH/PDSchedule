# Prefill 和 Decode 池管理多实例配置修改计划

## 修改目标

将当前 `config/ADDRESS_CONFIG.py` 中“Prefill/Decode 各一个 instance 代理地址”的配置方式，扩展为“Prefill 池和 Decode 池分别管理多个 instance 代理地址”的配置方式。

目标效果：

- Prefill Gateway 默认管理多个 Prefill instance 代理。
- Decode Gateway 默认管理多个 Decode instance 代理。
- 实例代理不再读取 `CONFIG_INSTANCE_P_IP`、`CONFIG_INSTANCE_P_PORT`、`CONFIG_VLLM_P_PORT`，而是直接读取 `CONFIG_INSTANCE_P_POOL`；Decode 侧同理直接读取 `CONFIG_INSTANCE_D_POOL`。
- 用户仍可通过命令行参数 `--prefill`、`--decode` 覆盖配置文件默认池。

## 当前代码现状

### `config/ADDRESS_CONFIG.py`

当前只定义了单个 Prefill instance 和单个 Decode instance：

```python
CONFIG_INSTANCE_P_IP = '172.18.0.169'
CONFIG_INSTANCE_P_PORT = 7001
CONFIG_VLLM_P_PORT = 8001

CONFIG_INSTANCE_D_IP = '172.18.0.169'
CONFIG_INSTANCE_D_PORT = 7002
CONFIG_VLLM_D_PORT = 8002
```

这会导致配置文件层面只能表达一个 P instance 和一个 D instance。

### `config/gatewayP.py`

当前默认 Prefill 列表虽然是 list，但只由单个 P instance 常量生成：

```python
DEFAULT_PREFILL = [f"{CONFIG_INSTANCE_P_IP}:{CONFIG_INSTANCE_P_PORT}"]
DEFAULT_DECODE = [f"{CONFIG_GATEWAY_D_IP}:{CONFIG_GATEWAY_D_PORT}"]
```

### `config/gatewayD.py`

当前默认 Decode 列表同样只由单个 D instance 常量生成：

```python
DEFAULT_DECODE = [f"{CONFIG_INSTANCE_D_IP}:{CONFIG_INSTANCE_D_PORT}"]
```

### 入口和存储层

入口层已经具备多地址能力：

- `entrypoints/gatewayP.py` 中 `--prefill`、`--decode` 使用 `nargs="+"`。
- `entrypoints/gatewayD.py` 中 `--decode` 使用 `nargs="+"`。
- `store/gateway/manager.py` 的 `GatewayStoreManager.set_config()` 接收列表，并逐个注册到 `_instance_table`。

因此，核心问题不在网关入口，而在默认配置仍是单实例。

## 拟修改文件

1. `config/ADDRESS_CONFIG.py`
2. `config/gatewayP.py`
3. `config/gatewayD.py`
4. `config/instanceP.py`
5. `config/instanceD.py`

可选检查文件：

1. `run_multi.sh`

## 推荐修改方案

### 1. 在 `ADDRESS_CONFIG.py` 增加实例池列表

新增 Prefill 池：

```python
CONFIG_INSTANCE_P_POOL = [
    {
        "ip": "172.18.0.169",
        "port": 7001,
        "vllm_port": 8001,
    },
    {
        "ip": "172.18.0.169",
        "port": 7002,
        "vllm_port": 8002,
    },
]
```

新增 Decode 池：

```python
CONFIG_INSTANCE_D_POOL = [
    {
        "ip": "172.18.0.169",
        "port": 7003,
        "vllm_port": 8003,
    },
    {
        "ip": "172.18.0.169",
        "port": 7004,
        "vllm_port": 8004,
    },
]
```

实例代理后续直接读取 `CONFIG_INSTANCE_P_POOL` / `CONFIG_INSTANCE_D_POOL`。旧的单实例常量不再作为 instance 代理的配置来源；如需保留，只作为其它历史代码的兼容项。

### 2. 在 `ADDRESS_CONFIG.py` 增加地址列表派生常量

为了让网关配置更简单，建议增加直接可用的地址列表：

```python
CONFIG_INSTANCE_P_ADDRS = [
    f"{item['ip']}:{item['port']}" for item in CONFIG_INSTANCE_P_POOL
]

CONFIG_INSTANCE_D_ADDRS = [
    f"{item['ip']}:{item['port']}" for item in CONFIG_INSTANCE_D_POOL
]
```

这样 `gatewayP.py`、`gatewayD.py` 不需要重复拼接逻辑。

### 3. 修改 `config/gatewayP.py`

将：

```python
DEFAULT_PREFILL = [f"{CONFIG_INSTANCE_P_IP}:{CONFIG_INSTANCE_P_PORT}"]
```

改为：

```python
DEFAULT_PREFILL = CONFIG_INSTANCE_P_ADDRS
```

`DEFAULT_DECODE` 建议暂时保留指向 Decode Gateway：

```python
DEFAULT_DECODE = [f"{CONFIG_GATEWAY_D_IP}:{CONFIG_GATEWAY_D_PORT}"]
```

原因：Prefill Gateway 当前对接的是 Decode 池网关，而不是直接对接多个 Decode instance。

### 4. 修改 `config/gatewayD.py`

将：

```python
DEFAULT_DECODE = [f"{CONFIG_INSTANCE_D_IP}:{CONFIG_INSTANCE_D_PORT}"]
```

改为：

```python
DEFAULT_DECODE = CONFIG_INSTANCE_D_ADDRS
```

这样 Decode Gateway 默认就会管理多个 Decode instance。

### 5. 修改 `config/instanceP.py`

实例代理配置直接从 `CONFIG_INSTANCE_P_POOL` 读取，不再读取 `CONFIG_INSTANCE_P_IP`、`CONFIG_INSTANCE_P_PORT`、`CONFIG_VLLM_P_PORT`。

为最小化修改范围，不修改 `entrypoints/instanceP.py`，仍然让入口使用 `DEFAULT_HOST`、`DEFAULT_PORT`、`VLLM_PORT`。在 `config/instanceP.py` 中增加一个默认索引常量：

```python
INSTANCE_INDEX = 0
```

然后由该索引读取池内配置：

```python
INSTANCE_CONFIG = CONFIG_INSTANCE_P_POOL[INSTANCE_INDEX]

DEFAULT_HOST = INSTANCE_CONFIG["ip"]
DEFAULT_PORT = INSTANCE_CONFIG["port"]
VLLM_PORT = INSTANCE_CONFIG["vllm_port"]
```

语义：

- 默认使用 `CONFIG_INSTANCE_P_POOL[0]`。
- 如果要启动第二个 P instance，只需在对应运行副本或配置文件中把 `INSTANCE_INDEX` 改为 `1`，或继续通过命令行显式传 `--host`、`--port`、`--vllm_port`。
- 不改入口参数，避免引入新命令行行为。

### 6. 修改 `config/instanceD.py`

Decode 侧同理，`config/instanceD.py` 通过 `INSTANCE_INDEX` 从 `CONFIG_INSTANCE_D_POOL` 读取 `ip`、`port`、`vllm_port`，不再读取 `CONFIG_INSTANCE_D_IP`、`CONFIG_INSTANCE_D_PORT`、`CONFIG_VLLM_D_PORT`。

## 不建议直接做的修改

暂不建议修改 `entrypoints/instanceP.py`、`entrypoints/instanceD.py`，也不建议新增复杂配置读取函数。

原因：

- 当前 `entrypoints/instanceP.py` 和 `entrypoints/instanceD.py` 每次启动只对应一个进程、一个代理端口、一个 vLLM 端口。
- 多实例本质上仍需要启动多个进程，应该由启动脚本、容器编排或命令行参数负责。
- `run_multi.sh` 当前已经展示了多进程启动方式，例如 `--port 7001 --vllm_port 8001`、`--port 7002 --vllm_port 8002`。
- 为避免无意义堆叠代码，本次只修改 config 层读取逻辑，不新增入口参数。

## 风险点

1. 端口冲突：P 池和 D 池的代理端口、vLLM 端口必须互不冲突。
2. IP 归属：如果多个实例分布在不同机器，池中每个实例的 `ip` 应写对应机器 IP。
3. Gateway 与 instance 启动顺序：网关默认注册多个实例地址，但这些 instance 进程仍需实际启动。
4. 单实例兼容：实例代理不再读取旧单实例常量；旧常量若保留，只用于其它历史代码兼容。
5. 配置结构：`CONFIG_INSTANCE_P_POOL` 和 `CONFIG_INSTANCE_D_POOL` 仅包含 `ip`、`port`、`vllm_port`，不引入实例名称字段。

## 验证方式

1. 静态检查：
   - 确认 `config/ADDRESS_CONFIG.py` 可以被 Python 正常导入。
   - 确认 `config.gatewayP.DEFAULT_PREFILL` 输出多个 P instance 地址。
   - 确认 `config.gatewayD.DEFAULT_DECODE` 输出多个 D instance 地址。
   - 确认 `config.instanceP.DEFAULT_HOST`、`DEFAULT_PORT`、`VLLM_PORT` 来自 `CONFIG_INSTANCE_P_POOL[INSTANCE_INDEX]`。
   - 确认 `config.instanceD.DEFAULT_HOST`、`DEFAULT_PORT`、`VLLM_PORT` 来自 `CONFIG_INSTANCE_D_POOL[INSTANCE_INDEX]`。

2. 推荐验证命令：

```bash
python -c "from config.gatewayP import DEFAULT_PREFILL, DEFAULT_DECODE; print(DEFAULT_PREFILL); print(DEFAULT_DECODE)"
python -c "from config.gatewayD import DEFAULT_DECODE; print(DEFAULT_DECODE)"
```

3. 运行验证：
   - 分别启动多个 P/D instance。
   - 启动 Decode Gateway，确认其默认注册多个 D instance。
   - 启动 Prefill Gateway，确认其默认注册多个 P instance，并连接 Decode Gateway。

## 待用户确认

请确认以下配置取值：

1. Prefill 池是否使用：
   - `172.18.0.169:7001 -> vLLM 8001`
   - `172.18.0.169:7002 -> vLLM 8002`

2. Decode 池是否使用：
   - `172.18.0.169:7003 -> vLLM 8003`
   - `172.18.0.169:7004 -> vLLM 8004`

3. 是否只修改配置默认值，不修改启动脚本 `run_multi.sh`。

确认后再执行代码修改。
