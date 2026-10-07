#!/usr/bin/env bash
# wgaio 公共函数: 日志/报错/依赖/Python 定位
# 版本号只维护在 wgaio.sh 的 VERSION=，此处不重复声明。
set -Eeuo pipefail

wgaio_root() { cd "$(dirname "${BASH_SOURCE[1]}")" && pwd; }

log()  { printf '[wgaio] %s\n' "$*"; }
warn() { printf '[wgaio] 警告: %s\n' "$*" >&2; }
die()  {
  # 升级下载到一半失败时，调用方把临时目录放在这里，退出前清掉。
  if [ -n "${WGAIO_CLEAN_DIR:-}" ]; then
    rm -rf "$WGAIO_CLEAN_DIR"
    unset WGAIO_CLEAN_DIR
  fi
  printf '[wgaio] 错误: %s\n' "$1" >&2
  exit "${2:-1}"
}

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

cfg_get() {  # cfg_get <config.json> <键> [默认值] → stdout
  local cfg="$1" key="$2" def="${3:-}" py v
  py="$(find_python)"
  v="$("$py" -c 'import json,sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    d = {}
v = d.get(sys.argv[2])
print("" if v is None else v)' "$cfg" "$key" 2>/dev/null || true)"
  printf '%s' "${v:-$def}"
}

tcp_port_busy() {  # 该 TCP 端口是否已被占用
  local port="$1" hits
  [ "${WGAIO_SKIP_NET_CHECK:-}" = "1" ] && return 1
  case ",${WGAIO_BUSY_TCP:-}," in *",$port,"*) return 0 ;; esac
  command -v ss >/dev/null 2>&1 || return 1
  hits="$(ss -H -tln "sport = :$port" 2>/dev/null || true)"
  [ -n "$hits" ]
}

udp_port_busy() {  # 该 UDP 端口是否已被占用
  local port="$1" hits
  [ "${WGAIO_SKIP_NET_CHECK:-}" = "1" ] && return 1
  case ",${WGAIO_BUSY_UDP:-}," in *",$port,"*) return 0 ;; esac
  command -v ss >/dev/null 2>&1 || return 1
  hits="$(ss -H -uln "sport = :$port" 2>/dev/null || true)"
  [ -n "$hits" ]
}

run_core() {
  local py; py="$(find_python)"
  # 核心读 WGAIO_BASE。已显式指定时保留(测试沙箱)，否则跟安装目录走。
  if [ -z "${WGAIO_BASE:-}" ]; then
    export WGAIO_BASE="${WGAIO_ROOT:-$(wgaio_root)}"
  fi
  "$py" "$(core_py)" "$@"
}
