# InkSuite 本地音频运行层

本目录提供墨迹、切水果、体感格斗、御剑飞行的循环背景音乐和并发音效，以及太鼓的击打音效。资源由 `manifest.json` 描述，使用预生成的 48 kHz、双声道、16 bit PCM WAV；空间感、混响和尾音在资源生成阶段完成。启动程序不会自动改动系统默认声卡。

## API 与场景生命周期

```python
from audio import AudioDirector

audio = AudioDirector('fruit', enabled=True, sink='autoaudiosink')
audio.set_levels(music=0.65, effects=0.8, master=0.8)
audio.start()                         # 后台预载并异步启动
audio.play('fruit_slice', key='fruit:round3:fruit17')  # ID以manifest为准
audio.pause()                         # 菜单/暂停：清旧音效，冻结BGM位置
audio.resume()                        # 从保留的BGM位置继续
audio.mute(True)                      # 只静音，BGM位置仍推进
audio.retry()                         # 设备恢复后显式重试
state = audio.status()                # 已缓存的状态，不访问设备
audio.stop()                          # 释放音频资源、重置BGM位置，可再次start
audio.close()                         # 切换场景时退出worker；不可再次start
```

`AudioDirector` 支持 `ink`、`fruit`、`fight`、`sword`、`taiko`。构造函数、`start/resume/pause/stop/close/play/set_levels/mute/retry/status` 都不在调用线程解码 WAV、等待 GStreamer 或等待工作线程结束，可从 GTK 回调调用。`wait_closed(timeout=2)` 仅供测试或非 UI 的进程退出流程，不能放在 GTK 回调中。

`play(id, key=None)` 返回是否接受入队，不代表声音已由扬声器播出。请从已确认的动作/命中事件调用，并用稳定的事件 ID 作为 `key`，避免对每一帧的同一个事件重复请求。未知 ID 会被后台拒绝并计入 `rejected`。暂停、停止、重试会清除队列和 key 缓存；事件适配器还需要保留自己的游戏事件去重状态，避免恢复时重播历史命中。

音量范围为有限数值 `0..1`。默认 `music=0.65, effects=0.8, master=0.8`；`set_levels()` 和 `mute()` 立即更新状态，最迟在下一混音块生效。`status()` 返回 `state/error/ready/backend`、`levels/muted`、`position_seconds/voices`、`queued/queue_limit/max_voices`、计数器和 `worker_alive`。`state` 可为 `disabled/stopped/loading/playing/paused/unavailable/closed`；`ready` 只表示管线接受 PCM，不能替代扬声器听音验收。`position_seconds` 是当前循环内的混音位置，用于状态显示，不能作为音游判定时钟。

太鼓模式不加载或播放 `bgm_taiko`。原有 `taiko.audio_clock.GstClock` 仍然是谱面、判定与乐曲的唯一时钟；这里仅混合击打音效。太鼓乐曲的静音/音量必须同步给原有乐曲播放器，不能只调这个音效 director。

御剑模式加载 `bgm_sword`，使用 `sword_shot/hit/kill/hurt/collect/special/boss/win/lose` 九种音效。引擎只对已接受的射击、命中和战斗结果计数，一次齐射只算一个射击事件；适配器在每次观察时把同类计数增长合并为一次请求。密集弹幕仍共用同一个音频 worker 和最多六路声音，不按每颗子弹创建线程或播放器。射击/命中音效的清单冷却与优先级用于给大招、受伤、首领和胜负提示保留播放机会。

## 有界运行和失败降级

- 每个活动场景一个 Python 工作线程、一条 GStreamer `appsrc → audioconvert → audioresample → sink` 管线；每个事件不创建线程或播放器进程。
- 工作线程验证 SHA-256，并预载当前模式的一条背景音乐和所有音效。限制单文件 48 MiB、总 PCM 96 MiB，拒绝越界路径、非 PCM16/48 kHz/双声道文件和不匹配的清单。
- 每次混音 480 帧（10 ms）；音效默认最多 6 路，允许配置 1–8 路；队列默认 32 项，上限可配置为 128。队列满时丢弃新请求，过期 250 ms 的事件丢弃。key 缓存最多 256 项。
- 音效饱和时优先替换最低优先级中最早的 voice；新音效优先级更低则丢弃。`cooldown_ms` 为每音效的最短启动间隔。非循环音效自然结束后释放 voice。
- NumPy 存在时使用数组混音，否则使用标准库。所有音轨共用混音输出，合成后饱和限幅，避免整数溢出；素材与默认音量预留余量。
- `appsrc` 最多排队三个 10 ms PCM 块，单一生产者每次检查 `current-level-bytes` 再投递，`block=False`。输出持续不消耗 PCM 超过一秒时降级为 `unavailable`。
- 暂停将输出切至 READY 以清空待播数据，并按 GStreamer 报告的位置回退尚未播放的 BGM 帧。恢复会重建时间段，BGM不重头开始。设备自身的缓冲/无线传输延迟仍需实测，软件位置不等于声波到达时间。
- 设备不可用、校验失败或播放错误仅影响音频，状态包含原因；清除旧音效后等待显式 `retry()`。`stop()` 释放资源并归零，`close()` 通知退出而不阻塞 GTK。

GStreamer 兼容基线不依赖 1.20 新增的 appsrc 属性。`current-level-bytes` 和 `max-bytes` 控制队列，详见 [GStreamer appsrc 官方文档](https://gstreamer.freedesktop.org/documentation/app/appsrc.html)。

## 本地无声验证

在 InkSuite 目录执行：

```sh
python3 -m unittest test_audio_runtime -v
python3 -m audio.device_probe --output diagnostics/audio-device-probe.json
```

运行层测试默认使用 `sink='memory'`，只在内存保留最近四个 PCM 块，不访问扬声器。安装了 PyGObject/GStreamer 的 Linux 环境还会运行 `fakesink` 集成测试，同样不发声。Windows 没有 PyGObject 时，该项会明确标记为跳过，其余内存混音、队列、设备异常注入与生命周期测试仍能运行。不能将 memory/fakesink 通过写成“KV260扬声器通过”。

2026-09-29 御剑补充验证：WSL Ubuntu 24.04 / Python 3.12.3 / GStreamer 1.24.2 的 25 项运行层测试全部通过，其中包含太鼓原有 `GstClock` 与击打混音管线同时使用 fakesink。最终 39 个 WAV 对应的五模式 memory/fakesink 加载、并发、暂停/恢复、线程退出均通过，报告保存在 `diagnostics/audio-sword-20260929/runtime-tests-linux.txt` 和 `runtime-real-assets-*.json`；前一轮四模式报告仍保留在 `diagnostics/audio-20260929`。

御剑压力脚本以每批 64 次、约每秒 60 批发送合成音效请求，2 秒共 7,680 次；memory 和 fakesink 的峰值均为 32 项请求、6 路声音、1 个输出实例、1 个 Python 输出线程。真实 appsrc 队列高水位为 5,760 字节（30 ms），超量请求按策略丢弃，暂停清空且关闭正常。该压力输入刻意高于正常游戏的合并事件流；本地微基准和入队耗时仅用于检查实现开销，不能代表 KV260 的 ARM 负载、全场景帧率、真实声卡、扬声器听感或无线延迟。

板端运行需要 Python 3.10+、PyGObject 的 `Gst 1.0` typelib，以及 GStreamer 的 appsrc、audioconvert、audioresample 和所选输出插件。NumPy 是可选加速。这里只做本地准备，不自动安装依赖、不连接板子、不打开摄像头、不自动外放音频。

## 设备恢复与 2.4G 接收器后续验证

`device_probe.py` 只读运行 `pactl info/list`、`aplay -l/-L`、`lsusb`、`gst-inspect --version` 和有超时的 `gst-device-monitor Audio/Sink`，保存 stdout/stderr/退出码；不会播放音频、改默认 sink、修改音量或写声卡配置。`gst-device-monitor` 通常持续监视设备，因此超时报告中已有的初始枚举仍然有效。

1. KV260 到手后，在拔下接收器和插入 2.4G 接收器两种状态分别采集 probe JSON，比较 USB VID/PID、ALSA 设备名和 PulseAudio sink 名。不要固定 `hw:0` 等会变动的卡号。
2. 核对接收器的实际模式、设备标识和 Linux 枚举结果。品牌或“2.4G”字样本身不能证明它支持 Linux 音频、具备 USB 声卡功能，或具有某个固定延迟；游戏手柄接收器也可能只枚举 HID。
3. 确定真实音频 sink 后，由操作者在系统界面选择需要的输出；重新采集设备信息，点击应用“重试音频”。预先调低物理音量后，再明确执行听音测试。
4. 先测有线基线，再测接收器，分别记录混音块大小、系统输出、设备/耳机型号、距离及干扰条件。通过外部录制同时捕捉可见命中标记与实际声音，或用双通道录音/示波器记录触发参考与声学输出；至少重复 30 次，报告中位数、P95、极差和掉音次数。
5. 太鼓需独立进行音频输出延迟和动作输入延迟校准；使用原有音频主时钟，不通过加快/放慢谱面来掩盖无线链路延迟。验收切换菜单、暂停/恢复、连击、设备拔插恢复，确认无积压音效、无双重BGM、资源和线程能释放。

以上步骤是待板端执行的验收流程，不代表已确认任何接收器型号兼容性或延迟结果。
