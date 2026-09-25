import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def make_sandbox(tmp):
    root = Path(tmp)
    (root / "lib").mkdir()
    (root / "panel").mkdir()
    for f in ("core.sh", "core.py", "wizard.sh", "install.sh"):
        os.symlink(ROOT / "lib" / f, root / "lib" / f)
    for f in ("index.html", "style.css", "app.js"):
        os.symlink(ROOT / "panel" / f, root / "panel" / f)
    os.symlink(ROOT / "wgaio.sh", root / "wgaio.sh")
    return root


class InstallTests(unittest.TestCase):
    def _run_install(self, extra_env=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        try:
            root = make_sandbox(tmp.name)
        except OSError:
            self.skipTest("需要 symlink 权限")
        all_env = {"WGAIO_SKIP_DETECT": "1", "WGAIO_SKIP_NET_CHECK": "1"}
        if extra_env:
            all_env.update(extra_env)
        env_cmd = " ".join("%s=%s" % kv for kv in all_env.items()) + " "
        script = (env_cmd +
                  "bash wgaio.sh install --dry-run <<'EOF'\n"
                  "\n" "\n" "203.0.113.7:51820\n" "\n" "\n" "\n" "\n" "\nEOF")
        r = subprocess.run(["bash", "-c", script], cwd=str(root),
                           capture_output=True, text=True, timeout=120,
                           encoding="utf-8")
        return r, root

    def test_install_dry_run_writes_layout(self):
        r, root = self._run_install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((root / "config.json").exists())
        self.assertTrue((root / "_stage" / "config.json").exists())
        self.assertFalse((root / "certs").exists())
        self.assertTrue((root / "_stage" / "lib" / "core.py").exists())
        self.assertTrue((root / "_stage" / "panel" / "index.html").exists())
        self.assertIn("安全组", r.stdout)
        self.assertIn("UDP", r.stdout)

    def test_install_keeps_existing_config(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        try:
            root = make_sandbox(tmp.name)
        except OSError:
            self.skipTest("需要 symlink 权限")
        stage = root / "_stage"
        stage.mkdir()
        payload = {
            "vpn_cidr": "10.77.77.0/24",
            "wg_port": 51821,
            "endpoint": "203.0.113.8:51821",
            "client_dns": "1.1.1.1",
            "lan_cidrs": [],
            "panel_bind": "10.77.77.1",
            "panel_port": 9999,
            "panel_token_hash": "keep-me",
            "default_mode": "split",
        }
        (stage / "config.json").write_text(json.dumps(payload), encoding="utf-8")
        r = subprocess.run(
            ["bash", "-c",
             "WGAIO_SKIP_DETECT=1 WGAIO_SKIP_NET_CHECK=1 "
             "bash wgaio.sh install --dry-run </dev/null"],
            cwd=str(root), capture_output=True, text=True, timeout=120,
            encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads((stage / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(got["panel_token_hash"], "keep-me")
        self.assertEqual(got["vpn_cidr"], "10.77.77.0/24")
        self.assertEqual(got["wg_port"], 51821)
        self.assertIn("跳过问答", r.stdout)
        self.assertIn("不再显示", r.stdout)
        self.assertNotIn("wgaio-", r.stdout)

    def test_install_dry_run_ok_without_root(self):
        r, root = self._run_install(extra_env={"WGAIO_FORCE_NONROOT": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_stage_files_inplace_no_crash(self):
        """Regression: stage_files must not cp file onto itself (the pre-fix crash)."""
        script = (
            'set -Eeuo pipefail; '
            'ROOT="$(pwd)"; '
            '. lib/core.sh; . lib/install.sh; '
            'stage_files "$(pwd)"'
        )
        r = subprocess.run(["bash", "-c", script],
                           capture_output=True, text=True, timeout=60,
                           cwd=str(ROOT), encoding="utf-8")
        self.assertEqual(r.returncode, 0,
                         "stage_files in-place crashed (rc=%d): %s" % (r.returncode, r.stderr))
        self.assertIn("跳过复制", r.stderr + r.stdout)

    def test_sync_config_inplace_no_crash(self):
        """Regression: sync_config must not cp config.json onto itself."""
        cfg = ROOT / "config.json"
        existed = cfg.exists()
        if not existed:
            cfg.write_text('{"test": true}\n', encoding="utf-8")
        try:
            script = (
                'set -Eeuo pipefail; '
                'ROOT="$(pwd)"; '
                '. lib/core.sh; . lib/install.sh; '
                'sync_config "$(pwd)"'
            )
            r = subprocess.run(["bash", "-c", script],
                               capture_output=True, text=True, timeout=60,
                               cwd=str(ROOT), encoding="utf-8")
            self.assertEqual(r.returncode, 0,
                             "sync_config in-place crashed (rc=%d): %s" % (r.returncode, r.stderr))
        finally:
            if not existed:
                cfg.unlink(missing_ok=True)

    def test_render_wg0_conf_pure(self):
        """render_wg0_conf is pure: takes args, outputs WireGuard config to stdout."""
        script = (
            'set -Eeuo pipefail; '
            'ROOT="$(pwd)"; '
            '. lib/core.sh; . lib/install.sh; '
            'render_wg0_conf TESTKEY 10.66.66.0/24 51820'
        )
        r = subprocess.run(["bash", "-c", script],
                           capture_output=True, text=True, timeout=60,
                           cwd=str(ROOT), encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Address = 10.66.66.1/24", r.stdout)
        self.assertIn("ListenPort = 51820", r.stdout)
        self.assertIn("PrivateKey = TESTKEY", r.stdout)
        self.assertIn("MASQUERADE", r.stdout)
        self.assertIn("TCPMSS", r.stdout)
        self.assertIn("-s 10.66.66.0/24", r.stdout)
        self.assertNotIn("Table = off", r.stdout)

    def test_init_wg_hub_idempotent(self):
        """init_wg_hub must NOT overwrite an existing wg0.conf."""
        # Create temp file under project root so bash can find it via $(pwd)
        tmpdir = ROOT / "_test_idem_tmp"
        tmpdir.mkdir(exist_ok=True)
        tmpconf = tmpdir / "wg0.conf"
        tmpconf.write_text("existing content\n", encoding="utf-8")
        try:
            # Use $(pwd) so the path works regardless of Windows mount style
            script = (
                'set -Eeuo pipefail; '
                'export WGAIO_WG_CONF="$(pwd)/_test_idem_tmp/wg0.conf"; '
                'ROOT="$(pwd)"; export ROOT; '
                '. lib/core.sh; . lib/install.sh; init_wg_hub'
            )
            r = subprocess.run(["bash", "-c", script],
                               capture_output=True, text=True, timeout=60,
                               cwd=str(ROOT), encoding="utf-8")
            self.assertEqual(r.returncode, 0,
                             "rc=%d stderr=%s" % (r.returncode, r.stderr))
            content = tmpconf.read_text(encoding="utf-8")
            self.assertEqual(content, "existing content\n",
                             "wg0.conf was overwritten — idempotence broken")
            combined = r.stdout + r.stderr
            self.assertIn("已存在", combined)
        finally:
            tmpconf.unlink(missing_ok=True)
            tmpdir.rmdir()


if __name__ == "__main__":
    unittest.main()
