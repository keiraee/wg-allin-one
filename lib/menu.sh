#!/usr/bin/env bash
# 交互菜单。直接执行 wgaio（终端里、不带子命令）进入。
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"
. "$ROOT/lib/upgrade.sh"
. "$ROOT/lib/user.sh"
. "$ROOT/lib/panel.sh"
. "$ROOT/lib/status.sh"
. "$ROOT/lib/logs.sh"
. "$ROOT/lib/uninstall.sh"
. "$ROOT/lib/install.sh"
. "$ROOT/lib/backup.sh"

menu_read() {
  local prompt="$1" ans=""
  if [ -t 0 ]; then
    IFS= read -e -r -p "$prompt" ans || ans=""
  else
    IFS= read -r -p "$prompt" ans || ans=""
  fi
  ans="${ans#"${ans%%[![:space:]]*}"}"
  ans="${ans%"${ans##*[![:space:]]}"}"
  printf '%s' "$ans"
}

menu_run() {
  ( "$@" ) || true
}

show_menu() {
  local ver track sha sum
  ver="$(installed_version)"
  track="$(read_track WGAIO_TRACK_REF)"
  sha="$(read_track WGAIO_REPO_SHA)"
  sum="$(read_track WGAIO_MODULES_SHA)"
  [ -n "$track" ] || track="未记录"
  [ -t 1 ] && clear 2>/dev/null || true
  cat <<EOF
wgaio ${ver}  管理菜单
轨道: ${track}    哈希: $(hash_label "$sum" "$sha")

── 升级 ──────────────────────────
  1) 升级稳定版（GitHub Release 最新 tag）
  2) 抢先试用 main（记住这条轨道）
  3) 查看版本、轨道和哈希

── 设备 ──────────────────────────
  4) 状态总览
  5) 设备列表
  6) 添加设备
  7) 删除设备
  8) 修改设备
  9) 导出设备配置（含私钥，勿外传）
 10) 停用或启用设备
 11) 更换设备密钥（IP 不变，旧配置作废）

── 面板 ──────────────────────────
 12) 启动面板
 13) 停止面板
 14) 重启面板
 15) 面板状态
 16) 面板日志
 17) 重新申请 HTTPS 证书

── 其他 ──────────────────────────
 18) 备份隧道配置
 19) 从备份恢复
 20) 回滚最近快照（只恢复程序，不动配置）
 21) 卸载
 99) 退出

EOF
}

menu_upgrade_stable() {
  WGAIO_REF=latest
  export WGAIO_REF
  cmd_upgrade
}

menu_upgrade_main() {
  WGAIO_REF=main
  export WGAIO_REF
  cmd_upgrade
}

menu_track_show() {
  log "版本: $(installed_version)"
  log "轨道: $(read_track WGAIO_TRACK_REF)"
  log "提交: $(read_track WGAIO_REPO_SHA)"
  log "模块哈希: $(read_track WGAIO_MODULES_SHA)"
  log "普通升级跟 GitHub 最新正式版。选过抢先试用后，本机会记住 main，之后不会被正式版带走。"
  log "切回稳定版: 选 1，或执行 WGAIO_REF=latest wgaio upgrade"
}

menu_add_user() {
  local name mode choice
  name="$(menu_read "设备名(字母/数字/_/-，1-15 位): ")"
  [ -n "$name" ] || { log "已取消"; return 0; }
  echo "  1) 分流(只进 VPN/内网)"
  echo "  2) 全隧道(全部流量走服务器)"
  choice="$(menu_read "流量模式 [1]: ")"
  case "${choice:-1}" in
    1) mode="split" ;;
    2) mode="full" ;;
    *) log "无效选择"; return 0 ;;
  esac
  run_core user add "$name" --mode "$mode"
}

menu_del_user() {
  local name force
  name="$(menu_read "要删除的设备名: ")"
  [ -n "$name" ] || { log "已取消"; return 0; }
  force="$(menu_read "如果它是内网网关，输入 force 确认，否则直接回车: ")"
  if [ "$force" = "force" ]; then
    run_core user del "$name" --force
  else
    run_core user del "$name"
  fi
}

menu_edit_user() {
  local name new_name ip dns ka mode routes choice
  local -a args
  name="$(menu_read "要修改的设备名: ")"
  [ -n "$name" ] || { log "已取消"; return 0; }
  new_name="$(menu_read "新名字(不改直接回车): ")"
  ip="$(menu_read "新 IP(不改直接回车): ")"
  dns="$(menu_read "新 DNS(不改直接回车, 多个用逗号): ")"
  ka="$(menu_read "保活秒数 0-120(不改直接回车): ")"
  routes="$(menu_read "网关路由段(逗号分隔; 清空输入 - ; 不改直接回车): ")"
  echo "  1) 分流  2) 全隧道  直接回车=不改"
  choice="$(menu_read "流量模式: ")"
  args=(user edit "$name")
  [ -n "$new_name" ] && args+=(--rename "$new_name")
  [ -n "$ip" ] && args+=(--ip "$ip")
  [ -n "$dns" ] && args+=(--dns "$dns")
  [ -n "$ka" ] && args+=(--ka "$ka")
  if [ "$routes" = "-" ]; then
    args+=(--routes "")
  elif [ -n "$routes" ]; then
    args+=(--routes "$routes")
  fi
  case "$choice" in
    1) mode="split" ;;
    2) mode="full" ;;
    "") mode="" ;;
    *) log "无效选择"; return 0 ;;
  esac
  [ -n "$mode" ] && args+=(--mode "$mode")
  run_core "${args[@]}"
}

menu_show_user() {
  local name
  name="$(menu_read "要导出的设备名: ")"
  [ -n "$name" ] || { log "已取消"; return 0; }
  warn "下面是客户端配置，含私钥。不要贴到聊天或日志里。"
  run_core user show "$name"
}

menu_active_user() {
  local name act force
  name="$(menu_read "设备名: ")"
  [ -n "$name" ] || { log "已取消"; return 0; }
  act="$(menu_read "输入 enable 启用，其他或直接回车则停用: ")"
  if [ "$act" = "enable" ]; then
    run_core user enable "$name"
    return 0
  fi
  force="$(menu_read "如果它是内网网关，输入 force 确认，否则直接回车: ")"
  if [ "$force" = "force" ]; then
    run_core user disable "$name" --force
  else
    run_core user disable "$name"
  fi
}

menu_rotate_user() {
  local name ok
  name="$(menu_read "要更换密钥的设备名: ")"
  [ -n "$name" ] || { log "已取消"; return 0; }
  warn "换密钥后旧的 .conf 和二维码都不能再用，手机要重新导入。IP 不会变。"
  ok="$(menu_read "确认请输入 yes: ")"
  [ "$ok" = "yes" ] || { log "已取消"; return 0; }
  run_core user rotate "$name"
  warn "请重新导出这份配置。不要把私钥贴到聊天里。"
}

menu_restore() {
  local path ok
  path="$(menu_read "备份文件路径: ")"
  [ -n "$path" ] || { log "已取消"; return 0; }
  warn "会用这份备份覆盖 config.json、clients 和 wg0.conf。"
  ok="$(menu_read "确认请输入 yes: ")"
  [ "$ok" = "yes" ] || { log "已取消"; return 0; }
  run_core backup restore "$path"
}

menu_logs() {
  local n
  n="$(menu_read "看最近多少行 [100]: ")"
  cmd_logs "${n:-100}"
}

menu_uninstall() {
  local ok
  ok="$(menu_read "确认卸载请输入 yes: ")"
  [ "$ok" = "yes" ] || { log "已取消"; return 0; }
  cmd_uninstall
  : > "${WGAIO_ROOT}/.wgaio-uninstalled" || true
}

cmd_menu() {
  local choice
  export WGAIO_IN_MENU=1
  if [ "$(id -u)" -ne 0 ]; then
    warn "当前不是 root，改配置和重启服务会失败。请用: sudo wgaio"
  fi
  while true; do
    show_menu
    choice="$(menu_read "请选择: ")"
    case "$choice" in
      1) menu_run menu_upgrade_stable ;;
      2) menu_run menu_upgrade_main ;;
      3) menu_track_show ;;
      4) menu_run cmd_status ;;
      5) menu_run run_core user list ;;
      6) menu_run menu_add_user ;;
      7) menu_run menu_del_user ;;
      8) menu_run menu_edit_user ;;
      9) menu_run menu_show_user ;;
      10) menu_run menu_active_user ;;
      11) menu_run menu_rotate_user ;;
      12) menu_run cmd_panel start ;;
      13) menu_run cmd_panel stop ;;
      14) menu_run cmd_panel restart ;;
      15) menu_run cmd_panel status ;;
      16) menu_run menu_logs ;;
      17) menu_run cmd_cert ;;
      18) menu_run cmd_backup ;;
      19) menu_run menu_restore ;;
      20) menu_run cmd_rollback ;;
      21) menu_run menu_uninstall ;;
      99) exit 0 ;;
      *) log "无效选择" ;;
    esac
    echo
    menu_read "按回车返回菜单..." >/dev/null || true
    if [ -f "${WGAIO_ROOT}/.wgaio-uninstalled" ]; then
      rm -f "${WGAIO_ROOT}/.wgaio-uninstalled"
      log "已卸载, 菜单退出"
      exit 0
    fi
    if [ -f "${WGAIO_ROOT}/.wgaio-menu-reload" ]; then
      rm -f "${WGAIO_ROOT}/.wgaio-menu-reload"
      exec bash "$ROOT/wgaio.sh"
    fi
  done
}
