#!/usr/bin/env bash
# 设备管理包装。
# 安全约定(不可违背): `user show` 的 stdout 含客户端私钥,
# 任何调用方(含计划外脚本)禁止把它重定向进日志/文件后长期留存。
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

cmd_user() {
  case "${1:-}" in
    show)
      shift
      run_core user show "$@"   # stdout = .conf 内容(含私钥), 勿记录
      ;;
    *)
      run_core user "$@"
      ;;
  esac
}
