#!/usr/bin/env bash
# 总览: 面板服务 + 设备摘要
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

cmd_status() {
  log "wgaio 状态总览"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl is-active wgaio-panel 2>/dev/null | sed 's/^/面板服务: /' || log "面板服务: 未运行"
  else
    log "面板服务: (无 systemd, 无法查询)"
  fi
  log "设备列表:"
  run_core user list || true
}