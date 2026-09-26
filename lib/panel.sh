#!/usr/bin/env bash
# 面板 systemd 单元与服务管理
: "${WGAIO_ROOT:=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export WGAIO_ROOT
# shellcheck source=lib/core.sh
. "$WGAIO_ROOT/lib/core.sh"

write_panel_unit() {  # 输出到 $WGAIO_UNIT_OUT 或 /etc/systemd/system/
  local dest="${WGAIO_UNIT_OUT:-/etc/systemd/system/wgaio-panel.service}"
  local root="${1:-${WGAIO_ROOT}}"
  cat > "$dest" <<EOF
[Unit]
Description=wgaio WireGuard panel
After=network.target wg-quick@wg0.service

[Service]
ExecStart=$(find_python) $root/lib/core.py --serve
WorkingDirectory=$root
Environment=WGAIO_BASE=$root
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
  # 注: systemd 不剥离路径引号(WorkingDirectory="..." 会被判非绝对路径),
  # 故本单元不支持含空格的安装路径。
}

install_panel_unit() {
  write_panel_unit "${1:-$WGAIO_ROOT}"
  # 测试模式: WGAIO_UNIT_OUT 已指定输出路径时跳过 systemctl
  [ -n "${WGAIO_UNIT_OUT:-}" ] && return 0
  systemctl daemon-reload || warn "systemd 重载失败"
  systemctl enable --now wgaio-panel \
    || warn "面板服务启动失败。安装会继续，稍后执行 wgaio panel start，日志用 wgaio logs"
}

cmd_panel() {
  command -v systemctl >/dev/null 2>&1 \
    || die "当前环境没有 systemd, 请在装有 systemd 的服务器上管理面板服务"
  case "${1:-}" in
    start)   systemctl start wgaio-panel ;;
    stop)    systemctl stop wgaio-panel ;;
    restart) systemctl restart wgaio-panel ;;
    status)  systemctl status wgaio-panel --no-pager ;;
    install) install_panel_unit ;;
    *) die "用法: wgaio panel start|stop|restart|status|install" 2 ;;
  esac
}
