#!/bin/sh
set -eu
cd "$(dirname "$0")"
# AArch64 includes NEON; the C source also has a scalar diagnostic fallback.
cc -O3 -std=c99 -shared -fPIC -Wall -Wextra -Werror alpha_blit.c -o libink_alpha.so
cc -O3 -std=c99 -shared -fPIC -Wall -Wextra -Werror rgb565.c -o libink_rgb565.so
