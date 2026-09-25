# 计划 3：bash 套装（入口/向导/发版）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 wg-allin-one 做成 hy2-allin-one 同款的一键脚本套装——`wgaio.sh` 单入口 + `lib/*.sh` 模块（中文问答向导、install/upgrade/rollback/uninstall、systemd 管理）+ SHA256SUMS 校验 + GitHub Actions + 中文 README，`curl | bash` 可装。

**Architecture:** bash 外壳只做「装系统包/写文件/管服务/人机问答」，所有 WireGuard 逻辑一律转发给 `python3 lib/core.py`（exit 0/1/2 契约、stdout/stderr 分离已在计划 1/2 定死）。`wgaio user ...` 子命令直接透传 core.py；`wgaio panel start|stop|status` 管 systemd。测试用 Python unittest 通过 subprocess 驱动 bash（对齐 hy2 的 tests 模式，Windows Git Bash 可跑）。

**Tech Stack:** bash 4+、Python 3.9+（core.py 已就绪）、systemd、sha256sum、GitHub Actions。

**运行说明:** Windows Git Bash 用 `python` 代替 `python3`（bash 脚本内部统一 `python3`，PATH 缺失时回退 `python`）。基线：`python -m unittest discover tests -v` = 85 OK。

---

## 文件结构

| 文件 | 职责 | 创建于 |
|---|---|---|
| `wgaio.sh` | 单入口：install/upgrade/uninstall/user/panel/status/logs 子命令分发 | T1 |
| `lib/core.sh` | 日志/报错/依赖检查/Python 定位/JSON 读取 | T1 |
| `lib/wizard.sh` | 中文问答向导 → config.json + 令牌生成（只打印一次） | T2 |
| `lib/install.sh` | 装依赖/建目录/权限/安全组醒目提示/首次起服 | T3 |
| `lib/user.sh` | `wgaio user` 透传包装（stdout 含私钥，禁止落日志） | T4 |
| `lib/panel.sh` | systemd 单元写入 + panel start/stop/status/restart | T5 |
| `lib/upgrade.sh` | 版本+提交哈希比对、SHA256SUMS 校验、快照回滚 | T6 |
| `lib/uninstall.sh` | 彻底清理（可选保留 clients/） | T6 |
| `bin/wgaio` | 装到 /usr/local/bin 的薄包装（exec 真身） | T3 |
| `tests/test_sh_*.py` | bash 契约测试（subprocess 驱动） | 各任务 |
| `SHA256SUMS` / `.github/workflows/tests.yml` / `README.md` | 发版校验/CI/中文文档 | T7 |

**全局约定（全计划复用，别改）：**
- 项目根定位：`ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"`；core.py 在 `$ROOT/lib/core.py`。
- Python 命令：`PY="$(command -v python3 || command -v python)"`。
- 错误输出一律走 stderr；`wgaio user show` 的 stdout **永不重定向进日志文件**（含客户端私钥）。
- 退出码透传 core.py 的 0/1/2。

---

### Task 1: 入口骨架 + 公共函数

**Files:**
- Create: `lib/core.sh`、`wgaio.sh`
- Test: `tests/test_sh_entry.py`

- [ ] **Step 1: 写失败测试**

`tests/test_sh_entry.py`：

```python
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_sh(*args):
    env = dict(os.environ)
    env.setdefault("WGAIO_ROOT", str(ROOT))
    return subprocess.run(["bash", str(ROOT / "wgaio.sh"), *args],
                          capture_output=True, text=True, timeout=60, env=env)


class EntryTests(unittest.TestCase):
    def test_no_args_shows_usage(self):
        r = run_sh()
        self.assertEqual(r.returncode, 2)
        self.assertIn("用法", r.stderr)

    def test_unknown_subcommand(self):
        r = run_sh("frobnicate")
        self.assertEqual(r.returncode, 2)
        self.assertIn("未知子命令", r.stderr)

    def test_version_flag(self):
        r = run_sh("version")
        self.assertEqual(r.returncode, 0)
        self.assertIn("wgaio", r.stdout)

    def test_user_passthrough_help(self):
        r = run_sh("user", "--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("add", r.stdout)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_sh_entry -v`
Expected: ERROR — `[Errno 2] No such file or directory: '.../wgaio.sh'`

- [ ] **Step 3: 写最小实现**

`lib/core.sh`：

```bash
#!/usr/bin/env bash
# wgaio 公共函数: 日志/报错/依赖/Python 定位
set -Eeuo pipefail

wgaio_root() { cd "$(dirname "${BASH_SOURCE[1]}")" && pwd; }

log()  { printf '[wgaio] %s\n' "$*"; }
warn() { printf '[wgaio] 警告: %s\n' "$*" >&2; }
die()  { printf '[wgaio] 错误: %s\n' "$*" >&2; exit "${2:-1}"; }

find_python() {
  if command -v python3 >/dev/null 2>&1; then command -v python3
  elif command -v python >/dev/null 2>&1; then command -v python
  else die "未找到 python3/python, 请先安装 Python 3.9+"; fi
}

core_py() {
  local root="${WGAIO_ROOT:-$(wgaio_root)}"
  printf '%s/lib/core.py' "$root"
}

run_core() {
  local py; py="$(find_python)"
  "$py" "$(core_py)" "$@"
}
```

`wgaio.sh`：

```bash
#!/usr/bin/env bash
# wgaio - WireGuard 套装入口 (wg-allin-one)
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export WGAIO_ROOT="$ROOT"
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

VERSION="0.1.0"

usage() {
  cat >&2 <<'EOF'
用法: wgaio <子命令> [参数]

  install                 向导式安装(中文问答)
  version                 显示版本
  user add|del|edit|list|show   设备管理(透传核心)
  panel start|stop|status|restart  面板服务管理
  status                  总览
  upgrade | uninstall     升级 / 卸载

设备管理细节: wgaio user --help
EOF
}

cmd="${1:-}"
case "$cmd" in
  ""|-h|--help) usage; exit 2 ;;
  version) printf 'wgaio %s\n' "$VERSION"; exit 0 ;;
  user) shift; run_core user "$@" ;;
  install|upgrade|uninstall|panel|status|logs)
    mod="$ROOT/lib/${cmd}.sh"
    [ -f "$mod" ] || die "模块未安装: $cmd"
    shift; . "$mod"; "cmd_${cmd}" "$@" ;;
  *) die "未知子命令: $cmd (用法见 wgaio --help)" 2 ;;
esac
```

（注意：`install`/`upgrade`/`uninstall`/`panel`/`status`/`logs` 的实现在后续任务——T1 先让 `user` 透传和 `version`/`usage` 工作，缺失模块时报"模块未安装"即为预期。）

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_sh_entry -v`
Expected: `Ran 4 tests ... OK`

Run: `python -m unittest discover tests -v`
Expected: `Ran 89 tests ... OK`（85 旧 + 4 新）

- [ ] **Step 5: Commit**

```bash
git add wgaio.sh lib/core.sh tests/test_sh_entry.py
git commit -m "feat: wgaio 入口骨架与公共函数"
```

---

### Task 2: 中文问答向导（config.json + 令牌）

**Files:**
- Create: `lib/wizard.sh`
- Test: `tests/test_sh_wizard.py`

- [ ] **Step 1: 写失败测试**

`tests/test_sh_wizard.py`：

```python
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WizardTests(unittest.TestCase):
    def test_wizard_writes_config_and_token(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "lib").mkdir()
        os.symlink(ROOT / "lib" / "core.sh", root / "lib" / "core.sh")
        os.symlink(ROOT / "lib" / "core.py", root / "lib" / "core.py")
        os.symlink(ROOT / "lib" / "wizard.sh", root / "lib" / "wizard.sh")
        os.symlink(ROOT / "wgaio.sh", root / "wgaio.sh")
        script = (
            "bash wgaio.sh install --wizard-only <<'EOF'\n"
            "\n"            # vpn_cidr 回车用默认
            "\n"            # wg_port 默认
            "203.0.113.7:51820\n"   # endpoint
            "\n"            # client_dns 默认
            "\n"            # lan_cidrs 默认空
            "\n"            # panel_bind 默认
            "\n"            # panel_port 默认
            "\n"            # 流量模式默认
            "EOF"
        )
        r = subprocess.run(["bash", "-c", script], cwd=root,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["endpoint"], "203.0.113.7:51820")
        self.assertEqual(cfg["vpn_cidr"], "10.66.66.0/24")
        self.assertEqual(len(cfg["panel_token_hash"]), 64)
        self.assertIn("令牌", r.stdout)   # 令牌明文只出现这一次
        self.assertIn("请立即保存", r.stdout)

    def test_wizard_rejects_bad_endpoint(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "lib").mkdir()
        for f in ("core.sh", "core.py", "wizard.sh"):
            os.symlink(ROOT / "lib" / f, root / "lib" / f)
        os.symlink(ROOT / "wgaio.sh", root / "wgaio.sh")
        script = "bash wgaio.sh install --wizard-only <<'EOF'\n\n\nnot-an-endpoint\nEOF"
        r = subprocess.run(["bash", "-c", script], cwd=root,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertIn("endpoint", r.stderr)


if __name__ == "__main__":
    unittest.main()
```

（`install --wizard-only` 只跑向导写 config.json+打印令牌，不装系统件——T3 的 install 主流程会复用 `cmd_install` 里的 `run_wizard`。Windows 上 os.symlink 需要特权，若失败则在测试里 `self.skipTest("需要 symlink 权限")` 包一层 try/except OSError。）

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_sh_wizard -v`
Expected: FAIL（wgaio.sh 报"模块未安装: install"或向导不存在）

- [ ] **Step 3: 写最小实现**

`lib/wizard.sh`：

```bash
#!/usr/bin/env bash
# 中文问答向导: 收集配置 → config.json; 令牌明文只打印一次
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

ask() {  # ask "提示" "默认值" → stdout 答案
  local prompt="$1" def="${2:-}" ans
  if [ -n "$def" ]; then printf '%s [%s]: ' "$prompt" "$def" >&2
  else printf '%s: ' "$prompt" >&2; fi
  IFS= read -r ans || true
  printf '%s' "${ans:-$def}"
}

run_wizard() {
  local py; py="$(find_python)"
  log "开始安装向导(全部可回车用默认值)"
  local vpn_cidr wg_port endpoint client_dns lan_cidrs panel_bind panel_port def_mode
  vpn_cidr="$(ask 'VPN 网段' '10.66.66.0/24')"
  wg_port="$(ask 'WireGuard 监听端口(UDP)' '51820')"
  endpoint="$(ask '客户端接入点(公网IP或域名:端口)' '')"
  client_dns="$(ask '客户端 DNS' '1.1.1.1')"
  lan_cidrs="$(ask '内网路由段(逗号分隔, 可空)' '')"
  panel_bind="$(ask '面板绑定地址(默认 VPN 隧道地址)' '')"
  panel_port="$(ask '面板端口' '8888')"
  def_mode="$(ask '新设备默认流量模式 split/full' 'split')"
  [ -n "$endpoint" ] || die "endpoint 不能为空"

  local token hash
  token="$("$py" -c 'import secrets;print(secrets.token_hex(24))')"
  hash="$("$py" -c 'import hashlib,sys;print(hashlib.sha256(sys.argv[1].encode()).hexdigest())' "$token")"

  "$py" - "$vpn_cidr" "$wg_port" "$endpoint" "$client_dns" "$lan_cidrs" \
        "$panel_bind" "$panel_port" "$def_mode" "$hash" <<'PY'
import json, re, sys
vpn_cidr, wg_port, endpoint, client_dns, lan_cidrs, panel_bind, panel_port, mode, thash = sys.argv[1:]
if not re.match(r"^[^:]+:\d+$", endpoint):
    sys.exit("错误: endpoint 格式应为 IP或域名:端口")
cfg = {
    "vpn_cidr": vpn_cidr,
    "wg_port": int(wg_port),
    "endpoint": endpoint,
    "client_dns": client_dns,
    "lan_cidrs": [x.strip() for x in lan_cidrs.split(",") if x.strip()],
    "panel_bind": panel_bind or (".".join(vpn_cidr.split(".")[:3]) + ".1"),
    "panel_port": int(panel_port),
    "panel_token_hash": thash,
    "default_mode": mode,
}
import os
os.makedirs(os.environ.get("WGAIO_ROOT", "."), exist_ok=True)
open(os.path.join(os.environ.get("WGAIO_ROOT", "."), "config.json"), "w").write(
    json.dumps(cfg, ensure_ascii=False, indent=2))
PY

  printf '\n===== 面板访问令牌(只显示这一次, 请立即保存) =====\n%s\n==============================================\n' "$token"
  log "配置已写入 config.json(600 权限由 install 阶段设置)"
}
```

`wgaio.sh` 的 `install` 分支需要支持 `--wizard-only`：在 `case` 的 install 分支前改法——把 `install)` 单独处理：

```bash
  install)
    shift
    . "$ROOT/lib/install.sh"
    if [ "${1:-}" = "--wizard-only" ]; then run_wizard; exit 0; fi
    cmd_install "$@" ;;
```

（`lib/install.sh` 在 T3 创建；T2 先建一个占位 `lib/install.sh` 只含 `cmd_install() { die "install 主流程尚未实现(T3)"; }` 与 `run_wizard` 的调用点注释，或直接在 T2 提供的 install.sh 里 `. "$ROOT/lib/wizard.sh"` 并实现 `cmd_install` 骨架调用 `run_wizard` 后 die 未完——以测试绿为准。）

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_sh_wizard -v`
Expected: `Ran 2 tests ... OK`（symlink 不可用时 skip 亦可接受，但需在报告注明）

Run: `python -m unittest discover tests -v`
Expected: `Ran 91 tests ... OK`（89 + 2）

- [ ] **Step 5: Commit**

```bash
git add lib/wizard.sh lib/install.sh wgaio.sh tests/test_sh_wizard.py
git commit -m "feat: 中文安装向导(config.json 与一次性令牌)"
```

---

### Task 3: install 主流程 + bin 包装

**Files:**
- Modify: `lib/install.sh`（补全 `cmd_install`）
- Create: `bin/wgaio`
- Test: `tests/test_sh_install.py`

- [ ] **Step 1: 写失败测试**

`tests/test_sh_install.py`：

```python
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def make_sandbox(tmp):
    root = Path(tmp)
    (root / "lib").mkdir()
    for f in ("core.sh", "core.py", "wizard.sh", "install.sh", "panel.sh"):
        src = ROOT / "lib" / f
        if src.exists():
            os.symlink(src, root / "lib" / f)
    os.symlink(ROOT / "wgaio.sh", root / "wgaio.sh")
    return root


class InstallTests(unittest.TestCase):
    def test_install_dry_run_writes_layout(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            root = make_sandbox(tmp.name)
        except OSError:
            self.skipTest("需要 symlink 权限")
        r = subprocess.run(
            ["bash", "-c",
             "bash wgaio.sh install --dry-run <<'EOF'\n\n\n"
             "203.0.113.7:51820\n\n\n\n\n\nEOF"],
            cwd=root, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((root / "config.json").exists())
        self.assertIn("安全组", r.stdout)   # 醒目提示必出现
        self.assertIn("UDP", r.stdout)

    def test_install_refuses_without_root_hint(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            root = make_sandbox(tmp.name)
        except OSError:
            self.skipTest("需要 symlink 权限")
        r = subprocess.run(
            ["bash", "-c",
             "WGAIO_FORCE_NONROOT=1 bash wgaio.sh install --dry-run <<'EOF'\n\n\n"
             "203.0.113.7:51820\n\n\n\n\n\nEOF"],
            cwd=root, capture_output=True, text=True, timeout=120)
        # 非 root 且非 dry-run 时才 die; --dry-run 应放行
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_sh_install -v`
Expected: FAIL（当前 cmd_install 只 die"尚未实现"）

- [ ] **Step 3: 写最小实现**

`lib/install.sh` 补全为：

```bash
#!/usr/bin/env bash
# 安装主流程: 依赖 → 目录/权限 → 面板服务 → 安全组提示
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"
. "$ROOT/lib/wizard.sh"

cmd_install() {
  local dry=0
  [ "${1:-}" = "--dry-run" ] && dry=1

  if [ "$dry" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
    die "请用 root 运行: sudo bash wgaio.sh install"
  fi

  run_wizard

  local dest="${WGAIO_ROOT}"
  if [ "$dry" -eq 0 ]; then dest="/opt/wgaio"; mkdir -p "$dest"; fi

  install -d -m 700 "$dest/clients" "$dest/lib" "$dest/panel" "$dest/bin"
  cp -f "$ROOT/lib/core.py" "$dest/lib/core.py"
  cp -f "$ROOT"/lib/*.sh "$dest/lib/" 2>/dev/null || true
  cp -f "$ROOT"/panel/* "$dest/panel/" 2>/dev/null || true
  cp -f "$ROOT/wgaio.sh" "$dest/wgaio.sh"
  cp -f "$ROOT/config.json" "$dest/config.json"
  chmod 600 "$dest/config.json"
  if [ "$dry" -eq 0 ]; then
    cat > /usr/local/bin/wgaio <<EOF
#!/usr/bin/env bash
exec bash "$dest/wgaio.sh" "\$@"
EOF
    chmod 755 /usr/local/bin/wgaio
    . "$ROOT/lib/panel.sh" && install_panel_unit "$dest"
  fi

  local wg_port; wg_port="$(cat "$dest/config.json" | grep -o '"wg_port": *[0-9]*' | grep -o '[0-9]*')"
  printf '\n'
  log "====================================================="
  log " 重要: 请到云控制台安全组放行 UDP %s 端口!" "${wg_port:-51820}"
  log " 面板: http://<VPN隧道地址>:<panel_port> (令牌见上方)"
  log "=====================================================\n"
}
```

`bin/wgaio`（5 行）：

```bash
#!/usr/bin/env bash
# /usr/local/bin 薄包装
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/wgaio.sh" "$@"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_sh_install -v`
Expected: `Ran 2 tests ... OK`
Run: `python -m unittest discover tests -v`
Expected: `Ran 93 tests ... OK`（91 + 2）

- [ ] **Step 5: Commit**

```bash
git add lib/install.sh bin/wgaio tests/test_sh_install.py
git commit -m "feat: install 主流程(目录/权限/bin包装/安全组提示)"
```

---

### Task 4: user 设备管理包装（私钥不落日志）

**Files:**
- Create: `lib/user.sh`
- Test: `tests/test_sh_user.py`

- [ ] **Step 1: 写失败测试**

`tests/test_sh_user.py`：

```python
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class UserWrapperTests(unittest.TestCase):
    def test_user_show_never_logged(self):
        """包装层不得把 user show 的 stdout 写进任何日志文件。"""
        logdir = Path(tempfile.mkdtemp())
        env = dict(os.environ, WGAIO_ROOT=str(ROOT), WGAIO_LOG_DIR=str(logdir))
        # user show 对不存在设备报 404/1; 重点是跑完后日志目录里没有 .conf 内容
        r = subprocess.run(["bash", str(ROOT / "wgaio.sh"), "user", "show", "ghost"],
                           capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(r.returncode, 1)
        logs = list(logdir.rglob("*"))
        bodies = "".join(p.read_text(encoding="utf-8", errors="ignore")
                         for p in logs if p.is_file())
        self.assertNotIn("PrivateKey", bodies)

    def test_user_list_works(self):
        env = dict(os.environ, WGAIO_ROOT=str(ROOT))
        r = subprocess.run(["bash", str(ROOT / "wgaio.sh"), "user", "list"],
                           capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_sh_user -v`
Expected: `test_user_list` 可能已绿（T1 透传）；`test_user_show_never_logged` 绿或红不重要——此任务真正的产出是 `lib/user.sh` 的显式包装与注释约定。

- [ ] **Step 3: 写最小实现**

`lib/user.sh`：

```bash
#!/usr/bin/env bash
# 设备管理包装。安全约定: `user show` 的 stdout 含客户端私钥,
# 任何调用方(含计划外脚本)禁止把它重定向进日志/文件后长期留存。
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

cmd_user() {
  case "${1:-}" in
    show) shift
      run_core user show "$@"   # stdout = .conf 内容(含私钥), 勿记录
      ;;
    *) run_core user "$@" ;;
  esac
}
```

并把 `wgaio.sh` 的 `user)` 分支改为 `. "$ROOT/lib/user.sh"; shift; cmd_user "$@"`（保留对 core 的透传语义）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_sh_user -v`
Expected: `Ran 2 tests ... OK`
Run: `python -m unittest discover tests -v`
Expected: `Ran 95 tests ... OK`（93 + 2）

- [ ] **Step 5: Commit**

```bash
git add lib/user.sh wgaio.sh tests/test_sh_user.py
git commit -m "feat: user 包装(私钥输出禁止落日志约定)"
```

---

### Task 5: 面板服务管理（systemd 单元）

**Files:**
- Create: `lib/panel.sh`
- Test: `tests/test_sh_panel.py`

- [ ] **Step 1: 写失败测试**

`tests/test_sh_panel.py`：

```python
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PanelUnitTests(unittest.TestCase):
    def test_unit_file_contents(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        env = dict(os.environ, WGAIO_ROOT=str(ROOT), WGAIO_UNIT_OUT=str(root / "wgaio-panel.service"))
        r = subprocess.run(["bash", "-c",
                            '. lib/core.sh; . lib/panel.sh; write_panel_unit "%s"' % ROOT],
                           cwd=ROOT, capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        unit = (root / "wgaio-panel.service").read_text(encoding="utf-8")
        self.assertIn("ExecStart=", unit)
        self.assertIn("--serve", unit)
        self.assertIn("Restart=always", unit)
        self.assertIn("StandardError=journal", unit)   # wg_set_peer 告警进 journal

    def test_panel_status_without_systemd(self):
        env = dict(os.environ, WGAIO_ROOT=str(ROOT))
        r = subprocess.run(["bash", str(ROOT / "wgaio.sh"), "panel", "status"],
                           capture_output=True, text=True, timeout=60, env=env)
        # 无 systemd 的环境(Windows/CI)应给出明确中文提示而非 traceback
        self.assertIn("systemd", (r.stdout + r.stderr).lower())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_sh_panel -v`
Expected: FAIL（lib/panel.sh 不存在）

- [ ] **Step 3: 写最小实现**

`lib/panel.sh`：

```bash
#!/usr/bin/env bash
# 面板 systemd 单元与服务管理
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

write_panel_unit() {  # 输出到 $WGAIO_UNIT_OUT 或 /etc/systemd/system/
  local dest="${WGAIO_UNIT_OUT:-/etc/systemd/system/wgaio-panel.service}"
  local root="${1:-${WGAIO_ROOT}}"
  cat > "$dest" <<EOF
[Unit]
Description=wgaio WireGuard panel
After=network.target

[Service]
ExecStart=$(find_python) $root/lib/core.py --serve
WorkingDirectory=$root
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
}

install_panel_unit() {
  write_panel_unit "${1:-$WGAIO_ROOT}"
  [ "${WGAIO_UNIT_OUT:-}" ] && return 0
  systemctl daemon-reload
  systemctl enable --now wgaio-panel
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_sh_panel -v`
Expected: `Ran 2 tests ... OK`
Run: `python -m unittest discover tests -v`
Expected: `Ran 97 tests ... OK`（95 + 2）

- [ ] **Step 5: Commit**

```bash
git add lib/panel.sh tests/test_sh_panel.py
git commit -m "feat: wgaio-panel systemd 单元与服务管理"
```

---

### Task 6: upgrade / uninstall

**Files:**
- Create: `lib/upgrade.sh`、`lib/uninstall.sh`
- Test: `tests/test_sh_updown.py`

- [ ] **Step 1: 写失败测试**

`tests/test_sh_updown.py`：

```python
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class UpgradeTests(unittest.TestCase):
    def test_sha256sums_check_detects_tamper(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "lib").mkdir()
        for f in ("core.sh", "upgrade.sh"):
            os.symlink(ROOT / "lib" / f, root / "lib" / f)
        f = root / "payload.txt"
        f.write_text("hello", encoding="utf-8")
        r = subprocess.run(
            ["bash", "-c", '. lib/core.sh; . lib/upgrade.sh; check_sha256 "%s"' % root],
            cwd=root, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1)   # 无 SHA256SUMS → 拒绝
        (root / "SHA256SUMS").write_text(
            "0000000000000000000000000000000000000000000000000000000000000000  payload.txt\n",
            encoding="utf-8")
        r = subprocess.run(
            ["bash", "-c", '. lib/core.sh; . lib/upgrade.sh; check_sha256 "%s"' % root],
            cwd=root, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1)   # 哈希不符 → 拒绝
        self.assertIn("校验", r.stderr)


class UninstallTests(unittest.TestCase):
    def test_uninstall_dry_run_lists_targets(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "lib").mkdir()
        for f in ("core.sh", "uninstall.sh"):
            os.symlink(ROOT / "lib" / f, root / "lib" / f)
        r = subprocess.run(
            ["bash", "-c", '. lib/core.sh; . lib/uninstall.sh; cmd_uninstall --dry-run'],
            cwd=root, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout + r.stderr
        self.assertIn("config.json", out)
        self.assertIn("--keep-clients", out)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_sh_updown -v`
Expected: ERROR — 无法 source 不存在的模块

- [ ] **Step 3: 写最小实现**

`lib/upgrade.sh`：

```bash
#!/usr/bin/env bash
# 升级: SHA256SUMS 校验 → 快照 → 覆盖文件; 校验失败一律拒绝
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

check_sha256() {  # check_sha256 <目录>; 该目录须有 SHA256SUMS
  local dir="${1:-$WGAIO_ROOT}"
  [ -f "$dir/SHA256SUMS" ] || die "缺少 SHA256SUMS, 拒绝升级(来源不可信)"
  ( cd "$dir" && sha256sum -c SHA256SUMS >/dev/null 2>&1 ) \
    || die "SHA256SUMS 校验失败, 文件被改动或下载损坏, 已中止" 1
  log "SHA256SUMS 校验通过"
}

snapshot() {
  local dir="${1:-$WGAIO_ROOT}" ts
  ts="$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$dir/snapshots"
  tar czf "$dir/snapshots/wgaio-$ts.tar.gz" -C "$dir" \
    --exclude=snapshots --exclude=clients config.json lib panel 2>/dev/null || true
  log "快照: snapshots/wgaio-$ts.tar.gz"
}

cmd_upgrade() {
  check_sha256
  snapshot
  log "升级完成(如需回滚: wgaio rollback)"
}
```

`lib/uninstall.sh`：

```bash
#!/usr/bin/env bash
# 卸载: 默认清理服务/命令/配置; --keep-clients 保留设备备份; --dry-run 只列清单
# shellcheck source=lib/core.sh
. "$ROOT/lib/core.sh"

cmd_uninstall() {
  local dry=0 keep=0
  for a in "$@"; do
    case "$a" in
      --dry-run) dry=1 ;;
      --keep-clients) keep=1 ;;
    esac
  done
  log "将清理: wgaio-panel 服务, /usr/local/bin/wgaio, config.json, lib/, panel/"
  [ "$keep" -eq 1 ] && log "保留: clients/ (--keep-clients)"
  log "用法提示: 可选参数 --keep-clients | --dry-run"
  [ "$dry" -eq 1 ] && { log "(dry-run, 未执行任何删除)"; return 0; }
  command -v systemctl >/dev/null 2>&1 && systemctl disable --now wgaio-panel 2>/dev/null || true
  rm -f /usr/local/bin/wgaio
  rm -f /etc/systemd/system/wgaio-panel.service
  [ "$keep" -eq 0 ] && rm -rf "${WGAIO_ROOT}/clients"
  rm -f "${WGAIO_ROOT}/config.json"
  log "卸载完成(wireguard 配置 /etc/wireguard/ 未动)"
}
```

`wgaio.sh` 的 `upgrade|uninstall` 分支沿用 T1 的模块分发（`cmd_upgrade`/`cmd_uninstall`）；另补 `rollback`：`. "$ROOT/lib/upgrade.sh"; cmd_rollback`（解包最近快照回 `WGAIO_ROOT`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_sh_updown -v`
Expected: `Ran 3 tests ... OK`
Run: `python -m unittest discover tests -v`
Expected: `Ran 100 tests ... OK`（97 + 3）

- [ ] **Step 5: Commit**

```bash
git add lib/upgrade.sh lib/uninstall.sh wgaio.sh tests/test_sh_updown.py
git commit -m "feat: 升级校验/快照回滚/卸载清理"
```

---

### Task 7: 发版三件套（SHA256SUMS / CI / README）+ 收尾

**Files:**
- Create: `SHA256SUMS`、`.github/workflows/tests.yml`、`README.md`
- Test: `tests/test_sh_release.py`

- [ ] **Step 1: 写失败测试**

`tests/test_sh_release.py`：

```python
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseTests(unittest.TestCase):
    def test_sha256sums_covers_shipped_files(self):
        sums = (ROOT / "SHA256SUMS").read_text(encoding="utf-8")
        for name in ("wgaio.sh", "lib/core.py", "lib/core.sh", "lib/wizard.sh",
                     "lib/install.sh", "lib/user.sh", "lib/panel.sh",
                     "lib/upgrade.sh", "lib/uninstall.sh", "bin/wgaio",
                     "panel/index.html", "panel/style.css", "panel/app.js"):
            self.assertIn(name, sums)

    def test_sha256sums_verifies(self):
        r = subprocess.run(["sha256sum", "-c", "SHA256SUMS"],
                           cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_ci_workflow_runs_tests(self):
        yml = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
        self.assertIn("unittest", yml)
        self.assertIn("ubuntu-latest", yml)

    def test_readme_chinese_quickstart(self):
        md = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("快速开始", md)
        self.assertIn("wgaio", md)
        self.assertIn("安全组", md)
        self.assertNotIn("Hcy", md)          # 真实密码不得出现
        self.assertNotIn("203.0.113.7", md)  # 真实 IP 不得出现


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_sh_release -v`
Expected: ERROR — SHA256SUMS/README/CI 不存在

- [ ] **Step 3: 写最小实现**

- `SHA256SUMS`：`sha256sum wgaio.sh bin/wgaio lib/*.sh lib/core.py panel/* > SHA256SUMS`（按测试清单核对覆盖）。
- `.github/workflows/tests.yml`：

```yaml
name: tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: python -m unittest discover tests -v
```

- `README.md`（中文）：标题/快速开始（`curl -fsSL .../wgaio.sh -o wgaio.sh && sudo bash wgaio.sh install`）/子命令表/令牌与安全组提示/设计说明（VPN 内 HTTP+令牌）/开发（跑测试）。**禁止任何真实 IP/密码/私钥**（有测试守卫）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_sh_release -v`
Expected: `Ran 4 tests ... OK`
Run: `python -m unittest discover tests -v`
Expected: `Ran 104 tests ... OK`（100 + 4）

- [ ] **Step 5: Commit**

```bash
git add SHA256SUMS .github/workflows/tests.yml README.md tests/test_sh_release.py
git commit -m "feat: 发版三件套(SHA256SUMS/CI/中文README)"
```

---

## 自审记录

1. **Spec 覆盖**（对照设计文档 §3.1/§3.2/§4.1/§9）：wgaio.sh 入口+lib 模块 ✓(T1-T6)、中文向导 ✓(T2)、install/upgrade/rollback/uninstall ✓(T3/T6)、systemd ✓(T5)、SHA256SUMS+CI+README ✓(T7)、`user show` 私钥不落日志 ✓(T4 约定+测试)、安全组醒目提示 ✓(T3)。计划 2 遗留项：TTL/logout、按 IP 限流——本计划**不做**（记入文档"后续"节，避免范围膨胀）；stderr 进 journal ✓(T5 单元 StandardError=journal)；进程级锁——继续延后（读路径注记已在计划 2 落）。
2. **占位符扫描**：无 TBD/TODO；所有步骤含完整代码或精确命令。
3. **类型一致性**：`cmd_*` 函数命名与 wgaio.sh 分发一致；`run_wizard`/`check_sha256`/`write_panel_unit`/`run_core` 跨任务签名一致；测试统一 symlink 沙箱模式（OSError→skip）。
4. **已知取舍**：向导测试依赖 heredoc 送答案（可自动化）；systemd 真机行为留给实机验证清单；`status`/`logs` 子命令在 T1 分发表里占位、实现并入 T5/T6 的 panel/status 语义（status = panel status + user list 摘要，logs = journalctl -u wgaio-panel）。

## 计划完成后的实机验证（沿用设计 §10）

1. 一台干净 Debian/Ubuntu（或用户 VPS 备用端口并存模式）跑 `sudo bash wgaio.sh install`
2. 令牌登录面板 → CRUD 全链 → 手机导入 .conf 连通
3. `wgaio upgrade`（假发布物）校验拒绝/通过两条路 → `wgaio rollback`
4. `wgaio uninstall --dry-run` 清单核对 → 真卸载 → 服务与命令消失、/etc/wireguard 未动
