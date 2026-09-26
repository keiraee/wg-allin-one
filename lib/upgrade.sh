#!/usr/bin/env bash
# 升级: 下载 → SHA256SUMS 校验 → 快照 → 覆盖程序文件。校验失败一律拒绝。
# 不覆盖 config.json 与 clients/。快照包含 wgaio.sh，回滚后入口版本与套件一致，
# 避免入口发现版本不一致后又按轨道重新下载、把回滚盖掉。
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

_upgrade_cleanup() {
  if [ -n "${WGAIO_CLEAN_DIR:-}" ]; then
    rm -rf "$WGAIO_CLEAN_DIR"
    unset WGAIO_CLEAN_DIR
  fi
  return 0
}

check_sha256() {  # check_sha256 <目录>; 该目录须有 SHA256SUMS
  local dir="${1:-$WGAIO_ROOT}"
  [ -f "$dir/SHA256SUMS" ] || die "缺少 SHA256SUMS, 拒绝升级(来源不可信)"
  command -v sha256sum >/dev/null 2>&1 || die "需要 sha256sum 才能校验, 请安装 coreutils"
  # 固定英文输出，避免中文系统把 OK 译成「成功」后误判全坏。
  ( cd "$dir" && LC_ALL=C sha256sum -c SHA256SUMS >/dev/null 2>&1 ) \
    || die "SHA256SUMS 校验失败, 文件被改动或下载损坏, 已中止"
  log "SHA256SUMS 校验通过"
}

tree_sums_ok() {  # 本地程序文件是否和 SHA256SUMS 一致
  local dir="${1:-$WGAIO_ROOT}"
  command -v sha256sum >/dev/null 2>&1 || return 1
  [ -f "$dir/SHA256SUMS" ] || return 1
  ( cd "$dir" && LC_ALL=C sha256sum -c SHA256SUMS >/dev/null 2>&1 )
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

repo_slug() {
  local s="${WGAIO_REPO:-keiraee/wg-allin-one}"
  if [[ "$s" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
    printf '%s' "$s"
  else
    die "WGAIO_REPO 不合法"
  fi
}

is_commit_sha() {
  [[ "${1:-}" =~ ^[0-9a-fA-F]{40}$ ]]
}

ref_ok() {
  case "${1:-}" in
    ''|-*|*[!A-Za-z0-9._/-]*|*..*) return 1 ;;
    *) return 0 ;;
  esac
}

require_ref() {
  ref_ok "${1:-}" || die "升级引用不合法: ${1:-}"
}

track_file() { printf '%s/.wgaio-track' "${1:-$WGAIO_ROOT}"; }

read_track() {
  local key="$1" file root="${2:-$WGAIO_ROOT}"
  file="$(track_file "$root")"
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
  local dir="${1:-$WGAIO_ROOT}" v=""
  if [ -f "${dir}/wgaio.sh" ]; then
    v="$(awk -F= '/^VERSION=/{gsub(/["\r]/,"",$2); print $2; exit}' "${dir}/wgaio.sh" || true)"
  fi
  [ -n "$v" ] || v="${WGAIO_VERSION:-未知}"
  printf '%s' "$v"
}

note_menu_reload() {
  [ "${WGAIO_IN_MENU:-}" = "1" ] || return 0
  : > "${WGAIO_ROOT}/.wgaio-menu-reload" || true
}

# 提交相同就不用再下载。供升级和测试共用。
same_commit() {
  is_commit_sha "${1:-}" && is_commit_sha "${2:-}" && [ "$1" = "$2" ]
}

github_curl() {
  command -v curl >/dev/null 2>&1 || die "需要 curl 才能下载升级包"
  curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 10 --max-time 120 \
    -H 'Cache-Control: no-cache' -H 'Pragma: no-cache' "$@"
}

# 把 HTTP 状态留在当前 shell。不要放进 $()，否则状态码会丢。
github_get() {
  local code
  command -v curl >/dev/null 2>&1 || die "需要 curl 才能下载升级包"
  code="$(curl -sS -L --retry 3 --retry-delay 2 --connect-timeout 10 --max-time 60 \
    -H 'Cache-Control: no-cache' -H 'Pragma: no-cache' \
    -o "$2" -w '%{http_code}' "$1" || true)"
  GITHUB_HTTP="${code:-000}"
}

json_sha() {
  # 同一行里可能有多个 sha。=~ 取最左边那一个，避免 grep -o 把后面的也捞进来。
  local payload="${1:-}"
  if [[ "$payload" =~ \"sha\"[[:space:]]*:[[:space:]]*\"([0-9a-fA-F]{40})\" ]]; then
    printf '%s' "${BASH_REMATCH[1],,}"
  fi
}

json_tag() {
  local payload="${1:-}"
  if [[ "$payload" =~ \"tag_name\"[[:space:]]*:[[:space:]]*\"([^\"]*)\" ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
  fi
}

resolve_latest_tag() {
  local body tag
  body="$(mktemp)"
  github_get "https://api.github.com/repos/$(repo_slug)/releases/latest" "$body"
  if [ "$GITHUB_HTTP" != "200" ]; then
    rm -f "$body"
    if [ "$GITHUB_HTTP" = "404" ]; then
      die "还没有正式版 Release。请先选抢先试用 main，或执行: WGAIO_REF=main wgaio upgrade"
    fi
    die "访问 GitHub 失败 (HTTP ${GITHUB_HTTP})。请检查网络后重试"
  fi
  tag="$(json_tag "$(cat "$body")")"
  rm -f "$body"
  [ -n "$tag" ] || die "latest release 为空"
  require_ref "$tag"
  printf '%s' "$tag"
}

resolve_commit() {
  # 只往 stdout 打 sha。日志必须由调用方打，否则 $(resolve_commit) 会把日志行吞进提交号。
  local ref="$1" body sha
  require_ref "$ref"
  if is_commit_sha "$ref"; then
    printf '%s' "$ref"
    return 0
  fi
  body="$(mktemp)"
  github_get "https://api.github.com/repos/$(repo_slug)/commits/${ref}" "$body"
  if [ "$GITHUB_HTTP" != "200" ]; then
    rm -f "$body"
    die "无法解析 Git 引用 ${ref} (HTTP ${GITHUB_HTTP})"
  fi
  sha="$(json_sha "$(cat "$body")")"
  rm -f "$body"
  is_commit_sha "$sha" || die "GitHub 返回的 sha 无效: ${sha}"
  printf '%s' "$sha"
}

download_commit_tree() {
  local sha="$1" dest="$2" tmp
  tmp="$(mktemp -d)"
  if ! github_curl "https://github.com/$(repo_slug)/archive/${sha}.tar.gz" -o "$tmp/src.tgz"; then
    rm -rf "$tmp"
    return 1
  fi
  mkdir -p "$dest"
  if ! tar xzf "$tmp/src.tgz" -C "$dest" --strip-components=1 --warning=no-timestamp \
      && ! tar xzf "$tmp/src.tgz" -C "$dest" --strip-components=1; then
    rm -rf "$tmp"
    return 1
  fi
  rm -rf "$tmp"
}

write_track() {
  local dir="${1:-$WGAIO_ROOT}" ref sha sums ver saved_ref saved_sha
  saved_ref="$(read_track WGAIO_TRACK_REF "$dir")"
  saved_sha="$(read_track WGAIO_REPO_SHA "$dir")"
  # 没显式指定时沿用文件里的记录。都没有就记 latest，不要改写成 main。
  ref="${WGAIO_PERSIST_TRACK:-${WGAIO_REF:-$saved_ref}}"
  [ -n "$ref" ] || ref="latest"
  require_ref "$ref"
  sha="${WGAIO_FETCH_COMMIT:-$saved_sha}"
  if [ -n "$sha" ] && ! is_commit_sha "$sha"; then
    die "记录的提交哈希不合法"
  fi
  sums=""
  if [ -f "$dir/SHA256SUMS" ]; then
    sums="$(file_sha256 "$dir/SHA256SUMS")"
  fi
  ver="$(installed_version "$dir")"
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
  trap _upgrade_cleanup EXIT
  old_sum="$(read_track WGAIO_MODULES_SHA)"
  old_commit="$(read_track WGAIO_REPO_SHA)"
  if [ -z "$src" ]; then
    persist="${WGAIO_REF:-}"
    if [ -z "$persist" ]; then
      persist="$(read_track WGAIO_TRACK_REF)"
    fi
    [ -n "$persist" ] || persist="latest"
    require_ref "$persist"
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
    log "钉住提交: ${resolved} → ${remote_sha:0:12}"
    log "上次哈希: $(hash_label "$old_sum" "$old_commit")"
    if same_commit "$old_commit" "$remote_sha" && tree_sums_ok "$WGAIO_ROOT"; then
      log "本次哈希: $(hash_label "$old_sum" "$remote_sha")"
      log "提交未变化, 无需覆盖 (若怀疑本地文件损坏: wgaio verify --fix)"
      write_track "$WGAIO_ROOT"
      return 0
    fi
    if same_commit "$old_commit" "$remote_sha"; then
      warn "提交未变化, 但本地文件和 SHA256SUMS 不一致, 将重新下载覆盖"
    fi
    src="$(mktemp -d)"
    cleanup="$src"
    WGAIO_CLEAN_DIR="$src"
    download_commit_tree "$remote_sha" "$src" || die "套件下载失败"
  fi
  check_sha256 "$src"
  if [ -n "$cleanup" ]; then
    new_sum="$(file_sha256 "$src/SHA256SUMS")"
    log "本次哈希: $(hash_label "$new_sum" "${WGAIO_FETCH_COMMIT:-}")"
    if [ -n "$old_sum" ] && [ "$old_sum" = "$new_sum" ] && tree_sums_ok "$WGAIO_ROOT"; then
      log "哈希未变化, 程序文件与上次相同, 跳过覆盖"
      write_track "$WGAIO_ROOT"
      unset WGAIO_CLEAN_DIR
      rm -rf "$cleanup"
      return 0
    fi
    if [ -n "$old_sum" ] && [ "$old_sum" = "$new_sum" ]; then
      warn "哈希未变化, 但本地文件已损坏, 将覆盖修复"
    else
      log "哈希已变化"
    fi
  fi
  snapshot
  apply_tree "$src" "$WGAIO_ROOT"
  if [ -n "$cleanup" ]; then
    unset WGAIO_CLEAN_DIR
    rm -rf "$cleanup"
  fi
  check_sha256 "$WGAIO_ROOT"
  write_track "$WGAIO_ROOT"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl try-restart wgaio-panel 2>/dev/null || true
  fi
  note_menu_reload
  log "升级完成(config.json 与 clients/ 未覆盖)"
}

newest_snapshot() {
  local dir="$1" f latest="" ng=0
  shopt -q nullglob && ng=1
  shopt -s nullglob
  local -a snaps=("$dir"/snapshots/wgaio-*.tar.gz)
  if [ "$ng" -eq 0 ]; then
    shopt -u nullglob
  fi
  [ "${#snaps[@]}" -gt 0 ] || return 1
  latest="${snaps[0]}"
  for f in "${snaps[@]}"; do
    [ "$f" -nt "$latest" ] && latest="$f"
  done
  printf '%s' "$latest"
}

cmd_rollback() {
  local dir="${WGAIO_ROOT}" latest has_track=0
  latest="$(newest_snapshot "$dir" || true)"
  [ -n "$latest" ] || die "没有可用快照, 无法回滚"
  if tar tzf "$latest" .wgaio-track >/dev/null 2>&1; then
    has_track=1
  fi
  # 默认不动 config.json: 升级本就不改配置，回滚也不应撤销用户事后对端口/令牌的调整
  tar xzf "$latest" -C "$dir" --exclude=config.json
  # 快照里没有轨道文件时，删掉升级刚写上的那份，避免回滚后哈希和文件对不上。
  if [ "$has_track" -eq 0 ]; then
    rm -f "$dir/.wgaio-track"
  fi
  if command -v systemctl >/dev/null 2>&1; then
    systemctl try-restart wgaio-panel 2>/dev/null || true
  fi
  log "已回滚到快照: $latest (config.json 未改动; 如需连配置一起恢复请手动从快照解出)"
}

cmd_verify() {
  local fix=0 dir="${WGAIO_ROOT}"
  trap _upgrade_cleanup EXIT
  [ "${1:-}" = "--fix" ] && fix=1
  [ -f "$dir/SHA256SUMS" ] || die "缺少 SHA256SUMS, 无法校验"
  command -v sha256sum >/dev/null 2>&1 || die "需要 sha256sum 才能校验, 请安装 coreutils"
  if ( cd "$dir" && LC_ALL=C sha256sum -c SHA256SUMS >/dev/null 2>&1 ); then
    log "本地文件与 SHA256SUMS 一致"
    return 0
  fi
  local bad
  bad="$(cd "$dir" && LC_ALL=C sha256sum -c SHA256SUMS 2>&1 | grep -v ': OK$' || true)"
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
  [ -n "$persist" ] || persist="latest"
  require_ref "$persist"
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
  log "钉住提交: ${resolved} → ${remote_sha:0:12}"
  src="$(mktemp -d)"
  WGAIO_CLEAN_DIR="$src"
  download_commit_tree "$remote_sha" "$src" || die "套件下载失败"
  check_sha256 "$src"
  snapshot "$dir"
  apply_tree "$src" "$dir"
  unset WGAIO_CLEAN_DIR
  rm -rf "$src"
  check_sha256 "$dir"
  write_track "$dir"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl try-restart wgaio-panel 2>/dev/null || true
  fi
  note_menu_reload
  log "已按轨道 ${WGAIO_PERSIST_TRACK} 修复本地文件"
}
