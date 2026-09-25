#!/usr/bin/env bash
# 卸载: 默认清理服务/命令/配置; --keep-clients 保留设备备份; --dry-run 只列清单
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

cmd_uninstall() {
  local dry=0 keep=0
  for a in "$@"; do
    case "$a" in
      --dry-run) dry=1 ;;
      --keep-clients) keep=1 ;;
    esac
  done
  log "将清理: wgaio-panel 服务, /usr/local/bin/wgaio, config.json, certs/, snapshots/, /etc/sysctl.d/99-wgaio.conf$([ "$keep" -eq 0 ] && echo ', clients/' || echo '')"
  log "程序文件(lib/ panel/ wgaio.sh)和 /etc/wireguard 保留"
  log "用法提示: 可选参数 --keep-clients | --dry-run"
  [ "$dry" -eq 1 ] && { log "(dry-run, 未执行任何删除)"; return 0; }
  if command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now wgaio-panel 2>/dev/null || true
    rm -f /etc/systemd/system/wgaio-panel.service
    systemctl daemon-reload 2>/dev/null || true
  else
    rm -f /etc/systemd/system/wgaio-panel.service
  fi
  rm -f /usr/local/bin/wgaio
  rm -f /etc/sysctl.d/99-wgaio.conf
  rm -rf "${WGAIO_ROOT}/certs" "${WGAIO_ROOT}/snapshots"
  [ "$keep" -eq 0 ] && rm -rf "${WGAIO_ROOT}/clients"
  rm -f "${WGAIO_ROOT}/config.json"
  log "卸载完成(wireguard 配置 /etc/wireguard/ 未动)"
}
