import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def make_sandbox(tmp):
    root = Path(tmp)
    (root / "lib").mkdir()
    for f in ("core.sh", "core.py", "wizard.sh", "install.sh"):
        os.symlink(ROOT / "lib" / f, root / "lib" / f)
    os.symlink(ROOT / "wgaio.sh", root / "wgaio.sh")
    return root


class WizardTests(unittest.TestCase):
    def _run_wizard(self, heredoc):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        try:
            root = make_sandbox(tmp.name)
        except OSError:
            self.skipTest("需要 symlink 权限")
        script = ("WGAIO_SKIP_DETECT=1 WGAIO_SKIP_NET_CHECK=1 "
                  "bash wgaio.sh install --wizard-only <<'EOF'\n"
                  "%s\nEOF" % heredoc)
        r = subprocess.run(["bash", "-c", script], cwd=str(root),
                           capture_output=True, text=True, timeout=60,
                           encoding="utf-8")
        return r, root

    def test_wizard_writes_config_and_token(self):
        r, root = self._run_wizard(
            "\n"                      # vpn_cidr 默认
            "\n"                      # wg_port 默认
            "203.0.113.7:51820\n"     # endpoint
            "\n"                      # client_dns 默认
            "\n"                      # lan_cidrs 默认空
            "\n"                      # panel_bind 默认
            "\n"                      # panel_port 默认
            "\n")                     # 流量模式默认
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["endpoint"], "203.0.113.7:51820")
        self.assertEqual(cfg["vpn_cidr"], "10.66.66.0/24")
        self.assertEqual(cfg["wg_port"], 51820)
        self.assertEqual(cfg["panel_bind"], "10.66.66.1")
        self.assertIn("10.77.77.0/24", r.stderr)
        self.assertIn("172.31.88.0/24", r.stderr)
        self.assertEqual(len(cfg["panel_token_hash"]), 64)
        self.assertIn("登录密码", r.stdout)
        self.assertIn("请立即保存", r.stdout)

    def test_wizard_endpoint_default_uses_chosen_port_and_gateway(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        try:
            root = make_sandbox(tmp.name)
        except OSError:
            self.skipTest("需要 symlink 权限")
        script = (
            "WGAIO_DETECT_IP=203.0.113.9 WGAIO_SKIP_NET_CHECK=1 "
            "bash wgaio.sh install --wizard-only <<'EOF'\n"
            "3\n"
            "51821\n"
            "\n"
            "\n"
            "\n"
            "\n"
            "\n"
            "\n"
            "EOF\n"
        )
        r = subprocess.run(["bash", "-c", script], cwd=str(root),
                           capture_output=True, text=True, timeout=60,
                           encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["wg_port"], 51821)
        self.assertEqual(cfg["endpoint"], "203.0.113.9:51821")
        self.assertEqual(cfg["vpn_cidr"], "172.31.88.0/24")
        self.assertEqual(cfg["panel_bind"], "172.31.88.1")

    def test_wizard_cidr_choice_two(self):
        r, root = self._run_wizard(
            "2\n" "\n" "203.0.113.7:51820\n" "\n" "\n" "\n" "\n" "\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["vpn_cidr"], "10.77.77.0/24")
        self.assertEqual(cfg["panel_bind"], "10.77.77.1")

    def test_wizard_rejects_bad_cidr_choice(self):
        r, root = self._run_wizard("9\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("1、2 或 3", r.stderr)

    def test_wizard_rejects_endpoint_port_mismatch(self):
        r, root = self._run_wizard(
            "\n" "51821\n" "203.0.113.9:51820\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("服务端口", r.stderr)

    def test_wizard_rejects_bad_dns(self):
        r, root = self._run_wizard(
            "\n" "\n" "203.0.113.7:51820\n" "nope\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("DNS", r.stderr)

    def test_wizard_rejects_bad_ip_endpoint(self):
        r, root = self._run_wizard("\n" "\n" "999.1.1.1:51820\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("endpoint", r.stderr)

    def test_wizard_public_hostname_is_not_sslip(self):
        r, root = self._run_wizard(
            "\n" "\n" "vpn.example.com:51820\n" "\n" "\n" "2\n" "\n" "\n" "\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["tls_cn"], "vpn.example.com")
        self.assertEqual(cfg["tls_mode"], "acme")
        self.assertNotIn("sslip.io", r.stdout + r.stderr)
        self.assertNotIn("HTTPS:", r.stderr)

    def _run_wizard_raw(self, heredoc, env):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        try:
            root = make_sandbox(tmp.name)
        except OSError:
            self.skipTest("需要 symlink 权限")
        script = ("%s WGAIO_SKIP_DETECT=1 bash wgaio.sh install --wizard-only <<'EOF'\n"
                  "%s\nEOF" % (env, heredoc))
        r = subprocess.run(["bash", "-c", script], cwd=str(root),
                           capture_output=True, text=True, timeout=60,
                           encoding="utf-8")
        return r, root

    def test_wizard_rejects_overlapping_local_cidr(self):
        r, root = self._run_wizard_raw("\n", "WGAIO_LOCAL_CIDRS=10.66.66.5/24")
        self.assertEqual(r.returncode, 1)
        self.assertIn("重叠", r.stderr)

    def test_wizard_rejects_busy_udp(self):
        r, root = self._run_wizard_raw(
            "\n\n", "WGAIO_LOCAL_CIDRS=192.168.9.0/24 WGAIO_BUSY_UDP=51820")
        self.assertEqual(r.returncode, 1)
        self.assertIn("已被占用", r.stderr)
        self.assertIn("wg-quick@wg0", r.stderr)

    def test_wizard_rejects_bad_endpoint(self):
        r, root = self._run_wizard(
            "\n"
            "\n"
            "not-an-endpoint\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("endpoint", r.stderr)

    def test_wizard_leading_zero_port_is_decimal(self):
        r, root = self._run_wizard(
            "\n"
            "08\n"
            "203.0.113.7:8\n"
            "\n" "\n" "\n" "\n" "\n")
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["wg_port"], 8)

    def test_wizard_rejects_bad_port(self):
        r, root = self._run_wizard(
            "\n" "\n" "203.0.113.7:51820\n" "\n" "\n" "\n" "abc\n" "\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("端口", r.stderr)

    def test_wizard_rejects_bad_mode(self):
        r, root = self._run_wizard(
            "\n" "\n" "203.0.113.7:51820\n" "\n" "\n" "\n" "\n" "foo\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("1 或 2", r.stderr)

    def test_wizard_public_access_bind(self):
        r, root = self._run_wizard(
            "\n"                      # vpn_cidr 默认
            "\n"                      # wg_port 默认
            "203.0.113.7:51820\n"     # endpoint
            "\n"                      # client_dns 默认
            "\n"                      # lan_cidrs 默认空
            "2\n"                     # 面板访问: 公网
            "\n"                      # 域名默认(sslip.io)
            "\n"                      # 面板端口默认 8443
            "\n")                     # 流量模式默认
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["panel_bind"], "0.0.0.0")
        self.assertEqual(cfg["panel_port"], 8443)
        self.assertEqual(cfg["panel_backend_port"], 8888)
        self.assertEqual(cfg["tls_mode"], "acme")
        self.assertEqual(cfg["tls_cn"], "203-0-113-7.sslip.io")
        self.assertNotIn("tls_cert", cfg)     # 证书交给 Caddy, 不再写证书路径
        self.assertRegex(cfg["panel_path"], r"^wgaio-[0-9a-f]{12}$")
        self.assertIn("/" + cfg["panel_path"] + "/", r.stdout)
        self.assertIn("203-0-113-7.sslip.io", r.stdout)
        self.assertNotIn("HTTPS:", r.stderr)

    def test_wizard_public_plain_http_only_via_env(self):
        """向导不再问 HTTPS；确实要纯 HTTP 时用 WGAIO_TLS=http 显式指定。"""
        r, root = self._run_wizard_raw(
            "\n" "\n" "203.0.113.7:51820\n" "\n" "\n" "2\n" "\n" "\n" "\n",
            "WGAIO_TLS=http")
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["panel_bind"], "0.0.0.0")
        self.assertEqual(cfg["tls_mode"], "off")
        self.assertNotIn("tls_cert", cfg)
        self.assertRegex(cfg["panel_path"], r"^wgaio-[0-9a-f]{12}$")
        self.assertIn("203-0-113-7.sslip.io", r.stdout)
        self.assertIn("明文传输", r.stderr)

    def test_wizard_public_self_signed_via_env(self):
        r, root = self._run_wizard_raw(
            "\n" "\n" "203.0.113.7:51820\n" "\n" "\n" "2\n" "\n" "\n" "\n",
            "WGAIO_TLS=self")
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["tls_mode"], "internal")   # 老名字 self 映射成 Caddy 的 internal
        self.assertNotIn("tls_cert", cfg)
        self.assertIn("不受信", r.stderr)

    def test_wizard_rejects_bad_tls_env(self):
        r, root = self._run_wizard_raw(
            "\n" "\n" "203.0.113.7:51820\n" "\n" "\n" "2\n",
            "WGAIO_TLS=weird")
        self.assertEqual(r.returncode, 1)
        self.assertIn("WGAIO_TLS", r.stderr)

    def test_wizard_rejects_panel_port_80(self):
        """80 留给 Caddy 申请证书, 面板不能占。"""
        r, root = self._run_wizard(
            "\n" "\n" "203.0.113.7:51820\n" "\n" "\n" "\n" "80\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("80", r.stderr)

    def test_wizard_vpn_only_defaults(self):
        """仅 VPN 内: 后端只听内网地址, 默认明文 HTTP(和以前一样)。"""
        r, root = self._run_wizard(
            "\n" "\n" "203.0.113.7:51820\n" "\n" "\n" "\n" "\n" "\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["panel_bind"], "10.66.66.1")
        self.assertEqual(cfg["tls_cn"], "10.66.66.1")
        self.assertEqual(cfg["tls_mode"], "off")
        self.assertEqual(cfg["panel_port"], 8443)
        self.assertEqual(cfg["panel_backend_port"], 8888)

    def test_wizard_vpn_only_self_signed_via_env(self):
        r, root = self._run_wizard_raw(
            "\n" "\n" "203.0.113.7:51820\n" "\n" "\n" "\n" "\n" "\n",
            "WGAIO_TLS=self")
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["tls_mode"], "internal")
        self.assertEqual(cfg["tls_cn"], "10.66.66.1")

    def test_wizard_public_custom_domain(self):
        r, root = self._run_wizard(
            "\n" "\n" "203.0.113.7:51820\n" "\n" "\n" "2\n"
            "panel.example.com\n" "\n" "\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["tls_cn"], "panel.example.com")
        self.assertEqual(cfg["tls_mode"], "acme")

    def test_wizard_public_rejects_bad_domain(self):
        r, root = self._run_wizard(
            "\n" "\n" "203.0.113.7:51820\n" "\n" "\n" "2\n" "bad..domain\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("域名", r.stderr)

    def test_wizard_eof_rejects_empty_endpoint(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        try:
            root = make_sandbox(tmp.name)
        except OSError:
            self.skipTest("需要 symlink 权限")
        r = subprocess.run(["bash", "-c",
                            "WGAIO_SKIP_DETECT=1 WGAIO_SKIP_NET_CHECK=1 "
                            "bash wgaio.sh install --wizard-only </dev/null"],
                           cwd=str(root), capture_output=True, text=True,
                           timeout=60, encoding="utf-8")
        self.assertEqual(r.returncode, 1)
        self.assertIn("endpoint", r.stderr)


class PasswordShapeTests(unittest.TestCase):
    _PATTERN = re.compile(r"^wgaio-[A-Za-z0-9]{5}-[A-Za-z0-9]{5}-[A-Za-z0-9]{5}-[A-Za-z0-9]{5}$")
    _GEN_CMD = (
        "import secrets,string as s; a=s.ascii_letters+s.digits; "
        "g=lambda n:\"\".join(secrets.choice(a) for _ in range(n)); "
        "print(\"wgaio-\" + \"-\".join(g(5) for _ in range(4)))"
    )

    def _gen_token(self):
        return subprocess.run(
            [sys.executable, "-c", self._GEN_CMD],
            capture_output=True, text=True, timeout=10, encoding="utf-8")

    def test_password_format(self):
        r = self._gen_token()
        self.assertEqual(r.returncode, 0, r.stderr)
        token = r.stdout.strip()
        self.assertRegex(token, self._PATTERN,
                         "密码格式应为 wgaio-XXXXX-XXXXX-XXXXX-XXXXX")

    def test_password_uniqueness(self):
        tokens = set()
        for _ in range(10):
            r = self._gen_token()
            self.assertEqual(r.returncode, 0, r.stderr)
            tokens.add(r.stdout.strip())
        self.assertEqual(len(tokens), 10, "10 次生成的密码应全部不同")


if __name__ == "__main__":
    unittest.main()
