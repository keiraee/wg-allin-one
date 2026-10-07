#!/usr/bin/env bash
# 总览: 面板服务 + 设备摘要
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

cmd_status() {
  log "wgaio 状态总览"
  if command -v systemctl >/dev/null 2>&1; then
    local state caddy_state
    # is-active 在未运行时自己就返回非 0。不能再接管道，否则 pipefail 会再打一行。
    state="$(systemctl is-active wgaio-panel 2>/dev/null || true)"
    if [ "$state" = "active" ]; then
      log "面板后端: 运行中"
    else
      log "面板后端: 未运行"
    fi
    if [ -f /etc/systemd/system/wgaio-caddy.service ]; then
      caddy_state="$(systemctl is-active wgaio-caddy 2>/dev/null || true)"
      if [ "$caddy_state" = "active" ]; then
        log "证书前置(Caddy): 运行中"
      else
        log "证书前置(Caddy): 未运行"
      fi
    fi
  else
    log "面板服务: (无 systemd, 无法查询)"
  fi
  if [ -f "$WGAIO_ROOT/config.json" ]; then
    local py out url
    py="$(find_python)"
    out="$("$py" -c 'import json,os,sys
d=json.load(open(sys.argv[1],encoding="utf-8"))
mode=str(d.get("tls_mode") or "").strip()
scheme="http" if mode == "off" else "https"
bind=str(d.get("panel_bind") or "")
host=d.get("tls_cn") or bind or "127.0.0.1"
port=d.get("panel_port") or 8443
path=str(d.get("panel_path") or "").strip().strip("/")
suffix=("/"+path+"/") if path else "/"
print("%s://%s:%s%s" % (scheme, host, port, suffix))
print("1" if (scheme == "http" and bind == "0.0.0.0") else "0")' "$WGAIO_ROOT/config.json")"
    url="$(printf '%s\n' "$out" | head -n 1)"
    log "面板地址: $url"
    log "只打开这一整条。只开端口会看到 404"
    if [ "$(printf '%s\n' "$out" | tail -n 1)" = "1" ]; then
      warn "面板绑在公网却没有证书(明文 HTTP), 令牌会明文过网; 腾出 80 或 443 后执行 wgaio cert"
    fi
  fi
  log "设备列表:"
  run_core user list || warn "设备列表读取失败(可能还没装完)"
}
