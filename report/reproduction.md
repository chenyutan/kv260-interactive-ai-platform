# 从源码到 KV260 现场演示

复现分三步。主机先比对功能仿真结果，再生成同一工程的位流、设备树和 XRT 元数据。KV260 加载这组三件套后，先让 PL 处理一张已知答案的合成图，最后接入摄像头、姿态网络和互动界面。

## 1. 主机验证与硬件构建

使用 Windows、Vivado/Vitis 2022.2、KV260 SOM 和 Carrier 板卡文件、Python 3 与 NumPy，以及 WSL 中的 gcc。工程使用已验证的 2022.2 板卡平台和 B4096 DPU IP，比赛推荐的新版 Vivado 可用于独立 Skill 示例。正式整板实现使用 2022.2。短英文路径可避免工具生成的层级路径超长。以下 PowerShell 命令从仓库根目录执行，先进入已配置好 Vivado 2022.2 环境的终端。

~~~powershell
python data/verify_evidence.py
cd fpga
python generate_vectors.py
python generate_roi_vectors.py
foreach ($test in 'run_control_sim','run_sim','run_roi_sim','run_dma_sim','run_ip_sim') {
    powershell -NoProfile -ExecutionPolicy Bypass -File "./$test.ps1"
    if ($LASTEXITCODE) { throw "$test failed" }
}
New-Item -ItemType Directory -Force platform-source | Out-Null
git clone --recursive --branch xlnx_rel_v2022.1 https://github.com/Xilinx/kria-vitis-platforms.git platform-source/kria-vitis-platforms-xlnx_rel_v2022.1
if ($LASTEXITCODE) { throw "platform clone failed" }
New-Item -ItemType Directory -Force packaged-ip,build/base | Out-Null
foreach ($step in 'package_ip','restore_platform','restore_dpu_package','integrate_platform','usb_platform','build_platform') {
    vivado -mode batch -source "./$step.tcl" -log "./$step.log"
    if ($LASTEXITCODE) { throw "$step failed" }
}
~~~

各仿真脚本检查功能通过标志，历史记录见 [仿真结果](../sim/README.md)。整板输出在 `fpga/build/buildusb`，包括 `bodyink.bit`、`bodyink.xsa`、资源与时序报告。继续在 `fpga` 目录运行以下命令。Windows 侧须能调用 `xclbinutil` 和 `bootgen`，WSL 内须有 `dtc`。

~~~powershell
$env:BODYINK_BUILD_LOG = (Resolve-Path ./build_platform.log).Path
python deployment/build_metadata.py
if ($LASTEXITCODE) { throw "XRT metadata failed" }
Push-Location deployment
wsl -e dtc -@ -I dts -O dtb -o bodyink-usb-pl.dtbo bodyink-usb-pl.dts
if ($LASTEXITCODE) { throw "device tree overlay failed" }
python package_firmware.py
if ($LASTEXITCODE) { throw "firmware packaging failed" }
Pop-Location
~~~

`deployment` 中应有 `bodyink-usb-pl.bin`、`bodyink-usb-pl.dtbo`、`bodyink-usb-pl.xclbin` 和 `firmware.sha256`。元数据程序核对本次生成的 HWH 中 DPU、PL 寄存器地址和中断连接，再核对架构指纹；打包程序读取本次布线报告和位流完成日志。将这四个新文件一同带到板上源码副本的 `fpga/deployment/`。

## 2. 准备板卡与摄像头

基准板为 KV260 revB，使用本地显示器、USB 3.0 摄像头、ARM64 Ubuntu 桌面、可访问的 SD 卡和串口控制台。软件依赖包括 Python 3.10、NumPy、Pillow、qrcode、OpenCV、GTK3/PyGObject、Cairo、GStreamer、Mali GLES2/EGL、C/C++ 编译工具、`xmutil`、XRT/zocl、XIR、VART 和 `xdputil`。比赛机的兼容 VART 库放在 `~/.local/share/xilinx-agent-bridge/vart2-static-init`，其中包括 `libvart-xrt-device-handle.so.2`、`libvart-dpu-controller.so.2`。已有匹配 VART 运行库的镜像可用系统库，核对 `/usr/bin/python3 -c 'import xir,vart'` 和下文 DPU 探测。

实测相机为 0bda:5858、固件修订 0001、SuperSpeed，输出 1080p60 YUYV。它需要项目中的 UVC 带宽补丁。已有匹配内核时，以 `test -e /sys/module/uvcvideo/parameters/bodyink_ss_bandwidth` 验证后进入下一节。重建实测内核时采用以下顺序：

1. 取 Ubuntu 对应内核源码 `5.15.0-1027.31`，从基准板复制 `/boot/config-5.15.0-1027-xilinx-zynqmp` 作为构建目录的 `.config`，在源码树应用 [UVC 补丁](../camera/kernel/0001-bodyink-usb-camera3-bandwidth.patch)。使用 GCC 11 和 ARM64 配置，执行 `make olddefconfig`、`make -j8 Image modules`、`make modules_install`，三个阶段都给出相同的 `ARCH=arm64`、`O=<构建目录>` 和 `KERNELRELEASE=5.15.0-1027-bodyink1`。交叉构建时再指定 `CROSS_COMPILE=aarch64-linux-gnu-` 及 `CC=aarch64-linux-gnu-gcc-11`。对 `modules_install` 设置 `INSTALL_MOD_PATH=<暂存目录>` 并将生成的匹配模块目录装到板端 `/lib/modules/`。
2. 在板上执行 `sudo depmod 5.15.0-1027-bodyink1` 和 `sudo mkinitramfs -o initrd.img 5.15.0-1027-bodyink1`。将本次编译的 `arch/arm64/boot/Image` 压缩为 `image.gz`，同目录放入新 `initrd.img`、来自本板原厂 FIT 的 revB 设备树 `kv260-revb.dtb` 和 [FIT 描述文件](../camera/kernel/kv260-revb.its)。执行 `mkimage -f kv260-revb.its bodyink-revb.itb`。
3. 保留原厂 SD 卡启动项。先用串口从测试 FIT 单次启动，核对 `uname -r`、UVC 参数及匹配模块加载，再使用该镜像工作。摄像服务会检查内核参数，缺少补丁时停止安装。

演示使用固定 1920×1080 YUYV 采集。安装模板 `camera/bodyink-camera.default` 设置 `--exposure 160`（16 ms）和 `--snapshot-fps 60`，允许每个新采集帧响应一次快照请求。升级安装会保留原有的 `/etc/default/bodyink-camera`。重启采集前应核对该文件的参数，服务启动后用 `pid=$(systemctl show -p MainPID --value bodyink-camera.service); tr '\0' ' ' < "/proc/$pid/cmdline"` 核对实际参数。姿态更新率另由板端测量。

在装有 GCC、Python 3 和 NumPy 的 Linux 主机上，可在一份临时工作副本的 `camera` 目录执行以下命令。1080p 用例调用产品 C 程序中的发送函数。720p、540p 用例通过合成回放检查 Python 接收端兼容性。

~~~bash
set -e
cc -std=c11 -O2 -Wall -Wextra -Werror selftest.c -o selftest-resolution
python3 selftest.py ./selftest-resolution
python3 test_resolution_transport.py
python3 test_mapped_transport.py
~~~

## 3. 模型、运行目录与本机库

以下 Bash 命令用于首次安装在干净的板端 `ubuntu` 账户，先进入上文准备的仓库副本。对已有演示机，另备工作副本并保留原固件、配置和作品。`camera/configure_capture.py` 从目标设备读取序列号并写入**该副本**的采集模板，应在复制运行目录之前执行。把 `/dev/video0` 换成实际相机的 index 0 节点。用 `id -nG` 核对当前板端账户具有 `video` 和 `render` 组权限。若首次配置缺少组，管理员执行 `sudo usermod -aG video,render ubuntu`，重新登录后继续。

~~~bash
set -e
repo=$(pwd -P)
sudo apt-get install python3-qrcode python3-pil
/usr/bin/python3 -c "from io import BytesIO; import qrcode; out=BytesIO(); qrcode.make('bodyink').save(out, format='PNG'); assert out.getvalue().startswith(b'\x89PNG')"
python3 camera/configure_capture.py /dev/video0
sudo sh camera/install_helpers.sh
for component in bodyink-live ink-suite ink-fight fruit-slice; do
    test ! -e "/home/ubuntu/$component"
    cp -a "$repo/$component" "/home/ubuntu/$component"
done
CC=gcc sh camera/build.sh
cd /home/ubuntu/bodyink-live
for lib in dtw:dtw start_geometry:geometry preprocess:preprocess hourglass_postprocess:hourglass; do
    cc -O3 -fPIC -shared "${lib%:*}.c" -o "libbodyink_${lib#*:}.so" -lm
done
c++ -O3 -fPIC -shared postprocess_fast.cpp -o libbodyink_postprocess.so -lm
cc -O3 -fPIC -shared pl_bridge/pl_mmio.c -o pl_bridge/libpl_mmio.so
cc -O3 -fPIC -shared pl_bridge/roi_copy.c -o pl_bridge/libroi_copy.so
cd /home/ubuntu/ink-suite
sh sword/compile.sh
python3 runner/native/build_native.py --output runner/native/librunner3d.so
python3 kart/native/build_native.py --output kart/native/libracer3d.so
cc -O3 -fPIC -shared pal/pause_bridge.c -o pal/libink-pal-control.so -ldl
cd "$repo"
~~~

按 [官方模型清单](../models/hourglass/model-official.yaml) 选 `zcu102 & zcu104 & kv260` 的 Hourglass MPII 模型，将解压出的 `hourglass-pe_mpii.xmodel` 放入新建的 `/home/ubuntu/bodyink-live/models/`，在该目录执行 `sha256sum -c "$repo/models/hourglass/sha256sums.txt"`。动作模板、校准文件及模型/ROI 身份描述均在 `bodyink-live/dataset/`，正式服务启用 A01、A02、A03、A04、A06。各游戏独立素材、教学视频及系统依赖的取得方式见 [外部资源与许可](../third_party.md)。

**照片水墨模型和运行库。** 上一步已将应用复制到板端运行目录。先在仓库副本的 `models/photo/qa-adain-v4/upstream/models/` 下放入获准使用的两个上游权重 `decoder.pth` 和 `vgg_normalised.pth`，目录不存在时先创建。下载地址与权重摘要见[照片模型说明](../models/photo/README.md)。以下命令在同一次板端 Bash 会话中执行，直接将还原的模型写入“创作 → 照片水墨”实际读取的位置。

~~~bash
set -e
cd "$repo"
photo=/home/ubuntu/ink-suite/environment
/usr/bin/python3 models/photo/qa-adain-v4/convert_adain_weights.py
/usr/bin/python3 models/photo/restore_model.py --output "$photo/models/figure-ink-v3.onnx"
printf '%s  %s\n' 7ca0cfd577415b034bc358f775ad9e2d317de344c07f4482945d2be85562d35b "$photo/models/figure-ink-v3.onnx" | sha256sum -c -

sudo apt-get install python3-pip
/usr/bin/python3 -m pip install --only-binary=:all: --target "$photo/runtime" 'numpy<2' 'onnxruntime==1.19.2'
PYTHONNOUSERSITE=1 PYTHONPATH="$photo/runtime" /usr/bin/python3 - "$photo/models/figure-ink-v3.onnx" <<'PY'
import sys
from pathlib import Path
import onnxruntime as ort
model = Path(sys.argv[1])
assert ort.__version__ == '1.19.2'
assert Path(ort.__file__).resolve().is_relative_to(model.parent.parent / 'runtime')
options = ort.SessionOptions()
options.intra_op_num_threads = 2
session = ort.InferenceSession(str(model), sess_options=options, providers=['CPUExecutionProvider'])
print('照片模型加载成功', session.get_providers(), session.get_inputs()[0].shape)
PY
~~~

此处按 KV260 的 ARM64 Ubuntu、CPython 3.10 安装。[ONNX Runtime 1.19.2 发布页](https://pypi.org/project/onnxruntime/1.19.2/#files)提供与该环境匹配的 Linux aarch64 wheel。板端无外网时，可预先备齐相同目标平台的 wheels，在安装命令中加入 `--no-index --find-links /path/to/wheels`。

管理员从已核对的代码包安装启动脚本。两份脚本保存在 root 所有的 `/usr/local/libexec/bodyink/`，安装程序逐级检查父目录的所有者和写权限。修改相机配置后需重新安装恢复脚本。

在 Linux 主机或 WSL 中安装 Python 3、NumPy 和 Pillow 后，可以先运行以下两项服务回归。第一项检查本地姿态 API 的地址、来源与清屏权限；第二项向实际服务进程发送 SIGTERM，在模拟 DMA 仍忙时检查它是否保留缓冲区，并在对应标签完成后退出。两项均使用测试数据。

~~~bash
python3 board/test_local_api.py -v
python3 board/test_dma_shutdown.py -v
~~~

## 4. 装载 PL 并先比对一帧

下列操作在板端仓库根目录执行，目标卡应处于 `k26-starter-kits` 独占活动槽或已加载匹配的 `bodyink-usb-pl`。先用 `xmutil listapps` 查看活动槽，确认没有其他程序占用 XRT。随附启动脚本先验证活动槽和 VCU 绑定状态，再装载指定固件。更换同名固件前，先停止相关服务，确认 DMA 已排空并备份原固件、校验清单与配置。复制新文件只会更新磁盘上的固件。若当前已装载同名旧版本，请在维护窗口将板卡启动到原厂槽，再按以下步骤装载本次构建；仅凭相同的 DPU 指纹不能区分两个前处理版本。

在目标内核上查看 `grep CONFIG_UIO_PDRV_GENIRQ /boot/config-$(uname -r)`。若为 `=m`，在模块首次加载前执行以下命令。若为 `=y`，将 `uio_pdrv_genirq.of_id=generic-uio` 加入板卡实际启动项的内核命令行，重启后核对 `/proc/cmdline`。若模块已加载但未匹配，在维护时段重启以应用参数。未启用该驱动时需先启用并部署匹配内核。采用串口测试启动时保留原厂启动项。

~~~bash
# CONFIG_UIO_PDRV_GENIRQ=m
printf '%s\n' 'options uio_pdrv_genirq of_id=generic-uio' | sudo tee /etc/modprobe.d/bodyink-uio.conf >/dev/null
sudo modprobe uio_pdrv_genirq of_id=generic-uio
~~~

~~~bash
cd "$repo/fpga/deployment"
sha256sum -c firmware.sha256
dest=/lib/firmware/xilinx/bodyink-usb-pl
sudo install -d -m 0755 "$dest"
sudo install -m 0644 bodyink-usb-pl.bin bodyink-usb-pl.dtbo bodyink-usb-pl.xclbin shell.json "$dest/"
sudo install -m 0644 71-bodyink-pl.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
install -d -m 0755 /home/ubuntu/bodyink-live/pl-deploy-0914
touch /home/ubuntu/bodyink-live/pl-deploy-0914/owner.lock
chmod 0660 /home/ubuntu/bodyink-live/pl-deploy-0914/owner.lock
printf 'firmware: %s/bodyink-usb-pl.xclbin\n' "$dest" | sudo tee /etc/vart.conf >/dev/null
sudo /usr/local/libexec/bodyink/prepare_dpu.sh
sudo udevadm trigger --subsystem-match=uio --action=add
sudo udevadm settle
# 以登录的 ubuntu 用户核对唯一设备名、地址、窗口大小和读写权限
cd /home/ubuntu/bodyink-live/pl_bridge
/usr/bin/python3 -c "import os; from pl_preprocess import find_uio; node=find_uio(); fd=os.open(node, os.O_RDWR); os.close(fd); print(node)"
cd "$repo"
python3 board/check_pl.py --runtime /home/ubuntu/bodyink-live
~~~

预期探测输出含 `BODYINK_DPU_READY: 0x101000016010407`，在 `/sys/class/uio/uio*/` 可找到名为 `bodyink-preprocess` 的窗口，基址 `0x81000000`、长度 `0x1000`。`check_pl.py` 使用同一窗口实际提交合成 YUYV 帧，输出 `equal_bytes` 为 196608，对应 256×256×3 的 INT8 张量。需在启动视觉服务前执行，以便该进程独占预处理器。若取消后仍有总线事务，视觉服务将停止提交新帧，同时保留设备锁和输入输出缓冲区，直至读到本次任务的完成标记。此时 `systemctl stop` 会等待。若硬件持续无响应，请隔离设备后整板断电重启；等待期间不要强杀进程或卸载固件。历史实板的 36 组结果另见 [上板记录](../board/README.md)。

默认平台冒险与仙剑的独立引擎需按[原生游戏部署说明](../integrations/native-games.md)完成构建、接入与校验。

## 5. 连通摄像、推理及交互

~~~bash
cd "$repo"
sudo install -m 0644 systemd/*.service systemd/*.timer /etc/systemd/system/
for dir in systemd/*.service.d; do
    dest=/etc/systemd/system/${dir##*/}
    sudo install -d -m 0755 "$dest"
    sudo install -m 0644 "$dir"/*.conf "$dest/"
done
sudo install -m 0755 bodyink-live/session_gate_0917.py /usr/local/sbin/bodyink-session-gate.py
sudo install -m 0644 camera/91-bodyink-camera-recover.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo systemctl daemon-reload
sudo sh camera/install.sh --install-only
sudo systemctl enable --now bodyink-session-gate.timer
~~~

在 KV260 本机以 `ubuntu` 登录图形桌面。会话持续稳定 60 秒后，定时器启动视觉服务并带起摄像服务。用 `systemctl is-active bodyink-camera.service bodyink-dpu.service bodyink-live.service` 查看三个单元，在本地图形终端执行以下命令：

~~~bash
/usr/local/bin/bodyink-camera-snapshot
curl -fsS http://127.0.0.1:8088/api/state
python3 /home/ubuntu/ink-suite/launch.py --fullscreen
~~~

本地视觉接口监听 `127.0.0.1:8088`。扫码时手机与 KV260 接入同一 Wi-Fi，访问访客页面的 TCP 8091 端口；可在板端用 `ss -ltn` 区分两者。快照命令返回实际宽、高和时间戳。观众入镜后，状态接口随动作更新关节点；在“创作”中移动手臂可以看到连续笔迹，在“健康”中可打开八段锦跟练。DeepSeek 在线功能由板端 `~/.config/ink-suite/deepseek.json` 读取用户自行配置的 `api_key`，将文件权限设为 `0600`。未配置时仍可通过本地界面操作。相机服务未启动先检查所选设备、带宽参数和本地登录门控，DPU 未就绪则查看 `journalctl -u bodyink-dpu.service -b`。
