# 两个外部原生游戏的安装与验收

以下步骤供首次配置的 KV260（Ubuntu 22.04 ARM64、X11 桌面）使用。先在仓库根目录设置 `repo=$(pwd -P)`，按板端复现说明部署宿主与 PAL 控制库。游戏引擎各自使用独立目录。赛车、跑酷等自研项目由仓库主流程构建。

## 平台冒险

菜单项“超级玛丽”的默认引擎是 TheXTech，载入的是 **Adventures of Demo** 的 *The First Adventure*。工程另有自研横版引擎，可用 `INK_PLATFORMER_ENGINE=legacy python3 /home/ubuntu/ink-suite/launch.py --fullscreen` 单独验证；这项选择与默认原生游戏分开记录。

从 [TheXTech v1.7.4 官方发布页](https://github.com/TheXTech/TheXTech/releases/tag/v1.7.4) 获取完整源码包 `thextech-full-src-v1.7.4.tar.gz` 和内容包 `adventures-of-demo-thextech-v1.7.4-linux-generic-u20.04-aarch64.tar.gz`，放入一个新建工作目录并进入该目录。保留内容包的署名和许可证。以下为目标机原生重建步骤，历史演示使用相同源码的 ARM64 交叉构建产物。

~~~bash
set -e
work=$(pwd -P)
sha256sum -c <<'SUM'
5f51bfbf62285453a5d6dbbcbb44fe518f58a8e8fdb28c5e6f7326154dea7063  thextech-full-src-v1.7.4.tar.gz
304ae8eafa6485bc83b9298b23a6c81085d2a473e40c868c957d3e9df932ce1b  adventures-of-demo-thextech-v1.7.4-linux-generic-u20.04-aarch64.tar.gz
SUM
patchfile="$repo/integrations/thextech/native-bridge.patch"
printf '%s  %s\n' d02204e23ae3435278928a402cb7a6061ccc7c820bafc5871542117682d88643 "$patchfile" | sha256sum -c -
tar -xzf thextech-full-src-v1.7.4.tar.gz
tar -xzf adventures-of-demo-thextech-v1.7.4-linux-generic-u20.04-aarch64.tar.gz
sudo apt-get install build-essential git pkg-config libsdl2-dev libegl-dev libgles-dev libpulse-dev
cd "$work/thextech-full-src"
git apply --check "$patchfile"
git apply "$patchfile"
# 构建记录使用 CMake 3.28.6，先将相应版本放入 PATH
cmake --version
cmake -S . -B "$work/build" -DCMAKE_BUILD_TYPE=Release \
  -DUSE_SYSTEM_LIBS=OFF -DUSE_SYSTEM_SDL2=ON \
  -DCMAKE_C_FLAGS=-mcpu=cortex-a53 -DCMAKE_CXX_FLAGS=-mcpu=cortex-a53 \
  -DTHEXTECH_BUILD_GL_DESKTOP_MODERN=OFF -DTHEXTECH_BUILD_GL_DESKTOP_LEGACY=OFF \
  -DTHEXTECH_BUILD_GL_ES_MODERN=ON -DTHEXTECH_BUILD_GL_ES_LEGACY=OFF \
  -DTHEXTECH_USE_ANGLE_TRANSLATOR=OFF -DTHEXTECH_ENABLE_DISCORD_RPC=OFF \
  -DWITH_UNIT_TESTS=OFF -DTHEXTECH_NO_BUILD_DATE=ON
cmake --build "$work/build" --parallel 2 --target thextech
engine="$work/build/output/bin/thextech"
content="$work/thextech-adventures-of-demo"
test -x "$engine" && test -f "$content/worlds/the first adventure/world.wldx"
test -f "$content/License.txt"
export LD_LIBRARY_PATH="$work/build/output/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
ldd "$engine" > "$work/engine-dependencies.txt"
if grep -F 'not found' "$work/engine-dependencies.txt"; then exit 1; fi
"$engine" --version
"$engine" --capabilities
~~~

保留该构建的 `output/bin`、`output/lib` 和内容目录。宿主会在每次启动时校验 ELF 摘要。首次调试时，在实际部署的宿主副本中登记刚通过检查的二进制；原仓库配置保留比赛机的已知 ELF 摘要。配置中的历史 `source_patch_sha256` 是构建时变更文件 **tar 包**的摘要，上文另行校验随仓库发布的 **文本补丁**。

~~~bash
cd /home/ubuntu/ink-suite
/usr/bin/python3 - "$engine" "$content" <<'PY'
import hashlib, json, sys
from pathlib import Path
from thextech.runtime import read_config
engine, content = (Path(p).resolve(strict=True) for p in sys.argv[1:])
cfg = Path('thextech/runtime.json')
data = json.loads(cfg.read_text())
data.update(engine=str(engine), engine_sha256=hashlib.sha256(engine.read_bytes()).hexdigest(), content=str(content))
candidate = cfg.with_suffix('.local-check.json')
candidate.write_text(json.dumps(data, indent=2) + '\n')
read_config(candidate)
candidate.replace(cfg)
PY
~~~

`read_config` 同时检查当前 Mali X11 库路径、关卡与许可证。如果镜像中使用另一条 Mali 路径，请先在图形桌面核实 EGL 和 GLES 使用同一个 X11 驱动，再在本机配置中设置实际的 `graphics_library`。从首页进入游戏，核对关卡加载、体感或手柄输入、暂停恢复与返回。开发机的补丁复核不代替这项验收。

## 仙剑奇侠传

首先按 [PAL 数据说明](pal/docs/resources.md) 自行合法准备受支持版本的游戏数据。PAL 宿主默认寻找比赛机上的已有安装；首次安装可用 `INK_PAL_ROOT` 指向自己的目录。以下命令在板端执行，游戏引擎和 SDL 1.2 使用相同架构。

~~~bash
set -e
cd "$repo/integrations/pal"
sudo apt-get install build-essential libsdl1.2-dev
bash scripts/build-native.sh
export INK_PAL_ROOT=/home/ubuntu/games/pal-kv260/local
install -d "$INK_PAL_ROOT/engine/bin" "$INK_PAL_ROOT/runtime/lib" "$INK_PAL_ROOT/data"
install -m 0755 build/native-candidate/sdlpal "$INK_PAL_ROOT/engine/bin/sdlpal"
sdl=$(gcc -print-file-name=libSDL-1.2.so.0)
test -f "$sdl"
ln -s "$(readlink -f "$sdl")" "$INK_PAL_ROOT/runtime/lib/libSDL-1.2.so.0"
test -f /home/ubuntu/ink-suite/pal/libink-pal-control.so
# 按数据说明将游戏数据放到 "$INK_PAL_ROOT/data" 后执行
"$INK_PAL_ROOT/engine/bin/sdlpal" --data-dir "$INK_PAL_ROOT/data" --check-data
python3 /home/ubuntu/ink-suite/launch.py --fullscreen
~~~

从同一终端启动宿主以继承 `INK_PAL_ROOT`，在“仙剑奇侠传”中核对画面、按键、暂停和存档。
