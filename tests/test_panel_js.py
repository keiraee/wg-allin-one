"""panel/app.js 的源码级回归断言。

仓库没有 JS 测试运行器，CI 只跑 Python 用例，所以这里用源码断言锁住几处
「改回去不会报错、只会静默失效」的前端行为。断言失败说明行为被改回去了，
不是格式问题。
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "panel" / "app.js").read_text(encoding="utf-8")


class PanelJsTests(unittest.TestCase):
    def test_copy_conf_guards_missing_clipboard(self):
        """HTTP(非安全上下文)下 navigator.clipboard 不存在，必须先判存在再调用。"""
        body = APP.split("function copyConf()", 1)[1].split("\n}", 1)[0]
        self.assertIn("if (!navigator.clipboard)", body)
        self.assertLess(body.index("if (!navigator.clipboard)"),
                        body.index("navigator.clipboard.writeText"))

    def test_new_peer_mode_follows_default_mode(self):
        """新建设备的下拉框要跟随 /api/status 的 default_mode。"""
        self.assertIn("st.default_mode", APP)
        self.assertIn('$("f-mode").dataset.dirty', APP)

    def test_disabled_and_manual_peers_hide_actions(self):
        """停用设备不给二维码/下载/换密钥；没有 # name: 的手工对等端不给「修改」。"""
        rows = APP.split("function renderRows", 1)[1].split("\n}", 1)[0]
        self.assertEqual(rows.count("p.has_client && !p.disabled"), 3)
        self.assertIn("p.editable ?", rows)

    def test_edit_modal_shows_current_dns(self):
        """编辑弹窗要预填当前 DNS，并且留空保存能真的清掉（后端把空串当清除）。"""
        self.assertIn('value="${esc(p.dns || "")}"', APP)
        self.assertIn('dns: $("e-dns").value.trim(),', APP)

    def test_login_error_keeps_server_message(self):
        """登录 401 要原样显示服务端的「令牌错误」，不能被 api() 的会话过期分支吃掉。"""
        self.assertIn("raw401", APP)
        login = APP.split('await api("/api/login"', 1)[1].split(");", 1)[0]
        self.assertIn("raw401: true", login)


if __name__ == "__main__":
    unittest.main()
