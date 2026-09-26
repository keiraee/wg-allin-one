#!/usr/bin/env bash
# 升级: 下载 → SHA256SUMS 校验 → 快照 → 覆盖程序文件。校验失败一律拒绝。
# 不覆盖 config.json 与 clients/。快照包含 wgaio.sh，回滚后入口版本与套件一致，
# 避免入口发现版本不一致后又从 main 重新下载、把回滚盖掉。
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

check_sha256() {  # check_sha256 <目录>; 该目录须有 SHA256SUMS
  local dir="${1:-$WGAIO_ROOT}"
  [ -f "$dir/SHA256SUMS" ] || die "缺少 SHA256SUMS, 拒绝升级(来源不可信)"
  ( cd "$dir" && sha256sum -c SHA256SUMS >/dev/null 2>&1 ) \
    || die "SHA256SUMS 校验失败, 文件被改动或下载损坏, 已中止"
  log "SHA256SUMS 校验通过"
}

snapshot() {
  local dir="${1:-$WGAIO_ROOT}" ts item
  local -a files=()
  ts="$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$dir/snapshots"
  for item in wgaio.sh SHA256SUMS config.json lib panel bin .wgaio-track; do
    [ -e "$dir/$item" ] && files+=("$item")
  done
  [ "${#files[@]}" -gt 0 ] || die "没有可快照的文件, 升级中止"
  tar czf "$dir/snapshots/wgaio-$ts.tar.gz" -C "$dir" \
    --exclude=snapshots --exclude=clients "${files[@]}" \
    || die "快照创建失败, 升级中止(磁盘满或权限不足?)"
  log "快照: snapshots/wgaio-$ts.tar.gz"
}

apply_tree() {  # apply_tree <来源> <安装目录>
  local src="$1" dest="$2"
  install -d -m 755 "$dest/bin" "$dest/lib" "$dest/panel"
  cp -f "$src/wgaio.sh" "$dest/wgaio.sh"
  cp -f "$src/SHA256SUMS" "$dest/SHA256SUMS"
  if [ -d "$src/bin" ]; then
    cp -f "$src"/bin/* "$dest/bin/"
  fi
  cp -f "$src"/lib/* "$dest/lib/"
  if [ -d "$src/panel" ]; then
    cp -f "$src"/panel/* "$dest/panel/"
  fi
}

repo_slug() { printf '%s' "${WGAIO_REPO:-keiraee/wg-allin-one}"; }

is_commit_sha() {
  printf '%s' "${1:-}" | grep -qiE '^[0-9a-f]{40}$'
}

track_file() { printf '%s/.wgaio-track' "${1:-$WGAIO_ROOT}"; }

read_track() {
  local key="$1" file
  file="$(track_file)"
  [ -f "$file" ] || return 0
  awk -F= -v key="$key" '$1==key {gsub(/\r/,""); print substr($0, index($0,"=")+1); exit}' "$file"
}

file_sha256() {
  local py
  py="$(find_python)"
  "$py" -c 'import hashlib,pathlib,sys; p=pathlib.Path(sys.argv[1]); d=p.read_bytes().replace(b"\r\n",b"\n").replace(b"\r",b"\n"); print(hashlib.sha256(d).hexdigest())' "$1"
}

hash_label() {
  local digest="${1:-}" commit="${2:-}"
  if [ -z "$digest" ]; then
    if is_commit_sha "$commit"; then
      printf '提交 %s' "${commit:0:12}"
    else
      printf '无'
    fi
    return 0
  fi
  if is_commit_sha "$commit"; then
    printf '%s（提交 %s）' "${digest:0:12}" "${commit:0:12}"
  else
    printf '%s' "${digest:0:12}"
  fi
}

installed_version() {
  local v=""
  if [ -f "${WGAIO_ROOT}/wgaio.sh" ]; then
    v="$(awk -F= '/^VERSION=/{gsub(/["\r]/,"",$2); print $2; exit}' "${WGAIO_ROOT}/wgaio.sh" || true)"
  fi
  [ -n "$v" ] || v="${WGAIO_VERSION:-未知}"
  printf '%s' "$v"
}

# 提交相同就不用再下载。供升级和测试共用。
same_commit() {
  is_commit_sha "${1:-}" && is_commit_sha "${2:-}" && [ "$1" = "$2" ]
}

github_curl() {
  command -v curl >/dev/null 2>&1 || die "需要 curl 才能下载升级包"
  curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 10 --max-time 60 \
    -H 'Cache-Control: no-cache' -H 'Pragma: no-cache' "$@"
}

json_sha() {
  printf '%s' "${1:-}" | grep -oE '"sha"[[:space:]]*:[[:space:]]*"[0-9a-fA-F]{40}"' | head -1 \
    | grep -oE '[0-9a-fA-F]{40}' | tr 'A-F' 'a-f' || true
}

json_tag() {
  printf '%s' "${1:-}" | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1
}

resolve_latest_tag() {
  local payload tag
  payload="$(github_curl "https://api.github.com/repos/$(repo_slug)/releases/latest")" \
    || die "还没有正式版 Release。请先选抢先试用 main，或执行: WGAIO_REF=main wgaio upgrade"
  tag="$(json_tag "$payload")"
  [ -n "$tag" ] || die "latest release 为空"
  printf '%s' "$tag"
}

resolve_commit() {
  local ref="$1" payload sha
  if is_commit_sha "$ref"; then
    printf '%s' "$ref"
    return 0
  fi
  payload="$(github_curl "https://api.github.com/repos/$(repo_slug)/commits/${ref}")" \
    || die "无法解析 Git 引用 ${ref}"
  sha="$(json_sha "$payload")"
  is_commit_sha "$sha" || die "GitHub 返回的 sha 无效: ${sha}"
  log "钉住提交: ${ref} → ${sha:0:12}"
  printf '%s' "$sha"
}

download_commit_tree() {
  local sha="$1" dest="$2" tmp
  tmp="$(mktemp -d)"
  github_curl "https://github.com/$(repo_slug)/archive/${sha}.tar.gz" -o "$tmp/src.tgz" \
    || { rm -rf "$tmp"; die "套件下载失败"; }
  mkdir -p "$dest"
  tar xzf "$tmp/src.tgz" -C "$dest" --strip-components=1 --warning=no-timestamp \
    || tar xzf "$tmp/src.tgz" -C "$dest" --strip-components=1 \
    || { rm -rf "$tmp"; die "套件解包失败"; }
  rm -rf "$tmp"
}

write_track() {
  local dir="${1:-$WGAIO_ROOT}" ref sha sums ver
  ref="${WGAIO_PERSIST_TRACK:-${WGAIO_TRACK_REF:-${WGAIO_REF:-main}}}"
  sha="${WGAIO_FETCH_COMMIT:-}"
  sums=""
  if [ -f "$dir/SHA256SUMS" ]; then
    sums="$(file_sha256 "$dir/SHA256SUMS")"
  fi
  ver="$(installed_version)"
  cat > "$dir/.wgaio-track" <<EOF
WGAIO_TRACK_REF=${ref}
WGAIO_REPO_SHA=${sha}
WGAIO_MODULES_SHA=${sums}
WGAIO_VERSION=${ver}
EOF
  chmod 600 "$dir/.wgaio-track" 2>/dev/null || true
  log "已记录轨道 ${ref}，哈希 $(hash_label "$sums" "$sha")"
}

cmd_upgrade() {
  local src="${WGAIO_UPGRADE_SRC:-}" cleanup="" persist resolved remote_sha
  local old_sum old_commit new_sum
  old_sum="$(read_track WGAIO_MODULES_SHA)"
  old_commit="$(read_track WGAIO_REPO_SHA)"
  if [ -z "$src" ]; then
    persist="${WGAIO_REF:-}"
    if [ -z "$persist" ]; then
      persist="$(read_track WGAIO_TRACK_REF)"
    fi
    [ -n "$persist" ] || persist="main"
    if [ "$persist" = "latest" ]; then
      resolved="$(resolve_latest_tag)"
      WGAIO_PERSIST_TRACK="latest"
    else
      resolved="$persist"
      WGAIO_PERSIST_TRACK="$persist"
    fi
    export WGAIO_PERSIST_TRACK
    remote_sha="$(resolve_commit "$resolved")"
    export WGAIO_FETCH_COMMIT="$remote_sha"
    log "升级轨道: ${WGAIO_PERSIST_TRACK} (${resolved})"
    log "上次哈希: $(hash_label "$old_sum" "$old_commit")"
    if same_commit "$old_commit" "$remote_sha"; then
      log "本次哈希: $(hash_label "$old_sum" "$remote_sha")"
      log "提交未变化, 无需覆盖 (若怀疑本地文件损坏: wgaio verify --fix)"
      return 0
    fi
    src="$(mktemp -d)"
    cleanup="$src"
    download_commit_tree "$remote_sha" "$src"
  fi
  check_sha256 "$src"
  if [ -n "$cleanup" ]; then
    new_sum="$(file_sha256 "$src/SHA256SUMS")"
    log "本次哈希: $(hash_label "$new_sum" "${WGAIO_FETCH_COMMIT:-}")"
    if [ -n "$old_sum" ] && [ "$old_sum" = "$new_sum" ]; then
      log "哈希未变化, 程序文件与上次相同, 跳过覆盖"
      write_track "$WGAIO_ROOT"
      rm -rf "$cleanup"
      return 0
    fi
    log "哈希已变化"
  fi
  snapshot
  apply_tree "$src" "$WGAIO_ROOT"
  [ -n "$cleanup" ] && rm -rf "$cleanup"
  check_sha256 "$WGAIO_ROOT"
  write_track "$WGAIO_ROOT"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl try-restart wgaio-panel 2>/dev/null || true
  fi
  log "升级完成(config.json 与 clients/ 未覆盖)"
}

cmd_rollback() {
  local dir="${WGAIO_ROOT}" latest
  latest="$(ls -1t "$dir"/snapshots/wgaio-*.tar.gz 2>/dev/null | head -1)"
  [ -n "$latest" ] || die "没有可用快照, 无法回滚"
  # 默认不动 config.json: 升级本就不改配置，回滚也不应撤销用户事后对端口/令牌的调整
  tar xzf "$latest" -C "$dir" --exclude=config.json
  if command -v systemctl >/dev/null 2>&1; then
    systemctl try-restart wgaio-panel 2>/dev/null || true
  fi
  log "已回滚到快照: $latest (config.json 未改动; 如需连配置一起恢复请手动从快照解出)"
}

cmd_verify() {
  local fix=0 dir="${WGAIO_ROOT}"
  [ "${1:-}" = "--fix" ] && fix=1
  [ -f "$dir/SHA256SUMS" ] || die "缺少 SHA256SUMS, 无法校验"
  local bad
  bad="$(cd "$dir" && sha256sum -c SHA256SUMS 2>/dev/null | grep -v ': OK$' || true)"
  if [ -z "$bad" ]; then
    log "本地文件与 SHA256SUMS 一致"
    return 0
  fi
  warn "以下文件与 SHA256SUMS 不一致:"
  printf '%s\n' "$bad" >&2
  if [ "$fix" -eq 0 ]; then
    die "校验未通过。执行 wgaio verify --fix 从当前轨道重新下载并修复" 1
  fi
  log "正在从当前轨道重新下载并修复..."
  local persist resolved remote_sha src
  persist="${WGAIO_REF:-}"
  if [ -z "$persist" ]; then
    persist="$(read_track WGAIO_TRACK_REF)"
  fi
  [ -n "$persist" ] || persist="main"
  if [ "$persist" = "latest" ]; then
    resolved="$(resolve_latest_tag)"
    WGAIO_PERSIST_TRACK="latest"
  else
    resolved="$persist"
    WGAIO_PERSIST_TRACK="$persist"
  fi
  export WGAIO_PERSIST_TRACK
  remote_sha="$(resolve_commit "$resolved")"
  export WGAIO_FETCH_COMMIT="$remote_sha"
  src="$(mktemp -d)"
  download_commit_tree "$remote_sha" "$src"
  check_sha256 "$src"
  apply_tree "$src" "$dir"
  rm -rf "$src"
  check_sha256 "$dir"
  write_track "$dir"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl try-restart wgaio-panel 2>/dev/null || true
  fi
  log "已按轨道 ${WGAIO_PERSIST_TRACK} 修复本地文件"
}
