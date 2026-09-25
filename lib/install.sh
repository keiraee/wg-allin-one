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

  local py wg_port panel_port
  py="$(find_python)"
  wg_port="$("$py" -c 'import json,sys;print(json.load(open(sys.argv[1],encoding="utf-8"))["wg_port"])' "$dest/config.json")"
  panel_port="$("$py" -c 'import json,sys;print(json.load(open(sys.argv[1],encoding="utf-8"))["panel_port"])' "$dest/config.json")"

  printf '\n'
  log "====================================================="
  log " 重要: 请到云控制台安全组放行 UDP %s 端口!" "$wg_port"
  log " 面板: http://<VPN隧道地址>:%s (令牌见上方)" "$panel_port"
  log "=====================================================\n"
}
