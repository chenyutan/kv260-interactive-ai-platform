# 运行参考

## 环境与来源

将整个 `xilinx-bridge-cli` 目录复制到支持 Agent Skills 的宿主技能目录，或在当前工作环境中直接读取 `SKILL.md`。保留 `scripts/`、`assets/`、`references/` 的相对布局。该技能的命令行可在没有智能体的终端独立运行。

Python 3.10+ 的标准库即可列工具、检查静态工程、调用已安装的 Vivado/Vitis。图像准备与图像评价需要 Pillow 10+，视频抽帧还需要 FFmpeg/ffprobe。Vitis AI 流程依赖所选版本的工具及模型环境；访问 KV260 需可到达的授权目标和 SSH 客户端。`serve` 是可选的 stdio MCP 入口，使用时才需要在同一 Python 环境安装 `fastmcp>=2`。

随包的 Bridge 1.5.1 源自 [4d0ac77 对应的源代码](https://github.com/chenyutan/xilinx-agent-bridge/tree/4d0ac77bcb285342c233527497c1d9fae3c487a5)。本包将工具函数与 MCP 注册分开，使直接 CLI 和可选 MCP 共用一套实际处理函数。新增的 `tools`、`describe`、`call` 提供本地发现、参数查询和 JSON 调用。`scripts/xilinx_bridge/` 含引擎、内嵌 Tcl、Vitis 脚本生成器与本地评阅界面。

默认配置和 Job 记录在当前用户的 `.xilinx-agent-bridge` 目录。要隔离某次演示，提前将环境变量 `XAB_STATE_DIR` 指向独立的绝对目录。工具不在默认搜索路径时，通过环境变量 `XAB_VIVADO_PATH` 或 `XAB_VITIS_PATH` 指定本机安装目录。仅需临时指定许可证时，用 `XAB_LICENSE_FILE` 指向已有许可证位置。

## 按需查找命令

| 工作 | 入口与检查点 |
|---|---|
| 查看原生命令 | `python BRIDGE --help`，再用 `python BRIDGE 命令 --help` 查看参数。跨板卡入口与配置字段见[板卡配置](boards.md)。 |
| 查询 36 个结构化工具 | `python BRIDGE tools`；例如 `python BRIDGE describe vitis_build`。 |
| 新工程 | `call vivado_create_project --file create.json` 可融合建工程、加源和自检仿真。 |
| 审查时序与资源 | 对已完成的实现运行 `vivado_report`。按实际报告检查指标及未约束项。 |
| Vitis / HLS | `vitis_list_components` 审核工程依赖，`vitis_build` 以 `workspace` 和 `hls` 选择 HLS 阶段。检查 `summary.hls.quality_gate`。 |
| 图像和板卡 | `vision-prepare` 准备输入和 golden，`kv260-board` 选择实际后端，`vision-evaluate` 回读比对。DPU 输入要匹配模型布局和量化类型。 |
| 已有任务 | 简写 `job ID` 或结构化 `xilinx_job_status`、`xilinx_job_log` 查询状态、摘要或日志尾部。 |

JSON 布尔值写作 `true` / `false`。参数较多时使用文件，消除不同 shell 对引号和反斜杠的差异。`--file -` 可接收 UTF-8 标准输入。前台模式在工作进程退出前收齐 Job 结果；若需异步提交和轮询，使用持续运行的 MCP 服务 `python BRIDGE serve`。
