#!/usr/bin/env bash
# 数据包：config.json、clients 和 wg0.conf。和升级快照不是一回事。
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

cmd_backup() {
  run_core backup "$@"
}
