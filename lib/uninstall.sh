#!/usr/bin/env bash
# 卸载: 先停服务, 再删文件, 最后检查残留。
# 默认清理服务/命令/配置; --keep-clients 保留设备备份; --dry-run 只列清单
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

WGAIO_CADDY_MARK="${WGAIO_CADDY_DATA:-/var/lib/wgaio-caddy}/.wgaio-installed-caddy"

cmd_uninstall() {
  local dry=0 keep=0
  for a in "$@"; do
    case "$a" in
      --dry-run) dry=1 ;;
      --keep-clients) keep=1 ;;
    esac
  done
  local wgconf="${WGAIO_WG_CONF:-/etc/wireguard/wg0.conf}"
  log "将清理: wgaio-panel / wgaio-caddy 服务, /etc/wgaio, /var/lib/wgaio-caddy, /var/log/wgaio, /usr/local/bin/wgaio, config.json, certs/, snapshots/, backups/, .wgaio-track, Let's Encrypt 续期钩子, /etc/sysctl.d/99-wgaio.conf"
  [ "$keep" -eq 0 ] || log "(--keep-clients: clients/ 保留)"
  log "程序文件(lib/ panel/ wgaio.sh bin/)保留, 便于重装; 不会动用户自有的 WireGuard 配置"
  log "本工具生成的 wg0.conf(含 wgaio-managed 标记)会先停掉再删除"
  log "用法提示: 可选参数 --keep-clients | --dry-run"
  [ "$dry" -eq 1 ] && { log "(dry-run, 未执行任何删除)"; return 0; }

  # --- 1. 先停服务 ---
  if command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now wgaio-caddy 2>/dev/null || true
    systemctl disable --now wgaio-panel 2>/dev/null || true
  fi
  # 本工具生成的 wg0: 先停接口, 再删配置(顺序反了接口会一直留着)
  local wg_ours=0
  if [ -f "$wgconf" ] && grep -q 'wgaio-managed' "$wgconf"; then
    wg_ours=1
    if command -v systemctl >/dev/null 2>&1; then
      systemctl disable --now wg-quick@wg0 2>/dev/null || true
    fi
    if command -v wg-quick >/dev/null 2>&1; then
      wg-quick down wg0 2>/dev/null || true
    fi
    if command -v ip >/dev/null 2>&1 && ip link show wg0 >/dev/null 2>&1; then
      ip link del wg0 2>/dev/null || warn "wg0 还在, 请手动: ip link del wg0"
    fi
  fi

  # --- 2. 删服务和文件 ---
  if command -v systemctl >/dev/null 2>&1; then
    rm -f /etc/systemd/system/wgaio-panel.service /etc/systemd/system/wgaio-caddy.service
    systemctl daemon-reload 2>/dev/null || true
  else
    rm -f /etc/systemd/system/wgaio-panel.service /etc/systemd/system/wgaio-caddy.service
  fi
  # caddy 二进制是共用的(同机 HY2 可能也在用): 只有确认是本工具装的那份才删
  if [ -f "$WGAIO_CADDY_MARK" ]; then
    rm -f /usr/local/bin/caddy
    log "已删除本工具安装的 caddy 二进制"
  fi
  rm -rf /etc/wgaio /var/lib/wgaio-caddy /var/log/wgaio
  rm -f /usr/local/bin/wgaio
  # 只删本工具写的短命令, 别动别人的 wg
  if [ -f /usr/local/bin/wg ] && grep -q "${WGAIO_SHORT_CMD_MARK}" /usr/local/bin/wg 2>/dev/null; then
    rm -f /usr/local/bin/wg
  fi
  rm -f /etc/letsencrypt/renewal-hooks/deploy/wgaio
  rm -f /etc/sysctl.d/99-wgaio.conf
  rm -rf "${WGAIO_ROOT}/certs" "${WGAIO_ROOT}/snapshots" "${WGAIO_ROOT}/backups"
  rm -f "${WGAIO_ROOT}/config.json" "${WGAIO_ROOT}/.wgaio-track" \
        "${WGAIO_ROOT}/.wgaio-menu-reload" "${WGAIO_ROOT}/.wgaio-uninstalled" \
        "${WGAIO_ROOT}/.update-check.json"
  [ "$keep" -eq 0 ] && rm -rf "${WGAIO_ROOT}/clients"
  if [ "$wg_ours" -eq 1 ]; then
    rm -f "$wgconf"
    log "已停止并删除本工具生成的 $wgconf"
  else
    log "未改动 $wgconf (不是本工具生成或不存在)"
  fi

  # --- 3. 检查残留 ---
  if command -v ip >/dev/null 2>&1 && ip link show wg0 >/dev/null 2>&1; then
    warn "wg0 接口还在运行, 但配置不是本工具生成的; 确认不需要后执行: ip link del wg0"
  fi
  if [ -f /usr/local/bin/caddy ] && [ ! -f "$WGAIO_CADDY_MARK" ]; then
    log "caddy 二进制保留在 /usr/local/bin/caddy (不是本工具装的, 或同机其它服务在用)"
  fi
  log "卸载完成(程序文件仍在 ${WGAIO_ROOT}, 需要彻底删除请自行 rm -rf)"
}
