# sanger_bench — 公开真实 AB1 基准文件

来源：Biopython 官方测试集 `Tests/Abi/`（github.com/biopython/biopython，
2026-11-07 取自 master）。许可：Biopython License（见仓库 LICENSE 惯例，
允许再分发；本目录仅作测试基准用途）。

- `3730.ab1`（1165bp，meanQ≈44.8）：含 6 处 0.30-0.39 染料肩峰次峰
  （集中修剪后 50-125bp，5/6 次峰为 A）——混合判定的**误报**基准（F1）。
- `3100.ab1`（795bp）：61 处次峰集中在 470-667bp 信号衰减段——同上（F2/M7）。
- `310.ab1`（868bp，无 PCON 质量记录）：无质量值文件的解析回退基准（F3）。

场景引用见 `tests/sanger_scenarios.py`；真值标注与 ROC 用法随 T23 补全
（`truth.yaml` + `scripts/sanger_benchmark.py`）。
