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
  log "将清理: wgaio-panel 服务, /usr/local/bin/wgaio, config.json, certs/, snapshots/, .wgaio-track, Let's Encrypt 续期钩子, /etc/sysctl.d/99-wgaio.conf$([ "$keep" -eq 0 ] && echo ', clients/' || echo '')"
  log "程序文件(lib/ panel/ wgaio.sh bin/)保留, 便于重装; 不会动用户自有的 WireGuard 配置"
  log "本工具生成的 wg0.conf(含 wgaio-managed 标记)会停掉并删除"
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
  rm -f /etc/letsencrypt/renewal-hooks/deploy/wgaio
  rm -f /etc/sysctl.d/99-wgaio.conf
  rm -rf "${WGAIO_ROOT}/certs" "${WGAIO_ROOT}/snapshots"
  [ "$keep" -eq 0 ] && rm -rf "${WGAIO_ROOT}/clients"
  rm -f "${WGAIO_ROOT}/config.json" "${WGAIO_ROOT}/.wgaio-track"
  local wgconf="${WGAIO_WG_CONF:-/etc/wireguard/wg0.conf}"
  if [ -f "$wgconf" ] && grep -q 'wgaio-managed' "$wgconf"; then
    if command -v systemctl >/dev/null 2>&1; then
      systemctl disable --now wg-quick@wg0 2>/dev/null || true
    fi
    rm -f "$wgconf"
    log "已停止并删除本工具生成的 $wgconf"
  else
    log "未改动 $wgconf (不是本工具生成或不存在)"
  fi
  log "卸载完成(程序文件仍在 ${WGAIO_ROOT}, 需要彻底删除请自行 rm -rf)"
}
