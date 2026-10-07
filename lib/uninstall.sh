#!/usr/bin/env bash
# 卸载: 默认彻底清干净 —— 先停服务, 再删文件/程序/包, 最后查残留。
# 开关: --keep-files 保留程序文件 | --keep-clients 保留设备备份
#       --keep-pkgs 保留本工具装的 WireGuard 包 | --purge-wg 连没标记的 wg0.conf 一起清
#       --dry-run 只列清单
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

WGAIO_CADDY_MARK="${WGAIO_CADDY_DATA:-/var/lib/wgaio-caddy}/.wgaio-installed-caddy"

cmd_uninstall() {
  local dry=0 keep_clients=0 keep_files=0 keep_pkgs=0 purge_wg=0
  for a in "$@"; do
    case "$a" in
      --dry-run) dry=1 ;;
      --keep-clients) keep_clients=1 ;;
      --keep-files) keep_files=1 ;;
      --keep-pkgs) keep_pkgs=1 ;;
      --purge-wg) purge_wg=1 ;;
    esac
  done
  local wgconf="${WGAIO_WG_CONF:-/etc/wireguard/wg0.conf}"
  local root="${WGAIO_ROOT:-$ROOT}"   # 单独 source 调用时 WGAIO_ROOT 可能没设
  log "将清理: wgaio-panel / wgaio-caddy 服务, /etc/wgaio, /var/lib/wgaio-caddy, /var/log/wgaio, /usr/local/bin/wgaio, config.json, certs/, snapshots/, backups/, .wgaio-track, Let's Encrypt 续期钩子, /etc/sysctl.d/99-wgaio.conf"
  if [ "$keep_files" -eq 1 ]; then
    log "程序文件(lib/ panel/ wgaio.sh bin/)保留, 便于重装"
  else
    log "程序文件(lib/ panel/ wgaio.sh bin/)连同整个安装目录 ${root} 一起删除(想保留便于重装就加 --keep-files)"
  fi
  if [ "$keep_pkgs" -eq 1 ]; then
    log "本工具装的 WireGuard 包保留 (--keep-pkgs)"
  else
    log "本工具装的 WireGuard 包(wireguard / wireguard-tools)也会卸载(想保留就加 --keep-pkgs)"
  fi
  [ "$keep_clients" -eq 0 ] || log "(--keep-clients: clients/ 保留)"
  if [ "$purge_wg" -eq 1 ]; then
    log "wg0.conf 无论有没有 wgaio-managed 标记都会被停掉并删除 (--purge-wg)"
  else
    log "本工具生成的 wg0.conf(含 wgaio-managed 标记)会先停掉再删除; 没标记的不动(要一起清加 --purge-wg)"
  fi
  log "用法提示: 可选参数 --keep-clients | --keep-files | --keep-pkgs | --purge-wg | --dry-run"
  [ "$dry" -eq 1 ] && { log "(dry-run, 未执行任何删除)"; return 0; }

  # --- 1. 先停服务 ---
  if command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now wgaio-caddy 2>/dev/null || true
    systemctl disable --now wgaio-panel 2>/dev/null || true
  fi
  local wg_ours=0
  if [ -f "$wgconf" ] && { grep -q 'wgaio-managed' "$wgconf" || [ "$purge_wg" -eq 1 ]; }; then
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
  # caddy 二进制: 本工具装过(有标记)就删; 没标记时只要系统里没有别的 Caddy 在用也删
  local other_caddy=0
  [ -f /etc/caddy/Caddyfile ] && other_caddy=1
  if command -v systemctl >/dev/null 2>&1 && systemctl cat caddy.service >/dev/null 2>&1; then
    other_caddy=1
  fi
  if [ -f /usr/local/bin/caddy ]; then
    if [ -f "$WGAIO_CADDY_MARK" ] || [ "$other_caddy" -eq 0 ]; then
      rm -f /usr/local/bin/caddy
      log "已删除 caddy 二进制"
    else
      log "caddy 二进制保留在 /usr/local/bin/caddy (系统里还有别的 Caddy 服务在用)"
    fi
  fi
  rm -rf /etc/wgaio /var/lib/wgaio-caddy /var/log/wgaio
  rm -f /usr/local/bin/wgaio
  if [ -f /usr/local/bin/wg ] && grep -q "${WGAIO_SHORT_CMD_MARK}" /usr/local/bin/wg 2>/dev/null; then
    rm -f /usr/local/bin/wg
  fi
  rm -f /etc/letsencrypt/renewal-hooks/deploy/wgaio
  rm -f /etc/sysctl.d/99-wgaio.conf
  rm -rf "${root}/certs" "${root}/snapshots" "${root}/backups"
  rm -f "${root}/config.json" "${root}/.wgaio-track" \
        "${root}/.wgaio-menu-reload" "${root}/.wgaio-uninstalled" \
        "${root}/.update-check.json"
  [ "$keep_clients" -eq 0 ] && rm -rf "${root}/clients"
  if [ "$wg_ours" -eq 1 ]; then
    rm -f "$wgconf"
    log "已停止并删除 $wgconf"
  else
    log "未改动 $wgconf (不是本工具生成; 要一起清就加 --purge-wg)"
  fi

  # --- 3. 卸载本工具装的 WireGuard 包 ---
  if [ "$keep_pkgs" -eq 0 ]; then
    local mf="${root}/.wgaio-installed-pkgs" pkgs="" p other_wg=0 f
    if [ -f "$mf" ]; then
      while IFS= read -r p; do
        case "$p" in ''|'#'*) continue ;; esac
        pkgs="$pkgs $p"
      done < "$mf"
    fi
    if [ -d /etc/wireguard ]; then
      for f in /etc/wireguard/*.conf; do
        [ -f "$f" ] || continue
        grep -q 'wgaio-managed' "$f" 2>/dev/null || other_wg=1
      done
    fi
    if [ -z "$pkgs" ]; then
      log "没有记录到本工具装的 WireGuard 包, 跳过卸载"
    elif [ "$other_wg" -eq 1 ]; then
      warn "系统里还有别的 WireGuard 配置, 跳过卸载这些包:$pkgs"
      warn "确认不需要后手动: apt-get purge -y$pkgs"
    else
      log "卸载本工具装的包:$pkgs"
      if command -v apt-get >/dev/null 2>&1; then
        DEBIAN_FRONTEND=noninteractive apt-get purge -y $pkgs >/dev/null 2>&1 || warn "没删干净, 请手动 purge:$pkgs"
      elif command -v dnf >/dev/null 2>&1; then
        dnf remove -y $pkgs >/dev/null 2>&1 || warn "没删干净:$pkgs"
      elif command -v yum >/dev/null 2>&1; then
        yum remove -y $pkgs >/dev/null 2>&1 || warn "没删干净:$pkgs"
      elif command -v apk >/dev/null 2>&1; then
        apk del $pkgs >/dev/null 2>&1 || warn "没删干净:$pkgs"
      fi
    fi
    rm -f "$mf"
  fi

  # --- 4. 程序文件 / 残留 ---
  if [ "$keep_files" -eq 1 ]; then
    log "程序文件保留在 ${root} (--keep-files)"
  else
    local root="$WGAIO_ROOT"
    case "$root" in
      ""|"/"|"/opt"|"/usr"|"/usr/local"|"/etc"|"/var"|"/root") die "安装目录异常, 拒绝删除: $root" ;;
    esac
    cd / 2>/dev/null || true
    if rm -rf "$root"; then
      log "已删除程序文件: $root"
    else
      warn "程序文件没删干净, 请手动: rm -rf $root"
    fi
  fi
  if command -v ip >/dev/null 2>&1 && ip link show wg0 >/dev/null 2>&1; then
    warn "wg0 接口还在运行(配置不是本工具生成的); 确认不需要后: ip link del wg0"
  fi
  [ -f /usr/local/bin/caddy ] && log "caddy 二进制还在 /usr/local/bin/caddy"
  log "卸载完成"
}
