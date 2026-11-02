# FPGA 工程验证 Skill

本目录提供独立的 [xilinx-bridge-cli](xilinx-bridge-cli/SKILL.md)。它将工程使用的 Agent Bridge 命令行、板卡配置和可运行样例封装为一个 Agent Skill。可将整个 `xilinx-bridge-cli/` 复制到智能体的技能目录，也可用 Python 在开发机上直接运行。常见用途是为指定器件建工程、运行带自检的仿真、综合 RTL，并在连接真实目标后核对板卡。

## 用一个像素验证整条工具链

准备 Python 3.10 或以上版本，以及安装了目标器件支持的 Vivado。将 [pixel-threshold](xilinx-bridge-cli/assets/pixel-threshold) 的四个文件复制到新的工作目录，在那里打开终端。将下文的 `BRIDGE` 替换为 `xilinx-bridge-cli/scripts/bridge.py` 的完整路径，`BOARD` 替换为一份[板卡配置](xilinx-bridge-cli/assets/boards)的完整路径。

```text
python BRIDGE detect
python BRIDGE --board-profile BOARD call vivado_create_project --file create-profile.json
```

示例将每个 8 位灰度送过阈值电路。测试台以灰度值的最高位为参照，逐个检查 0 至 255，覆盖阈值两侧的 127 和 128。核对返回结果中的 `summary.simulation.verdict` 为 `pass`，并在 xsim 日志查到 `WB_PASS|256 gray levels checked`。改动 RTL 后可在同一工程目录执行以下命令复测。

```text
python BRIDGE --board-profile BOARD sim threshold_demo/threshold_demo.xpr --tb tb_pixel_threshold --runtime all
```

## 为什么要选择板卡，再判读结果

固定器件号能让单个工程运行，但换板后可能让同一份 RTL 按另一种芯片综合。实现中把完整器件号收进板卡配置，并检查已有工程及运行配置。以 Basys 3 配置打开 ZedBoard 工程时，命令会在启动 Vivado 前报告器件冲突。此时应重新选择匹配的工程、板卡或约束。

另一类问题出现在仿真结束时。本例的成功和失败分支都会执行 `$finish`。因此测试台在首个不匹配处先输出 `WB_FAIL` 和像素值，只有遍历全部输入才输出 `WB_PASS`。智能体用这条结果决定返回定位代码，还是进入下一步综合。[测试台源码](xilinx-bridge-cli/assets/pixel-threshold/tb_pixel_threshold.v)

## 已验证的效果

在 Vivado 2026.1 中，三份配置分别完成建工程与 xsim。器件综合另外使用带时钟的 8 位加法器，检查综合器能否将 9 位输出寄存器映射到所选器件。两项实验采用不同电路，便于分别检查功能与器件映射。

| 板卡配置 | 从生成工程读回的器件型号 | 阈值测试台 | 加法器独立综合 |
|---|---|---|---|
| Basys 3 | `xc7a35tcpg236-1` | 256 / 256 | 9 个触发器 |
| ZedBoard | `xc7z020clg484-1` | 256 / 256 | 9 个触发器 |
| KV260 | `xck26-sfvc784-2LV-c` | 256 / 256 | 9 个触发器 |

逐板工程器件、xsim 判定、通过标记、综合 RTL、寄存器查询条件与综合结果保存在[跨板验证记录](verification/board-profiles.json)。在仓库根目录执行 `python -m unittest discover -s skill/xilinx-bridge-cli/tests -p test_board_profiles.py -v`，可单独复核器件冲突、JTAG 选择、SSH 板型检查和 DPU 架构选择等五项离线测试。

## 使用边界

对新的 AMD/Xilinx 目标，应提供 Vivado 支持的完整器件料号。写入实物前，另行准备该板的时钟与引脚约束，用 `devices` 读取实际线缆和器件标识。DPU 编译须提供当前设计的 `arch.json`。本作品的摄像头、Linux 固件加载和姿态识别使用 KV260 专用适配。Basys 3 与 ZedBoard 的验证止于开发机仿真和器件综合。具体字段、硬件连接方法和调用例子见[跨板使用说明](xilinx-bridge-cli/references/boards.md)。
