# DistServe 四机中心网关启动脚本

本目录只用于 DistServe 基线实验，不要与仓库根目录的旧 `run_166.sh`、`run_167.sh`、`run_169.sh`、`run_170.sh` 混用。四份脚本继续使用 `new_env` Docker 容器和 GNOME Terminal 多标签页启动方式。

## 拓扑和职责

| 机器 | 组件 | Instance Proxy 地址 | 注册网关 |
| --- | --- | --- | --- |
| 166 | 2 个 Decode vLLM + 2 个 D Instance Proxy | `166:7001/7002` | `172.18.0.170:8082` |
| 167 | 2 个 Prefill vLLM + 2 个 P Instance Proxy | `167:7001/7002` | `172.18.0.170:8081` |
| 169 | 2 个 Prefill、2 个 Decode vLLM 及其 Instance Proxy | P `169:7001/7002`，D `169:7003/7004` | P `170:8081`，D `170:8082` |
| 170 | 唯一 Controller、中心 P/D 网关及本机 P/D 实例 | P `170:7001/7002`，D `170:7003/7004` | P `170:8081`，D `170:8082` |

Controller 固定路由到 `172.18.0.170:8081/8082`。中心 P 网关预置六个 P 地址，中心 D 网关预置六个 D 地址；实例仍通过独立注册接口确认上线，心跳只更新负载。

## 启动

先在 170 运行中心脚本，再分别在 166、167、169 运行对应脚本：

```bash
chmod u+x DistServe启动脚本/run_*.sh
./DistServe启动脚本/run_170.sh
./DistServe启动脚本/run_166.sh
./DistServe启动脚本/run_167.sh
./DistServe启动脚本/run_169.sh
```

170 脚本显式启用 P/D 的 `distserve`，P 侧使用 `--distserve-prefill-batch-tokens 2048`，并关闭 TTFT/TPOT predictor warmup。这里的 `L_m=2048` 来自当前实验 profile；更换模型、GPU 或并行配置后，应重新运行 `profile/profile_distserve_lm.py` 并同步修改启动参数。

## 核对与在线切换

中心服务启动后可先检查 HTTP 服务，再从各标签页日志核对六个 P 和六个 D 实例均已注册：

```bash
curl -fsS http://172.18.0.170:5000/ >/dev/null
curl -fsS http://172.18.0.170:8081/docs >/dev/null
curl -fsS http://172.18.0.170:8082/docs >/dev/null
```

在中心 P 或 D 网关 CLI 中执行：

```text
show scheduler
switch scheduler -m distserve
```

停止时在各标签页按 `Ctrl+C`，或使用项目已有的停止脚本清理容器内进程。中心网关是单点；中心网关不可用时不要继续发送实验流量。
