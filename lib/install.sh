#!/usr/bin/env bash
# 安装主流程: 向导 → 目录/权限 → 文件就位 → 面板服务 → 安全组提示
# --dry-run: 只写 WGAIO_ROOT/_stage 暂存布局(测试/预览用), 不碰系统
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"
. "$ROOT/lib/wizard.sh"

install_deps() {
  log "安装系统依赖(wireguard / python3)..."
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq wireguard wireguard-tools python3
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y wireguard-tools python3
  elif command -v yum >/dev/null 2>&1; then
    yum install -y wireguard-tools python3
  elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache wireguard-tools python3
  else
    die "不识别的包管理器, 请手动安装 wireguard-tools 和 python3 后重试"
  fi
  command -v wg >/dev/null 2>&1 || die "wireguard-tools 安装失败(wg 仍不可用)"
}

render_wg0_conf() {  # render_wg0_conf <private_key> <vpn_cidr> <wg_port> → stdout
  local priv="$1" vpn_cidr="$2" wg_port="$3"
  local py prefix gw_ip
  py="$(find_python)"
  prefix="${vpn_cidr#*/}"
  gw_ip="$("$py" -c "import sys;sys.path.insert(0,'$ROOT/lib');import core;b,_=core.cidr_bounds(sys.argv[1]);print(core.int_to_ip(b+1))" "$vpn_cidr")"
  # 不设 Table=off：中枢要让 wg-quick 安装对等端网段路由。
  # 默认路由只出现在客户端配置里，服务端拒绝 0.0.0.0/0，避免 SSH 被吸走。
  cat <<EOF
[Interface]
Address = ${gw_ip}/${prefix}
ListenPort = ${wg_port}
MTU = 1420
PrivateKey = ${priv}
PostUp = sysctl -w net.ipv4.ip_forward=1
PostUp = iptables -C FORWARD -i %i -j ACCEPT || iptables -A FORWARD -i %i -j ACCEPT
PostUp = iptables -C FORWARD -o %i -j ACCEPT || iptables -A FORWARD -o %i -j ACCEPT
PostUp = iptables -t mangle -C FORWARD -i %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu || iptables -t mangle -A FORWARD -i %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
PostUp = iptables -t mangle -C FORWARD -o %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu || iptables -t mangle -A FORWARD -o %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu
PostUp = wan=\$(ip -4 route show default 2>/dev/null | awk '{print \$5; exit}'); [ -n "\$wan" ] && { iptables -t nat -C POSTROUTING -s ${vpn_cidr} -o "\$wan" -j MASQUERADE || iptables -t nat -A POSTROUTING -s ${vpn_cidr} -o "\$wan" -j MASQUERADE; }
PostDown = iptables -D FORWARD -i %i -j ACCEPT || true
PostDown = iptables -D FORWARD -o %i -j ACCEPT || true
PostDown = iptables -t mangle -D FORWARD -i %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu || true
PostDown = iptables -t mangle -D FORWARD -o %i -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --clamp-mss-to-pmtu || true
PostDown = wan=\$(ip -4 route show default 2>/dev/null | awk '{print \$5; exit}'); [ -n "\$wan" ] && iptables -t nat -D POSTROUTING -s ${vpn_cidr} -o "\$wan" -j MASQUERADE || true
EOF
}

init_wg_hub() {
  local conf="${WGAIO_WG_CONF:-/etc/wireguard/wg0.conf}"
  if [ -f "$conf" ]; then
    log "wg0.conf 已存在, 保持不动(不覆盖现有 WireGuard 配置)"
    grep -q "MASQUERADE" "$conf" 2>/dev/null \
      || warn "现有 wg0.conf 没有 NAT, 全隧道上网需要自行加转发和 MASQUERADE"
    return 0
  fi
  command -v wg >/dev/null 2>&1 || die "wg 不可用, 请先安装 wireguard-tools"
  install -d -m 700 "$(dirname "$conf")"
  local priv
  priv="$(wg genkey)"
  render_wg0_conf "$priv" "$(read_cfg vpn_cidr)" "$(read_cfg wg_port)" > "$conf"
  chmod 600 "$conf"
  log "已生成 WireGuard 中枢配置: $conf"
  sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 || warn "开启转发失败, 请手动设置 ip_forward=1"
  printf 'net.ipv4.ip_forward=1\n' > /etc/sysctl.d/99-wgaio.conf 2>/dev/null || true
  if command -v systemctl >/dev/null 2>&1; then
    systemctl enable --now wg-quick@wg0 2>/dev/null \
      || warn "wg-quick@wg0 启动失败, 可用 wgaio status 查看"
  fi
}

stage_files() {  # stage_files <dest>
  local dest="$1"
  local src
  src="$(cd "$ROOT" && pwd)"
  if [ "$src" = "$(cd "$dest" 2>/dev/null && pwd)" ]; then
    log "文件已在位(安装目录=运行目录), 跳过复制"
    return 0
  fi
  install -d -m 700 "$dest/clients" "$dest/lib" "$dest/panel" "$dest/bin"
  shopt -s nullglob
  cp -f "$ROOT/lib/core.py" "$dest/lib/core.py"
  cp -f "$ROOT"/lib/*.sh "$dest/lib/"
  cp -f "$ROOT"/panel/* "$dest/panel/"
  cp -f "$ROOT/wgaio.sh" "$dest/wgaio.sh"
  shopt -u nullglob
}

sync_config() {  # sync_config <dest>
  local dest="$1"
  if [ "$(cd "$ROOT" && pwd)" != "$(cd "$dest" && pwd)" ]; then
    cp -f "$ROOT/config.json" "$dest/config.json"
  fi
  chmod 600 "$dest/config.json"
}

maybe_gen_tls() {
  local cert key cn
  cert="$(read_cfg tls_cert)"; key="$(read_cfg tls_key)"; cn="$(read_cfg tls_cn)"
  [ -n "$cert" ] || return 0
  [ -f "$cert" ] && return 0
  command -v openssl >/dev/null 2>&1 || { warn "没有 openssl, 跳过 HTTPS(可稍后手动补证书)"; return 0; }
  mkdir -p "$(dirname "$cert")"
  openssl req -x509 -newkey rsa:2048 -keyout "$key" -out "$cert" -days 3650 -nodes \
    -subj "/CN=${cn:-wgaio}" \
    -addext "subjectAltName=DNS:${cn:-wgaio},IP:127.0.0.1" 2>/dev/null \
    || { warn "证书生成失败, 面板将退回纯 HTTP"; return 0; }
  chmod 600 "$key"
  log "已生成自签 TLS 证书: $cert"
}

read_cfg() {
  local py; py="$(find_python)"
  "$py" -c "import json,sys;d=json.load(open(sys.argv[1],encoding='utf-8'));print(d.get('$1',''))" "$dest/config.json"
}

cmd_install() {
  if [ "${1:-}" = "--wizard-only" ]; then
    run_wizard
    return 0
  fi

  local dry=0
  [ "${1:-}" = "--dry-run" ] && dry=1

  if [ "$dry" -eq 0 ] && [ "${WGAIO_FORCE_NONROOT:-}" != "1" ] && [ "$(id -u)" -ne 0 ]; then
    die "请用 root 运行: sudo bash wgaio.sh install"
  fi

  umask 077
  run_wizard

  if [ "$dry" -eq 0 ]; then
    install_deps
  fi

  local dest="$WGAIO_ROOT"
  [ "$dry" -eq 1 ] && dest="$WGAIO_ROOT/_stage"

  install -d -m 700 "$dest/clients" "$dest/lib" "$dest/panel" "$dest/bin"
  stage_files "$dest"
  sync_config "$dest"

  if [ "$dry" -eq 0 ]; then
    init_wg_hub
  fi

  maybe_gen_tls

  if [ "$dry" -eq 0 ]; then
    cat > /usr/local/bin/wgaio <<EOF
#!/usr/bin/env bash
exec bash '$dest/wgaio.sh' "\$@"
EOF
    chmod 755 /usr/local/bin/wgaio
    if [ -f "$ROOT/lib/panel.sh" ]; then
      . "$ROOT/lib/panel.sh"
      install_panel_unit "$dest"
    fi
  fi

  local py wg_port panel_port tls_cert tls_cn scheme
  py="$(find_python)"
  wg_port="$("$py" -c 'import json,sys;print(json.load(open(sys.argv[1],encoding="utf-8"))["wg_port"])' "$dest/config.json")"
  panel_port="$("$py" -c 'import json,sys;print(json.load(open(sys.argv[1],encoding="utf-8"))["panel_port"])' "$dest/config.json")"
  tls_cert="$("$py" -c 'import json,sys;print(json.load(open(sys.argv[1],encoding="utf-8")).get("tls_cert",""))' "$dest/config.json")"
  tls_cn="$("$py" -c 'import json,sys;print(json.load(open(sys.argv[1],encoding="utf-8")).get("tls_cn",""))' "$dest/config.json")"

  if [ -n "$tls_cert" ] && [ -f "$tls_cert" ]; then
    scheme="https"
  else
    scheme="http"
  fi

  printf '\n'
  log "====================================================="
  log " 重要: 请到云控制台安全组放行 UDP %s 端口!" "$wg_port"
  if [ -n "$tls_cn" ]; then
    log " 面板: %s://%s:%s (令牌见上方)" "$scheme" "$tls_cn" "$panel_port"
  else
    log " 面板: %s://<VPN隧道地址>:%s (令牌见上方)" "$scheme" "$panel_port"
  fi
  log "====================================================="

  local gw_ip
  gw_ip="$("$py" -c "import sys;sys.path.insert(0,'$ROOT/lib');import core;b,_=core.cidr_bounds(sys.argv[1]);print(core.int_to_ip(b+1))" "$(read_cfg vpn_cidr)")"
  log "WireGuard 中枢: wg0 (网关 ${gw_ip}) — 设备通过面板添加"
  printf '\n'
}
