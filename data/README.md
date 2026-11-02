# 黄金数据与离线复核

| 文件 | 内容 |
| --- | --- |
| [roi-reference.json](roi-reference.json) | 从旧固件冻结包读取 36 组输入和 DPU 输出的 SHA-256，按 `分辨率高度_帧号_ROI序号` 编号。输入为 1×256×256×3 的 INT8 张量，输出为 1×64×64×16 的 INT8 热图。 |
| [roi-heatmaps.npz](roi-heatmaps.npz) | 从旧固件冻结包逐数组无损提取的全部 36 张 DPU 热图。以 [实板记录](../board/roi-equivalence.json) 中的 `height`、`frame`、`roi` 匹配。原始录制画面及含人物像素的输入张量保留在本地，公开索引提供输入摘要。 |
| [synthetic-roi.npz](synthetic-roi.npz) · [几何参数和摘要](synthetic-roi.json) | 原 RTL 仿真第 4 号合成用例。32×24 的 YUYV 输入，每行 76 字节。ROI 为 (3,5,17,13)。`source` 含行尾填充，`rgb` 为 C 参考采样，`int8_tensor` 为逐通道查表量化的 256×256×3 个输出字节。 |

在仓库根目录用 Python 3 和 NumPy 执行 `python data/verify_evidence.py`，可按帧检查 36 份热图摘要、历史板端记录、解码比较及公开样例。已有 GCC 或 Clang 时，运行 `python data/verify_evidence.py --cc gcc`，会重新编译产品的 C 参考采样函数，生成全部 65,536 个 RGB 像素，调用三张量化表后逐字节与黄金数据对照。

要在对应 KV260 上直接比较上述合成样例的输出，可使用 [板卡运行入口](../board/README.md)。离线复核读取历史记录和数值数组，上板入口会向 PL 提交一次真实 DMA 任务。
