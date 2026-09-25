# 计划 2：HTTP 面板与令牌鉴权 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `lib/core.py` 加 `--serve` HTTP 模式——令牌登录、REST API（按 name 键）、并发安全的设备管理、复古终端风面板静态页——纯标准库，与 CLI 共享同一份核心逻辑。

**Architecture:** 在 core.py 追加 HTTP 层（ThreadingHTTPServer + 自定义 Handler），API 复用 T1-T8 的 `add_peer`/`remove_peer`/`update_peer`/`full_status`/`show_conf`；并发写操作收口到 `_conf_lock` 修 TOCTOU；令牌 = 安装时生成的随机串，配置里存 sha256 哈希，登录发 HttpOnly Cookie 会话。静态页从 `H:\wg-panel-build\static\` 移植改造（登录页、模式选择、网关路由字段、name 键 API）。

**Tech Stack:** Python 3.9+ 标准库（http.server/threading/hmac/hashlib/secrets/http.cookies/shurllib.parse）；无第三方依赖。

**运行说明:** Windows Git Bash 用 `python` 代替 `python3`。HTTP 测试起临时服务器（port 0 自动分配）+ `urllib.request`，无需真机 wg。基线：计划 1 合入后 `python -m unittest discover tests -v` = 64 OK。

---

## 文件结构

| 文件 | 变化 | 创建于 |
|---|---|---|
| `lib/core.py` | 追加：`_conf_lock`/`_sessions`、`hash_token`/`verify_token`、`PanelHandler`、`serve()`、`--serve` 分支 | T1-T4、T6 |
| `panel/index.html` / `style.css` / `app.js` | 自 `H:\wg-panel-build\static\` 移植改造 | T5 |
| `tests/test_lock.py` | 并发添加拿不同 IP | T1 |
| `tests/test_token.py` | 令牌哈希/校验 | T2 |
| `tests/test_http_auth.py` | 登录/401/CSRF/静态放行 | T3 |
| `tests/test_http_api.py` | REST 路由全链路 | T4 |

**core.py 新增模块级对象（T1 创建，后续复用，别改名）：**

```python
_conf_lock = threading.Lock()      # 串行化 conf 读改写(修 TOCTOU)
_sessions_lock = threading.Lock()
_sessions = set()                  # 内存会话: token_hex 串集合
```

（`threading`/`hmac`/`hashlib`/`secrets` 等 import 在 T1/T2 追加段顶部引入，不动 T1 历史头部。）

---

### Task 1: 并发锁收口（修 add_peer TOCTOU）

**Files:**
- Modify: `lib/core.py`（追加锁对象；给 `add_peer`/`remove_peer`/`update_peer` 的"解析→写盘"段加锁）
- Test: `tests/test_lock.py`

- [ ] **Step 1: 写失败测试**

`tests/test_lock.py`：

```python
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import core


CFG = {"vpn_cidr": "10.66.66.0/24", "endpoint": "203.0.113.1:51820",
       "client_dns": "1.1.1.1", "lan_cidrs": [], "default_mode": "split"}


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._orig = (core.WG_CONF, core.CLIENTS)
        core.WG_CONF = root / "wg0.conf"
        core.CLIENTS = root / "clients"
        core.WG_CONF.write_text("[Interface]\nPrivateKey = S\n", encoding="utf-8")

    def tearDown(self):
        core.WG_CONF, core.CLIENTS = self._orig
        self.tmp.cleanup()

    @mock.patch("core.wg_set_peer")
    @mock.patch("core.server_pubkey", return_value="SPUB")
    def test_parallel_adds_get_distinct_ips(self, m_pub, m_set):
        results, errors = [], []

        def worker(i):
            try:
                with mock.patch("core.gen_keypair",
                                return_value=("PRIV%d" % i, "PUB%d" % i)):
                    meta, _ = core.add_peer("dev%d" % i, None, None, None,
                                            None, None, CFG)
                results.append(meta["ip"])
            except Exception as e:  # noqa: BLE001 - 测试收集并发错误
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(set(results)), 8, "并发添加分配到了重复 IP: %s" % results)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败（无锁时大概率红）**

Run: `python -m unittest tests.test_lock -v`
Expected: 可能直接 FAIL（`len(set(results)) < 8`）或偶发绿（竞态）——若第一次绿，重跑几次或把线程数调到 8 已足够暴露；以 Step 4 加锁后 100% 稳绿为准。

- [ ] **Step 3: 写最小实现**

`lib/core.py` 追加段顶部（`def wg_set_peer` 之前）：

```python
import threading

_conf_lock = threading.Lock()
```

给三个函数的临界区加锁（保持函数签名/返回值不变；只包住"parse_conf → write_conf → wg_set_peer"和客户端存档整段）：

```python
def add_peer(name, ip, dns, keepalive, mode, routes, cfg):
    name = normalize_name(name)
    with _conf_lock:
        iface_lines, peers = parse_conf()
        ...  # 原函数体全部在锁内(从 dup 检查到 save_client)
        return meta, conf_text
```

`remove_peer(name, force=False)` 与 `update_peer(...)` 同样：函数体整体进 `with _conf_lock:`（函数体本就短，整体加锁最简单可靠）。`update_peer` 里对 `load_config()` 的调用移到锁外（cfg 解析不碰 conf 文件）。

- [ ] **Step 4: 跑测试确认通过（连跑 3 次稳绿）**

Run: `python -m unittest tests.test_lock -v && python -m unittest tests.test_lock -v && python -m unittest tests.test_lock -v`
Expected: 3 次 `Ran 1 test ... OK`

Run: `python -m unittest discover tests -v`
Expected: `Ran 65 tests ... OK`

- [ ] **Step 5: Commit**

```bash
git add lib/core.py tests/test_lock.py
git commit -m "fix: 设备读改写加并发锁, 根治 next_ip TOCTOU"
```

---

### Task 2: 令牌哈希与常量时间校验

**Files:**
- Modify: `lib/core.py`（追加函数）
- Test: `tests/test_token.py`

- [ ] **Step 1: 写失败测试**

`tests/test_token.py`：

```python
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import core


class TokenTests(unittest.TestCase):
    def test_hash_stable_and_not_plaintext(self):
        h = core.hash_token("secret-token")
        self.assertEqual(h, core.hash_token("secret-token"))
        self.assertNotEqual(h, "secret-token")
        self.assertEqual(len(h), 64)  # sha256 hex

    def test_verify(self):
        h = core.hash_token("secret-token")
        self.assertTrue(core.verify_token("secret-token", h))
        self.assertFalse(core.verify_token("wrong", h))
        self.assertFalse(core.verify_token("secret-token", ""))
        self.assertFalse(core.verify_token("", h))

    def test_verify_tolerates_bad_hash_format(self):
        self.assertFalse(core.verify_token("x", "not-hex-but-string"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_token -v`
Expected: ERROR — `AttributeError: module 'core' has no attribute 'hash_token'`

- [ ] **Step 3: 写最小实现**

`lib/core.py` 追加：

```python
def hash_token(plain):
    return hashlib.sha256((plain or "").encode("utf-8")).hexdigest()


def verify_token(plain, token_hash):
    if not token_hash:
        return False
    try:
        return hmac.compare_digest(hash_token(plain), str(token_hash))
    except Exception:
        return False
```

（`import hashlib`/`import hmac` 加到追加段顶部的 import 行。）

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_token -v`
Expected: `Ran 3 tests ... OK`

- [ ] **Step 5: Commit**

```bash
git add lib/core.py tests/test_token.py
git commit -m "feat: 令牌 sha256 哈希与常量时间校验"
```

---

### Task 3: HTTP 骨架 + 令牌鉴权 + `--serve` 接线

**Files:**
- Modify: `lib/core.py`（追加 `PAGES`/`PanelHandler`/`start_server`/`serve`；`main()` 加 `--serve` 分支——这是对 T8 代码的授权修改）
- Test: `tests/test_http_auth.py`

- [ ] **Step 1: 写失败测试**

`tests/test_http_auth.py`：

```python
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import core


class HttpTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._orig = (core.WG_CONF, core.CLIENTS)
        core.WG_CONF = root / "wg0.conf"
        core.CLIENTS = root / "clients"
        core.WG_CONF.write_text("[Interface]\nPrivateKey = S\n", encoding="utf-8")
        core._sessions.clear()
        self.cfg = {"vpn_cidr": "10.66.66.0/24", "endpoint": "203.0.113.1:51820",
                    "client_dns": "1.1.1.1", "lan_cidrs": [], "default_mode": "split",
                    "wg_port": 51820, "panel_bind": "127.0.0.1", "panel_port": 0,
                    "panel_token_hash": core.hash_token("topsecret")}
        self.httpd = core.start_server(self.cfg)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        core.WG_CONF, core.CLIENTS = self._orig
        self.tmp.cleanup()

    def req(self, method, path, body=None, cookie=None, ctype="application/json"):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        data, headers = None, {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = ctype
        if cookie:
            headers["Cookie"] = cookie
        r = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read().decode("utf-8")

    def login(self):
        st, hd, body = self.req("POST", "/api/login", {"token": "topsecret"})
        self.assertEqual(st, 200, body)
        return hd.get("Set-Cookie", "").split(";")[0]


class AuthTests(HttpTestBase):
    def test_static_no_auth(self):
        st, hd, body = self.req("GET", "/")
        self.assertEqual(st, 200)
        self.assertIn("text/html", hd.get("Content-Type", ""))
        st, hd, body = self.req("GET", "/static/style.css")
        self.assertEqual(st, 200)

    def test_api_requires_auth(self):
        st, hd, body = self.req("GET", "/api/status")
        self.assertEqual(st, 401)

    def test_login_wrong_token(self):
        st, hd, body = self.req("POST", "/api/login", {"token": "bad"})
        self.assertEqual(st, 401)
        self.assertNotIn("Set-Cookie", hd)

    def test_login_and_access(self):
        cookie = self.login()
        st, hd, body = self.req("GET", "/api/status", cookie=cookie)
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_login_cookie_httponly(self):
        st, hd, body = self.req("POST", "/api/login", {"token": "topsecret"})
        self.assertEqual(st, 200)
        self.assertIn("HttpOnly", hd.get("Set-Cookie", ""))

    def test_csrf_requires_json(self):
        st, hd, body = self.req("POST", "/api/login", {"token": "topsecret"},
                                ctype="application/x-www-form-urlencoded")
        self.assertEqual(st, 415)


class ServeFlagTests(unittest.TestCase):
    def test_serve_flag_dispatches(self):
        with mock.patch("core.serve") as m:
            code = core.main(["--serve"])
        self.assertEqual(code, 0)
        m.assert_called_once()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_http_auth -v`
Expected: ERROR — `AttributeError: module 'core' has no attribute 'start_server'`

- [ ] **Step 3: 写最小实现**

`lib/core.py` 追加段顶部 import 行扩展为（追加段自己的 import，不动历史头部）：

```python
import secrets
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote
```

追加 `PAGES` 白名单 + `PanelHandler` + `start_server` + `serve`：

```python
PAGES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/static/style.css": ("style.css", "text/css; charset=utf-8"),
    "/static/app.js": ("app.js", "application/javascript; charset=utf-8"),
}
MAX_BODY = 64 * 1024


class PanelHandler(BaseHTTPRequestHandler):
    server_version = "wgaio/1.0"

    def log_message(self, fmt, *args):
        pass  # 不落访问日志: 防止令牌/私钥随日志外泄

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _body(self):
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise ApiError("只接受 application/json", 415)
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            raise ApiError("请求体不合法", 400)
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            raise ApiError("请求体不是合法 JSON", 400)
        if not isinstance(data, dict):
            raise ApiError("请求体须为 JSON 对象", 400)
        return data

    def _session(self):
        raw = self.headers.get("Cookie") or ""
        c = SimpleCookie()
        try:
            c.load(raw)
        except Exception:
            return None
        m = c.get("wgaio_sess")
        return m.value if m else None

    def _authed(self):
        s = self._session()
        if not s:
            return False
        with _sessions_lock:
            return s in _sessions

    def _cfg(self):
        return self.server.app_cfg

    def _handle(self, method):
        try:
            u = urlparse(self.path)
            parts = [unquote(x) for x in u.path.split("/") if x]
            if u.path in PAGES and method == "GET":
                fname, ctype = PAGES[u.path]
                self._send(200, (BASE / "panel" / fname).read_bytes(), ctype)
                return
            if parts[:2] == ["api", "login"] and method == "POST":
                b = self._body()
                if not verify_token(b.get("token"), self._cfg().get("panel_token_hash")):
                    self._json({"ok": False, "error": "令牌错误"}, 401)
                    return
                sess = secrets.token_hex(32)
                with _sessions_lock:
                    _sessions.add(sess)
                self._send(200, json.dumps({"ok": True}, ensure_ascii=False),
                           extra={"Set-Cookie":
                                  "wgaio_sess=%s; HttpOnly; SameSite=Strict; Path=/" % sess})
                return
            if parts[:1] == ["api"]:
                if not self._authed():
                    self._json({"ok": False, "error": "未登录"}, 401)
                    return
                self._api(method, parts, u)
                return
            self._json({"ok": False, "error": "页面不存在"}, 404)
        except ApiError as e:
            self._json({"ok": False, "error": str(e)}, e.code)
        except Exception as e:
            self._json({"ok": False, "error": "内部错误: %s" % e}, 500)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_DELETE(self):
        self._handle("DELETE")
```

`_api` 方法（T3 先给骨架，T4 填路由）：

```python
    def _api(self, method, parts, u):
        raise ApiError("接口不存在", 404)
```

`start_server` + `serve`：

```python
def start_server(cfg):
    bind = cfg.get("panel_bind") or "127.0.0.1"
    port = int(cfg.get("panel_port") or 8888)
    httpd = ThreadingHTTPServer((bind, port), PanelHandler)
    httpd.app_cfg = cfg
    return httpd


def serve(cfg=None):
    cfg = cfg or load_config()
    httpd = start_server(cfg)
    host, port = httpd.server_address[0], httpd.server_address[1]
    print("wgaio 面板已启动: http://%s:%d (令牌登录)" % (host, port), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
```

`main()` 修改（授权修改 T8 代码）：`parser.add_argument("--serve", action="store_true", help="启动面板 HTTP 服务")` 加在 `sub = ...` 之前；`sub` 的 `required=True` 改 `required=False`；`args = parser.parse_args(argv)` 之后、`try:` 之前插入：

```python
    if args.serve:
        try:
            serve()
        except ApiError as e:
            print("错误: %s" % e, file=sys.stderr)
            return 1
        return 0
    if not args.cmd:
        parser.error("需要子命令或 --serve")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_http_auth -v`
Expected: `Ran 7 tests ... OK`

Run: `python -m unittest discover tests -v`
Expected: `Ran 75 tests ... OK`（68 旧 + 7 新；此时 `_api` 尚未实现是预期——T4 填）

- [ ] **Step 5: Commit**

```bash
git add lib/core.py tests/test_http_auth.py
git commit -m "feat: HTTP 骨架与令牌会话鉴权(--serve)"
```

---

### Task 4: REST 路由（按 name 键）

**Files:**
- Modify: `lib/core.py`（实现 `_api`）
- Test: `tests/test_http_api.py`

- [ ] **Step 1: 写失败测试**

`tests/test_http_api.py`：

```python
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import core


class ApiTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._orig = (core.WG_CONF, core.CLIENTS)
        core.WG_CONF = root / "wg0.conf"
        core.CLIENTS = root / "clients"
        core.WG_CONF.write_text("[Interface]\nPrivateKey = S\n", encoding="utf-8")
        core._sessions.clear()
        self.cfg = {"vpn_cidr": "10.66.66.0/24", "endpoint": "203.0.113.1:51820",
                    "client_dns": "1.1.1.1", "lan_cidrs": ["192.168.1.0/24"],
                    "default_mode": "split", "wg_port": 51820,
                    "panel_bind": "127.0.0.1", "panel_port": 0,
                    "panel_token_hash": core.hash_token("topsecret")}
        self.httpd = core.start_server(self.cfg)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        st, hd, body = self.req("POST", "/api/login", {"token": "topsecret"})
        self.cookie = hd.get("Set-Cookie", "").split(";")[0]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        core.WG_CONF, core.CLIENTS = self._orig
        self.tmp.cleanup()

    def req(self, method, path, body=None, ctype="application/json"):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        data, headers = None, {"Cookie": self.cookie}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = ctype
        r = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, dict(resp.headers), resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read().decode("utf-8")


@mock.patch("core.wg_set_peer")
@mock.patch("core.server_pubkey", return_value="SPUB")
@mock.patch("core.gen_keypair", return_value=("PRIV", "PUB"))
class ApiTests(ApiTestBase):
    def test_add_status_conf_del(self, m_gen, m_pub, m_set):
        st, hd, body = self.req("POST", "/api/peers", {"name": "phone"})
        self.assertEqual(st, 200, body)
        data = json.loads(body)
        self.assertIn("PrivateKey = PRIV", data["conf"])

        st, hd, body = self.req("GET", "/api/status")
        names = [p["name"] for p in json.loads(body)["peers"]]
        self.assertIn("phone", names)

        st, hd, body = self.req("GET", "/api/peers/phone/conf")
        self.assertEqual(st, 200)
        self.assertIn("attachment", hd.get("Content-Disposition", ""))
        self.assertIn("PrivateKey = PRIV", body)

        st, hd, body = self.req("DELETE", "/api/peers/phone")
        self.assertEqual(st, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_del_gateway_409_then_force(self, m_gen, m_pub, m_set):
        self.req("POST", "/api/peers",
                 {"name": "router", "routes": "192.168.1.0/24"})
        st, hd, body = self.req("DELETE", "/api/peers/router")
        self.assertEqual(st, 409)
        st, hd, body = self.req("DELETE", "/api/peers/router?force=1")
        self.assertEqual(st, 200)

    def test_patch_rename_and_ip(self, m_gen, m_pub, m_set):
        self.req("POST", "/api/peers", {"name": "phone"})
        st, hd, body = self.req("PATCH", "/api/peers/phone",
                                {"new_name": "phone2", "ip": "10.66.66.9"})
        self.assertEqual(st, 200, body)
        st, hd, body = self.req("GET", "/api/status")
        row = [p for p in json.loads(body)["peers"] if p["name"] == "phone2"][0]
        self.assertEqual(row["ip"], "10.66.66.9")

    def test_conf_404_unknown(self, m_gen, m_pub, m_set):
        st, hd, body = self.req("GET", "/api/peers/ghost/conf")
        self.assertEqual(st, 404)

    def test_bad_json_400(self, m_gen, m_pub, m_set):
        st, hd, body = self.req("POST", "/api/peers", "not-dict")
        self.assertIn(st, (400, 415))


if __name__ == "__main__":
    unittest.main()
```

（`test_bad_json_400` 传字符串体——`json.dumps("not-dict")` 生成 JSON 标量，`_body` 的对象校验返回 400；若实现先在 415 拦下也接受。）

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m unittest tests.test_http_api -v`
Expected: 全部 FAIL（`_api` 骨架返回 404 "接口不存在"）

- [ ] **Step 3: 写最小实现**

`lib/core.py` 中把 `_api` 骨架替换为（路由按 name 键，`unquote` 已在 `_handle` 做过防御）：

```python
    def _api(self, method, parts, u):
        cfg = self._cfg()
        if parts == ["api", "status"] and method == "GET":
            self._json(full_status(cfg))
            return
        if parts == ["api", "peers"] and method == "POST":
            b = self._body()
            meta, conf_text = add_peer(b.get("name"), b.get("ip"), b.get("dns"),
                                       b.get("keepalive"), b.get("mode"),
                                       b.get("routes"), cfg)
            self._json({"ok": True, "peer": meta, "conf": conf_text})
            return
        if len(parts) == 3 and parts[:2] == ["api", "peers"] and method == "DELETE":
            force = parse_qs(u.query).get("force", ["0"])[0] == "1"
            self._json({"ok": True, **remove_peer(parts[2], force=force)})
            return
        if len(parts) == 3 and parts[:2] == ["api", "peers"] and method == "PATCH":
            b = self._body()
            meta = update_peer(parts[2], new_name=b.get("new_name"), ip=b.get("ip"),
                               dns=b.get("dns"), keepalive=b.get("keepalive"),
                               mode=b.get("mode"), routes=b.get("routes"), cfg=cfg)
            self._json({"ok": True, "peer": meta})
            return
        if len(parts) == 4 and parts[:2] == ["api", "peers"] and parts[3] == "conf" \
                and method == "GET":
            text = show_conf(parts[2])
            self._send(200, text, "text/plain; charset=utf-8",
                       {"Content-Disposition":
                        'attachment; filename="%s.conf"' % parts[2]})
            return
        raise ApiError("接口不存在", 404)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m unittest tests.test_http_api -v`
Expected: `Ran 5 tests ... OK`

Run: `python -m unittest discover tests -v`
Expected: `Ran 80 tests ... OK`

- [ ] **Step 5: Commit**

```bash
git add lib/core.py tests/test_http_api.py
git commit -m "feat: REST 路由(按设备名键, 网关 409/force)"
```

---

### Task 5: 面板静态页移植（登录门 + 模式/网关字段）

**Files:**
- Create: `panel/index.html`（自 `H:\wg-panel-build\static\index.html` 复制后按下述增补）
- Create: `panel/style.css`（自 `H:\wg-panel-build\static\style.css` 复制后追加登录样式）
- Create: `panel/app.js`（**整文件替换**为下方完整代码——旧版按公钥调 API，必须全换）

本任务无新增 Python 测试（HTTP 已被 T3/T4 覆盖）；Step 4 用全套回归 + 页面加载冒烟断言验证。

- [ ] **Step 1: 复制基底文件**

```bash
mkdir -p panel
cp "H:/wg-panel-build/static/index.html" panel/index.html
cp "H:/wg-panel-build/static/style.css" panel/style.css
```

- [ ] **Step 2: index.html 增补两处**

(a) `<div id="msg"></div>` 之前（header 之后）插入登录层：

```html
<div id="login-mask">
  <div class="login-box">
    <h3>访问令牌</h3>
    <input id="login-token" type="password" placeholder="输入安装时生成的令牌" autocomplete="current-password">
    <button class="primary" id="btn-login">登录</button>
    <p class="hint" id="login-err"></p>
  </div>
</div>
```

(b) 「新建设备」表单 form-grid 里 `<button class="primary" id="btn-add">` 之前插入两个字段：

```html
      <label class="field">流量模式
        <select id="f-mode">
          <option value="split">分流(只进内网)</option>
          <option value="full">全隧道(0.0.0.0/0)</option>
        </select>
      </label>
      <label class="field">网关路由段(可选)
        <input id="f-routes" class="mono" placeholder="192.168.1.0/24">
      </label>
```

- [ ] **Step 3: style.css 末尾追加登录样式**

```css
/* ---- 登录层 ---- */
#login-mask {
  display: none; position: fixed; inset: 0; background: rgba(255,255,255,.92);
  align-items: center; justify-content: center; z-index: 20;
}
.login-box {
  background: var(--bg); border: 1px solid var(--border-dark);
  padding: 20px; display: flex; flex-direction: column; gap: 10px; width: 300px;
}
.login-box h3 { font-size: 13px; letter-spacing: 1px; }
```

- [ ] **Step 4: app.js 整文件替换为：**

```javascript
/* wg-allin-one 面板 - 交互逻辑(登录门 + name 键 API) */
"use strict";

const $ = (id) => document.getElementById(id);

function showMsg(text, isErr) {
  const el = $("msg");
  el.textContent = text;
  el.className = isErr ? "err" : "";
  el.style.display = "block";
  if (!isErr) setTimeout(() => { el.style.display = "none"; }, 5000);
}

function openModal(title, bodyHtml) {
  $("modal-title").textContent = title;
  $("modal-body").innerHTML = bodyHtml;
  $("modal-mask").classList.add("show");
}
function closeModal() { $("modal-mask").classList.remove("show"); }
$("modal-mask").addEventListener("click", (e) => {
  if (e.target === $("modal-mask")) closeModal();
});

function fmtBytes(n) {
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
  if (n < 1073741824) return (n / 1048576).toFixed(1) + " MB";
  return (n / 1073741824).toFixed(2) + " GB";
}
function fmtAgo(ts, now) {
  if (!ts) return "从未";
  const d = Math.max(0, now - ts);
  if (d < 60) return d + " 秒前";
  if (d < 3600) return Math.floor(d / 60) + " 分钟前";
  if (d < 86400) return Math.floor(d / 3600) + " 小时前";
  return Math.floor(d / 86400) + " 天前";
}
const STATE_TXT = { ok: "已连接", stale: "掉线", off: "离线" };

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function showLogin() { $("login-mask").style.display = "flex"; }

async function api(path, opts) {
  const res = await fetch(path, opts);
  if (res.status === 401) {
    showLogin();
    throw new Error("未登录或会话过期");
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* 非 JSON */ }
  if (!res.ok) {
    const err = (data && data.error) ? data.error : ("请求失败 HTTP " + res.status);
    const e = new Error(err);
    e.code = res.status;
    throw e;
  }
  return data;
}

async function tryLogin() {
  const token = $("login-token").value.trim();
  if (!token) { $("login-err").textContent = "请输入令牌"; return; }
  try {
    await api("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: token }),
    });
    $("login-mask").style.display = "none";
    $("login-err").textContent = "";
    $("login-token").value = "";
    refresh();
  } catch (e) {
    $("login-err").textContent = "登录失败: " + e.message;
  }
}
$("btn-login").addEventListener("click", tryLogin);
$("login-token").addEventListener("keydown", (e) => {
  if (e.key === "Enter") tryLogin();
});

function extraRoutesOf(p) {
  return p.allowed_ips.filter((a) => a !== p.ip + "/32").join(", ");
}

function renderRows(st) {
  const tb = $("peer-rows");
  if (!st.peers.length) {
    tb.innerHTML = '<tr><td colspan="8" class="muted">还没有设备, 先在上面生成一个</td></tr>';
    return;
  }
  tb.innerHTML = st.peers.map((p) => {
    const gw = p.is_gateway ? ' <span class="dot accent" title="内网网关"></span>' : "";
    const routes = extraRoutesOf(p) || "—";
    return `<tr>
      <td><span class="dot ${p.state}" title="${STATE_TXT[p.state] || p.state}"></span>${STATE_TXT[p.state] || p.state}</td>
      <td>${esc(p.name)}${gw}</td>
      <td class="mono">${esc(p.ip)}</td>
      <td class="mono muted">${esc(routes)}</td>
      <td class="mono">${fmtAgo(p.last_handshake, st.now)}</td>
      <td class="mono">${fmtBytes(p.rx)}</td>
      <td class="mono">${fmtBytes(p.tx)}</td>
      <td class="ops">
        ${p.has_client ? `<button class="mini" onclick="dlConf('${esc(p.name)}')">下载</button>` : ""}
        <button class="mini" onclick="editPeer('${esc(p.name)}')">修改</button>
        <button class="mini danger" onclick="delPeer('${esc(p.name)}',${p.is_gateway})">删除</button>
      </td>
    </tr>`;
  }).join("");
}

async function refresh() {
  try {
    const st = await api("/api/status");
    $("st-iface").innerHTML = `<span class="dot ${st.iface.up ? "ok" : "off"}"></span>${st.iface.up ? "运行中" : "未启动"}`;
    $("st-port").textContent = st.iface.listen_port + "/udp";
    $("st-ep").textContent = st.endpoint;
    $("st-pub").textContent = st.iface.public_key ? st.iface.public_key.slice(0, 16) + "…" : "(未配置)";
    $("st-count").textContent = st.peers.length + " 个";
    if (!$("f-ip").value) $("f-ip").placeholder = st.next_ip || "地址池已满";
    renderRows(st);
  } catch (e) {
    if (e.message !== "未登录或会话过期") showMsg("加载状态失败: " + e.message, true);
  }
}

$("btn-add").addEventListener("click", async () => {
  const name = $("f-name").value.trim();
  if (!name) { showMsg("请填设备名(1-15 位字母/数字/_/-)", true); return; }
  try {
    const d = await api("/api/peers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: name,
        ip: $("f-ip").value.trim(),
        dns: $("f-dns").value.trim(),
        keepalive: $("f-ka").value.trim(),
        mode: $("f-mode").value,
        routes: $("f-routes").value.trim(),
      }),
    });
    $("f-name").value = ""; $("f-ip").value = ""; $("f-dns").value = "";
    $("f-ka").value = "25"; $("f-routes").value = "";
    openModal("✓ 已生成: " + d.peer.name + " (" + d.peer.ip + "/" + d.peer.mode + ")",
      `<p class="hint">把下面内容保存为 <b>${esc(d.peer.name)}.conf</b> 发到设备上, 在 WireGuard 里「从文件导入」即可。</p>
       <pre id="conf-text">${esc(d.conf)}</pre>
       <div class="modal-ops">
         <button onclick="copyConf()">复制内容</button>
         <button class="primary" onclick="dlConf('${esc(d.peer.name)}')">下载 .conf</button>
         <button onclick="closeModal()">关闭</button>
       </div>`);
    showMsg("设备 " + d.peer.name + " 已创建");
    refresh();
  } catch (e) { showMsg("创建失败: " + e.message, true); }
});

function copyConf() {
  const t = $("conf-text");
  if (!t) return;
  navigator.clipboard.writeText(t.textContent).then(
    () => showMsg("已复制到剪贴板"),
    () => showMsg("复制失败, 请手动全选复制", true));
}

function dlConf(name) {
  window.open("/api/peers/" + encodeURIComponent(name) + "/conf", "_blank");
}

function editPeer(name) {
  api("/api/status").then((st) => {
    const p = st.peers.find((x) => x.name === name);
    if (!p) { showMsg("设备不存在", true); return; }
    openModal("修改设备: " + p.name,
      `<label class="field">设备名<input id="e-name" maxlength="15" value="${esc(p.name)}"></label>
       <label class="field">内网 IP<input id="e-ip" class="mono" value="${esc(p.ip)}"></label>
       <label class="field">DNS(重下载配置生效)<input id="e-dns" class="mono" value=""></label>
       <label class="field">保活(秒)<input id="e-ka" class="mono" value="${p.keepalive || 25}"></label>
       <label class="field">流量模式
         <select id="e-mode">
           <option value="split">分流(只进内网)</option>
           <option value="full">全隧道(0.0.0.0/0)</option>
         </select>
       </label>
       <label class="field">网关路由段(逗号分隔, 空=无)<input id="e-routes" class="mono" value="${esc(extraRoutesOf(p))}"></label>
       ${p.is_gateway ? '<p class="warn-text">⚠ 这是内网网关, 改动请确认无误。</p>' : ""}
       <p class="hint">改 IP/名称后请重新下载 .conf 导入到设备。全隧道需要服务端转发配合。</p>
       <div class="modal-ops">
         <button class="primary" onclick="saveEdit('${esc(p.name)}')">保存</button>
         <button onclick="closeModal()">取消</button>
       </div>`);
    $("e-mode").value = p.mode || "split";
  }).catch((e) => showMsg("读取设备失败: " + e.message, true));
}

async function saveEdit(name) {
  try {
    await api("/api/peers/" + encodeURIComponent(name), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        new_name: $("e-name").value.trim(),
        ip: $("e-ip").value.trim(),
        dns: $("e-dns").value.trim() || undefined,
        keepalive: $("e-ka").value.trim(),
        mode: $("e-mode").value,
        routes: $("e-routes").value.trim(),
      }),
    });
    closeModal();
    showMsg("已保存修改");
    refresh();
  } catch (e) { showMsg("保存失败: " + e.message, true); }
}

function delPeer(name, isGateway) {
  openModal("删除设备: " + name,
    `<p>确定删除 <b>${esc(name)}</b> ?</p>
     <p class="hint">会同时移除 wg0 里的对等端和已保存的 .conf, 不可恢复。</p>
     ${isGateway ? '<p class="warn-text">⚠ 这是内网网关! 删除后 VPN 进内网会断! 若确定, 再点一次「仍要删除」。</p>' : ""}
     <div class="modal-ops">
       ${isGateway
         ? `<button class="danger" onclick="doDel('${esc(name)}',true)">仍要删除</button>`
         : `<button class="danger" onclick="doDel('${esc(name)}',false)">删除</button>`}
       <button onclick="closeModal()">取消</button>
     </div>`);
}

async function doDel(name, force) {
  try {
    await api("/api/peers/" + encodeURIComponent(name) + (force ? "?force=1" : ""),
              { method: "DELETE" });
    closeModal();
    showMsg("设备已删除");
    refresh();
  } catch (e) {
    if (e.code === 409) {
      openModal("需要二次确认",
        `<p class="warn-text">${esc(e.message)}</p>
         <div class="modal-ops">
           <button class="danger" onclick="doDel('${esc(name)}',true)">仍要删除</button>
           <button onclick="closeModal()">取消</button>
         </div>`);
    } else {
      showMsg("删除失败: " + e.message, true);
    }
  }
}

refresh();
setInterval(refresh, 10000);
```

- [ ] **Step 5: 验证 + Commit**

Run: `python -m unittest discover tests -v`
Expected: `Ran 80 tests ... OK`（无回归；NoSecretsInRepoTests 会顺带扫 panel/ 三个新文件）

Run: `python -c "import pathlib; [print(f.name, f.stat().st_size) for f in pathlib.Path('panel').iterdir()]"`
Expected: index.html / style.css / app.js 三个文件都在

```bash
git add panel/
git commit -m "feat: 面板静态页移植(登录门/模式选择/网关路由字段)"
```

---

### Task 6: 收尾——热更新告警 + 全套回归 + 部署冒烟清单

**Files:**
- Modify: `lib/core.py`（`wg_set_peer` 两条静默路径加 stderr 告警）

- [ ] **Step 1: wg_set_peer 告警**

把 `wg_set_peer` 的两处静默跳过改为带提示（conf 已落盘但内核未同步的不对称必须可见）：

```python
    try:
        r = subprocess.run(["ip", "link", "show", WG_IFACE], capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        print("[wgaio] 警告: 探测 %s 失败, 跳过内核热更新(配置已落盘, 重启 wg 后生效)"
              % WG_IFACE, file=sys.stderr)
        return
    if r.returncode != 0:
        print("[wgaio] 警告: %s 未启动, 跳过内核热更新(配置已落盘)" % WG_IFACE,
              file=sys.stderr)
        return
```

- [ ] **Step 2: 全套回归 + 守卫复跑**

Run: `python -m unittest discover tests -v`
Expected: `Ran 80 tests ... OK`

Run: `python -m unittest tests.test_cli.NoSecretsInRepoTests -v`
Expected: `Ran 1 test ... OK`（panel/ 新文件也干净）

- [ ] **Step 3: --serve 冒烟（本机 127.0.0.1 临时配置）**

Run（一个命令内完成起服、curl、停服）：
```bash
python - <<'PY'
import json, subprocess, sys, tempfile, threading, time, urllib.request
from pathlib import Path
sys.path.insert(0, "lib")
import core
tmp = tempfile.TemporaryDirectory()
root = Path(tmp.name)
core.WG_CONF = root / "wg0.conf"; core.CLIENTS = root / "clients"
core.WG_CONF.write_text("[Interface]\nPrivateKey = S\n", encoding="utf-8")
cfg = {"vpn_cidr": "10.66.66.0/24", "endpoint": "203.0.113.1:51820",
       "client_dns": "1.1.1.1", "lan_cidrs": [], "default_mode": "split",
       "wg_port": 51820, "panel_bind": "127.0.0.1", "panel_port": 0,
       "panel_token_hash": core.hash_token("smoke")}
h = core.start_server(cfg)
threading.Thread(target=h.serve_forever, daemon=True).start()
port = h.server_address[1]
html = urllib.request.urlopen("http://127.0.0.1:%d/" % port, timeout=5).read().decode()
assert "login-mask" in html and "wg-allin-one" in html.lower() or "WG-PANEL" in html
print("SMOKE_OK port=%d" % port)
h.shutdown(); h.server_close()
PY
```
Expected: `SMOKE_OK port=...`

- [ ] **Step 4: Commit**

```bash
git add lib/core.py
git commit -m "fix: wg 热更新跳过时告警, 不再静默吞掉不同步"
```

- [ ] **Step 5: 真机部署冒烟清单（留给实机验证, 不自动化）**

报告里附上这 9 项清单（对应设计 §10：备用端口并存→替换旧面板, wg0.conf 不动）：
1. scp panel/ + lib/core.py 到 VPS `/opt/wgaio/`
2. 生成令牌：`python3 -c "import secrets,hashlib; t=secrets.token_hex(24); print('令牌:',t); print('哈希:',hashlib.sha256(t.encode()).hexdigest())"`（令牌只显示这一次）
3. config.json 写入 panel_token_hash / panel_bind=10.66.66.1 / panel_port=8899（并存测试端口）
4. `python3 /opt/wgaio/lib/core.py --serve` 前台起服确认打印绑定地址
5. 浏览器走 WireGuard 开 `http://10.66.66.1:8899` → 登录页出现
6. 错误令牌 401 / 正确令牌进入 → 列表显示现有设备
7. 建一个测试设备 → 下载 .conf → 手机导入可用 → 删除
8. 测完换正式端口 8888、停旧 wg-panel、注册 systemd（计划 3 做）
9. `NoSecretsInRepoTests` 与全套测试在部署后再次本地复跑

---

## 自审记录

1. **Spec 覆盖**（对照设计文档）：§5 panel_token_hash/panel_bind/panel_port ✓(T3/T4)、§4.2 面板功能（列表/生成/修改/删除/下载/登录/中文/复古风）✓(T5)、§6 TOCTOU 并发 ✓(T1)、§7 分流/全隧道 per-device ✓(T4 路由字段 + T5 表单)、§8 CSRF(仅 application/json)✓(T3)、令牌不进 argv ✓(登录走 POST body)、percent-decode 防御 ✓(T3 `_handle` 统一 unquote；name 键设计使公钥编码坑天然不复现)、§12 公钥 404 坑 ✓(name 键根治)。
2. **占位符扫描**：无 TBD/TODO；所有步骤含完整代码。
3. **类型一致性**：`start_server(cfg)`/`serve(cfg=None)`/`hash_token`/`verify_token`/`_conf_lock`/`_sessions` 命名前后一致；测试统一 `panel_port: 0` 注入法；API JSON 形状与 CLI 层函数返回值一致。
4. **已修的计划内矛盾**：T3 登录响应草稿的错误占位行已改为最终 `_send(..., extra=Set-Cookie)` 版本。
5. **测试数核对**：64 基线 +1(T1) +3(T2) +7(T3) +5(T4) = 80；T5/T6 不加 Python 测试。

## 计划 3 预告（本计划完成后写）

bash 套装：`wgaio.sh` 入口 + `lib/*.sh`（wizard 中文向导/install/upgrade/rollback/uninstall/backup）+ systemd 单元 + SHA256SUMS + GitHub Actions + README。携带遗留项：`user show` 输出含私钥 → 包装层禁止落日志；装完打印「安全组放行 UDP」醒目提示。
