# 原生计算队列容量复验档位

此档位是原 `capacity-v1` 的后续工程复验，不能覆盖旧实测失败。新 v2 文件原始字节 SHA256：

```text
8d232aa52fdc5b60bbdf32c258aaa964585b3faf2e5b4b50f7d75349a00c043c
```

原 v1 字节 SHA256 保持 `bd82c7ab050a53610742515d4af082140f6fce5593b203cabaa4e847ffb4a170`。

仅两处变化：`profile_id=single-enterprise-native-queue-v2`；
`limits.configured_compute_concurrency=2`。原目标并发2、数据规模、硬件要求、
300秒预热/1800秒稳态、完整4200操作、HTTP限时/重试、30秒模拟等待及全部门槛均保持。
`tests/test_capacity_load.py` 逐字段核对这一不变量，并实际启动对应配置。

运行命令（输出目录必须不存在，工作树须为干净固定SHA）：

```bash
.venv/bin/python scripts/capacity_probe.py \
  --output /absolute/private/new-capacity-run \
  --manifest evals/capacity-native-queue-v2/manifest.json \
  --manifest-sha256 8d232aa52fdc5b60bbdf32c258aaa964585b3faf2e5b4b50f7d75349a00c043c
```

不加 `--unit-smoke`。FunctionModel、合成身份及本机完整HTTP负载只能证明工程行为，
不能替代真实DeepSeek、企业身份、目标机器或具名用户验收。判定须读完整报告，不从exit0推断。
