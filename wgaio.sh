#!/usr/bin/env bash
# wgaio - WireGuard 套装入口 (wg-allin-one)
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export WGAIO_ROOT="$ROOT"
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

VERSION="0.1.0"

usage() {
  cat >&2 <<'EOF'
用法: wgaio <子命令> [参数]

  install                 向导式安装(中文问答)
  version                 显示版本
  user add|del|edit|list|show   设备管理(透传核心)
  panel start|stop|status|restart  面板服务管理
  status                  总览
  upgrade | uninstall     升级 / 卸载

设备管理细节: wgaio user --help
EOF
}

cmd="${1:-}"
case "$cmd" in
  ""|-h|--help) usage; exit 2 ;;
  version) printf 'wgaio %s\n' "$VERSION"; exit 0 ;;
  user) shift; run_core user "$@" ;;
  install|upgrade|uninstall|panel|status|logs)
    mod="$ROOT/lib/${cmd}.sh"
    [ -f "$mod" ] || die "模块未安装: $cmd"
    shift; . "$mod"; "cmd_${cmd}" "$@" ;;
  *) die "未知子命令: $cmd (用法见 wgaio --help)" 2 ;;
esac
