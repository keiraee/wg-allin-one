#!/usr/bin/env bash
# wgaio 公共函数: 日志/报错/依赖/Python 定位
set -Eeuo pipefail

wgaio_root() { cd "$(dirname "${BASH_SOURCE[1]}")" && pwd; }

log()  { printf '[wgaio] %s\n' "$*"; }
warn() { printf '[wgaio] 警告: %s\n' "$*" >&2; }
die()  { printf '[wgaio] 错误: %s\n' "$*" >&2; exit "${2:-1}"; }

find_python() {
  if command -v python3 >/dev/null 2>&1; then command -v python3
  elif command -v python >/dev/null 2>&1; then command -v python
  else die "未找到 python3/python, 请先安装 Python 3.9+"; fi
}

core_py() {
  local root="${WGAIO_ROOT:-$(wgaio_root)}"
  printf '%s/lib/core.py' "$root"
}

run_core() {
  local py; py="$(find_python)"
  "$py" "$(core_py)" "$@"
}
