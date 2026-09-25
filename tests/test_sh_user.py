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
            shutil.rmtree(self.sandbox)
        self.sandbox.mkdir()
        make_sandbox(self.sandbox)

    def tearDown(self):
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def test_user_show_never_logged(self):
        """包装层不得把 user show 的 stdout 写进任何日志文件。"""
        logdir = self.sandbox / "logs"
        logdir.mkdir()
        r = subprocess.run(
            ["bash", "run.sh", "user", "show", "ghost"],
            cwd=str(self.sandbox), capture_output=True, text=True,
            timeout=60, encoding="utf-8", errors="replace",
            env=dict(os.environ, WGAIO_LOG_DIR=str(logdir)))
        self.assertEqual(r.returncode, 1)  # 无此设备 -> exit 1
        bodies = "".join(
            p.read_text(encoding="utf-8", errors="ignore")
            for p in logdir.rglob("*") if p.is_file())
        self.assertNotIn("PrivateKey", bodies)

    def test_user_list_works(self):
        r = subprocess.run(
            ["bash", "run.sh", "user", "list"],
            cwd=str(self.sandbox), capture_output=True, text=True,
            timeout=60, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
