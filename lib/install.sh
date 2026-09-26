#!/usr/bin/env bash
# 安装主流程: 向导 → 目录/权限 → 文件就位 → 面板服务 → 安全组提示
# --dry-run: 只写 WGAIO_ROOT/_stage 暂存布局(测试/预览用), 不碰系统
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"
. "$ROOT/lib/wizard.sh"

install_deps() {
  log "安装系统依赖(wireguard / python3 / iptables)..."
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq && apt-get install -y -qq wireguard wireguard-tools python3 iptables openssl
    apt-get install -y -qq certbot || warn "没有 certbot 包, 正式证书会改用自签。装上后执行 wgaio cert"
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y wireguard-tools python3 iptables openssl
    dnf install -y certbot || warn "没有 certbot 包, 正式证书会改用自签。装上后执行 wgaio cert"
  elif command -v yum >/dev/null 2>&1; then
    yum install -y wireguard-tools python3 iptables openssl
    yum install -y certbot || warn "没有 certbot 包, 正式证书会改用自签。装上后执行 wgaio cert"
  elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache wireguard-tools python3 iptables openssl
    apk add --no-cache certbot || warn "没有 certbot 包, 正式证书会改用自签。装上后执行 wgaio cert"
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
# wgaio-managed
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

init_wg_hub() {  # init_wg_hub <config_dir>
  local conf_dir="$1"
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
  render_wg0_conf "$priv" "$(read_cfg "$conf_dir/config.json" vpn_cidr)" "$(read_cfg "$conf_dir/config.json" wg_port)" > "$conf"
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
  [ -d "$ROOT/bin" ] && cp -f "$ROOT"/bin/* "$dest/bin/"
  [ -f "$ROOT/SHA256SUMS" ] && cp -f "$ROOT/SHA256SUMS" "$dest/SHA256SUMS"
  shopt -u nullglob
}

sync_config() {  # sync_config <dest>
  local dest="$1"
  # 已有配置不覆盖（向导刚写入的为准）；仅当目标缺失时才从运行目录搬一份
  if [ ! -f "$dest/config.json" ] && [ -f "$ROOT/config.json" ]; then
    cp -f "$ROOT/config.json" "$dest/config.json"
  fi
  [ -f "$dest/config.json" ] && chmod 600 "$dest/config.json"
}

issue_self_cert() {  # issue_self_cert <cn> <cert> <key>
  local cn="$1" cert="$2" key="$3"
  command -v openssl >/dev/null 2>&1 || return 1
  mkdir -p "$(dirname "$cert")" || return 1
  rm -f "$cert" "$key"
  if ! openssl req -x509 -newkey rsa:2048 -keyout "$key" -out "$cert" -days 3650 -nodes \
      -subj "/CN=${cn:-wgaio}" \
      -addext "subjectAltName=DNS:${cn:-wgaio}"; then
    openssl req -x509 -newkey rsa:2048 -keyout "$key" -out "$cert" -days 3650 -nodes \
      -subj "/CN=${cn:-wgaio}" || return 1
  fi
  chmod 600 "$key" "$cert" || return 1
}

clear_tls_fields() {  # clear_tls_fields <config.json>
  "$(find_python)" - "$1" <<'PY'
import json, sys
p = sys.argv[1]
with open(p, encoding="utf-8") as f:
    d = json.load(f)
d["tls_cert"] = ""
d["tls_key"] = ""
d["tls_mode"] = ""
with open(p, "w", encoding="utf-8") as f:
    f.write(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
PY
}

install_renew_hook() {
  local hook="/etc/letsencrypt/renewal-hooks/deploy/wgaio"
  install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy 2>/dev/null || return 0
  cat > "$hook" <<'EOF'
#!/bin/sh
systemctl try-restart wgaio-panel >/dev/null 2>&1 || true
EOF
  chmod 755 "$hook" 2>/dev/null || true
  if command -v systemctl >/dev/null 2>&1; then
    systemctl enable --now certbot.timer >/dev/null 2>&1 || true
  fi
}

issue_acme() {  # issue_acme <domain> <cert> <key>
  local domain="$1" cert="$2" key="$3" live
  command -v certbot >/dev/null 2>&1 || return 1
  printf '%s' "$domain" | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$' || return 1
  printf '%s' "$domain" | grep -Eq '^[0-9.]+$' && return 1
  certbot certonly --standalone --non-interactive --agree-tos \
    --register-unsafely-without-email --keep-until-expiring \
    --preferred-challenges http --http-01-port 80 \
    -d "$domain" || return 1
  live="/etc/letsencrypt/live/${domain}"
  [ -f "$live/fullchain.pem" ] && [ -f "$live/privkey.pem" ] || return 1
  mkdir -p "$(dirname "$cert")" || return 1
  rm -f "$cert" "$key"
  ln -s "$live/fullchain.pem" "$cert" || return 1
  ln -s "$live/privkey.pem" "$key" || return 1
  install_renew_hook
}

maybe_gen_tls() {  # maybe_gen_tls <config_dir>
  local cfgdir="$1"
  local cert key cn mode
  cert="$(read_cfg "$cfgdir/config.json" tls_cert)"
  key="$(read_cfg "$cfgdir/config.json" tls_key)"
  cn="$(read_cfg "$cfgdir/config.json" tls_cn)"
  mode="$(read_cfg "$cfgdir/config.json" tls_mode)"
  [ -n "$cert" ] || return 0
  if [ -f "$cert" ] && [ -f "$key" ] && [ "${WGAIO_FORCE_CERT:-}" != "1" ]; then
    return 0
  fi
  if [ "$mode" = "acme" ]; then
    log "正在向 Let's Encrypt 申请证书: ${cn} (需要公网能访问本机 TCP 80)"
    if issue_acme "$cn" "$cert" "$key"; then
      log "已申请 Let's Encrypt 证书: $cn"
      return 0
    fi
    warn "正式证书没申请到。常见原因: 安全组没放行 TCP 80, 域名没指到这台机器, 或 80 端口已被占用"
    warn "先用自签证书, 浏览器会提示不受信。放行 80 后执行: wgaio cert"
  fi
  if issue_self_cert "$cn" "$cert" "$key"; then
    log "已生成自签 TLS 证书: $cert"
    return 0
  fi
  warn "证书没有生成, 面板改为纯 HTTP"
  rm -f "$cert" "$key"
  clear_tls_fields "$cfgdir/config.json"
}

cmd_cert() {
  umask 077
  local dest="$WGAIO_ROOT" cn cert key
  if [ ! -f "$dest/config.json" ] && [ -n "${WGAIO_DIR:-}" ] && [ -f "${WGAIO_DIR}/config.json" ]; then
    dest="$WGAIO_DIR"
  fi
  [ -f "$dest/config.json" ] || die "还没有配置, 请先安装"
  cn="$(read_cfg "$dest/config.json" tls_cn)"
  [ -n "$cn" ] || die "当前不是公网面板, 没有可申请证书的域名"
  "$(find_python)" - "$dest/config.json" <<'PY'
import json, sys
p = sys.argv[1]
with open(p, encoding="utf-8") as f:
    d = json.load(f)
root = p.rsplit("/", 1)[0]
d["tls_mode"] = "acme"
d["tls_cn"] = d.get("tls_cn") or ""
d["tls_cert"] = d.get("tls_cert") or (root + "/certs/wgaio.crt")
d["tls_key"] = d.get("tls_key") or (root + "/certs/wgaio.key")
with open(p, "w", encoding="utf-8") as f:
    f.write(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
PY
  cert="$(read_cfg "$dest/config.json" tls_cert)"
  key="$(read_cfg "$dest/config.json" tls_key)"
  if [ -n "$cert" ] && [ -e "$cert" ] && [ ! -L "$cert" ]; then
    rm -f "$cert" "$key"
  fi
  WGAIO_FORCE_CERT=1 maybe_gen_tls "$dest"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl try-restart wgaio-panel >/dev/null 2>&1 || true
  fi
  log "证书处理完成。完整地址见: wgaio status"
}

read_cfg() {  # read_cfg <config.json 路径> <键>
  local cfg="$1" key="$2" py
  py="$(find_python)"
  "$py" -c 'import json,sys;d=json.load(open(sys.argv[1],encoding="utf-8"));print(d.get(sys.argv[2],""))' "$cfg" "$key"
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
  # 正式安装固定落在安装根（默认 /opt/wgaio，可用 WGAIO_DIR 覆盖），
  # 不跟随入口脚本所在目录——否则从克隆目录安装会装进克隆目录。
  local dest fresh=1
  if [ "$dry" -eq 1 ]; then
    dest="$WGAIO_ROOT/_stage"
  else
    dest="${WGAIO_DIR:-/opt/wgaio}"
  fi
  install -d -m 700 "$dest"
  # 再跑一次安装不能重写配置：登录密码会换掉，wg0.conf 却保持原样，隧道和面板对不上。
  if [ -f "$dest/config.json" ]; then
    if ! "$(find_python)" -c 'import json,sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$dest/config.json"; then
      die "已有 config.json 但不是合法 JSON, 请备份后删除再安装"
    fi
    fresh=0
    log "检测到已有配置, 跳过问答(不更换登录密码, 不改网段和端口)"
  else
    WGAIO_CONFIG_DIR="$dest" run_wizard
  fi

  if [ "$dry" -eq 0 ]; then
    install_deps
  fi

  install -d -m 700 "$dest/clients" "$dest/lib" "$dest/panel" "$dest/bin"
  stage_files "$dest"
  sync_config "$dest"

  if [ "$dry" -eq 0 ]; then
    init_wg_hub "$dest"
  fi

  if [ "$dry" -eq 0 ]; then
    maybe_gen_tls "$dest"
  fi

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

  local py wg_port panel_port tls_cert tls_cn tls_mode panel_path scheme gw_ip base
  py="$(find_python)"
  wg_port="$(read_cfg "$dest/config.json" wg_port)"
  panel_port="$(read_cfg "$dest/config.json" panel_port)"
  tls_cert="$(read_cfg "$dest/config.json" tls_cert)"
  tls_cn="$(read_cfg "$dest/config.json" tls_cn)"
  tls_mode="$(read_cfg "$dest/config.json" tls_mode)"
  panel_path="$(read_cfg "$dest/config.json" panel_path)"
  gw_ip="$("$py" -c "import sys;sys.path.insert(0,'$ROOT/lib');import core;b,_=core.cidr_bounds(sys.argv[1]);print(core.int_to_ip(b+1))" "$(read_cfg "$dest/config.json" vpn_cidr)")"

  if [ -n "$tls_cert" ] && [ -f "$tls_cert" ]; then
    scheme="https"
  else
    scheme="http"
  fi
  if [ -n "$tls_cn" ]; then
    base="${scheme}://${tls_cn}:${panel_port}"
  else
    base="${scheme}://${gw_ip}:${panel_port}"
  fi

  printf '\n'
  log "====================================================="
  log " 重要: 请到云控制台安全组放行 UDP ${wg_port} 端口!"
  if [ "$tls_mode" = "acme" ]; then
    log " 申请正式证书还要放行 TCP 80, 且 80 不能被别的程序占用"
  fi
  if [ -n "$tls_cn" ]; then
    log " 公网打开面板还要放行 TCP ${panel_port}"
  fi
  if [ -n "$panel_path" ]; then
    log " 面板: ${base}/${panel_path}/"
    log " 只打开这一整条地址。只开端口 ${panel_port} 会看到 404"
  else
    log " 面板: ${base}/"
  fi
  if [ "$fresh" -eq 0 ]; then
    log " 登录密码沿用已有配置, 本次不再显示"
  else
    log " 登录密码见上方, 只显示过一次"
  fi
  log "====================================================="

  log "WireGuard 中枢: wg0 (网关 ${gw_ip}) — 设备通过面板添加"
  # shellcheck source=lib/upgrade.sh
  . "$ROOT/lib/upgrade.sh"
  write_track "$dest"
  log "之后直接执行 wgaio 进入管理菜单"
  printf '\n'
}
