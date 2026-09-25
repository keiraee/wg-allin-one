#!/usr/bin/env bash
# 中文问答向导: 收集配置 → config.json; 令牌明文只打印一次
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

ask() {  # ask "提示" "默认值" → stdout 答案
  local prompt="$1" def="${2:-}" ans
  if [ -n "$def" ]; then printf '%s [%s]: ' "$prompt" "$def" >&2
  else printf '%s: ' "$prompt" >&2; fi
  IFS= read -r ans || true
  printf '%s' "${ans:-$def}"
}

run_wizard() {
  local py; py="$(find_python)"
  log "开始安装向导(全部可回车用默认值)"
  local vpn_cidr wg_port endpoint client_dns lan_cidrs panel_bind panel_port def_mode access_choice mode_choice

  # 1. WireGuard 网段
  vpn_cidr="$(ask 'WireGuard 网段(设备互联用, 一般不用改)' '10.66.66.0/24')"

  # 2. 服务端口
  wg_port="$(ask '服务端口 UDP(一般不用改)' '51820')"

  # 3. 设备连接地址(自动检测公网IP)
  local detected def_endpoint
  if [ "${WGAIO_SKIP_DETECT:-}" = "1" ]; then
    detected=""
  else
    detected="$(curl -fsSL --max-time 5 https://api.ipify.org 2>/dev/null || true)"
  fi
  if [ -n "$detected" ]; then
    def_endpoint="${detected}:51820"
  else
    def_endpoint=""
  fi
  endpoint="$(ask '设备连接地址(手机/电脑连 VPN 时要填的服务器地址)' "$def_endpoint")"
  [ -n "$endpoint" ] || die "endpoint 不能为空"

  # 4. 设备 DNS
  client_dns="$(ask '设备 DNS(设备连上后解析域名用)' '1.1.1.1')"

  # 5. 内网路由段
  lan_cidrs="$(ask '内网路由段(如 192.168.1.0/24, 让设备能访问家里/公司内网; 不需要直接回车)' '')"

  # 6. 面板访问范围(菜单选择)
  printf '面板从哪里可以打开:\n' >&2
  printf '  1) 仅 VPN 内(更安全)\n' >&2
  printf '  2) 公网直接访问(方便, 令牌登录)\n' >&2
  access_choice="$(ask '选 1 或 2' '1')"
  case "$access_choice" in
    1) panel_bind="" ;;
    2) panel_bind="0.0.0.0" ;;
    *) die "无效选择, 请输入 1 或 2" ;;
  esac

  # 7. 面板端口
  panel_port="$(ask '面板端口' '8888')"

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
  token="$("$py" -c 'import secrets;print(secrets.token_hex(24))')"
  hash="$(printf '%s' "$token" | "$py" -c 'import hashlib,sys;print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"

  "$py" - "$vpn_cidr" "$wg_port" "$endpoint" "$client_dns" "$lan_cidrs" \
        "$panel_bind" "$panel_port" "$def_mode" "$hash" <<'PY'
import json, os, re, sys
vpn_cidr, wg_port, endpoint, client_dns, lan_cidrs, panel_bind, panel_port, mode, thash = sys.argv[1:]
if not re.match(r"^[^:]+:\d+$", endpoint):
    sys.exit("错误: endpoint 格式应为 IP或域名:端口")
try:
    wg_port_i = int(wg_port)
    panel_port_i = int(panel_port)
except ValueError:
    sys.exit("错误: 端口必须是纯数字(1-65535)")
cfg = {
    "vpn_cidr": vpn_cidr,
    "wg_port": wg_port_i,
    "endpoint": endpoint,
    "client_dns": client_dns,
    "lan_cidrs": [x.strip() for x in lan_cidrs.split(",") if x.strip()],
    "panel_bind": panel_bind or (".".join(vpn_cidr.split(".")[:3]) + ".1"),
    "panel_port": panel_port_i,
    "panel_token_hash": thash,
    "default_mode": mode,
}
root = os.environ.get("WGAIO_ROOT", ".")
sys.path.insert(0, os.path.join(root, "lib"))
try:
    from core import ApiError, validate_config
    validate_config(cfg)
except ApiError as e:
    sys.exit("错误: %s" % e)
with open(os.path.join(root, "config.json"), "w", encoding="utf-8") as f:
    f.write(json.dumps(cfg, ensure_ascii=False, indent=2))
PY

  printf '\n===== 面板访问令牌(只显示这一次, 请立即保存) =====\n%s\n==============================================\n' "$token"
  log "配置已写入 config.json"
}
