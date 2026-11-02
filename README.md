# KV260端云AI互动体验平台

观众站在摄像头前，以身体动作绘制水墨、控制游戏，或跟练八段锦和太极。KV260 负责本地视觉推理与画面显示，DeepSeek 助手提供玩法说明、创作建议及成绩复盘。

## 源码目录

| 目录 | 内容 |
|---|---|
| fpga | 七个 SystemVerilog RTL 模块、五个测试平台、量化 ROM、平台打包与实现 Tcl |
| bodyink-live | 视觉后端、动作引擎、绘画、Hourglass 解码和 PL 与 DDR 的适配 |
| camera、systemd | USB 摄像、内核补丁、板端服务及 PL 模式配置 |
| ink-suite | GTK 主页，体感游戏、跟练、水墨、DeepSeek、局域网作品站 |
| fruit-slice、ink-fight | 主机调用的独立游戏引擎和配套资源 |
| integrations | PAL 移植和 TheXTech 原生控制补丁 |
| models、tools | 模型来源与照片模型恢复、训练代码、动作标注工具 |
| skills/xilinx-bridge-cli | 标准 Agent Skill，内含可独立运行的桥接 CLI |

## 从源码构建

1. Windows 主机安装 Vivado 2022.2、KV260 SOM/Carrier board files 和 Vitis 2022.2。在 fpga/platform-source 下用以下命令取得平台（含子模块）。

       git clone --recursive --branch xlnx_rel_v2022.1 https://github.com/Xilinx/kria-vitis-platforms.git kria-vitis-platforms-xlnx_rel_v2022.1

   在 fpga 目录依次以独立的 Vivado batch 进程运行 package_ip.tcl、restore_platform.tcl、restore_dpu_package.tcl、integrate_platform.tcl、usb_platform.tcl 和 build_platform.tcl。每一步的入口如 vivado -mode batch -source package_ip.tcl。最终输出在 fpga/build/buildusb。将 Vitis 的 xclbinutil 加入 PATH 后运行 fpga/deployment/build_metadata.py；随后可按设备树和板卡固件流程生成 dtbo 与 bit.bin。

2. RTL 功能仿真可直接先运行 run_control_sim.ps1。算术与 ROI 测试使用确定性合成 YUYV 帧；安装 Python、NumPy 和 WSL 中的 gcc，依次运行 generate_vectors.py 和 generate_roi_vectors.py，再执行 run_sim.ps1、run_roi_sim.ps1、run_dma_sim.ps1、run_ip_sim.ps1。运行脚本前设置 XILINX_VIVADO。ROM 文件在 vectors 目录。

3. 目标环境为 KV260 ARM64 Linux、Python 3.10、XRT、VART、NumPy、GTK3/PyGObject、Cairo、Pillow、GStreamer 与 GLES2/EGL。按 models/hourglass/model-official.yaml 的 KV260 链接下载 Hourglass，将校验匹配的 hourglass-pe_mpii.xmodel 放到 bodyink-live/models。照片模型恢复方法见 models/photo/README.md，需另备 ARM64 CPython 3.10 版 ONNX Runtime 1.19.2 依赖。板端各组件使用源码中的路径布局 /home/ubuntu/{bodyink-live,ink-suite,ink-fight,fruit-slice}。

4. 在目标的 bodyink-live 目录，每条普通 C 库使用 cc -O3 -shared -fPIC SOURCE.c -o TARGET.so -lm。对应关系为 dtw.c → libbodyink_dtw.so，start_geometry.c → libbodyink_geometry.so，preprocess.c → libbodyink_preprocess.so，hourglass_postprocess.c → libbodyink_hourglass.so，pl_bridge/pl_mmio.c → pl_bridge/libpl_mmio.so，pl_bridge/roi_copy.c → pl_bridge/libroi_copy.so。使用 c++ -O3 -shared -fPIC postprocess_fast.cpp -o libbodyink_postprocess.so -lm 构建兼容解码库。摄像程序从 camera/build.sh 构建。systemd 服务和 drop-in 给出目标工作目录、DPU/PL 参数及依赖顺序。

5. ink-suite/sword/compile.sh、runner/native/build_native.py、kart/native 的构建脚本和 pal/pause_bridge.c 分别构建游戏原生库。按 THIRD_PARTY.md 准备教学视频、商业游戏自有数据、品牌资源和系统依赖。仅在受信板卡本地的 ~/.config/ink-suite/deepseek.json 设置自己的 api_key，权限为 600。无密钥时可用本地玩法说明。目标板已有正确的服务和固件后运行 python3 /home/ubuntu/ink-suite/launch.py --fullscreen。标签工具从 tools/ink-label/Install.cmd 安装。开发用提取服务 board_pose_server.py 默认只监听回环，可使用 SSH 端口转发。

6. 可在 ink-suite 目录运行 python -B -m unittest test_ai_assistant health.test_courses runner.test_body_controls fruit.test_input_gate，使用本机测试替身。桥接 CLI 的安装、配置、权限和示例见 skills/xilinx-bridge-cli/SKILL.md。此仓库为源码交付，系统镜像、商业教学/游戏素材和受许可约束的 AMD 软件由使用者按相应条款取得。

## 代码基线

前端使用 10 月 29 日健康与太极最终版，叠加同日菜单修改、10 月 28 日绘画操作说明及 10 月 26 日跑酷修复。水墨后端采用 10 月 28 日实际安装的八项变更。PL 平台为 USB 摄像、B4096 DPU 和团队预处理 IP 的组合。原始个人摄像帧以确定性仿真向量代替。每个发布文件的摘要记录在 SHA256SUMS。

针对演示所用 0bda:5858 USB 相机，先在目标板的一份源码副本中运行 python3 camera/configure_capture.py /dev/video0（选择实际的 index 0 节点），再构建 camera 并安装对应服务配置。固件部署时在 fpga/deployment 下执行 dtc -@ -I dts -O dtb -o bodyink-usb-pl.dtbo bodyink-usb-pl.dts，设置 BODYINK_BUILD_LOG 指向本次 Vivado 完整实现日志，并通过 PATH 提供 bootgen 后运行 package_firmware.py。脚本检查时序和位流生成日志。
