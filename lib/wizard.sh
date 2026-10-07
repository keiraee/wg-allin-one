#!/usr/bin/env bash
# 中文问答向导: 收集配置 → config.json; 令牌明文只打印一次
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

ask() {  # ask "提示" "默认值" → stdout 答案
  local prompt="$1" def="${2:-}" ans rc
  if [ -n "$def" ]; then printf '%s [%s]: ' "$prompt" "$def" >&2
  else printf '%s: ' "$prompt" >&2; fi
  if [ -t 0 ]; then
    # readline 吃掉退格和方向键。默认值只写在方括号里，不填进输入行。
    rc=0
    IFS= read -e -r ans || rc=$?
    if [ "$rc" -gt 128 ] || [ "$rc" -eq 2 ]; then
      stty erase '^H' -echoctl 2>/dev/null || true
      IFS= read -r ans || true
    elif [ "$rc" -ne 0 ]; then
      ans=""
    fi
  else
    IFS= read -r ans || true
  fi
  # 去掉首尾空白。直接回车时 ans 为空，用方括号里的默认值。
  ans="${ans#"${ans%%[![:space:]]*}"}"
  ans="${ans%"${ans##*[![:space:]]}"}"
  printf '%s' "${ans:-$def}"
}

# udp_port_busy / tcp_port_busy 在 lib/core.sh（caddy.sh 也要用）

valid_port() {  # 08 按十进制 8，不按八进制报错；拒绝 0 和大于 65535
  local p="$1" n
  case "$p" in
    ''|*[!0-9]*) return 1 ;;
  esac
  [ "${#p}" -le 5 ] || return 1
  n="$((10#$p))"
  [ "$n" -ge 1 ] && [ "$n" -le 65535 ]
}

local_cidr_conflict() {  # 打印和本机地址重叠的那条前缀；没有重叠则空
  local cidr="$1" locals py
  [ "${WGAIO_SKIP_NET_CHECK:-}" = "1" ] && return 0
  if [ -n "${WGAIO_LOCAL_CIDRS:-}" ]; then
    locals="$WGAIO_LOCAL_CIDRS"
  else
    command -v ip >/dev/null 2>&1 || return 0
    locals="$(ip -4 -o addr show scope global 2>/dev/null | awk '{printf "%s%s", (NR>1?",":""), $4}')" || true
  fi
  [ -n "$locals" ] || return 0
  py="$(find_python)"
  "$py" - "$cidr" "$locals" <<'PY'
import os, sys
sys.path.insert(0, os.path.join(os.environ.get("WGAIO_ROOT", "."), "lib"))
from core import cidrs_overlap
chosen, locals_ = sys.argv[1], sys.argv[2]
for item in locals_.split(","):
    item = item.strip()
    if not item:
        continue
    try:
        if cidrs_overlap(chosen, item):
            sys.stdout.write(item)
            break
    except ValueError:
        continue
PY
}

detect_ip() {
  if [ -n "${WGAIO_DETECT_IP:-}" ]; then
    printf '%s' "$WGAIO_DETECT_IP"
    return 0
  fi
  # 国内机器访问 api.ipify.org 经常超时, 所以多放几个源; 有的接口带中文前缀, 统一抠 IPv4
  local ip s
  for s in "https://ip.sb" "https://api.ipify.org" "https://ipinfo.io/ip" \
           "https://ifconfig.me/ip" "https://icanhazip.com" "https://myip.ipip.net"; do
    ip="$(curl -fsSL --max-time 4 "$s" 2>/dev/null \
          | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | head -n 1 || true)"
    if printf '%s' "$ip" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'; then
      printf '%s' "$ip"
      return 0
    fi
  done
  return 1
}

run_wizard() {
  local py; py="$(find_python)"
  log "开始安装向导(全部可回车用默认值)"
  local vpn_cidr wg_port endpoint client_dns lan_cidrs panel_bind panel_port def_mode access_choice mode_choice

  # 1. WireGuard 网段（三选一，避免手打网段时退格把字打乱）
  printf '设备之间用哪段地址(请选一个不和家里、公司现有网段重复的):\n' >&2
  printf '  1) 10.66.66.0/24（推荐）\n' >&2
  printf '  2) 10.77.77.0/24\n' >&2
  printf '  3) 172.31.88.0/24\n' >&2
  local cidr_choice
  cidr_choice="$(ask '选 1、2 或 3' '1')"
  case "$cidr_choice" in
    1) vpn_cidr="10.66.66.0/24" ;;
    2) vpn_cidr="10.77.77.0/24" ;;
    3) vpn_cidr="172.31.88.0/24" ;;
    *) die "无效选择, 请输入 1、2 或 3" ;;
  esac
  local conflict conflict_iface
  conflict="$(local_cidr_conflict "$vpn_cidr" || true)"
  if [ -n "$conflict" ]; then
    conflict_iface=""
    if command -v ip >/dev/null 2>&1; then
      conflict_iface="$(ip -4 -o addr show 2>/dev/null | awk -v c="$conflict" '$4 == c {print $2; exit}')"
    fi
    if [ -n "$conflict_iface" ]; then
      die "网段 ${vpn_cidr} 和 ${conflict_iface} 上的 ${conflict} 重叠。如果是上次安装留下的 WireGuard, 先执行: wg-quick down ${conflict_iface} 2>/dev/null; rm -f /etc/wireguard/${conflict_iface}.conf 然后重新安装; 否则请改选 1、2 或 3"
    fi
    die "网段 ${vpn_cidr} 和本机地址 ${conflict} 重叠, 请改选 1、2 或 3"
  fi

  # 2. 服务端口
  wg_port="$(ask '服务端口 UDP(一般不用改)' '51820')"
  valid_port "$wg_port" || die "端口必须是纯数字(1-65535)"
  if udp_port_busy "$wg_port"; then
    die "UDP 端口 ${wg_port} 已被占用, 多半是上次安装留下的 WireGuard。请先执行: systemctl disable --now wg-quick@wg0 && rm -f /etc/wireguard/wg0.conf  然后重新安装。若这个端口是别的程序占用的, 请换一个服务端口"
  fi

  # 3. 设备连接地址(自动检测公网IP, 多源回退)
  local detected def_endpoint
  if [ "${WGAIO_SKIP_DETECT:-}" = "1" ]; then
    detected=""
  else
    detected="$(detect_ip || true)"
  fi
  if [ -n "$detected" ]; then
    def_endpoint="${detected}:${wg_port}"
  else
    def_endpoint=""
    warn "自动探测公网 IP 失败, 请手动输入(形如 1.2.3.4:${wg_port})"
  fi
  endpoint="$(ask '设备连接地址(手机/电脑连 VPN 时要填的服务器地址)' "$def_endpoint")"
  [ -n "$endpoint" ] || die "endpoint 不能为空"

  # 4. 设备 DNS
  client_dns="$(ask '设备 DNS(设备连上后解析域名用)' '1.1.1.1')"

  # 5. 内网路由段
  lan_cidrs="$(ask '内网路由段(如 192.168.1.0/24, 让设备能访问家里/公司内网; 不需要直接回车)' '')"

  # 6. 面板访问范围(菜单选择)。面板 TLS 交给 wgaio 自己的 Caddy：公网一律自动申请正式证书，
  #    只有显式 WGAIO_TLS=internal|off 才用自签/明文。
  printf '面板从哪里可以打开:\n' >&2
  printf '  1) 仅 VPN 内(更安全)\n' >&2
  printf '  2) 公网直接访问(自动申请 Let'"'"'s Encrypt 正式证书)\n' >&2
  access_choice="$(ask '选 1 或 2' '1')"
  local panel_bind="" tls_choice="${WGAIO_TLS:-}" domain="" tls_cn="" gw_ip
  gw_ip="$("$py" -c "import sys;sys.path.insert(0,'$ROOT/lib');import core;b,_=core.cidr_bounds(sys.argv[1]);print(core.int_to_ip(b+1))" "$vpn_cidr")"
  case "$access_choice" in
    1) panel_bind="$gw_ip" ;;
    2) panel_bind="0.0.0.0" ;;
    *) die "无效选择, 请输入 1 或 2" ;;
  esac
  case "$tls_choice" in
    self) tls_choice="internal" ;;
    http) tls_choice="off" ;;
  esac

  if [ "$access_choice" = "2" ]; then
    local ip_part default_domain
    ip_part="${endpoint%%:*}"
    # 默认自动域名：纯 IPv4 拼 sslip.io；endpoint 本来就是域名就直接用它。
    if printf '%s' "$ip_part" | grep -Eq '^[0-9]{1,3}(\.[0-9]{1,3}){3}$'; then
      default_domain="$(printf '%s' "$ip_part" | tr '.' '-').sslip.io"
    else
      default_domain="$ip_part"
    fi
    # 有自己的域名就填(证书签它)；直接回车走 sslip.io，证书一样自动申请。
    domain="${WGAIO_DOMAIN:-$(ask '面板/证书域名(没有域名就直接回车)' "$default_domain")}"
    [ -n "$domain" ] || domain="$default_domain"
    if ! printf '%s' "$domain" | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$' \
        || printf '%s' "$domain" | grep -q '\.\.'; then
      die "域名格式不对: $domain"
    fi
    tls_cn="$domain"
    [ -n "$tls_choice" ] || tls_choice="acme"
    case "$tls_choice" in
      acme)
        log "面板域名 $domain：Caddy 会自动申请并续期正式证书"
        log "申请时 80(HTTP-01) 或 443(TLS-ALPN-01) 要有一个空闲, 域名也要能解析到本机"
        ;;
      internal) warn "WGAIO_TLS=internal: 用自签证书, 浏览器会提示不受信" ;;
      off) warn "WGAIO_TLS=off: 令牌明文传输, 公网环境可能被窃听" ;;
      *) die "WGAIO_TLS 只能是 acme、internal(self) 或 off(http)" ;;
    esac
  else
    tls_cn="$gw_ip"
    if [ "$tls_choice" = "acme" ]; then
      warn "仅 VPN 内模式没有公网域名, WGAIO_TLS=acme 用不上, 改用明文 HTTP"
      tls_choice="off"
    fi
    [ -n "$tls_choice" ] || tls_choice="off"
    case "$tls_choice" in
      internal) warn "WGAIO_TLS=internal: 面板用自签 HTTPS, 浏览器会提示不受信" ;;
      off) ;;
      *) die "WGAIO_TLS 只能是 acme、internal(self) 或 off(http)" ;;
    esac
  fi

  # 7. 面板端口(Caddy 对外监听；后端只在 127.0.0.1)
  panel_port="$(ask '面板 HTTPS 端口' '8443')"
  valid_port "$panel_port" || die "端口必须是纯数字(1-65535)"
  if [ "$panel_port" = "80" ]; then
    die "面板不能占用 80: Caddy 申请证书和跳转都要用它"
  fi
  if tcp_port_busy "$panel_port"; then
    die "面板端口 ${panel_port} 已被占用, 请换一个(同机装了 HY2 就别用它的端口)"
  fi

  # 8. 流量模式(菜单选择)
  printf '新设备连上后怎么走流量:\n' >&2
  printf '  1) 只访问 VPN/内网, 其余走自己流量(省流量)\n' >&2
  printf '  2) 全部流量走服务器(隐藏上网地点)\n' >&2
  mode_choice="$(ask '选 1 或 2' '1')"
  case "$mode_choice" in
    1) def_mode="split" ;;
    2) def_mode="full" ;;
    *) die "无效选择, 请输入 1 或 2" ;;
  esac

  local token hash
  token="$("$py" -c 'import secrets,string as s; a=s.ascii_letters+s.digits; g=lambda n:"".join(secrets.choice(a) for _ in range(n)); print("wgaio-" + "-".join(g(5) for _ in range(4)))')"
  hash="$(printf '%s' "$token" | "$py" -c 'import hashlib,sys;print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"

  local tls_mode="" panel_path panel_backend_port
  panel_backend_port="${WGAIO_BACKEND_PORT:-8888}"
  valid_port "$panel_backend_port" || die "后端端口必须是纯数字(1-65535)"
  panel_path="${WGAIO_PANEL_PATH:-}"
  if ! printf '%s' "$panel_path" | grep -Eq '^[A-Za-z0-9_-]{4,80}$'; then
    panel_path="wgaio-$("$py" -c 'import secrets; print(secrets.token_hex(6))')"
  fi
  tls_mode="$tls_choice"

  "$py" - "$vpn_cidr" "$wg_port" "$endpoint" "$client_dns" "$lan_cidrs" \
        "$panel_bind" "$panel_port" "$def_mode" "$hash" \
        "$tls_cn" "$tls_mode" "$panel_path" "$panel_backend_port" <<'PY'
import json, os, re, sys
vpn_cidr, wg_port, endpoint, client_dns, lan_cidrs, panel_bind, panel_port, mode, thash, tls_cn, tls_mode, panel_path, backend_port = sys.argv[1:]
root = os.environ.get("WGAIO_ROOT", ".")
out_root = os.environ.get("WGAIO_CONFIG_DIR") or root
sys.path.insert(0, os.path.join(root, "lib"))
from core import ApiError, cidr_bounds, int_to_ip, parse_endpoint, validate_config
try:
    parse_endpoint(endpoint)
except ApiError as e:
    sys.exit("错误: %s" % e)
try:
    wg_port_i = int(wg_port)
    panel_port_i = int(panel_port)
    backend_port_i = int(backend_port)
except ValueError:
    sys.exit("错误: 端口必须是纯数字(1-65535)")
try:
    base, _last = cidr_bounds(vpn_cidr)
except ValueError:
    sys.exit("错误: vpn_cidr 不合法: %s" % vpn_cidr)
cfg = {
    "vpn_cidr": vpn_cidr,
    "wg_port": wg_port_i,
    "endpoint": endpoint,
    "client_dns": client_dns,
    "lan_cidrs": [x.strip() for x in lan_cidrs.split(",") if x.strip()],
    "panel_bind": panel_bind or int_to_ip(base + 1),
    "panel_port": panel_port_i,
    "panel_backend_port": backend_port_i,
    "panel_token_hash": thash,
    "default_mode": mode,
    "panel_path": panel_path,
}
if tls_cn:
    cfg["tls_cn"] = tls_cn
if tls_mode:
    cfg["tls_mode"] = tls_mode
try:
    validate_config(cfg)
except ApiError as e:
    sys.exit("错误: %s" % e)
os.makedirs(out_root, exist_ok=True)
with open(os.path.join(out_root, "config.json"), "w", encoding="utf-8") as f:
    f.write(json.dumps(cfg, ensure_ascii=False, indent=2))
PY

  printf '\n===== 面板登录密码(只显示这一次, 请立即保存) =====\n%s\n=====================================================\n' "$token"

  local scheme="https"
  [ "$tls_mode" = "off" ] && scheme="http"
  log "面板访问地址: ${scheme}://${tls_cn}:${panel_port}/${panel_path}/"
  log "只打开这一整条。只开端口 ${panel_port} 会看到 404"
  if [ "$access_choice" = "2" ] && [ "$tls_mode" = "acme" ]; then
    log "Caddy 会自动申请正式证书; 申请不到会先用自签, 腾出 80/443 后执行 wgaio cert"
  fi
  log "配置已写入 config.json"
}
