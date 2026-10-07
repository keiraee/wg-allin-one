#!/usr/bin/env bash
# wgaio 公共函数: 日志/报错/依赖/Python 定位
# 版本号只维护在 wgaio.sh 的 VERSION=，此处不重复声明。
set -Eeuo pipefail

wgaio_root() { cd "$(dirname "${BASH_SOURCE[1]}")" && pwd; }

log()  { printf '[wgaio] %s\n' "$*"; }
warn() { printf '[wgaio] 警告: %s\n' "$*" >&2; }
die()  {
  # 升级下载到一半失败时，调用方把临时目录放在这里，退出前清掉。
  if [ -n "${WGAIO_CLEAN_DIR:-}" ]; then
    rm -rf "$WGAIO_CLEAN_DIR"
    unset WGAIO_CLEAN_DIR
  fi
  printf '[wgaio] 错误: %s\n' "$1" >&2
  exit "${2:-1}"
}

find_python() {
  local candidates=("python3" "python") cmd
  for cmd in "${candidates[@]}"; do
    if command -v "$cmd" >/dev/null 2>&1 && "$cmd" -c "import sys" 2>/dev/null; then
      command -v "$cmd"
      return 0
    fi
  done
  die "未找到可用的 python3/python, 请先安装 Python 3.9+"
}

core_py() {
  local root="${WGAIO_ROOT:-$(wgaio_root)}"
  printf '%s/lib/core.py' "$root"
}

cfg_get() {  # cfg_get <config.json> <键> [默认值] → stdout
  local cfg="$1" key="$2" def="${3:-}" py v
  py="$(find_python)"
  v="$("$py" -c 'import json,sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    d = {}
v = d.get(sys.argv[2])
print("" if v is None else v)' "$cfg" "$key" 2>/dev/null || true)"
  printf '%s' "${v:-$def}"
}

tcp_port_busy() {  # 该 TCP 端口是否已被占用
  local port="$1" hits
  [ "${WGAIO_SKIP_NET_CHECK:-}" = "1" ] && return 1
  case ",${WGAIO_BUSY_TCP:-}," in *",$port,"*) return 0 ;; esac
  command -v ss >/dev/null 2>&1 || return 1
  hits="$(ss -H -tln "sport = :$port" 2>/dev/null || true)"
  [ -n "$hits" ]
}

udp_port_busy() {  # 该 UDP 端口是否已被占用
  local port="$1" hits
  [ "${WGAIO_SKIP_NET_CHECK:-}" = "1" ] && return 1
  case ",${WGAIO_BUSY_UDP:-}," in *",$port,"*) return 0 ;; esac
  command -v ss >/dev/null 2>&1 || return 1
  hits="$(ss -H -uln "sport = :$port" 2>/dev/null || true)"
  [ -n "$hits" ]
}

WGAIO_SHORT_CMD_MARK="wgaio-short-cmd"

wgaio_repo_url() {
  printf 'https://github.com/%s' "${WGAIO_REPO:-keiraee/wg-allin-one}"
}

print_welcome_banner() {
  printf '\033[1;36m'
  cat <<'EOF'

 __      __  _____          _____ _____
 \ \    / / / ____|   /\   |_   _/ __ \
  \ \  / / | |  __   /  \    | || |  | |
   \ \/ /  | | |_ | / /\ \   | || |  | |
    \  /   | |__| |/ ____ \ _| || |__| |
     \/     \_____/_/    \_\_____\____/

EOF
  printf '\033[0m'
  printf 'Welcome WGAIO\n'
  printf '作者 @keiraee\n'
  printf '开源地址: %s\n\n' "$(wgaio_repo_url)"
}

write_short_wg_wrapper() {  # <安装根> <输出路径>
  local dest="$1" out="$2"
  cat > "$out" <<EOF
#!/usr/bin/env bash
# $WGAIO_SHORT_CMD_MARK: wgaio 的短命令 wg。
# 不跟 WireGuard 抢: 真正的 wg 在 /usr/bin/wg, WireGuard 自己的子命令原样转交,
# 其余(包括不带参数)交给 wgaio。
real="\${WGAIO_REAL_WG:-}"
if [ -z "\$real" ]; then
  for c in /usr/bin/wg /bin/wg /usr/sbin/wg; do
    [ -x "\$c" ] && { real="\$c"; break; }
  done
fi
case "\${1:-}" in
  show|showconf|set|setconf|addconf|syncconf|genkey|genpsk|pubkey|version|help|--help|-h|--version)
    if [ -n "\$real" ] && [ -x "\$real" ]; then exec "\$real" "\$@"; fi
    printf '[wgaio] 找不到真正的 WireGuard wg, 请先安装 wireguard-tools\n' >&2
    exit 127
    ;;
esac
exec bash '$dest/wgaio.sh' "\$@"
EOF
  chmod 755 "$out"
}

run_core() {
  local py; py="$(find_python)"
  # 核心读 WGAIO_BASE。已显式指定时保留(测试沙箱)，否则跟安装目录走。
  if [ -z "${WGAIO_BASE:-}" ]; then
    export WGAIO_BASE="${WGAIO_ROOT:-$(wgaio_root)}"
  fi
  "$py" "$(core_py)" "$@"
}
