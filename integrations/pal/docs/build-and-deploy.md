# 构建

源自 https://github.com/NJU-ProjectN/pal-navy ，基线提交 32e0a2f5c47aff04270cdb7ef5e5e5c150ce6234。目标机有 SDL 1.2 开发头文件、sdl-config、gcc 和 g++ 时，在 integrations/pal 运行 bash scripts/build-native.sh。交叉构建另行配置 PAL_SDK_ROOT，要求 bin 子目录包含 aarch64-linux-gnu-gcc、aarch64-linux-gnu-g++ 和 aarch64-sdl-config，再运行 bash scripts/build-arm64.sh。
