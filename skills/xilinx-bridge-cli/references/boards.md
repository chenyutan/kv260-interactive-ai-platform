# 在不同板卡上使用 Bridge

Bridge 在安装 Vivado 或 OpenSSH 的开发机运行。每次调用通过 `--board-profile` 选择板卡，配置随命令生效。把下列配置复制到自己的工程目录，接入其他目标时按实际芯片、线缆和系统填写。不同厂商的板卡只要采用当前 Vivado 支持的 AMD/Xilinx 器件，也可自建配置。

| 样板 | Vivado 完整器件型号 | 可用入口 |
|---|---|---|
| [Basys 3](../assets/boards/basys3.json) | xc7a35tcpg236-1 | RTL、HLS、JTAG |
| [ZedBoard](../assets/boards/zedboard.json) | xc7z020clg484-1 | RTL、HLS、JTAG，启动 Linux 后可用 SSH |
| [KV260](../assets/boards/kv260.json) | xck26-sfvc784-2LV-c | RTL、HLS、JTAG、Linux SSH，另有工程专用的视觉与 DPU 流程 |

## 建工程和综合

以下命令中 `BRIDGE` 是 skill 的 `scripts/bridge.py`，`BOARD` 是选定配置的路径。先安装 Vivado 对应器件的支持文件。将[阈值示例](../assets/pixel-threshold)复制到新的工作目录，使用不预设器件的 `create-profile.json`。

```text
python BRIDGE --board-profile BOARD call vivado_create_project --file create-profile.json
python BRIDGE --board-profile BOARD sim threshold_demo/threshold_demo.xpr --tb tb_pixel_threshold --runtime all
```

只建空工程可运行 `python BRIDGE --board-profile BOARD create . demo`。要综合已有工程，准备参数文件 `{"xpr":"threshold_demo/threshold_demo.xpr"}`，用 `call vivado_synthesize --file synth.json` 执行。HLS 工程用 `call vitis_build`，请求内提供 `workspace` 和 `hls` 对象，配置会填入 `hls.part`。若请求中的器件与所选板卡相冲突，命令直接返回错误。生成板级位流前应加载该板的引脚、电平和时钟约束。涉及处理器子系统时使用对应硬件设计的 XSA。

## 选择 JTAG 线缆和器件

1. 在配置副本的 `jtag.server` 填入 hw_server 的主机和端口。运行 `python BRIDGE --board-profile BOARD devices` 发现线缆与链上设备。
2. 将 `WB_TARGET` 的完整值填进 `jtag.target_name`，将 `WB_DEVICE` 记录的可编程器件 `name` 和 `part` 原样填进 `jtag.device_name` 和 `jtag.device_part`。这里的 `device_part` 通常是芯片短名，如 `xc7z020`。顶层 `part` 仍是包含封装和速度等级的完整型号。
3. 执行已获授权的 `program design.bit`、`ila probes.ltx` 或 `flash design.mcs 具体Flash料号` 时，沿用相同前缀。Bridge 在该次硬件会话重新定位线缆和器件，核实芯片型号后才执行操作。Flash 料号按板上实际存储器填写。运行 Linux 的 SoC 使用系统支持的加载流程重新配置正在被驱动占用的逻辑。

## Linux 板卡和 DPU

把下例中的连接信息填入本地配置。使用 SSH Agent 或 OpenSSH 已配置的身份即可，若须指定密钥可添加 `ssh_key`，其相对路径以配置文件所在目录为基准。

```json
{
  "name": "ZedBoard on lab network",
  "part": "xc7z020clg484-1",
  "ssh": {"host": "zedboard.local", "user": "operator", "port": 22}
}
```

运行 `python BRIDGE --board-profile BOARD board-inspect`。返回的 `kernel`、`machine`、`reported_model` 和 `compatible` 来自实际 SSH 目标。未提供设备树的 Linux 系统仍可回报内核和架构。纯 FPGA 板使用 JTAG 接口。

做 Vitis AI 编译时，将该设计的 DPU 架构 JSON 路径写进配置的 `dpu_arch`。通过 `call vitis_ai_flow` 运行 `compile` 或 `pipeline`，编译器使用所选文件。模型部署前需把 XMODEL 的目标指纹与实机 DPU 核对。KV260 配置中的 `adapter: "kv260"` 开启 `call kv260_board_flow` 的专用相机、固件与 DPU 流程。填写 `ssh` 后，Bridge 会先从目标设备树确认 KV260 载板型号。

Basys 3、ZedBoard 和 KV260 配置已分别完成 Vivado 建工程、256 个输入的 xsim 检查和器件综合。JTAG 实物下载、Flash 以及板上 DPU 推理仍以每块实物的运行结果为准。
