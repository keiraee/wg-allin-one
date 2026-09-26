#!/usr/bin/env bash
# 面板日志: journalctl(注意: user show 的输出永不落日志)
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

cmd_logs() {
  local n="${1:-100}"
  case "$n" in
    ''|*[!0-9]*) die "行数必须是正整数" 2 ;;
  esac
  n="$((10#$n))"
  if [ "$n" -lt 1 ] || [ "$n" -gt 5000 ]; then
    die "行数必须是 1-5000" 2
  fi
  command -v journalctl >/dev/null 2>&1 \
    || die "当前环境没有 journalctl, 请在 systemd 服务器上查看日志"
  journalctl -u wgaio-panel --no-pager -n "$n"
}
