#!/usr/bin/env bash
# 面板 TLS: 由 wgaio 自己的 Caddy 实例申请并续期证书(和 HY2 同款做法)。
#
# 为什么不直接用系统的 caddy.service / /etc/caddy/Caddyfile:
#   一台机器上可能已经装了 HY2, 它也用 Caddy。共用一套配置和服务会互相覆盖,
#   所以 wgaio 只复用 caddy 二进制, 配置/服务/数据目录全部独立:
#     /etc/wgaio/Caddyfile      wgaio 自己的站点
#     wgaio-caddy.service       wgaio 自己的服务
#     /var/lib/wgaio-caddy      自己的证书与状态
#
# 端口账:
#   面板对外 HTTPS 默认 8443, 不占 443, 不和同机 HY2 的面板抢;
#   ACME 挑战只能在 80(HTTP-01) 或 443(TLS-ALPN-01) 里挑一个空闲的,
#   两个都被占就退回自签(tls internal), 面板照常能用。
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

WGAIO_CADDY_DIR="${WGAIO_CADDY_DIR:-/etc/wgaio}"
WGAIO_CADDY_DATA="${WGAIO_CADDY_DATA:-/var/lib/wgaio-caddy}"
WGAIO_CADDY_LOG="${WGAIO_CADDY_LOG:-/var/log/wgaio}"
WGAIO_CADDY_UNIT="${WGAIO_CADDY_UNIT:-/etc/systemd/system/wgaio-caddy.service}"
WGAIO_CADDY_VERSION="${WGAIO_CADDY_VERSION:-v2.11.7}"

caddy_bin() {  # 找一个可用的 caddy; 找不到返回 1
  local c
  for c in "${WGAIO_CADDY_BIN:-}" "$(command -v caddy 2>/dev/null || true)" \
           /usr/local/bin/caddy /usr/bin/caddy /usr/sbin/caddy; do
    if [ -n "$c" ] && [ -x "$c" ]; then printf '%s' "$c"; return 0; fi
  done
  return 1
}

install_caddy_release() {  # 官方 release 二进制, 带 SHA256 校验
  local ver arch asset base tmp expected actual
  ver="${WGAIO_CADDY_VERSION#v}"
  case "$(uname -m)" in
    x86_64|amd64) arch="amd64" ;;
    aarch64|arm64) arch="arm64" ;;
    armv7l) arch="armv7" ;;
    *) warn "不认识的架构 $(uname -m), 请手动安装 caddy"; return 1 ;;
  esac
  command -v curl >/dev/null 2>&1 || { warn "需要 curl 才能下载 Caddy"; return 1; }
  command -v sha256sum >/dev/null 2>&1 || { warn "需要 sha256sum 才能校验 Caddy"; return 1; }
  asset="caddy_${ver}_linux_${arch}.tar.gz"
  base="https://github.com/caddyserver/caddy/releases/download/v${ver}"
  tmp="$(mktemp -d)"
  # Caddy 包 18MB 上下, 国内直连和镜像都可能很慢: 断点续传 + 放宽超时(900s),
  # 换镜像也能接着下(内容一样), 下完仍然按官方 checksums 校验。
  log "下载 Caddy(约 18MB, 国内可能比较慢, 断了会接着下)..."
  local u got=0
  while IFS= read -r u; do
    [ "$u" = "${base}/${asset}" ] || warn "直连失败, 改走镜像: $u"
    if curl -fL -C - --retry 3 --retry-delay 2 --connect-timeout 15 --max-time 900 \
        -H 'Cache-Control: no-cache' -H 'Pragma: no-cache' \
        "$u" -o "${tmp}/${asset}"; then
      got=1
      break
    fi
  done < <(github_urls "${base}/${asset}")
  if [ "$got" -ne 1 ]; then
    rm -rf "$tmp"; warn "下载 Caddy 失败。${MIRROR_HINT}"; return 1
  fi
  if ! github_curl "${base}/caddy_${ver}_checksums.txt" -o "${tmp}/sums.txt"; then
    rm -rf "$tmp"; warn "下载 Caddy 校验文件失败。${MIRROR_HINT}"; return 1
  fi
  expected="$(awk -v a="$asset" '$2==a {print $1; exit}' "${tmp}/sums.txt")"
  actual="$(sha256sum "${tmp}/${asset}" | awk '{print $1}')"
  if [ -z "$expected" ] || [ "$expected" != "$actual" ]; then
    # 删掉半成品, 免得下次续传把坏文件接着拼
    rm -rf "$tmp"; warn "Caddy 校验失败(镜像可能被篡改或缓存坏了), 换一个镜像再试: ${MIRROR_HINT}"; return 1
  fi
  tar xzf "${tmp}/${asset}" -C "$tmp" caddy || { rm -rf "$tmp"; return 1; }
  install -m 0755 "${tmp}/caddy" /usr/local/bin/caddy || { rm -rf "$tmp"; return 1; }
  rm -rf "$tmp"
  caddy_bin
}

install_caddy_bin() {  # 复用已有的 caddy; 没有才装
  local bin
  if bin="$(caddy_bin)"; then printf '%s' "$bin"; return 0; fi
  log "安装 Caddy(面板 HTTPS 用)..."
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get install -y -qq caddy >/dev/null 2>&1 || true
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y caddy >/dev/null 2>&1 || true
  elif command -v yum >/dev/null 2>&1; then
    yum install -y caddy >/dev/null 2>&1 || true
  elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache caddy >/dev/null 2>&1 || true
  fi
  if bin="$(caddy_bin)"; then printf '%s' "$bin"; return 0; fi
  install_caddy_release
}

migrate_config_for_caddy() {  # 老配置(没有 panel_backend_port)迁移到 Caddy 前置
  local cfg="$1" old_port new_port bind cn mode
  [ -f "$cfg" ] || return 0
  [ -n "$(cfg_get "$cfg" panel_backend_port)" ] && return 0
  old_port="$(cfg_get "$cfg" panel_port 8888)"
  bind="$(cfg_get "$cfg" panel_bind)"
  cn="$(cfg_get "$cfg" tls_cn)"
  mode="$(cfg_get "$cfg" tls_mode)"
  [ -n "$cn" ] || cn="$bind"
  case "$mode" in
    acme) ;;                    # 公网正式证书, 继续
    self) mode="internal" ;;    # 老自签 → Caddy 自签
    *) mode="off" ;;            # 老内网明文面板 / 老公网明文, 维持不变
  esac
  new_port=8443
  [ "$new_port" = "$old_port" ] && new_port=8444
  "$(find_python)" - "$cfg" "$old_port" "$new_port" "$cn" "$mode" <<'PY'
import json, sys
p, backend, port, cn, mode = sys.argv[1:6]
with open(p, encoding="utf-8") as f:
    d = json.load(f)
d["panel_backend_port"] = int(backend)
d["panel_port"] = int(port)
if cn:
    d["tls_cn"] = cn
d["tls_mode"] = mode
d["tls_cert"] = ""
d["tls_key"] = ""
with open(p, "w", encoding="utf-8") as f:
    f.write(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
PY
  chmod 600 "$cfg" 2>/dev/null || true
  log "老配置已改成 Caddy 前置: 对外端口 $old_port → $new_port, 后端留在本机 $old_port"
  return 0
}

fallback_self_signed() {  # 没有 Caddy 时的兜底: openssl 自签 + Python 面板直接对外提供 HTTPS
  local cfg="$1" domain="$2" port="$3" root cert key
  root="$(dirname "$cfg")"
  cert="$root/certs/wgaio.crt"
  key="$root/certs/wgaio.key"
  mkdir -p "$root/certs"
  if command -v openssl >/dev/null 2>&1; then
    rm -f "$cert" "$key"
    openssl req -x509 -newkey rsa:2048 -keyout "$key" -out "$cert" -days 3650 -nodes \
      -subj "/CN=${domain}" -addext "subjectAltName=DNS:${domain}" 2>/dev/null \
      || openssl req -x509 -newkey rsa:2048 -keyout "$key" -out "$cert" -days 3650 -nodes \
           -subj "/CN=${domain}" 2>/dev/null || true
    chmod 600 "$key" "$cert" 2>/dev/null || true
  fi
  "$(find_python)" - "$cfg" "$cert" "$key" "$port" <<'PY'
import json, sys
p, cert, key, port = sys.argv[1:5]
with open(p, encoding="utf-8") as f:
    d = json.load(f)
# 清掉内部端口: Python 面板直接对外监听, 不再经 Caddy
d["panel_backend_port"] = ""
d["panel_port"] = int(port)
if cert:
    d["tls_cert"] = cert
    d["tls_key"] = key
    d["tls_mode"] = "self"
with open(p, "w", encoding="utf-8") as f:
    f.write(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
PY
  chmod 600 "$cfg" 2>/dev/null || true
  log "面板已改为直接提供 HTTPS(自签): https://${domain}:${port}/"
  warn "等能连上 GitHub 了, 重新执行 wgaio install 会换成 Caddy 自动申请正式证书"
}

pick_challenge() {  # http / tls-alpn / 空(两个端口都被占)
  if ! tcp_port_busy 80; then printf 'http'; return 0; fi
  if ! tcp_port_busy 443; then printf 'tls-alpn'; return 0; fi
  printf ''
}

render_caddyfile() {  # render_caddyfile <域名> <面板端口> <后端端口> <模式> <挑战> <输出> [绑定地址]
  local domain="$1" port="$2" backend="$3" mode="$4" challenge="$5" out="$6" bind_addr="${7:-0.0.0.0}"
  local addr
  case "$mode" in
    off) addr="http://${domain}:${port}" ;;
    *)
      case "$domain" in
        # 纯 IP 站点要显式写 https://, 否则 Caddy 会当成域名去申请证书
        [0-9]*.[0-9]*.[0-9]*.[0-9]*) addr="https://${domain}:${port}" ;;
        *) addr="${domain}:${port}" ;;
      esac
      ;;
  esac
  {
    printf '{\n'
    printf '\tadmin off\n'
    # 纯 IP 站点: 客户端(浏览器/curl)对 IP 不发 SNI, 不指定默认证书会直接 internal error
    if [ "$mode" != "off" ]; then
      case "$domain" in
        [0-9]*.[0-9]*.[0-9]*.[0-9]*) printf '\tdefault_sni %s\n' "$domain" ;;
      esac
    fi
    # 面板不在 443, 不需要 HTTP->HTTPS 跳转, 也就不会去占 80
    printf '\tauto_https disable_redirects\n'
    if [ "$mode" != "off" ]; then
      printf '\tstorage file_system {\n\t\troot %s\n\t}\n' "$WGAIO_CADDY_DATA"
    fi
    printf '}\n\n'
    printf '%s {\n' "$addr"
    # 仅 VPN 内时只绑内网地址, 不要顺手暴露到公网
    if [ -n "$bind_addr" ] && [ "$bind_addr" != "0.0.0.0" ]; then
      printf '\tbind %s\n' "$bind_addr"
    fi
    case "$mode" in
      internal) printf '\ttls internal\n' ;;
      off) : ;;
      *)
        printf '\ttls {\n\t\tissuer acme {\n'
        if [ "$challenge" = "tls-alpn" ]; then
          printf '\t\t\tdisable_http_challenge\n'
        else
          printf '\t\t\tdisable_tlsalpn_challenge\n'
        fi
        printf '\t\t}\n\t}\n'
        ;;
    esac
    printf '\tencode zstd gzip\n'
    printf '\tlog {\n\t\toutput file %s/caddy-access.log {\n\t\t\troll_size 10mb\n\t\t\troll_keep 3\n\t\t}\n\t\tformat json\n\t}\n' "$WGAIO_CADDY_LOG"
    printf '\treverse_proxy 127.0.0.1:%s {\n\t\theader_up X-Real-IP {remote_host}\n\t}\n' "$backend"
    printf '}\n'
  } > "$out"
}

write_wgaio_caddy_unit() {  # write_wgaio_caddy_unit <caddy 路径> [输出路径]
  local bin="$1" dest="${2:-$WGAIO_CADDY_UNIT}"
  cat > "$dest" <<EOF
[Unit]
Description=wgaio panel HTTPS front (Caddy)
After=network-online.target wgaio-panel.service
Wants=network-online.target wgaio-panel.service

[Service]
Type=simple
Environment=XDG_DATA_HOME=${WGAIO_CADDY_DATA}
Environment=XDG_CONFIG_HOME=${WGAIO_CADDY_DATA}
ExecStart=${bin} run --config ${WGAIO_CADDY_DIR}/Caddyfile --adapter caddyfile
ExecReload=${bin} reload --config ${WGAIO_CADDY_DIR}/Caddyfile --adapter caddyfile
Restart=always
RestartSec=5
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF
}

install_wgaio_caddy() {  # install_wgaio_caddy <config.json>
  local cfg="$1" bin domain port backend mode challenge
  [ -f "$cfg" ] || die "找不到配置: $cfg"
  domain="$(cfg_get "$cfg" tls_cn)"
  [ -n "$domain" ] || die "配置里没有 tls_cn(面板域名), 无法申请证书"
  port="$(cfg_get "$cfg" panel_port 8443)"
  backend="$(cfg_get "$cfg" panel_backend_port 8888)"
  mode="$(cfg_get "$cfg" tls_mode acme)"
  [ "$mode" = "self" ] && mode="internal"
  case "$mode" in
    acme|internal|off) ;;
    *) die "tls_mode 只能是 acme、internal 或 off(当前: $mode)" ;;
  esac
  challenge=""
  if [ "$mode" = "acme" ]; then
    challenge="$(pick_challenge)"
    if [ -z "$challenge" ]; then
      warn "80 和 443 都被别的程序占用了, 正式证书申请不到, 先退回自签证书"
      warn "腾出 80 或 443 之后再执行: wgaio cert"
      mode="internal"
    fi
  fi
  bin="$(install_caddy_bin)" || bin=""
  if [ -z "$bin" ]; then
    warn "Caddy 装不上(GitHub 太慢/连不上), 先用自签证书让面板直接提供 HTTPS"
    fallback_self_signed "$cfg" "$domain" "$port"
    return 0
  fi
  install -d -m 755 "$WGAIO_CADDY_DIR" "$WGAIO_CADDY_LOG"
  install -d -m 700 "$WGAIO_CADDY_DATA"
  render_caddyfile "$domain" "$port" "$backend" "$mode" "$challenge" \
    "$WGAIO_CADDY_DIR/Caddyfile" "$(cfg_get "$cfg" panel_bind)"
  if ! "$bin" validate --config "$WGAIO_CADDY_DIR/Caddyfile" --adapter caddyfile >/dev/null 2>&1; then
    die "生成的 Caddyfile 校验失败: $WGAIO_CADDY_DIR/Caddyfile"
  fi
  if [ -n "${WGAIO_CADDY_UNIT_OUT:-}" ]; then
    write_wgaio_caddy_unit "$bin" "$WGAIO_CADDY_UNIT_OUT"
    log "已写出 Caddy 单元(测试模式): $WGAIO_CADDY_UNIT_OUT"
    return 0
  fi
  write_wgaio_caddy_unit "$bin"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload || warn "systemd 重载失败"
    systemctl enable wgaio-caddy >/dev/null 2>&1 || true
    systemctl restart wgaio-caddy \
      || warn "wgaio-caddy 启动失败, 用 wgaio logs caddy 查看"
  fi
  case "$mode" in
    acme) log "面板 TLS: Caddy 自动申请正式证书(域名 $domain, 端口 $port, 挑战 $challenge)" ;;
    internal) log "面板 TLS: Caddy 自签证书(域名 $domain, 端口 $port, 浏览器会提示不受信)" ;;
    off) log "面板 TLS: 已关闭(明文, 域名 $domain, 端口 $port)" ;;
  esac
}
