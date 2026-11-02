# 可复现的像素阈值仿真

这个练习用一位输出区分灰度 0–127 与 128–255。测试台逐个送入 256 个灰度，并用输入的最高位作为独立参照。它适合验证换电脑、换智能体后能否沿真实命令行走通 Vivado 的建工程和仿真流程。

1. 将 [pixel-threshold](../assets/pixel-threshold) 中的文件复制到新建的工作目录，并在该目录打开终端。
2. 用 `python BRIDGE detect` 检查 Vivado。示例器件为 `xc7a35tcsg324-1`；若未安装其器件支持，在工作副本的 `create.json` 中换为已安装的器件。
3. 运行以下命令。将 `BRIDGE` 替换为 skill 内 `scripts/bridge.py` 的完整路径。

```text
python BRIDGE describe vivado_create_project
python BRIDGE call vivado_create_project --file create.json
```

4. 检查输出中的 `status=success`、`summary.simulation.verdict=pass` 和 `summary.markers.PASS` 中的 `256 gray levels checked`。工程位于工作目录的 `threshold_demo/threshold_demo.xpr`。可用返回的 `summary.xsim_log` 找到测试台原始记录。
5. 修改测试台或 RTL 后，用 `python BRIDGE sim threshold_demo/threshold_demo.xpr --tb tb_pixel_threshold --runtime all` 在同一项目复测。再次执行建工程命令会保护已存在的同名工程。

同一份 RTL 与测试台已按 Basys 3、ZedBoard、KV260 三种器件配置在 Vivado 2026.1 中各自建工程。每组 xsim 都通过了 256 个灰度输入的自检，并分别完成了面向所选器件的综合。跨板操作命令见[板卡配置](boards.md)。样例未约束物理引脚。
