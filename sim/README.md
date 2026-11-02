# RTL 仿真

下表记录 Vivado XSim 2022.2 对团队 RTL 的逐级检查。文本保留原始每例通过行、最终计数及工具版本，省略主机与临时目录。仿真中的时钟周期来自测试平台。

| 测试对象 | 原始输出摘录 | 主要检查 |
| --- | --- | --- |
| 定点颜色、双线性采样与量化 | [pipeline.txt](pipeline.txt) | 854,288 次 RGB/INT8 对照；加入反压和流水复位 |
| ROI 与双行缓存 | [roi.txt](roi.txt) · [整行读取基线](roi-full-row.txt) | 655,360 次像素检查；1080p 主 ROI 读取源数据从 1,966,080 降到 1,107,968 字节 |
| AXI-Lite 控制 | [control.txt](control.txt) | 地址/数据独立到达、响应反压、配置锁存、撤销排空 |
| AXI DMA | [256 拍](dma-256.txt) · [7 拍](dma-7.txt) | 344,067 与 196,611 次检查。主动注入四种错误并检验恢复 |
| 控制与 DMA 集成 | [256 拍](ip-256.txt) · [7 拍](ip-7.txt) · [已封装 IP](packaged-ip-7.txt) | 相同事务在寄存器接口与封装边界处复验 |

波形检查结果见 [ROI](roi-waveform-result.json)、[控制](control-waveform-result.json) 与 [DMA](dma-waveform-result.json)。DMA 窗口核对了 513 个写数据字以及 202 个反压时钟沿。

复现时先在 `fpga` 运行 `python generate_vectors.py` 和 `python generate_roi_vectors.py`，随后运行同目录的 `run_sim.ps1`、`run_roi_sim.ps1`、`run_control_sim.ps1`、`run_dma_sim.ps1` 和 `run_ip_sim.ps1`。设置 `XILINX_VIVADO` 指向 Vivado 2022.2 安装目录，并准备 Windows 的 Python、NumPy 和 WSL 中的 GCC。公开生成器以确定性合成帧替代原录制图像，验证方法及尺寸、ROI、反压和边界场景保留。
