# 赛车：参考与来源

核对日期：2026-10-19。玩法参考官方竞速游戏资料中的追逐镜头、三圈比赛、竞速道具和漂移加速等通用设计。

- Nintendo 官方基础技巧：<https://www.nintendo.com/jp/ichikara/aabpa/index_en.html>。参考分段漂移火花、释放后短时加速、道具选择与自动加速的可读性；未提取其代码、角色、图像、音频或关卡。
- SuperTuxKart 官方美术风格：<https://supertuxkart.net/Style>。参考轻快、友好的统一主题与用地标表达场景的方法。
- Kenney Racing Kit：<https://kenney.nl/assets/racing-kit>，页面标注 CC0。本轮仅核对其开源素材许可和卡丁车组件类别，未下载或使用该资源包。

- SuperTuxKart 官方代码仓库：<https://github.com/supertuxkart/stk-code>。
- SuperTuxKart 官方许可说明：<https://supertuxkart.github.io/stk-website/Licensing>；源代码许可 GPL-3.0-or-later，参见 <https://github.com/supertuxkart/stk-code/blob/master/COPYING>。

本目录没有复制或链接第三方竞速游戏的代码、模型、纹理、角色、音频或关卡；没有使用 Mario / Nintendo 美术或商标作为游戏素材，也没有下载大型游戏资源包。赛车物理、封闭赛道、车手、车辆、国风自然场景、HUD 与特效均为原创代码生成。512×512 材质图集在 `material_atlas()` 中确定性生成，只含低频道路、草地、石材、木材、瓦片和水波明暗，没有外部图像路径。这里的资料用于设计和来源边界核对。

`native/ink_gl_backend.cpp`、`ink_gl_backend.h`、`ink_gl_packed_vertex.h` 复制自本工程已发布的原生 GLES2 runner 后端（`runner-stability-20261018/working/ink-suite/runner/native/`），本目录保留独立副本，不依赖 runner 后端后续 AA 改动。新 `kart_scene.cpp` 不要求 OpenGL ES 3。

`audio.py` 复用同工程 `runner/audio_assets/manifest.json` 标注的原创确定性五声音阶拨弦/笛子追逐音乐和合成效果，未复制一份 WAV。音频仍走已有异步 Cube 输出管理；素材路径由当前工程目录相对解析，不引用旧工程或 C 盘产物。

F helmet: original procedural study authored for this project, no third-party model or bitmap. Source QA helmet_candidate.h SHA256 `05b332d4cbe47aa719cff686c835a6e542d38a42dd72085388442c49cc70a5d4`; functions are inlined into kart_scene.cpp and require no external runtime resource.
