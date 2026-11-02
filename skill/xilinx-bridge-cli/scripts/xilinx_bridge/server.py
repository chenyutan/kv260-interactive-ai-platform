"""Xilinx Agent Bridge MCP Server。

暴露给 AI Agent 的工具集: 环境探测 / Vivado 工程 / 仿真 / 综合实现 / 比特流 /
JTAG 下载 / ILA / FLASH / Vitis。所有长任务支持异步 job。
"""
from __future__ import annotations

from fastmcp import FastMCP

from .handlers import TOOLS

mcp = FastMCP(
    "xilinx-agent-bridge",
    instructions=(
        "Xilinx Vivado/Vitis 桥接服务。优先减少 Vivado 冷启动: "
        "共享文件系统已有 HDL 时 items 传 path，避免把源码放进 MCP; "
        "新工程用 vivado_create_project(items=..., simulate_after_create=true) 一次完成建工程/加源/仿真; "
        "Agent 直接改了已加入工程的文件时只调用 vivado_simulate; "
        "内容必须经 MCP 写入时才用 vivado_write_sources(simulate_after_write=true) 合并写源/复测; "
        "需要波形就在同一调用传 waveform，优先读取有界 transitions，不要重复启动 xsim; "
        "AXI4-Stream 图像算法在上述工具中传 image_test，自动生成黄金向量和协议背压检查; "
        "KV260 真实图片先用 kv260_vision_prepare 一次生成仿真 mem、DMA bin、平台信息和 review session; "
        "外部视频先用 vivado_video_prepare 在主机侧限帧/抽帧/缩放并生成多帧 path-only image_test，"
        "再把它传给一次 create/write/simulate，禁止逐帧启动 xsim 或回传像素数组; "
        "板端输出再用 kv260_vision_evaluate 做定量门禁或打开本地人工确认界面，不传像素大数组; "
        "Vitis AI 任务只用 vitis_ai_flow：环境变化后 inspect，一般模型用一次 pipeline；已有XMODEL用analyze/compile；"
        "默认自动选 3.5，当前工具链选择 2022.2 时自动选 3.0，并自动使用 KV260 DPU 架构；"
        "长任务wait=false走现有Job；不要读取Conda包清单、完整日志或arch.json；声明cache_inputs后安全复用量化；"
        "KV260 接线后先用 kv260_board_flow(action=host_inspect) 合并检查USB/UART/JTAG；启动Linux后再用action=inspect；构建继续复用 vitis_build，"
        "再用同一工具 action=run 融合上传、XRT host、结果回收和现有 evaluate，不逐项 SSH; "
        "HLS/xf::cv 算法复用 vitis_build(hls=...)，一次启动完成写源和所选阶段，"
        "必须检查 summary.hls.quality_gate; "
        "复制/迁移 Vitis workspace 后先用 vitis_list_components(audit_only=true) 零启动检查 XPFM/XSA/BSP 外链、"
        "compile_commands 和 -O0；普通 build 会只在发现问题时附审计摘要; "
        "仅在不需要写文件时单独调用 vivado_simulate。"
        "之后用 vivado_rtl_analysis 做 RTL 分析; vivado_build_all 后台跑综合+实现+比特流, "
        "按 poll_after_seconds 用 xilinx_job_status(detail=compact) 轮询; build_all 已含综合，不要先重复 synth; "
        "再按需执行 JTAG、ILA 和 FLASH 工具。Zynq JTAG 默认 device_index=-1 自动跳过 ARM DAP; "
        "ILA 裸触发值如 1'b1 自动按相等比较，trigger_position 可保留触发前样本; "
        "运行 ELF 时同时传匹配 XSA/ps7_init 和 symbol_checks，不要把下载成功当应用通过。"
        "成功结果默认紧凑，不回传冗长日志；失败时看 summary.errors/log_tail，"
        "需要完整日志再调用 xilinx_job_log。xilinx_detect 默认 compact；只在路径诊断时用 standard。"
        "创建工程默认不覆盖，只有明确确认后才传 overwrite=true。"
    ),
)


for function in TOOLS.values():
    mcp.tool(function)


def main() -> None:
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
