---
name: xilinx-bridge-cli
description: 使用随包命令行驱动 AMD/Xilinx FPGA 设计与验证。在需要建立 Vivado 工程、运行 xsim、检查约束与实现、调用 Vitis/HLS、核对 KV260 视觉与 DPU 结果，或诊断其工程和工具链时使用。
---

# 用命令行完成 FPGA 设计验证

以本文件所在目录为技能根目录。使用 Python 3.10 或更新版本，从**项目的工作目录**运行技能中的 [scripts/bridge.py](scripts/bridge.py)。下面用 `BRIDGE` 表示该文件的完整路径；运行命令时替换这个名称，路径含空格时加引号。相对工程路径按调用者的工作目录解析。

## 选择执行入口

- 先运行 `python BRIDGE detect`，读取可用版本和工具。已有工程需仿真时运行 `python BRIDGE sim design.xpr --tb tb_top --runtime all`。综合、实现到生成位流用一次 `python BRIDGE build design.xpr --wait`。
- 新建工程并同时加源、抓波形、跑 HLS、审核复制后的 Vitis 工程等任务，运行 `python BRIDGE tools` 选工具，随后用 `python BRIDGE describe 工具名` 查看参数与默认值。将参数写成 UTF-8 JSON 对象，运行 `python BRIDGE call 工具名 --file 请求文件.json`。已有 HDL 直接在 `items` 里传路径，保持 `sources_1` 和 `sim_1` 的顶层各自独立。
- 例如，给刚复制的 Vivado 项目做静态审计时，向 `vivado_list_sources` 传 `{"xpr":"project/design.xpr","audit_only":true}`。检查它返回的缺失文件和外部引用，先解决需要复核的依赖，再在副本上构建。
- 初次验证或向其他队伍演示时，可按[独立样例](references/example.md)一次性创建工程、加入 RTL 与测试台并启动 xsim。完成标准是 256 个输入逐项检查通过。

## 解读结果

仿真查看 `summary.simulation.verdict` 和测试台的通过标记。综合实现检查时序、DRC 和位流路径。图像比对保留输入、期望输出、打包顺序和尺寸；回环、仿真、推理输出和物理显示各自记录。KV260 的摄像头、固件加载、JTAG 和 Flash 操作要与用户选定的板卡和本次任务一致。首次连接优先 `kv260-board HOST --action inspect`，该命令默认不会占用相机。

普通命令行前台等待作业结束。需要跨请求调度长任务时，也可选择独立的 MCP 服务入口。依赖、运行环境、常用工具及结果字段见[运行参考](references/runtime.md)。
