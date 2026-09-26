import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import core


CFG = {"vpn_cidr": "10.66.66.0/24", "endpoint": "203.0.113.1:51820",
       "client_dns": "1.1.1.1", "lan_cidrs": [], "default_mode": "split",
       "wg_port": 51820}


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = core.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._orig = (core.WG_CONF, core.CLIENTS, core.CONFIG_PATH)
        core.WG_CONF = root / "wg0.conf"
        core.CLIENTS = root / "clients"
        core.CONFIG_PATH = root / "config.json"
        core.WG_CONF.write_text("[Interface]\nPrivateKey = S\n", encoding="utf-8")
        core.CONFIG_PATH.write_text(json.dumps(CFG), encoding="utf-8")

    def tearDown(self):
        core.WG_CONF, core.CLIENTS, core.CONFIG_PATH = self._orig
        self.tmp.cleanup()

    @mock.patch("core.wg_set_peer")
    @mock.patch("core.server_pubkey", return_value="SPUB")
    @mock.patch("core.gen_keypair", return_value=("PRIV", "PUB"))
    def test_add_list_show_del(self, m_gen, m_pub, m_set):
        code, out, err = run_cli("user", "add", "phone")
        self.assertEqual(code, 0)
        self.assertIn("phone", out)

        code, out, err = run_cli("user", "list")
        self.assertIn("phone", out)

        code, out, err = run_cli("user", "show", "phone")
        self.assertIn("PrivateKey = PRIV", out)

        code, out, err = run_cli("user", "del", "phone")
        self.assertEqual(code, 0)

    @mock.patch("core.wg_set_peer")
    @mock.patch("core.server_pubkey", return_value="SPUB")
    @mock.patch("core.gen_keypair", return_value=("PRIV", "PUB"))
    def test_del_gateway_needs_force(self, m_gen, m_pub, m_set):
        run_cli("user", "add", "router", "--routes", "192.168.1.0/24")
        code, out, err = run_cli("user", "del", "router")
        self.assertEqual(code, 1)
        self.assertIn("网关", err)
        code, out, err = run_cli("user", "del", "router", "--force")
        self.assertEqual(code, 0)

    @mock.patch("core.wg_set_peer")
    @mock.patch("core.server_pubkey", return_value="SPUB")
    @mock.patch("core.gen_keypair", side_effect=[("PRIV", "PUB"), ("PRIV2", "PUB2")])
    def test_disable_rotate_backup(self, m_gen, m_pub, m_set):
        self.assertEqual(run_cli("user", "add", "phone")[0], 0)
        code, out, err = run_cli("user", "disable", "phone")
        self.assertEqual(code, 0, err)
        self.assertIn("已停用", out)
        self.assertEqual(core.parse_conf()[1], [])
        code, out, err = run_cli("user", "rotate", "phone")
        self.assertEqual(code, 0, err)
        self.assertIn("旧配置不能再用", out)
        self.assertEqual(core.load_client_meta("phone")["ip"], "10.66.66.2")
        self.assertEqual(core.parse_conf()[1], [])
        code, out, err = run_cli("user", "enable", "phone")
        self.assertEqual(code, 0, err)
        self.assertEqual(core.parse_conf()[1][0]["allowed_ips"], ["10.66.66.2/32"])
        code, out, err = run_cli("backup")
        self.assertEqual(code, 0, err)
        path = Path(out.strip())
        self.assertTrue(path.is_file())
        core.CONFIG_PATH.write_text('{"endpoint":"198.51.100.9:51820"}\n', encoding="utf-8")
        code, out, err = run_cli("backup", "restore", str(path))
        self.assertEqual(code, 0, err)
        self.assertIn("10.66.66.0/24", core.CONFIG_PATH.read_text(encoding="utf-8"))

    def test_bad_name_fails(self):
        code, out, err = run_cli("user", "add", "中文名")
        self.assertEqual(code, 1)
        self.assertIn("错误", err)


class NoSecretsInRepoTests(unittest.TestCase):
    """扫描仓库: 私钥/真实 IP/密码永不入库。模式用片段构造, 源文件不含明文。"""

    BANNED = [
        re.compile(r"^\s*PrivateKey\s*=\s*[A-Za-z0-9+/=]{20,}", re.M),
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        re.compile("Hcy" + chr(64) + "520" + "mh"),
    ]

    def test_repo_clean(self):
        root = Path(__file__).resolve().parents[1]
        hits = []
        for p in root.rglob("*"):
            if not p.is_file() or ".git" in p.parts:
                continue
            if p.suffix not in (".py", ".sh", ".json", ".md", ".html", ".css", ".js",
                                ".yaml", ".yml", ".toml", ".env", ".conf", ".txt"):
                if p.name != ".env":
                    continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            for pat in self.BANNED:
                if pat.search(text):
                    hits.append("%s: %s" % (p.name, pat.pattern))
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
