# Controller 本地预测模型

发布仓库仅提供预测器和调度代码，不包含特定机器的已拟合模型。
`demand_aware` 可以在没有模型文件时运行，使用现有容量/吞吐信息进行粗略代价估计。
这保留调度可用性，但不提供硬件校准精度。

源码运行默认使用仓库的 profile/；wheel 安装后默认使用服务工作目录下的 profile/。
启动前可用 PDSCHEDULE_PROFILE_DIR 指定部署方可写的 profile 根目录；Controller 在其下
使用 controller/，不会把拟合结果写入 site-packages。目录选择在策略导入时确定，修改后需重启。

## 本地生成与放置

先使用 [TTFT](../TTFT_predictor/README.md) 或 [TPOT](../TPOT_predictor/README.md)
代码在目标模型、GPU、TP/PP 和运行时上测量拟合，再由部署方放置可信本地文件。

现有实现从 Pool 的机器标识（如存在）或地址尾段推导标识，按下列路径加载：

```text
profile/controller/profile_<id>/TTFT_predictor/artifacts/prefill_ttft_regressor.pkl
profile/controller/profile_<id>/TPOT_predictor/artifacts/decode_tpot_regressor.pkl
```

例如地址 `10.0.0.7` 默认对应 `profile_7`。这是当前路径约定，不意味着同尾号
或不同型号的机器可以共享校准结果。Gateway 的 artifacts 不会自动复制到 Controller。

端口和数值 Pool ID 不参与上述标识。同一 IP 下的多个同角色 Pool 会共用同一个模型路径；
不同地址若尾段相同也会冲突。常规 Gateway CLI 没有上报独立 machine-ID 的参数。
因此 `127.0.0.1` 下的多池示例只能在硬件、模型及配置等价且验证通过时使用代表性
`profile_1`；不能把它描述为同地址异构池的独立校准。完整放置和重启命令见
[SLO 使用流程](../../docs/slo.md)。

Controller 从模型状态读取 `coeffs`：TTFT 使用 `tokens` 和 `d`；TPOT 使用
`d1`～`d4`。通过对应回归器保存状态，避免手工伪造生产模型。

## 缺失、加载和更新

- 文件缺失或加载失败时，策略使用现有 fallback，不要求克隆者预先下载模型。
- 策略缓存已加载结果和缺失记录；补充或替换本地模型后需重建策略/重启 Controller，
  不应假设文件会自动热加载。CLI 切换至其他策略再切回会复用策略实例，不清除这些缓存。
- 只加载自己生成或已核验的 pickle；不能根据外部请求加载任意文件。
- 本地生成的 artifacts、pickle/joblib 已被 Git 忽略，不应使用强制暂存将它们发布。
- 测量原始数据、验证记录、硬件/模型版本和哈希由部署方在本地管理。
