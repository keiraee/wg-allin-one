#!/usr/bin/env bash
# 面板日志: journalctl(注意: user show 的输出永不落日志)
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

cmd_logs() {
  command -v journalctl >/dev/null 2>&1 \
    || die "当前环境没有 journalctl, 请在 systemd 服务器上查看日志"
  journalctl -u wgaio-panel --no-pager -n "${1:-100}"
}