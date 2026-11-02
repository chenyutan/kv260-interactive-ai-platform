# 原生统一宿主

`main.py` 只有一个顶层 GTK 窗口，三列两行主菜单、体验内操作菜单与当前体验放在同一 Gtk.Stack。体验通过 X11 Gtk.Socket/Plug 嵌入；同一时刻仅保留一个体验子进程。三个原应用源码保持原样，不调用旧游戏 launcher，不因切换体验重新加载摄像头/DPU或清空水墨画布。

## 文件

- `main.py`：模式事务、共享状态读取、菜单选择、子进程生命周期及错误重试。
- `startup_readiness.py`：独立确认采集和推理已连续产出新帧；不把 HTTP 成功、服务 active 或有人入镜当作就绪。
- `menu_view.py`：1024×600 下的原生三列两行菜单绘制；指针悬停区与 `DwellSelector` 一致。
- `embedded_runner.py`：将已验收的水墨显示/格斗/水果程序嵌入；转发键盘、冻结菜单期间的游戏时钟，关闭 stdin 自动结束子进程。
- `taiko/main.py`：原生音游；分别读取同一姿态状态。太鼓把谱面和判定绑定音频播放位置，音频失败时停止计分并提示。
- `sword/main.py`：御剑山河入口，直接使用同一 XEmbed/控制协议；只读姿态状态，不使用预览和照片接口。旧角色与照片模块保留但不在当前菜单启动。
- `launch.py`：单实例，与两个旧游戏启动器共用游戏锁；启用共享 `bodyink-live`。按当前用户及 `/proc/<pid>/cmdline` 的精确脚本位置识别原水墨独立窗口，记录其图形会话环境后结束显示，退出套件时恢复单个原水墨显示。
- `ink-suite.desktop`：候选桌面入口，部署目录为 `/home/ubuntu/ink-suite`。
- `test_gtk_suite.py`：板端 Xvfb，真正五个原生界面、离线御剑输入、假状态服务；不访问摄像头。假服务使用真实 SuiteControl 校验模式 ID。
- `test_launcher.py`：临时 `/proc` 树匹配测试，不给实际进程发送信号。

## 运行与协议

正式入口 `/usr/bin/python3 /home/ubuntu/ink-suite/launch.py`。宿主需要 X11；当前 KV260 桌面及 Xvfb 已验证 Gtk.Socket/Plug。不能用独立游戏 launcher 启动嵌入子进程，否则旧 launcher 会切换推理服务。

界面可先展示六张卡片和准备提示，但模式命令确认及帧流确认必须同时通过，才能用右手或鼠标选择。帧流确认要求同一后端的采集序号、帧时间戳及推理计数连续推进至少三次，跨度至少 80 毫秒。即使没有人在画面里，也能完成准备；随后显示等待肩膀与右手入镜。准备过程中退出、全屏和菜单按钮保持可用。

HTTP 断线、采集缺失或帧流停滞会清除旧墨迹和未完成的停留进度，并暂停体验。恢复后自动确认新帧，无须重新打开程序。`--entry-delay` 从首次实际就绪后开始；倒计时中失联则在恢复后重新给予完整入场时间。时间戳使用同一板卡的 monotonic 时基，正式宿主和状态 API 应运行在同一台 KV260 上。

当前模式为 `ink / sword / fight / fruit / taiko`。启动及切换顺序：后端设 `suite.active=true, menu=true` → 旧子进程退出 → 构造新 Plug → 收到 `ready` → 后端确认所选模式且 `menu=false` → 恢复子进程。关闭/后端断线/菜单时暂停；候选转圈时宿主仅暂停格斗和水果。御剑和太鼓在自身较高频的状态读取中检查 `control_active` 并冻结局内推进，候选解除直接继续，避免一次普通圆弧误候选触发三秒恢复倒数。真正打开菜单或后端断线/重启仍由宿主暂停御剑，恢复后保留安全倒数。动作绘画交由后端暂缓提交。后端 PID 变化或 suite.active 丢失时，先重新确认模式，后恢复输入。过期模式请求使用 generation 校验排除，HTTP 模式写入串行执行。

子进程 stdin 接受逐行 JSON：`{"op":"pause"}`、`{"op":"resume"}`、`{"op":"shutdown"}`；EOF 视为退出。stdout 返回逐行 JSON 事件 `ready`、`menu_requested`、`fullscreen_requested`；其余输出写入 `logs/<mode>.log`。御剑和太鼓还报告带 `game_state / game_clock` 的 `paused / resumed`；原两款游戏在离线预览模式报告时钟供冻结测试。

模式选择支持手腕停留两秒、鼠标点击；Esc/Menu/F1 在体验内打开二级菜单，在二级菜单内返回或继续。关闭当前体验的原“退出”按钮回到菜单；宿主顶栏“退出”才结束整个套件。御剑先显示开始页，确认开始后倒计时；原两款游戏沿用原有倒计时。菜单取消恢复原局，重新选择模式启动新局；未完成局不记入成绩。

## 验证范围

本轮测试目录为 `/home/ubuntu/ink-suite-release-staging-sword-20260926`，使用 `xvfb-run -a` 独立虚拟显示，未替换真实窗口、服务或摄像头。离线原游戏使用原有 benchmark 分支且关闭成绩导出；御剑 `--offline-preview` 不开启状态轮询。通过范围以本轮 `diagnostics/sword-integration-*20260926*` 记录为准。

检查项目：六模式嵌入、菜单暂停/继续、游戏时钟不在菜单中前进、六次 stdin EOF 清理、错误启动后的重试、连接中断再连、模拟后端 PID 重启与模式重同步、窗口结束后无孤儿子进程。结果与截图在 `test-evidence/`。正式桌面的焦点/全屏行为和真人两圈/悬停仍需用户实际验收；Xvfb 测试不替代这部分。

注意：原显示不是 `bodyink-display.service`，它由 GNOME autostart 脚本启动。启动器只匹配 `python* /home/ubuntu/bodyink-live/native_display.py`，不会匹配嵌入 runner 的 `--source` 参数、其他用户进程或摄像头服务。

## 历史六体验增量验证

2026-09-27 新候选路径 `/home/ubuntu/ink-suite-release-staging-koi-taiko-20260927`；新游戏与六入口使用独立 Xvfb、合成姿态及音频 fakesink 检查。正式运行默认真实音频输出，离线预览不得访问生产摄像头。源代码新增不等同于真人验收，结果以对应 release 记录为准。


## 2026-09-27 显示缓冲修复

所有嵌入页面强制使用 GDK `similar` 离屏缓冲后提交，保留显式 `image` 缓冲回退。禁止继承 `recording`：游戏在内存中画完一帧仍不足以防止 GTK 背景清除直接落到可见窗口。RGB565/NEON 转换和素材缓存保持有效。

## 2026-09-28 体验内手势菜单

`action_menu.py` 复用主菜单的 RGB24 静态背景缓存、局部重绘与右手墨迹；隐藏后不保留动画定时器。`action_catalog.py` 固定六体验菜单、帮助及二次确认。`DwellSelector.set_choices` 将固定卡片槽映射到当前页操作，空槽不触发；换页、失联和追踪换人后重置停留，重新移动才可确认。

游戏内两圈的 `open_serial` 先打开本体验菜单。根页“返回主菜单”才进入原主菜单。所有二级菜单、帮助和确认页保持后端 `suite.menu=true` 与子进程暂停。HTTP 请求在锁内检查 generation 与 navigation，拒绝过期写入；继续与开菜单等待服务端确认后才重新启用交互。

固定动作协议：`{"op":"action","command_id":"id","action":"restart"}`。`action_dispatch.py` 仅在 host 暂停时接受白名单动作，拒绝额外 payload，并缓存最近 128 个完成回执以防重复执行。子进程回传 `action_result`，包含同一 command_id、action、ok 和 reason。host 等待回执后再确认后端 menu=false 并恢复；失败保留操作页，超时不重复执行。所有耗时网络操作走工作线程。

水墨保存成功后恢复显示下载二维码，再次打开手势菜单会关闭二维码并使旧异步响应失效。太鼓难度切换重开谱面，静音或重试声音则保留曲目位置。测试见 `test_action_host.py`、`test_action_dispatch.py`、`test_action_menu.py` 与 `test_gtk_action_suite.py`。
