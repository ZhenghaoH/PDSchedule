# NIXL over TCP

PDSchedule 的完整真实部署流程已统一到：

- [环境准备](../../docs/environment.md)：NVIDIA GPU、vLLM/NIXL、容器和依赖。
- [NIXL 1P/1D 启动](../../docs/quickstart.md)：全部七个 Python 服务、拓扑、请求和预期输出。

本目录原有 Shell 辅助脚本只保留在本地，不作为 GitHub 用户的启动入口。
按上述文档直接执行 Python 命令，无需下载这些脚本或额外启动 toy proxy。

## TCP 配置

两个 vLLM 进程均使用：

```bash
export UCX_TLS=tcp,cuda_copy
export UCX_NET_DEVICES=all
```

NIXL 使用 UCX 后端；TCP 路线仍需要支持 CUDA 内存访问的 UCX/NIXL 构建。
side-channel 的 host/port、两侧模型/布局和 Instance 上报的 metadata 必须对应。
不是所有 UCX 构建只设置环境变量就能自动获得 TCP/CUDA 支持。

## 区分两类检查

上游 [vLLM NIXL 指南](https://docs.vllm.ai/en/v0.13.0/features/nixl_connector_usage/)
中的 toy proxy 可用于独立检查底层 vLLM Connector；它不运行 PDSchedule 的
Controller、Gateway 或 Instance Proxy，不能替代项目端到端部署验证。

本项目以经 Controller 返回的 PD 请求和 D vLLM 的 NIXL 传输指标作为分层检查，
具体命令与成功判据见[公开启动流程](../../docs/quickstart.md#10-经-controller-发出真实请求)。
