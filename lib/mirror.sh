#!/usr/bin/env bash
# 系统包源换成国内镜像: 自动探测哪个镜像站能通, 再替换并备份原文件。
# 依赖装不上时安装流程会自动调一次, 也可以手动: wgaio mirror / wgaio mirror restore
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

CN_MIRROR_LIST="${CN_MIRROR_LIST:-https://mirrors.aliyun.com https://mirrors.cloud.tencent.com https://mirrors.tuna.tsinghua.edu.cn https://mirrors.ustc.edu.cn https://mirrors.huaweicloud.com}"
WGAIO_APT_SOURCES="${WGAIO_APT_SOURCES:-/etc/apt/sources.list}"
WGAIO_APT_PARTS="${WGAIO_APT_PARTS:-/etc/apt/sources.list.d}"
WGAIO_APK_REPOS="${WGAIO_APK_REPOS:-/etc/apk/repositories}"
WGAIO_OS_RELEASE="${WGAIO_OS_RELEASE:-/etc/os-release}"
WGAIO_ALPINE_RELEASE="${WGAIO_ALPINE_RELEASE:-/etc/alpine-release}"

os_id() {
  [ -r "$WGAIO_OS_RELEASE" ] || return 1
  awk -F= '$1=="ID"{gsub(/"/,"",$2); print $2; exit}' "$WGAIO_OS_RELEASE"
}

os_codename() {
  [ -r "$WGAIO_OS_RELEASE" ] || return 1
  local v
  v="$(awk -F= '$1=="VERSION_CODENAME"{gsub(/"/,"",$2); print $2; exit}' "$WGAIO_OS_RELEASE")"
  [ -n "$v" ] || v="$(awk -F= '$1=="UBUNTU_CODENAME"{gsub(/"/,"",$2); print $2; exit}' "$WGAIO_OS_RELEASE")"
  printf '%s' "$v"
}

cn_mirror_probe() {  # cn_mirror_probe <ubuntu|debian|alpine> <代号> → 第一个能通的镜像
  local kind="$1" codename="$2" m ver path
  command -v curl >/dev/null 2>&1 || return 1
  for m in ${CN_MIRROR_LIST}; do
    case "$kind" in
      ubuntu) path="/ubuntu/dists/${codename}/Release" ;;
      debian) path="/debian/dists/${codename}/Release" ;;
      alpine)
        ver="$(cut -d. -f1,2 "$WGAIO_ALPINE_RELEASE" 2>/dev/null)"
        [ -n "$ver" ] || return 1
        path="/alpine/v${ver}/main/x86_64/APKINDEX.tar.gz"
        ;;
      *) return 1 ;;
    esac
    if curl -fsSL --max-time 5 -o /dev/null "${m}${path}" 2>/dev/null; then
      printf '%s' "$m"
      return 0
    fi
  done
  return 1
}

apt_sources_to_cn() {  # Debian / Ubuntu
  local id codename mirror base sec
  id="$(os_id)" || { warn "读不到 /etc/os-release, 不改 apt 源"; return 1; }
  codename="$(os_codename)"
  [ -n "$codename" ] || { warn "读不到发行版代号, 不改 apt 源"; return 1; }
  case "$id" in
    ubuntu|debian) ;;
    *) warn "不是 Debian/Ubuntu(${id}), 不改 apt 源"; return 1 ;;
  esac
  mirror="$(cn_mirror_probe "$id" "$codename")" \
    || { warn "国内镜像站都不通, 不改 apt 源"; return 1; }
  base="${mirror}/${id}"
  sec="${base}"
  [ "$id" = "debian" ] && sec="${mirror}/debian-security"
  # 备份只做一次, 免得把换过之后的源当成原始源
  if [ ! -e "${WGAIO_APT_SOURCES}.wgaio.bak" ] && [ -f "$WGAIO_APT_SOURCES" ]; then
    cp -a "$WGAIO_APT_SOURCES" "${WGAIO_APT_SOURCES}.wgaio.bak" 2>/dev/null || true
  fi
  if [ "$id" = "ubuntu" ]; then
    cat > "$WGAIO_APT_SOURCES" <<EOF
deb ${base}/ ${codename} main restricted universe multiverse
deb ${base}/ ${codename}-updates main restricted universe multiverse
deb ${base}/ ${codename}-backports main restricted universe multiverse
deb ${sec}/ ${codename}-security main restricted universe multiverse
EOF
  else
    cat > "$WGAIO_APT_SOURCES" <<EOF
deb ${base}/ ${codename} main contrib non-free non-free-firmware
deb ${base}/ ${codename}-updates main contrib non-free non-free-firmware
deb ${sec}/ ${codename}-security main contrib non-free non-free-firmware
EOF
  fi
  # 原有的其它源文件挪开, 免得新旧混着用
  if [ -d "$WGAIO_APT_PARTS" ]; then
    mkdir -p "${WGAIO_APT_PARTS}.wgaio-saved"
    find "$WGAIO_APT_PARTS" -maxdepth 1 -type f \( -name '*.list' -o -name '*.sources' \) \
      -exec mv {} "${WGAIO_APT_PARTS}.wgaio-saved/" \; 2>/dev/null || true
  fi
  log "apt 源已换成 $mirror(原文件备份在 ${WGAIO_APT_SOURCES}.wgaio.bak, 还原: wgaio mirror restore)"
}

apk_repos_to_cn() {  # Alpine
  local mirror ver
  ver="$(cut -d. -f1,2 "$WGAIO_ALPINE_RELEASE" 2>/dev/null)"
  [ -n "$ver" ] || { warn "读不到 Alpine 版本, 不改 apk 源"; return 1; }
  mirror="$(cn_mirror_probe alpine "$ver")" || { warn "国内镜像站都不通, 不改 apk 源"; return 1; }
  if [ ! -e "${WGAIO_APK_REPOS}.wgaio.bak" ] && [ -f "$WGAIO_APK_REPOS" ]; then
    cp -a "$WGAIO_APK_REPOS" "${WGAIO_APK_REPOS}.wgaio.bak" 2>/dev/null || true
  fi
  cat > "$WGAIO_APK_REPOS" <<EOF
${mirror}/alpine/v${ver}/main
${mirror}/alpine/v${ver}/community
EOF
  log "apk 源已换成 $mirror(备份: ${WGAIO_APK_REPOS}.wgaio.bak)"
}

pkg_mirror_switch() {
  if command -v apt-get >/dev/null 2>&1; then
    apt_sources_to_cn
  elif command -v apk >/dev/null 2>&1; then
    apk_repos_to_cn
  else
    warn "这个发行版($(os_id 2>/dev/null || echo 未知))的包源没做自动替换, 请手动换国内镜像"
    return 1
  fi
}

pkg_mirror_restore() {
  local done_any=0
  if [ -f "${WGAIO_APT_SOURCES}.wgaio.bak" ]; then
    mv -f "${WGAIO_APT_SOURCES}.wgaio.bak" "$WGAIO_APT_SOURCES" && done_any=1
  fi
  if [ -d "${WGAIO_APT_PARTS}.wgaio-saved" ]; then
    mv "${WGAIO_APT_PARTS}.wgaio-saved"/* "$WGAIO_APT_PARTS"/ 2>/dev/null || true
    rmdir "${WGAIO_APT_PARTS}.wgaio-saved" 2>/dev/null || true
    done_any=1
  fi
  if [ -f "${WGAIO_APK_REPOS}.wgaio.bak" ]; then
    mv -f "${WGAIO_APK_REPOS}.wgaio.bak" "$WGAIO_APK_REPOS" && done_any=1
  fi
  if [ "$done_any" = "1" ]; then
    log "已还原原来的包源"
  else
    log "没有找到本工具做的备份, 无需还原"
  fi
}

cmd_mirror() {
  case "${1:-}" in
    restore) pkg_mirror_restore ;;
    ""|switch) pkg_mirror_switch ;;
    *) die "用法: wgaio mirror [restore]" 2 ;;
  esac
}
