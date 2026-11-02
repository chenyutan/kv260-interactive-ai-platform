# 仙剑奇侠传 · InkSuite 原生入口

这是既有 ARM64 SDL 1.2 移植程序的窗口、输入和生命周期适配层。商业游戏资源、原二进制和原独立桌面入口保持在 `/home/ubuntu/games/pal-kv260/releases/20261015-dos-b9626ce0d0c6`，不拷贝进公开源码包。默认继续使用其 `saves` 目录，重开只重启进程，不删除存档。

运行：`python3 pal/main.py --embed-socket XID --base-url http://127.0.0.1:8088`。`INK_CONTROL_MODE=body` 或 `gamepad`。独立入口不带 `--embed-socket`。`--no-audio --offline-preview --save-dir PRIVATE` 用于不取景、不播放音频的隔离验证。

体感：右手相对肩膀向四方指引行走；左手先放下再举过肩确认；左手向身体外侧伸出返回。支持原主机转两圈二级菜单。此映射用于兼容，长篇 RPG 推荐手柄。

手柄：左摇杆或方向键行走，A 确认，B 返回/游戏系统菜单，X 调查，LB/RB 翻页。Start/Back 和右摇杆/R3 留给主机二级菜单与鼠标功能。

GTK Plug 内的专用 DrawingArea 原生 XID 通过 `SDL_WINDOWID` 交给 PAL。9 个固定游戏键通过该子进程专用的共享控制块交给私有 SDL 适配库，不向 X11 注入事件、不切换全局焦点、不直接打开摄像头。SDL 进程运行在独立进程组中，退出先请求 SDL_QUIT，超时才回收自己的进程组。

显示按 DOS 画面的 4:3 比例居中适配，宽屏两侧留边。等待 GTK 尺寸分配完成后才初始化 SDL；窗口或屏幕尺寸变化时，通过共享控制块的第 3 号字传递最新宽高，由游戏线程调用原有 `VIDEO_Resize` 路径更新显示，保持调色板，不重启游戏。暂停中收到的尺寸请求在继续后处理；请求范围与原生程序一致，为 320–3840 × 200–2160。

`libink-pal-control.so` 只为 PAL 子进程设置私有 LD_PRELOAD，不替换系统 SDL。主机暂停时在 SDL 主线程暂停声音和逻辑，并从 SDL 时钟扣除暂停时间；未使用 SIGSTOP。共享控制文件仅 32 字节，位于用户运行时目录或私有临时目录，退出移除。音频静音是整体静音；PAL 现有音频内部没有与 InkSuite 一致的独立音乐/音效混音器。

源码构建：`python -B audit/pal_build_bridge.py`，使用已安装 WSL ARM64 编译器和 E 盘旧 SDK sysroot，库仅依赖 GLIBC 2.17 接口。相应 ARM、XEmbed、输入、暂停、退出验证记录在本次 audit 目录；自动验证不替代真人画面与声音验收。
