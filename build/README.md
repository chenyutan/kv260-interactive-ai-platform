# 综合与实现

Vivado 2022.2，目标器件为 `xck26-sfvc784-2LV-c`。报告来自 2026 年 9 月 14 日的构建，保留原始表格、工具版本和构建时间。公开副本移除了工具报告头中的主机名，并将输出路径缩短为文件名。

| 设计范围 | 文件 | 阅读位置 |
| --- | --- | --- |
| 独立视觉 DMA 模块，写缓存优化前后 | [优化前](dma-util-before.rpt) · [优化后](dma-util-after.rpt) | `bodyink_preprocess_dma` 顶层从 4570 LUT、9320 FF 降到 1896 LUT、941 FF。展开 `writer` 行可见同步读地址合并后推断出一个 RAMB18。 |
| 集成 DPU 与视觉前处理的 KV260 整机平台，综合后 | [资源](synth_utilization.rpt) · [时序](synth_timing.rpt) | 综合阶段的整机结果。 |
| 同一平台，布线后 | [资源](routed_utilization.rpt) · [时序](routed_timing.rpt) · [路由](route_status.rpt) | 203,274 条可布线网络全部完成。WNS 为 0.000 ns，WHS 为 0.003 ns，WPWS 为 0.071 ns。时钟频率与各时钟域余量见时序报告的 Clock Summary 和 Intra Clock Table。 |
| 实现检查 | [DRC](routed_drc.rpt) · [跨时钟域](cdc.rpt) | 保留完整检查项和工具警告。 |

重建整机请按仓库首页的 KV260 平台步骤准备 AMD 平台源码与工具。最后一步 `fpga/build_platform.tcl` 会生成这一组综合与布线报告。单独重建优化后的视觉 DMA 可在 `fpga` 中运行 `vivado -mode batch -source run_ooc.tcl`。优化前报告用作同一综合层次的历史资源对照。
