# 照片水墨模型

部署版本将写意风格统计固化在计算图中。冻结 VGG 编码器后微调 AdaIN 解码器 300 步，在 ARM64 上运行 ONNX Runtime。仓库提供原始训练与导出程序、图结构以及相对上游预训练参数的数值差分。

1. 从维护者发布的 [模型页](https://github.com/naoto0804/pytorch-AdaIN/releases/tag/v0.0.0) 取得有权使用的 `decoder.pth` 和 `vgg_normalised.pth`，放入 `qa-adain-v4/upstream/models`。两个文件的准确 SHA256 见 `layout.json` 的 `base_pth`。
2. 安装 NumPy，从本目录运行 `python qa-adain-v4/convert_adain_weights.py`，产生严格读取的两个 NPZ。
3. 运行 `python restore_model.py`。程序验证每个来源张量、应用训练差分并核对最终 SHA256 后，写入 `ink-suite/environment/models/figure-ink-v3.onnx`。还原部署模型无需训练集、相机或 GPU。

最终模型为 28,101,420 字节，SHA256 为 `7ca0cfd577415b034bc358f775ad9e2d317de344c07f4482945d2be85562d35b`。训练过程见 `qa-style-finetune-v5/finetune_adain.py`。上游网络代码固定到 `naoto0804/pytorch-AdaIN@47950d0e6656a95a80a4b105c4c0f58d38ef785c`，随附 MIT 原文。
