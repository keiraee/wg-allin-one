#!/usr/bin/env bash
# 总览: 面板服务 + 设备摘要
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

cmd_status() {
  log "wgaio 状态总览"
  if command -v systemctl >/dev/null 2>&1; then
    local state
    # is-active 在未运行时自己就返回非 0。不能再接管道，否则 pipefail 会再打一行。
    state="$(systemctl is-active wgaio-panel 2>/dev/null || true)"
    if [ "$state" = "active" ]; then
      log "面板服务: 运行中"
    else
      log "面板服务: 未运行"
    fi
  else
    log "面板服务: (无 systemd, 无法查询)"
  fi
  log "设备列表:"
  run_core user list || warn "设备列表读取失败(可能还没装完)"
}
