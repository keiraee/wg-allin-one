#!/usr/bin/env bash
# 升级: 下载 → SHA256SUMS 校验 → 快照 → 覆盖程序文件。校验失败一律拒绝。
# 不覆盖 config.json 与 clients/。快照包含 wgaio.sh，回滚后入口版本与套件一致，
# 避免入口发现版本不一致后又从 main 重新下载、把回滚盖掉。
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

check_sha256() {  # check_sha256 <目录>; 该目录须有 SHA256SUMS
  local dir="${1:-$WGAIO_ROOT}"
  [ -f "$dir/SHA256SUMS" ] || die "缺少 SHA256SUMS, 拒绝升级(来源不可信)"
  ( cd "$dir" && sha256sum -c SHA256SUMS >/dev/null 2>&1 ) \
    || die "SHA256SUMS 校验失败, 文件被改动或下载损坏, 已中止"
  log "SHA256SUMS 校验通过"
}

snapshot() {
  local dir="${1:-$WGAIO_ROOT}" ts item
  local -a files=()
  ts="$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$dir/snapshots"
  for item in wgaio.sh SHA256SUMS config.json lib panel bin; do
    [ -e "$dir/$item" ] && files+=("$item")
  done
  [ "${#files[@]}" -gt 0 ] || die "没有可快照的文件, 升级中止"
  tar czf "$dir/snapshots/wgaio-$ts.tar.gz" -C "$dir" \
    --exclude=snapshots --exclude=clients "${files[@]}" \
    || die "快照创建失败, 升级中止(磁盘满或权限不足?)"
  log "快照: snapshots/wgaio-$ts.tar.gz"
}

apply_tree() {  # apply_tree <来源> <安装目录>
  local src="$1" dest="$2"
  install -d -m 755 "$dest/bin" "$dest/lib" "$dest/panel"
  cp -f "$src/wgaio.sh" "$dest/wgaio.sh"
  cp -f "$src/SHA256SUMS" "$dest/SHA256SUMS"
  if [ -d "$src/bin" ]; then
    cp -f "$src"/bin/* "$dest/bin/"
  fi
  cp -f "$src"/lib/* "$dest/lib/"
  if [ -d "$src/panel" ]; then
    cp -f "$src"/panel/* "$dest/panel/"
  fi
}

fetch_tree() {  # fetch_tree <目录>
  local dest="$1" ref tmp
  ref="${WGAIO_REF:-main}"
  command -v curl >/dev/null 2>&1 || die "需要 curl 才能下载升级包"
  tmp="$(mktemp -d)"
  curl -fsSL "https://github.com/keiraee/wg-allin-one/archive/refs/heads/${ref}.tar.gz" -o "$tmp/src.tgz" \
    || { rm -rf "$tmp"; die "套件下载失败"; }
  mkdir -p "$dest"
  tar xzf "$tmp/src.tgz" -C "$dest" --strip-components=1 --warning=no-timestamp \
    || tar xzf "$tmp/src.tgz" -C "$dest" --strip-components=1 \
    || { rm -rf "$tmp"; die "套件解包失败"; }
  rm -rf "$tmp"
}

cmd_upgrade() {
  local src="${WGAIO_UPGRADE_SRC:-}" cleanup=""
  if [ -z "$src" ]; then
    src="$(mktemp -d)"
    cleanup="$src"
    fetch_tree "$src"
  fi
  check_sha256 "$src"
  snapshot
  apply_tree "$src" "$WGAIO_ROOT"
  [ -n "$cleanup" ] && rm -rf "$cleanup"
  check_sha256 "$WGAIO_ROOT"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl try-restart wgaio-panel 2>/dev/null || true
  fi
  log "升级完成(config.json 与 clients/ 未覆盖)"
}

cmd_rollback() {
  local dir="${WGAIO_ROOT}" latest
  latest="$(ls -1t "$dir"/snapshots/wgaio-*.tar.gz 2>/dev/null | head -1)"
  [ -n "$latest" ] || die "没有可用快照, 无法回滚"
  tar xzf "$latest" -C "$dir"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl try-restart wgaio-panel 2>/dev/null || true
  fi
  log "已回滚到快照: $latest"
}
