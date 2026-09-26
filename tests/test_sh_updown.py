import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_bash(script, cwd=None):
    return subprocess.run(["bash", "-c", script], cwd=str(cwd or ROOT),
                          capture_output=True, text=True, timeout=60,
                          encoding="utf-8")


class UpgradeTests(unittest.TestCase):
    def test_sha256sums_check_detects_tamper(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "lib").mkdir()
        for f in ("core.sh", "upgrade.sh"):
            os.symlink(ROOT / "lib" / f, root / "lib" / f)
        (root / "payload.txt").write_text("hello", encoding="utf-8")
        r = run_bash(
            'ROOT="$(pwd)"; WGAIO_ROOT="$(pwd)"; . lib/core.sh; . lib/upgrade.sh; check_sha256',
            cwd=root)
        self.assertEqual(r.returncode, 1)   # 无 SHA256SUMS → 拒绝
        (root / "SHA256SUMS").write_text(
            "0000000000000000000000000000000000000000000000000000000000000000  payload.txt\n",
            encoding="utf-8")
        r = run_bash(
            'ROOT="$(pwd)"; WGAIO_ROOT="$(pwd)"; . lib/core.sh; . lib/upgrade.sh; check_sha256',
            cwd=root)
        self.assertEqual(r.returncode, 1)   # 哈希不符 → 拒绝
        self.assertIn("校验", r.stderr)

    def test_sha256sums_check_passes_when_valid(self):
        import hashlib
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "lib").mkdir()
        for f in ("core.sh", "upgrade.sh"):
            os.symlink(ROOT / "lib" / f, root / "lib" / f)
        (root / "payload.txt").write_text("hello", encoding="utf-8")
        h = hashlib.sha256(b"hello").hexdigest()
        (root / "SHA256SUMS").write_text("%s  payload.txt\n" % h, encoding="utf-8", newline="\n")
        r = run_bash(
            'ROOT="$(pwd)"; WGAIO_ROOT="$(pwd)"; . lib/core.sh; . lib/upgrade.sh; check_sha256',
            cwd=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("校验通过", r.stdout + r.stderr)

    def test_verify_reports_tamper_without_fix(self):
        import hashlib
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "lib").mkdir()
        for f in ("core.sh", "upgrade.sh"):
            os.symlink(ROOT / "lib" / f, root / "lib" / f)
        (root / "payload.txt").write_text("hello", encoding="utf-8")
        h = hashlib.sha256(b"hello").hexdigest()
        (root / "SHA256SUMS").write_text("%s  payload.txt\n" % h, encoding="utf-8", newline="\n")
        r = run_bash(
            'ROOT="$(pwd)"; WGAIO_ROOT="$(pwd)"; . lib/core.sh; . lib/upgrade.sh; cmd_verify',
            cwd=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("一致", r.stdout + r.stderr)
        (root / "payload.txt").write_text("tampered", encoding="utf-8")
        r = run_bash(
            'ROOT="$(pwd)"; WGAIO_ROOT="$(pwd)"; . lib/core.sh; . lib/upgrade.sh; cmd_verify',
            cwd=root)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("verify --fix", r.stderr)


class UpgradeApplyTests(unittest.TestCase):
    def test_upgrade_keeps_config_and_rollback_restores_entry(self):
        import hashlib
        import shutil
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        root = base / "inst"
        src = base / "src"
        (root / "lib").mkdir(parents=True)
        (src / "lib").mkdir(parents=True)
        shutil.copy(ROOT / "lib" / "core.sh", root / "lib" / "core.sh")
        shutil.copy(ROOT / "lib" / "core.sh", src / "lib" / "core.sh")
        shutil.copy(ROOT / "lib" / "upgrade.sh", root / "lib" / "upgrade.sh")
        shutil.copy(ROOT / "lib" / "upgrade.sh", src / "lib" / "upgrade.sh")
        (root / "wgaio.sh").write_text("old-entry\n", encoding="utf-8", newline="\n")
        (root / "config.json").write_text("KEEP\n", encoding="utf-8", newline="\n")
        (src / "wgaio.sh").write_text("new-entry\n", encoding="utf-8", newline="\n")
        names = ["wgaio.sh", "lib/core.sh", "lib/upgrade.sh"]
        lines = []
        for name in names:
            digest = hashlib.sha256((src / name).read_bytes()).hexdigest()
            lines.append("%s  %s" % (digest, name))
        (src / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        r = run_bash(
            'ROOT="$(pwd)"; WGAIO_ROOT="$(pwd)"; WGAIO_UPGRADE_SRC="../src"; '
            '. lib/core.sh; . lib/upgrade.sh; cmd_upgrade',
            cwd=root)
        self.assertEqual(r.returncode, 0, "STDOUT:\n%s\nSTDERR:\n%s" % (r.stdout, r.stderr))
        self.assertEqual((root / "wgaio.sh").read_text(encoding="utf-8"), "new-entry\n")
        self.assertEqual((root / "config.json").read_text(encoding="utf-8"), "KEEP\n")
        listed = run_bash('tar -xOf snapshots/wgaio-*.tar.gz wgaio.sh', cwd=root)
        self.assertEqual(listed.stdout, "old-entry\n", listed.stderr)
        r = run_bash(
            'ROOT="$(pwd)"; WGAIO_ROOT="$(pwd)"; . lib/core.sh; . lib/upgrade.sh; cmd_rollback',
            cwd=root)
        self.assertEqual(r.returncode, 0, "STDOUT:\n%s\nSTDERR:\n%s" % (r.stdout, r.stderr))
        self.assertEqual((root / "wgaio.sh").read_text(encoding="utf-8"), "old-entry\n")
        self.assertEqual((root / "config.json").read_text(encoding="utf-8"), "KEEP\n")
        # 快照里没有轨道文件时，回滚必须删掉升级刚写上的那份
        self.assertFalse((root / ".wgaio-track").exists())

    def test_rollback_does_not_restore_config(self):
        import hashlib
        import shutil
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        root = base / "inst"
        src = base / "src"
        (root / "lib").mkdir(parents=True)
        (src / "lib").mkdir(parents=True)
        for name in ("core.sh", "upgrade.sh"):
            shutil.copy(ROOT / "lib" / name, root / "lib" / name)
            shutil.copy(ROOT / "lib" / name, src / "lib" / name)
        (root / "wgaio.sh").write_text("old-entry\n", encoding="utf-8", newline="\n")
        (root / "config.json").write_text('{"token":"old"}\n', encoding="utf-8", newline="\n")
        (src / "wgaio.sh").write_text("new-entry\n", encoding="utf-8", newline="\n")
        names = ["wgaio.sh", "lib/core.sh", "lib/upgrade.sh"]
        lines = []
        for name in names:
            digest = hashlib.sha256((src / name).read_bytes()).hexdigest()
            lines.append("%s  %s" % (digest, name))
        (src / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        r = run_bash(
            'ROOT="$(pwd)"; WGAIO_ROOT="$(pwd)"; WGAIO_UPGRADE_SRC="../src"; '
            '. lib/core.sh; . lib/upgrade.sh; cmd_upgrade',
            cwd=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        # 升级后用户改了配置，回滚不得撤销这次修改
        (root / "config.json").write_text('{"token":"user-changed"}\n', encoding="utf-8", newline="\n")
        r = run_bash(
            'ROOT="$(pwd)"; WGAIO_ROOT="$(pwd)"; . lib/core.sh; . lib/upgrade.sh; cmd_rollback',
            cwd=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((root / "wgaio.sh").read_text(encoding="utf-8"), "old-entry\n")
        self.assertEqual((root / "config.json").read_text(encoding="utf-8"), '{"token":"user-changed"}\n')


class TrackTests(unittest.TestCase):
    def test_same_commit_and_hash_label(self):
        script = """
set -Eeuo pipefail
ROOT="$(pwd)"
. lib/core.sh
. lib/upgrade.sh
same_commit aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
if same_commit aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb; then
  echo 'unexpected same' >&2
  exit 1
fi
hash_label 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
echo
"""
        r = run_bash(script)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("0123456789ab", r.stdout)
        self.assertIn("aaaaaaaaaaaa", r.stdout)

    def test_menu_lists_tracks_and_exits(self):
        r = run_bash("ROOT=\"$(pwd)\"; . lib/core.sh; . lib/menu.sh; cmd_menu <<'EOF'\n99\nEOF\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout + r.stderr
        self.assertIn("管理菜单", out)
        self.assertIn("抢先试用 main", out)
        self.assertIn("升级稳定版", out)

    def test_resolve_commit_stdout_is_only_sha(self):
        script = r"""
set -Eeuo pipefail
ROOT="$(pwd)"
export WGAIO_ROOT="$ROOT"
. lib/core.sh
. lib/upgrade.sh
github_get() {
  printf '%s\n' '{"sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","commit":{"sha":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}' > "$2"
  GITHUB_HTTP=200
}
sha="$(resolve_commit main)"
printf '%s\n' "$sha"
if ref_ok '$(reboot)'; then
  echo 'ref should be rejected' >&2
  exit 1
fi
"""
        # 写到仓库里再执行。Windows 的 bash -c 会拆掉 $(resolve_commit)，
        # 系统临时目录的路径在 WSL bash 里也对不上。
        name = ROOT / "_t_resolve_test.sh"
        name.write_text(script, encoding="utf-8", newline="\n")
        self.addCleanup(name.unlink, missing_ok=True)
        r = subprocess.run(["bash", name.name], cwd=str(ROOT), capture_output=True,
                           text=True, timeout=60, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "a" * 40, "STDOUT=%r STDERR=%r" % (r.stdout, r.stderr))

    def test_empty_track_records_latest(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "lib").mkdir()
        for name in ("core.sh", "upgrade.sh"):
            os.symlink(ROOT / "lib" / name, root / "lib" / name)
        (root / "wgaio.sh").write_text('VERSION="0.2.3"\n', encoding="utf-8", newline="\n")
        r = run_bash(
            'unset WGAIO_REF WGAIO_PERSIST_TRACK WGAIO_FETCH_COMMIT; '
            'ROOT="$(pwd)"; WGAIO_ROOT="$(pwd)"; '
            '. lib/core.sh; . lib/upgrade.sh; write_track "$(pwd)"',
            cwd=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (root / ".wgaio-track").read_text(encoding="utf-8")
        self.assertIn("WGAIO_TRACK_REF=latest", text)
        self.assertNotIn("WGAIO_TRACK_REF=main", text)

    def test_offline_upgrade_keeps_saved_track(self):
        import hashlib
        import shutil
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        root = base / "inst"
        src = base / "src"
        (root / "lib").mkdir(parents=True)
        (src / "lib").mkdir(parents=True)
        for name in ("core.sh", "upgrade.sh"):
            shutil.copy(ROOT / "lib" / name, root / "lib" / name)
            shutil.copy(ROOT / "lib" / name, src / "lib" / name)
        (root / "wgaio.sh").write_text('VERSION="0.2.3"\n', encoding="utf-8", newline="\n")
        (src / "wgaio.sh").write_text('VERSION="0.2.3"\n', encoding="utf-8", newline="\n")
        names = ["wgaio.sh", "lib/core.sh", "lib/upgrade.sh"]
        lines = []
        for name in names:
            digest = hashlib.sha256((src / name).read_bytes()).hexdigest()
            lines.append("%s  %s" % (digest, name))
        (src / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        sha = "a" * 40
        (root / ".wgaio-track").write_text(
            "WGAIO_TRACK_REF=latest\nWGAIO_REPO_SHA=%s\nWGAIO_MODULES_SHA=%s\nWGAIO_VERSION=0.2.3\n"
            % (sha, "b" * 64),
            encoding="utf-8", newline="\n")
        r = run_bash(
            'ROOT="$(pwd)"; WGAIO_ROOT="$(pwd)"; WGAIO_UPGRADE_SRC="../src"; '
            '. lib/core.sh; . lib/upgrade.sh; cmd_upgrade',
            cwd=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (root / ".wgaio-track").read_text(encoding="utf-8")
        self.assertIn("WGAIO_TRACK_REF=latest", text)
        self.assertIn("WGAIO_REPO_SHA=%s" % sha, text)
        self.assertNotIn("WGAIO_TRACK_REF=main", text)

    def test_rollback_without_snapshot_explains(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "lib").mkdir()
        os.symlink(ROOT / "lib" / "core.sh", root / "lib" / "core.sh")
        os.symlink(ROOT / "lib" / "upgrade.sh", root / "lib" / "upgrade.sh")
        r = run_bash(
            'ROOT="$(pwd)"; WGAIO_ROOT="$(pwd)"; . lib/core.sh; . lib/upgrade.sh; cmd_rollback',
            cwd=root)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("没有可用快照", r.stderr)

    def test_logs_rejects_non_numeric(self):
        r = run_bash(
            'ROOT="$(pwd)"; WGAIO_ROOT="$(pwd)"; . lib/core.sh; . lib/logs.sh; cmd_logs abc')
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("行数", r.stderr)

    def test_status_inactive_is_single_line(self):
        script = r"""
set -Eeuo pipefail
d="$(mktemp -d)"
cat > "$d/systemctl" << 'EOF'
#!/bin/sh
echo inactive
exit 3
EOF
chmod +x "$d/systemctl"
export PATH="$d:$PATH"
ROOT="$(pwd)"
export WGAIO_ROOT="$ROOT"
. lib/core.sh
. lib/status.sh
cmd_status || true
"""
        r = run_bash(script)
        out = r.stdout + r.stderr
        self.assertIn("面板服务: 未运行", out)
        self.assertNotIn("面板服务: inactive", out)


class UninstallTests(unittest.TestCase):
    def test_uninstall_dry_run_lists_targets(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "lib").mkdir()
        for f in ("core.sh", "uninstall.sh"):
            os.symlink(ROOT / "lib" / f, root / "lib" / f)
        r = run_bash('ROOT="$(pwd)"; . lib/core.sh; . lib/uninstall.sh; cmd_uninstall --dry-run',
                     cwd=root)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout + r.stderr
        self.assertIn("config.json", out)
        self.assertIn("certs/", out)
        self.assertIn("99-wgaio.conf", out)
        self.assertIn("wg0", out)
        self.assertIn("--keep-clients", out)


class StatusLogsTests(unittest.TestCase):
    def test_status_shows_summary(self):
        r = run_bash("bash wgaio.sh status")
        self.assertIn("状态总览", r.stdout + r.stderr)

    def test_logs_mentions_journal(self):
        r = run_bash("bash wgaio.sh logs")
        out = (r.stdout + r.stderr).lower()
        self.assertTrue("journal" in out or "systemd" in out or "wgaio-panel" in out or "no entries" in out, out)


if __name__ == "__main__":
    unittest.main()
