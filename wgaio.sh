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
  # 先下到临时目录并校验，通过后才覆盖安装目录。失败不会改掉正在用的套件。
  work="$(mktemp -d)"
  boot_fail() {
    [ -n "${work:-}" ] && rm -rf "$work"
    printf '[wgaio] 错误: %s\n' "$1" >&2
    exit 1
  }
  boot_ref_ok() {
    case "${1:-}" in
      ''|-*|*[!A-Za-z0-9._/-]*|*..*) return 1 ;;
      *) return 0 ;;
    esac
  }
  slug="${WGAIO_REPO:-keiraee/wg-allin-one}"
  [[ "$slug" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || boot_fail "WGAIO_REPO 不合法"
  REF="${WGAIO_REF:-main}"
  boot_ref_ok "$REF" || boot_fail "升级引用不合法"
  # 稳定版=latest release；main=抢先试用。能解析提交就按提交下载，避免分支缓存。
  if [ "$REF" = "latest" ]; then
    code="$(curl -sS -L --retry 3 --retry-delay 2 --connect-timeout 10 --max-time 60 \
      -H 'Cache-Control: no-cache' -o "$work/release.json" -w '%{http_code}' \
      "https://api.github.com/repos/${slug}/releases/latest" || true)"
    if [ "$code" = "404" ]; then
      boot_fail "还没有正式版 Release，请去掉 WGAIO_REF=latest 改用 main"
    fi
    [ "$code" = "200" ] || boot_fail "访问 GitHub 失败 (HTTP ${code})"
    release_payload="$(cat "$work/release.json" || true)"
    if [[ "$release_payload" =~ \"tag_name\"[[:space:]]*:[[:space:]]*\"([^\"]*)\" ]]; then
      REF="${BASH_REMATCH[1]}"
    else
      REF=""
    fi
    [ -n "$REF" ] || boot_fail "latest release 为空"
    boot_ref_ok "$REF" || boot_fail "latest release 的 tag 不合法"
    export WGAIO_PERSIST_TRACK="latest"
  else
    export WGAIO_PERSIST_TRACK="$REF"
  fi
  if [[ "$REF" =~ ^[0-9a-fA-F]{40}$ ]]; then
    export WGAIO_FETCH_COMMIT="$REF"
    archive="https://github.com/${slug}/archive/${REF}.tar.gz"
  else
    code="$(curl -sS -L --retry 3 --retry-delay 2 --connect-timeout 10 --max-time 60 \
      -H 'Cache-Control: no-cache' -o "$work/commit.json" -w '%{http_code}' \
      "https://api.github.com/repos/${slug}/commits/${REF}" || true)"
    sha=""
    if [ "$code" = "200" ]; then
      commit_payload="$(cat "$work/commit.json" || true)"
      if [[ "$commit_payload" =~ \"sha\"[[:space:]]*:[[:space:]]*\"([0-9a-fA-F]{40})\" ]]; then
        sha="${BASH_REMATCH[1],,}"
      fi
    fi
    if [[ "$sha" =~ ^[0-9a-fA-F]{40}$ ]]; then
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
  curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 10 --max-time 120 \
    -H 'Cache-Control: no-cache' "$archive" -o "$work/src.tgz" \
    || boot_fail "套件下载失败"
  stage="$work/tree"
  mkdir -p "$stage"
  tar xzf "$work/src.tgz" -C "$stage" --strip-components=1 --warning=no-timestamp \
    || tar xzf "$work/src.tgz" -C "$stage" --strip-components=1 \
    || boot_fail "套件解包失败"
  if command -v sha256sum >/dev/null 2>&1; then
    ( cd "$stage" && LC_ALL=C sha256sum -c SHA256SUMS ) || boot_fail "下载的套件校验失败, 已保留原安装目录"
  else
    printf '[wgaio] 警告: 没有 sha256sum, 跳过下载校验\n' >&2
  fi
  new_sum=""
  old_sum=""
  if command -v sha256sum >/dev/null 2>&1 && [ -f "$stage/SHA256SUMS" ]; then
    new_sum="$(sha256sum "$stage/SHA256SUMS" | awk '{print $1}')"
  fi
  if [ -f "$DEST/.wgaio-track" ]; then
    old_sum="$(awk -F= '$1=="WGAIO_MODULES_SHA"{gsub(/\r/,""); print substr($0, index($0,"=")+1); exit}' "$DEST/.wgaio-track" || true)"
  fi
  printf '[wgaio] 上次哈希: %s\n' "${old_sum:0:12}"
  printf '[wgaio] 本次哈希: %s\n' "${new_sum:0:12}"
  skip_copy=0
  if [ -n "$old_sum" ] && [ "$old_sum" = "$new_sum" ] \
      && [ -f "$DEST/SHA256SUMS" ] && command -v sha256sum >/dev/null 2>&1 \
      && ( cd "$DEST" && LC_ALL=C sha256sum -c SHA256SUMS >/dev/null 2>&1 ); then
    printf '[wgaio] 哈希未变化, 保留现有套件\n'
    skip_copy=1
  fi
  boot_apply() {
    install -d -m 755 "$DEST/bin" "$DEST/lib" "$DEST/panel" || return 1
    cp -f "$stage/wgaio.sh" "$DEST/wgaio.sh" || return 1
    cp -f "$stage/SHA256SUMS" "$DEST/SHA256SUMS" || return 1
    cp -f "$stage"/lib/* "$DEST/lib/" || return 1
    if compgen -G "$stage/bin/*" >/dev/null; then
      cp -f "$stage"/bin/* "$DEST/bin/" || return 1
    fi
    if compgen -G "$stage/panel/*" >/dev/null; then
      cp -f "$stage"/panel/* "$DEST/panel/" || return 1
    fi
  }
  if [ "$skip_copy" -eq 0 ]; then
    if [ -f "$DEST/wgaio.sh" ]; then
      mkdir -p "$DEST/snapshots"
      snap_items=()
      for item in wgaio.sh SHA256SUMS lib panel bin .wgaio-track; do
        [ -e "$DEST/$item" ] && snap_items+=("$item")
      done
      if [ "${#snap_items[@]}" -gt 0 ]; then
        tar czf "$DEST/snapshots/wgaio-bootstrap-$(date +%Y%m%d-%H%M%S).tar.gz" -C "$DEST" "${snap_items[@]}" \
          || boot_fail "覆盖前快照失败, 已保留原安装目录"
      fi
    fi
    boot_apply || boot_fail "写入安装目录失败"
  fi
  ver="$(awk -F= '/^VERSION=/{gsub(/["\r]/,"",$2); print $2; exit}' "$DEST/wgaio.sh" || true)"
  [ -n "$ver" ] || ver="$VERSION"
  old_umask="$(umask)"
  umask 077
  cat > "$DEST/.wgaio-track" <<EOF
WGAIO_TRACK_REF=${WGAIO_PERSIST_TRACK}
WGAIO_REPO_SHA=${WGAIO_FETCH_COMMIT:-}
WGAIO_MODULES_SHA=${new_sum}
WGAIO_VERSION=${ver}
EOF
  chmod 600 "$DEST/.wgaio-track" 2>/dev/null || true
  umask "$old_umask"
  rm -rf "$work"
  printf '[wgaio] 套件就绪\n'
  exec bash "$DEST/wgaio.sh" "$@"
  printf '[wgaio] 错误: 无法继续执行套件\n' >&2
  exit 1
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
  status                  总览(含面板完整地址)
  cert                    重新申请 Let's Encrypt 证书
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
  cert) shift; . "$ROOT/lib/install.sh"; cmd_cert "$@" ;;
  install|upgrade|uninstall|panel|status|logs)
    mod="$ROOT/lib/${cmd}.sh"
    [ -f "$mod" ] || die "模块未安装: $cmd"
    shift; . "$mod"; "cmd_${cmd}" "$@" ;;
  *) die "未知子命令: $cmd (用法见 wgaio --help)" 2 ;;
esac
