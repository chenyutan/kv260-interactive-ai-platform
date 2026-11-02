# 战绩长图字体

两份完整字体均保留原二进制，未做子集或格式转换。它们内部的 `name` 表声明 SIL Open Font License 1.1；本目录的 `.ofl.txt` 保存原版权说明及 Noto CJK 官方许可证文本，`manifest.json` 固定大小、SHA256 与许可证来源。

- `notoserifsc-vf.ttf`：25,129,160 字节，30,928 个 Unicode cmap 条目。用于标题、昵称、大号成绩；渲染时采用原有可变字重轴的 700 字重。
- `notosanssc-regular.otf`：8,482,020 字节，30,892 个 Unicode cmap 条目。用于说明、时间、统计与规则。

运行时优先读取 Linux 已安装的 Noto / Source Han CJK 字体，随后读取这里打包的完整 Noto 字体，Windows 字体路径只用于开发机备用。没有可显示中文的字体时明确失败，不用缺字方框替代。测试对“墨境御剑山河鼓动彩水果武对决访客战绩麒麟龘”等字符实际生成字形遮罩，并确认不等于缺字框。

许可原文来源：[Noto CJK Serif](https://github.com/notofonts/noto-cjk/blob/main/Serif/LICENSE)、[Noto CJK Sans](https://github.com/notofonts/noto-cjk/blob/main/Sans/LICENSE)。随字体分发本目录的许可证和 manifest；未复制或再分发微软字体。

## 图像构建接口

`visitor.report_image` 接受 `VisitorStore.view(token)` 字典，或 `VisitorStore.report(token, 'json')` 的 JSON 字节。

```python
from visitor.report_image import report_manifest, render_report_page, render_report_pages

view = store.view(token)
manifest = report_manifest(view)
png = render_report_page(view, page=1,
    artwork_reader=lambda ident: store.artwork(token, ident)[1])

# ZIP 可逐页写入，避免同时保留所有页的像素。
for filename, png in render_report_pages(view,
        artwork_reader=lambda ident: store.artwork(token, ident)[1]):
    archive.writestr(filename, png)
```

页码从 1 开始。默认每页最多 20 回合；带非常长计数器说明的回合会自动提前分页。所有回合按原顺序保留，每页 PNG 元数据记录自己的回合 ID；尺寸固定 1080 像素宽且不超过 7200 像素高。第一页包含概览与可选作品缩略图；后续页保留姓名、日期、汇总、页码及全部局次。

`artwork_reader` 只能传入已经验证所属游客的 `VisitorStore.artwork` 读取器；图像模块只接受 32 位十六进制作品 ID 和有尺寸/字节上限的 PNG 字节，不接收用户文件路径。图片只在第一页读取。

构建为同步纯 Pillow 计算，宿主须放在低优先级后台进程或下载构建任务里，不能在游戏、音频或 GTK 绘制线程中调用。`report_fingerprint(view)` 可作为缓存键；昵称、日期、作品及任意回合成绩改变都会使缓存失效。每个构建器同时仅生成一页，下载 ZIP 时逐页写入。

在 InkSuite 目录验证：`python -m unittest visitor.test_report_image`。测试使用临时 SQLite、合成战绩及合成画作，不访问真实摄像头、板卡或用户数据。板端实际构建时间与同时运行游戏的 CPU 负载仍需部署后测量。
