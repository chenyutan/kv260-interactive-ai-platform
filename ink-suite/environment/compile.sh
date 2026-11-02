#!/bin/sh
set -eu
cd "$(dirname "$0")"
# Cortex-A53 on KV260 includes NEON. Keep a portable default for other hosts.
arch_flags=""
case "$(uname -m)" in aarch64) arch_flags="-mcpu=cortex-a53" ;; esac
c++ -O3 -DNDEBUG -std=c++17 $arch_flags -fPIC -shared ink_filter.cpp -I/usr/include/opencv4 -lopencv_imgproc -lopencv_core -o libink_filter.so
