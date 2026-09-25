import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CFG = {"vpn_cidr": "10.66.66.0/24", "endpoint": "203.0.113.1:51820",
       "client_dns": "1.1.1.1", "lan_cidrs": [], "default_mode": "split",
       "wg_port": 51820, "panel_port": 8888, "panel_token_hash": ""}


def make_sandbox(root):
    """Create a sandbox with symlinks to lib/ and wgaio.sh, plus a runner
    script that provisions config and exports WGAIO_BASE/WGAIO_WG_CONF."""
    root = Path(root)
    (root / "lib").mkdir()
    for f in ("core.sh", "core.py", "user.sh"):
        src = ROOT / "lib" / f
        if src.exists():
            try:
                os.symlink(src, root / "lib" / f)
            except OSError:
                pass
    try:
        os.symlink(ROOT / "wgaio.sh", root / "wgaio.sh")
    except OSError:
        pass
    for critical in ("lib/core.py", "lib/user.sh"):
        assert (root / critical).exists(), "沙箱 symlink 失败: %s" % critical
    # runner.sh: creates config/wg0.conf, exports env, delegates to wgaio.sh
    runner = root / "run.sh"
    cfg_json = json.dumps(CFG)
    lines = [
        "#!/usr/bin/env bash",
        "mkdir -p clients",
        "cat > config.json << 'EOF'",
        cfg_json,
        "EOF",
        "printf '[Interface]\\nPrivateKey = S\\n' > wg0.conf",
        'export WGAIO_BASE="$PWD"',
        'export WGAIO_WG_CONF="$PWD/wg0.conf"',
        'bash wgaio.sh "$@"',
    ]
    runner.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    runner.chmod(0o755)
    return root


class UserWrapperTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = ROOT / "_test_user_sandbox"
        if self.sandbox.exists():
            shutil.rmtree(self.sandbox, ignore_errors=True)
        self.sandbox.mkdir()
        make_sandbox(self.sandbox)

    def tearDown(self):
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def test_user_show_never_logged(self):
        """私钥只允许出现在 stdout: 除原始备份外, 任何文件都不得出现。"""
        logdir = self.sandbox / "logs"
        logdir.mkdir()
        # 在沙箱造一个带私钥备份的设备(假密钥, 不触发仓库密钥守卫)
        # 用 bash 创建, 确保 MSYS2 和 Windows Python 都可见
        setup = (
            "mkdir -p clients && "
            "printf '[Interface]\\nPrivateKey = FAKEKEY-not-real\\n"
            "Address = 10.66.66.2/32\\n' > clients/logtest.conf && "
            "bash run.sh user show logtest"
        )
        r = subprocess.run(
            ["bash", "-c", setup],
            cwd=str(self.sandbox), capture_output=True, text=True,
            timeout=60, encoding="utf-8", errors="replace",
            # WGAIO_LOG_DIR 是"额外偏执"扫描目标; 真正的守卫是下面的全沙箱扫描
            env=dict(os.environ, WGAIO_LOG_DIR=str(logdir)))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("FAKEKEY-not-real", r.stdout)   # 私钥走 stdout(设计如此)
        self.assertNotIn("FAKEKEY-not-real", r.stderr,
                         "私钥泄漏到 stderr (systemd 下会进 journal)")
        # 契约: 除原始备份文件外, 任何文件不得出现私钥内容
        conf_p = self.sandbox / "clients" / "logtest.conf"
        for p in (list(self.sandbox.rglob("*"))
                  + list(logdir.rglob("*"))):
            if not p.is_file() or p == conf_p or ".git" in p.parts:
                continue
            body = p.read_text(encoding="utf-8", errors="ignore")
            self.assertNotIn("FAKEKEY-not-real", body,
                             "私钥泄漏到文件: %s" % p)

    def test_user_show_missing_exits_1(self):
        r = subprocess.run(
            ["bash", "run.sh", "user", "show", "ghost"],
            cwd=str(self.sandbox), capture_output=True,
            text=True, timeout=60, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 1)
        self.assertIn("错误", r.stderr)

    def test_user_list_works(self):
        r = subprocess.run(
            ["bash", "run.sh", "user", "list"],
            cwd=str(self.sandbox), capture_output=True, text=True,
            timeout=60, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
