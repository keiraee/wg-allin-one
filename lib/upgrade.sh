#!/usr/bin/env bash
# 升级: SHA256SUMS 校验 → 快照 → 覆盖文件; 校验失败一律拒绝
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
  local dir="${1:-$WGAIO_ROOT}" ts
  ts="$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$dir/snapshots"
  tar czf "$dir/snapshots/wgaio-$ts.tar.gz" -C "$dir" \
    --exclude=snapshots --exclude=clients config.json lib panel 2>/dev/null \
    || die "快照创建失败, 升级中止(磁盘满或权限不足?)"
  log "快照: snapshots/wgaio-$ts.tar.gz"
}

cmd_upgrade() {
  check_sha256
  snapshot
  log "SHA256SUMS 校验通过 + 快照已创建; 升级覆盖步骤待实现(当前仅校验与快照)"
}

cmd_rollback() {
  local dir="${WGAIO_ROOT}" latest
  latest="$(ls -1t "$dir"/snapshots/wgaio-*.tar.gz 2>/dev/null | head -1)"
  [ -n "$latest" ] || die "没有可用快照, 无法回滚"
  tar xzf "$latest" -C "$dir"
  log "已回滚到快照: $latest"
}
