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
  local vpn_cidr wg_port endpoint client_dns lan_cidrs panel_bind panel_port def_mode
  vpn_cidr="$(ask 'VPN 网段' '10.66.66.0/24')"
  wg_port="$(ask 'WireGuard 监听端口(UDP)' '51820')"
  endpoint="$(ask '客户端接入点(公网IP或域名:端口)' '')"
  client_dns="$(ask '客户端 DNS' '1.1.1.1')"
  lan_cidrs="$(ask '内网路由段(逗号分隔, 可空)' '')"
  panel_bind="$(ask '面板绑定地址(默认 VPN 隧道地址)' '')"
  panel_port="$(ask '面板端口' '8888')"
  def_mode="$(ask '新设备默认流量模式 split/full' 'split')"
  [ -n "$endpoint" ] || die "endpoint 不能为空"

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
