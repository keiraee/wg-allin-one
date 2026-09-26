#!/usr/bin/env bash
# wgaio 公共函数: 日志/报错/依赖/Python 定位
# 版本号只维护在 wgaio.sh 的 VERSION=，此处不重复声明。
set -Eeuo pipefail

wgaio_root() { cd "$(dirname "${BASH_SOURCE[1]}")" && pwd; }

log()  { printf '[wgaio] %s\n' "$*"; }
warn() { printf '[wgaio] 警告: %s\n' "$*" >&2; }
die()  { printf '[wgaio] 错误: %s\n' "$1" >&2; exit "${2:-1}"; }

find_python() {
  local candidates=("python3" "python") cmd
  for cmd in "${candidates[@]}"; do
    if command -v "$cmd" >/dev/null 2>&1 && "$cmd" -c "import sys" 2>/dev/null; then
      command -v "$cmd"
      return 0
    fi
  done
  die "未找到可用的 python3/python, 请先安装 Python 3.9+"
}

core_py() {
  local root="${WGAIO_ROOT:-$(wgaio_root)}"
  printf '%s/lib/core.py' "$root"
}

run_core() {
  local py; py="$(find_python)"
  # 核心读 WGAIO_BASE。已显式指定时保留(测试沙箱)，否则跟安装目录走。
  if [ -z "${WGAIO_BASE:-}" ]; then
    export WGAIO_BASE="${WGAIO_ROOT:-$(wgaio_root)}"
  fi
  "$py" "$(core_py)" "$@"
}
