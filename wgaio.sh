#!/usr/bin/env bash
# wgaio - WireGuard 套装入口 (wg-allin-one)
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export WGAIO_ROOT="$ROOT"

VERSION="0.2.3"

# --- 引导模式: 套件缺失或版本不一致时自动拉取 ---
need_bootstrap=0
if [ ! -f "$ROOT/lib/core.py" ]; then
  need_bootstrap=1
elif ! grep -q "SUITE_VERSION=\"$VERSION\"" "$ROOT/lib/core.sh" 2>/dev/null; then
  printf '[wgaio] 检测到套件版本与入口不一致(旧版残留?), 正在更新...\n'
  need_bootstrap=1
fi

if [ "$need_bootstrap" = "1" ]; then
  if [ "${1:-}" = "version" ]; then printf 'wgaio %s\n' "$VERSION"; exit 0; fi
  if [ -n "${WGAIO_OFFLINE:-}" ]; then
    printf '[wgaio] 错误: 离线模式且套件缺失/过旧, 请手动更新套件后重试\n' >&2
    exit 1
  fi
  DEST="${WGAIO_DIR:-/opt/wgaio}"
  printf '[wgaio] 引导模式: 正在下载完整套件到 %s ...\n' "$DEST"
  if [ "$(id -u)" -ne 0 ]; then
    printf '[wgaio] 错误: 首次引导需要 root(写入 %s), 请用: sudo bash wgaio.sh install\n' "$DEST" >&2
    exit 1
  fi
  command -v curl >/dev/null 2>&1 || { printf '[wgaio] 错误: 需要 curl\n' >&2; exit 1; }
  mkdir -p "$DEST"
  REF="${WGAIO_REF:-main}"
  curl -fsSL "https://github.com/keiraee/wg-allin-one/archive/refs/heads/${REF}.tar.gz" -o "$DEST/.wgaio.tgz" \
    || { printf '[wgaio] 错误: 套件下载失败\n' >&2; exit 1; }
  tar xzf "$DEST/.wgaio.tgz" -C "$DEST" --strip-components=1
  rm -f "$DEST/.wgaio.tgz"
  printf '[wgaio] 套件就绪, 继续安装...\n'
  exec bash "$DEST/wgaio.sh" "$@"
fi
# --- 引导结束 ---

# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

usage() {
  cat >&2 <<'EOF'
用法: wgaio <子命令> [参数]

  install                 向导式安装(中文问答)
  version                 显示版本
  user add|del|edit|list|show   设备管理(透传核心)
  panel start|stop|restart|status|install  面板服务管理
  status                  总览
  logs [行数]             面板日志
  upgrade | uninstall     升级 / 卸载
  rollback                回滚到最近快照

设备管理细节: wgaio user --help
EOF
}

cmd="${1:-}"
case "$cmd" in
  ""|-h|--help) usage; exit 2 ;;
  version) printf 'wgaio %s\n' "$VERSION"; exit 0 ;;
  user) shift; . "$ROOT/lib/user.sh"; cmd_user "$@" ;;
  rollback) shift; . "$ROOT/lib/upgrade.sh"; cmd_rollback "$@" ;;
  install|upgrade|uninstall|panel|status|logs)
    mod="$ROOT/lib/${cmd}.sh"
    [ -f "$mod" ] || die "模块未安装: $cmd"
    shift; . "$mod"; "cmd_${cmd}" "$@" ;;
  *) die "未知子命令: $cmd (用法见 wgaio --help)" 2 ;;
esac
