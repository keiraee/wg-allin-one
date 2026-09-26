#!/usr/bin/env bash
# wgaio - WireGuard 套装入口 (wg-allin-one)
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export WGAIO_ROOT="$ROOT"

VERSION="0.2.3"
export WGAIO_VERSION="$VERSION"

# --- 引导模式: 套件缺失时自动拉取 (版本号只写在下面 VERSION= 一处) ---
# 只认真正跑 CLI/面板必需的 core：wizard/install 等缺失不该让 user list 去联网下载。
need_bootstrap=0
if [ ! -f "$ROOT/lib/core.py" ] || [ ! -f "$ROOT/lib/core.sh" ]; then
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
  # 稳定版=latest release；main=抢先试用。能解析提交就按提交下载，避免分支缓存。
  slug="${WGAIO_REPO:-keiraee/wg-allin-one}"
  if [ "$REF" = "latest" ]; then
    payload="$(curl -fsSL --retry 3 --retry-delay 2 -H 'Cache-Control: no-cache' \
      "https://api.github.com/repos/${slug}/releases/latest")" \
      || { printf '[wgaio] 错误: 还没有正式版 Release，请去掉 WGAIO_REF=latest 改用 main\n' >&2; exit 1; }
    REF="$(printf '%s' "$payload" | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
    [ -n "$REF" ] || { printf '[wgaio] 错误: latest release 为空\n' >&2; exit 1; }
    export WGAIO_PERSIST_TRACK="latest"
  else
    export WGAIO_PERSIST_TRACK="$REF"
  fi
  if printf '%s' "$REF" | grep -qiE '^[0-9a-f]{40}$'; then
    export WGAIO_FETCH_COMMIT="$REF"
    archive="https://github.com/${slug}/archive/${REF}.tar.gz"
  else
    payload="$(curl -fsSL --retry 3 --retry-delay 2 -H 'Cache-Control: no-cache' \
      "https://api.github.com/repos/${slug}/commits/${REF}" || true)"
    sha="$(printf '%s' "$payload" | grep -oE '"sha"[[:space:]]*:[[:space:]]*"[0-9a-fA-F]{40}"' | head -1 | grep -oE '[0-9a-fA-F]{40}' | tr 'A-F' 'a-f' || true)"
    if printf '%s' "$sha" | grep -qiE '^[0-9a-f]{40}$'; then
      printf '[wgaio] 钉住提交: %s → %s\n' "$REF" "${sha:0:12}"
      export WGAIO_FETCH_COMMIT="$sha"
      archive="https://github.com/${slug}/archive/${sha}.tar.gz"
    else
      printf '[wgaio] 警告: 未能解析提交, 按引用 %s 下载\n' "$REF" >&2
      case "$REF" in
        v*) archive="https://github.com/${slug}/archive/refs/tags/${REF}.tar.gz" ;;
        *) archive="https://github.com/${slug}/archive/refs/heads/${REF}.tar.gz" ;;
      esac
    fi
  fi
  curl -fsSL --retry 3 --retry-delay 2 -H 'Cache-Control: no-cache' "$archive" -o "$DEST/.wgaio.tgz" \
    || { printf '[wgaio] 错误: 套件下载失败\n' >&2; exit 1; }
  tar xzf "$DEST/.wgaio.tgz" -C "$DEST" --strip-components=1 --warning=no-timestamp \
    || tar xzf "$DEST/.wgaio.tgz" -C "$DEST" --strip-components=1 \
    || { printf '[wgaio] 错误: 套件解包失败\n' >&2; exit 1; }
  rm -f "$DEST/.wgaio.tgz"
  printf '[wgaio] 套件就绪, 继续安装...\n'
  exec bash "$DEST/wgaio.sh" "$@"
fi
# --- 引导结束 ---

# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

usage() {
  cat >&2 <<'EOF'
用法: wgaio [子命令]

  wgaio                   打开管理菜单(终端里直接进入)
  install                 向导式安装(中文问答)
  version                 显示版本
  user add|del|edit|list|show   设备管理
  panel start|stop|restart|status|install  面板服务
  status                  总览
  logs [行数]             面板日志
  upgrade                 按已记住的轨道升级(稳定版=Release, main=抢先试用)
  verify [--fix]          校验本地文件是否被改动, --fix 从轨道重新下载修复
  rollback                回滚到最近快照(不覆盖 config.json)
  uninstall               卸载

稳定版: WGAIO_REF=latest wgaio upgrade
抢先试用: WGAIO_REF=main wgaio upgrade
设备管理细节: wgaio user --help
EOF
}

cmd="${1:-}"
case "$cmd" in
  "")
    if [ -t 0 ]; then
      # shellcheck source=lib/menu.sh
      . "$ROOT/lib/menu.sh"
      cmd_menu
      exit $?
    fi
    usage
    exit 2
    ;;
  -h|--help) usage; exit 2 ;;
  version) printf 'wgaio %s\n' "$VERSION"; exit 0 ;;
  user) shift; . "$ROOT/lib/user.sh"; cmd_user "$@" ;;
  rollback) shift; . "$ROOT/lib/upgrade.sh"; cmd_rollback "$@" ;;
  verify) shift; . "$ROOT/lib/upgrade.sh"; cmd_verify "$@" ;;
  install|upgrade|uninstall|panel|status|logs)
    mod="$ROOT/lib/${cmd}.sh"
    [ -f "$mod" ] || die "模块未安装: $cmd"
    shift; . "$mod"; "cmd_${cmd}" "$@" ;;
  *) die "未知子命令: $cmd (用法见 wgaio --help)" 2 ;;
esac
