#!/usr/bin/env bash
# 安装主流程(骨架; T3 补全装机步骤)
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"
. "$ROOT/lib/wizard.sh"

cmd_install() {
  if [ "${1:-}" = "--wizard-only" ]; then
    run_wizard
    return 0
  fi
  die "install 主流程尚未实现(等 T3), 可先用 --wizard-only 体验向导"
}
