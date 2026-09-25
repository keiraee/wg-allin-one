#!/usr/bin/env bash
# 安装主流程: 向导 → 目录/权限 → 文件就位 → 面板服务 → 安全组提示
# --dry-run: 只写 WGAIO_ROOT/_stage 暂存布局(测试/预览用), 不碰系统
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"
. "$ROOT/lib/wizard.sh"

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

  local dest="$WGAIO_ROOT"
  [ "$dry" -eq 1 ] && dest="$WGAIO_ROOT/_stage"

  install -d -m 700 "$dest/clients" "$dest/lib" "$dest/panel" "$dest/bin"
  stage_files "$dest"
  cp -f "$ROOT/config.json" "$dest/config.json"
  chmod 600 "$dest/config.json"

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
  log "=====================================================\n"
}
