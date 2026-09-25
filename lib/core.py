#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wg-allin-one 核心：WireGuard 设备管理（CLI 后端）。"""
import argparse
import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import traceback
from collections import OrderedDict
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

def resolve_base(env=None):
    """安装根目录。显式 WGAIO_BASE 优先，否则跟入口的 WGAIO_ROOT，最后才是 /opt/wgaio。"""
    env = os.environ if env is None else env
    raw = env.get("WGAIO_BASE") or env.get("WGAIO_ROOT") or "/opt/wgaio"
    return Path(raw)


BASE = resolve_base()
CLIENTS = BASE / "clients"
WG_CONF = Path(os.environ.get("WGAIO_WG_CONF", "/etc/wireguard/wg0.conf"))
CONFIG_PATH = BASE / "config.json"
WG_IFACE = "wg0"
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,15}$")
IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
CIDR_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$")

DEFAULTS = {
    "vpn_cidr": "10.66.66.0/24",
    "wg_port": 51820,
    "endpoint": "",
    "client_dns": "1.1.1.1",
    "lan_cidrs": [],
    "panel_bind": "",
    "panel_port": 8888,
    "panel_token_hash": "",
    "default_mode": "split",
    "tls_cert": "",
    "tls_key": "",
    "tls_cn": "",
}


class ApiError(Exception):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def load_config(path=None):
    p = Path(path) if path else CONFIG_PATH
    if not p.exists():
        raise ApiError("配置不存在: %s" % p, 500)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        raise ApiError("配置不是合法 JSON: %s" % p, 500)
    cfg = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()}
    for k, v in data.items():
        if k in DEFAULTS:
            cfg[k] = v
    cfg["lan_cidrs"] = cfg.get("lan_cidrs") or []
    validate_config(cfg)
    return cfg


def validate_config(cfg):
    if not CIDR_RE.match(str(cfg.get("vpn_cidr", ""))):
        raise ApiError("vpn_cidr 不是合法网段: %s" % cfg.get("vpn_cidr"))
    try:
        cidr_bounds(cfg["vpn_cidr"])
    except ValueError:
        raise ApiError("vpn_cidr 前缀长度不合法: %s" % cfg.get("vpn_cidr"))
    port = cfg.get("wg_port")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ApiError("wg_port 必须是 1-65535 的整数")
    ep = str(cfg.get("endpoint") or "")
    if not ep or ":" not in ep:
        raise ApiError("endpoint 必须是 IP或域名:端口")
    if cfg.get("default_mode") not in ("split", "full"):
        raise ApiError("default_mode 只能是 split/full")
    for c in cfg.get("lan_cidrs") or []:
        if not CIDR_RE.match(str(c)):
            raise ApiError("lan_cidrs 含非法网段: %s" % c)
    pp = cfg.get("panel_port")
    if type(pp) is not int or not 1 <= pp <= 65535:
        raise ApiError("panel_port 必须是 1-65535 的整数")


def _ip_to_int(ip):
    parts = str(ip).split(".")
    if len(parts) != 4:
        raise ValueError(ip)
    octets = [int(x) for x in parts]
    if any(o > 255 or o < 0 for o in octets):
        raise ValueError(ip)
    return (octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3]


def int_to_ip(n):
    return ".".join(str((n >> s) & 255) for s in (24, 16, 8, 0))


def cidr_bounds(cidr):
    net, _, prefix = str(cidr).partition("/")
    p = int(prefix or 32)
    if not 0 <= p <= 32:
        raise ValueError(cidr)
    mask = (0xFFFFFFFF << (32 - p)) & 0xFFFFFFFF if p else 0
    base = _ip_to_int(net) & mask
    return base, base + (1 << (32 - p)) - 1


def ip_in_cidr(ip, cidr):
    base, last = cidr_bounds(cidr)
    n = _ip_to_int(ip)
    return base <= n <= last


def normalize_name(name):
    name = (name or "").strip()
    if not NAME_RE.match(name) or name[:1] == "-":
        raise ApiError("设备名仅允许 1-15 位字母/数字/下划线/连字符(导入隧道需要合法名称)")
    return name


def normalize_ip(ip, cfg, used, current=None):
    ip = (ip or "").strip()
    try:
        n = _ip_to_int(ip)
    except ValueError:
        raise ApiError("IP 不合法: %s" % ip)
    vpn_cidr = cfg.get("vpn_cidr")
    if not vpn_cidr:
        raise ApiError("配置缺少 vpn_cidr", 500)
    if not ip_in_cidr(ip, vpn_cidr):
        raise ApiError("IP 不在 VPN 网段 %s 内" % vpn_cidr)
    base, _ = cidr_bounds(vpn_cidr)
    if n == base + 1:
        raise ApiError("该 IP 是服务器地址, 请换一个")
    used = set(used or ())
    if current:
        used.discard(current)
    if ip in used:
        raise ApiError("该 IP 已被占用")
    return ip


def normalize_routes(routes):
    if routes is None:
        return []
    if isinstance(routes, str):
        routes = [x.strip() for x in routes.split(",") if x.strip()]
    out = []
    for r in routes:
        r = str(r).strip()
        if not CIDR_RE.match(r):
            raise ApiError("路由段不合法(要形如 192.168.1.0/24): %s" % r)
        try:
            _net, _sep, prefix = r.partition("/")
            if int(prefix or "32") == 0:
                raise ApiError("网关路由不能是默认路由，全隧道请改流量模式")
            cidr_bounds(r)  # 拒绝 /33 之类越界前缀
        except ApiError:
            raise
        except ValueError:
            raise ApiError("路由段不合法(要形如 192.168.1.0/24): %s" % r)
        out.append(r)
    return out


def parse_conf():
    iface_lines, peers, cur, section = [], [], None, None
    if not Path(WG_CONF).exists():
        return iface_lines, peers
    for raw in Path(WG_CONF).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line == "[Interface]":
            section, cur = "iface", None
            iface_lines.append(raw)
            continue
        if line == "[Peer]":
            section = "peer"
            cur = {"name": "", "pubkey": "", "allowed_ips": [], "keepalive": 0, "extra": []}
            peers.append(cur)
            continue
        if section == "iface":
            iface_lines.append(raw)
            continue
        if section == "peer" and cur is not None:
            if line.startswith("#"):
                if not cur["name"]:
                    comment = line.lstrip("#").strip()
                    for pfx in ("name:", "Name =", "name ="):
                        if comment.startswith(pfx):
                            comment = comment[len(pfx):].strip()
                    cur["name"] = comment
                continue
            if "=" in line:
                k, v = (x.strip() for x in line.split("=", 1))
                if k == "PublicKey":
                    cur["pubkey"] = v
                elif k == "AllowedIPs":
                    cur["allowed_ips"] = [x.strip() for x in v.split(",") if x.strip()]
                elif k == "PersistentKeepalive":
                    try:
                        cur["keepalive"] = max(0, int(v))
                    except ValueError:
                        pass
                else:
                    cur["extra"].append(raw)
            elif line:
                cur["extra"].append(raw)
    return iface_lines, peers


def write_conf(iface_lines, peers):
    out = list(iface_lines)
    while out and not out[-1].strip():
        out.pop()
    for p in peers:
        out.append("")
        out.append("[Peer]")
        out.append("# name: %s" % (p["name"] or p["pubkey"][:8]))
        out.append("PublicKey = %s" % p["pubkey"])
        out.append("AllowedIPs = %s" % ", ".join(p["allowed_ips"]))
        if p.get("keepalive"):
            out.append("PersistentKeepalive = %d" % p["keepalive"])
        out.extend(p.get("extra", []))
    text = "\n".join(out).rstrip() + "\n"
    conf = Path(WG_CONF)
    tmp = conf.with_name(conf.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(conf)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise ApiError("写入 wg0.conf 失败", 500)


def used_ips(peers=None):
    used = set()
    for p in (parse_conf()[1] if peers is None else peers):
        for a in p["allowed_ips"]:
            if a.endswith("/32"):
                used.add(a.split("/")[0])
    if Path(CLIENTS).exists():
        for meta_p in Path(CLIENTS).glob("*.json"):
            try:
                ip = json.loads(meta_p.read_text(encoding="utf-8")).get("ip")
                if ip:
                    used.add(ip)
            except Exception:
                pass
    return used


def next_ip(cfg, peers=None):
    used = used_ips(peers)
    base, last = cidr_bounds(cfg["vpn_cidr"])
    for n in range(base + 2, last):
        cand = int_to_ip(n)
        if cand not in used:
            return cand
    raise ApiError("%s 地址已用尽" % cfg["vpn_cidr"])


def split_allowed(cfg, peers, self_name):
    """分流客户端要进隧道的网段：本机网段、全局内网，以及其他设备宣布的网关路由。"""
    routes = []
    for item in cfg.get("lan_cidrs") or []:
        item = str(item)
        if item and item not in routes:
            routes.append(item)
    for peer in peers or []:
        if self_name and peer.get("name") == self_name:
            continue
        for item in peer.get("allowed_ips") or []:
            if item.endswith("/32") or item == cfg.get("vpn_cidr") or item in routes:
                continue
            routes.append(item)
    return ", ".join([cfg["vpn_cidr"]] + routes)


def build_client_conf(priv, ip, cfg, mode, server_pub, keepalive, peers=None, self_name=None):
    try:
        keepalive = int(keepalive)
    except (TypeError, ValueError):
        keepalive = 25
    endpoint = cfg.get("endpoint")
    if not endpoint:
        raise ApiError("配置缺少 endpoint", 500)
    # 本期不做 IPv6。带上 ::/0 会把双栈设备的 IPv6 吸进没有 v6 地址的隧道。
    allowed = "0.0.0.0/0" if mode == "full" else split_allowed(cfg, peers, self_name)
    return (
        "[Interface]\n"
        "PrivateKey = %s\n"
        "Address = %s/32\n"
        "DNS = %s\n"
        "\n"
        "[Peer]\n"
        "PublicKey = %s\n"
        "AllowedIPs = %s\n"
        "Endpoint = %s\n"
        "PersistentKeepalive = %d\n"
    ) % (priv, ip, cfg.get("client_dns") or "1.1.1.1", server_pub, allowed, endpoint, keepalive)


def refresh_client_confs(cfg, peers):
    """网关路由变化后，重写已有私钥的客户端配置。调用方须已持有 wg_lock。"""
    pub = server_pubkey()
    for peer in peers:
        name = peer.get("name") or ""
        if not name:
            continue
        try:
            normalize_name(name)
        except ApiError:
            continue
        priv = read_priv(name)
        meta = load_client_meta(name)
        if not priv or not meta:
            continue
        ip = meta.get("ip") or ""
        if not ip and peer.get("allowed_ips"):
            ip = peer["allowed_ips"][0].split("/")[0]
        if not ip:
            continue
        cfg_run = dict(cfg)
        if meta.get("dns"):
            cfg_run["client_dns"] = meta["dns"]
        text = build_client_conf(
            priv, ip, cfg_run, meta.get("mode") or cfg.get("default_mode") or "split",
            pub, meta.get("keepalive", peer.get("keepalive") or 25), peers, name)
        save_client(name, text, meta)


_PRIVKEY_RE = re.compile(r"^\s*PrivateKey\s*=\s*(\S+)", re.M)


def run_wg(args, input_text=None):
    try:
        r = subprocess.run(["wg", *args], input=input_text,
                           capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        raise ApiError("wg 命令未找到, 请确认已安装 WireGuard 工具", 500)
    except subprocess.TimeoutExpired:
        raise ApiError("wg 命令超时(15s): %s" % " ".join(args), 500)
    if r.returncode != 0:
        raise ApiError(r.stderr.strip() or "wg 命令执行失败", 500)
    return r.stdout


def gen_keypair():
    priv = run_wg(["genkey"]).strip()
    pub = run_wg(["pubkey"], input_text=priv + "\n").strip()
    return priv, pub


def server_pubkey():
    try:
        out = run_wg(["show", WG_IFACE, "public-key"]).strip()
        if out:
            return out
    except ApiError:
        pass
    if Path(WG_CONF).exists():
        m = _PRIVKEY_RE.search(Path(WG_CONF).read_text(encoding="utf-8"))
        if m:
            try:
                return run_wg(["pubkey"], input_text=m.group(1) + "\n").strip()
            except ApiError:
                pass
    return ""


def client_paths(name):
    normalize_name(name)  # 拒绝路径穿越/非法名(纵深防御, T6 也会先校验)
    return Path(CLIENTS) / ("%s.conf" % name), Path(CLIENTS) / ("%s.json" % name)


def save_client(name, conf_text, meta):
    Path(CLIENTS).mkdir(parents=True, exist_ok=True)
    conf_p, meta_p = client_paths(name)
    conf_p.write_text(conf_text, encoding="utf-8")
    # NOTE: chmod 在 Windows 是无操作(NTFS 需 icacls); 生产目标是 Linux
    conf_p.chmod(0o600)
    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    meta_p.chmod(0o600)


def load_client_meta(name):
    _, meta_p = client_paths(name)
    if meta_p.exists():
        return json.loads(meta_p.read_text(encoding="utf-8"))
    return None


def drop_client(name):
    for p in client_paths(name):
        if p.exists():
            p.unlink()


def read_priv(name):
    conf_p, _ = client_paths(name)
    if conf_p.exists():
        m = _PRIVKEY_RE.search(conf_p.read_text(encoding="utf-8"))
        if m:
            return m.group(1)
    return ""


_conf_lock = threading.Lock()
_sessions_lock = threading.Lock()


def _lock_fd(fh):
    if os.name == "posix":
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        return
    import msvcrt
    fh.seek(0, os.SEEK_END)
    if fh.tell() == 0:
        fh.write(b"\0")
        fh.flush()
    fh.seek(0)
    while True:
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
            return
        except OSError:
            time.sleep(0.05)


def _unlock_fd(fh):
    if os.name == "posix":
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return
    import msvcrt
    fh.seek(0)
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


@contextlib.contextmanager
def wg_lock():
    """同一进程用线程锁排队，不同进程用 wg0.conf.lock 互斥。"""
    lock_path = Path(str(WG_CONF) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _conf_lock:
        fh = open(lock_path, "a+b")
        try:
            _lock_fd(fh)
            yield
        finally:
            _unlock_fd(fh)
            fh.close()
# 滑动过期。进程重启后会话自然失效。
SESSION_TTL = 12 * 3600
_sessions = OrderedDict()


def wg_set_peer(pubkey, allowed_ips=None, keepalive=None, remove=False):
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
    if remove:
        run_wg(["set", WG_IFACE, "peer", pubkey, "remove"])
        return
    args = ["set", WG_IFACE, "peer", pubkey]
    if allowed_ips is not None:
        args += ["allowed-ips", allowed_ips]
    if keepalive is not None:
        args += ["persistent-keepalive", str(keepalive)]
    run_wg(args)


def find_peer(peers, name):
    for p in peers:
        if p["name"] == name:
            return p
    return None


def is_gateway(peer, ip):
    return any(a != "%s/32" % ip for a in peer["allowed_ips"])


def add_peer(name, ip, dns, keepalive, mode, routes, cfg):
    name = normalize_name(name)
    with wg_lock():
        iface_lines, peers = parse_conf()
        if find_peer(peers, name) or load_client_meta(name):
            raise ApiError("设备名已存在: %s" % name)
        ip = normalize_ip(ip, cfg, used_ips()) if ip else next_ip(cfg)
        mode = mode or cfg.get("default_mode") or "split"
        if mode not in ("split", "full"):
            raise ApiError("mode 只能是 split/full")
        try:
            keepalive = int(keepalive) if keepalive is not None else 25
        except (TypeError, ValueError):
            keepalive = 25
        keepalive = max(0, min(120, keepalive))
        extra_routes = normalize_routes(routes)

        priv, pub = gen_keypair()
        peer = {"name": name, "pubkey": pub,
                "allowed_ips": ["%s/32" % ip] + extra_routes,
                "keepalive": keepalive, "extra": []}
        peers.append(peer)
        write_conf(iface_lines, peers)
        wg_set_peer(pub, allowed_ips=", ".join(peer["allowed_ips"]), keepalive=keepalive)
        use_dns = dns or cfg.get("client_dns") or "1.1.1.1"
        cfg_run = dict(cfg)
        cfg_run["client_dns"] = use_dns
        conf_text = build_client_conf(priv, ip, cfg_run, mode, server_pubkey(), keepalive, peers, name)
        meta = {"name": name, "pubkey": pub, "ip": ip, "mode": mode,
                "dns": use_dns, "keepalive": keepalive, "routes": extra_routes}
        save_client(name, conf_text, meta)
        refresh_client_confs(cfg, peers)
        conf_text = client_paths(name)[0].read_text(encoding="utf-8")
        return meta, conf_text


def remove_peer(name, force=False, cfg=None):
    with wg_lock():
        iface_lines, peers = parse_conf()
        peer = find_peer(peers, name)
        if not peer:
            raise ApiError("找不到设备: %s" % name, 404)
        ip = peer["allowed_ips"][0].split("/")[0] if peer["allowed_ips"] else ""
        if is_gateway(peer, ip) and not force:
            extras = [a for a in peer["allowed_ips"] if a != "%s/32" % ip]
            raise ApiError("该设备是内网网关(带路由 %s), 删除会断掉进内网; 确认请加 --force"
                           % ", ".join(extras), 409)
        peers.remove(peer)
        write_conf(iface_lines, peers)
        wg_set_peer(peer["pubkey"], remove=True)
        drop_client(name)
        if cfg:
            refresh_client_confs(cfg, peers)
        return {"removed": name}


def update_peer(name, new_name=None, ip=None, dns=None, keepalive=None,
                mode=None, routes=None, cfg=None):
    cfg = cfg or load_config()
    with wg_lock():
        iface_lines, peers = parse_conf()
        peer = find_peer(peers, name)
        if not peer:
            raise ApiError("找不到设备: %s" % name, 404)
        meta = load_client_meta(name) or {"name": name, "pubkey": peer["pubkey"],
                                          "routes": [], "ip": ""}
        priv = read_priv(name)
        old_name = name

        if new_name and new_name != name:
            new_name = normalize_name(new_name)
            if find_peer(peers, new_name) or load_client_meta(new_name):
                raise ApiError("设备名已存在: %s" % new_name)
            peer["name"] = new_name
            meta["name"] = new_name
            name = new_name

        cur_ip = peer["allowed_ips"][0].split("/")[0] if peer["allowed_ips"] else ""
        if ip:
            new_ip = normalize_ip(ip, cfg, used_ips(), current=cur_ip)
            extras = [a for a in peer["allowed_ips"] if a != "%s/32" % cur_ip]
            peer["allowed_ips"] = ["%s/32" % new_ip] + extras
            meta["ip"] = new_ip

        if routes is not None:
            extra_routes = normalize_routes(routes)
            base = peer["allowed_ips"][0]
            peer["allowed_ips"] = [base] + extra_routes
            meta["routes"] = extra_routes

        if keepalive is not None:
            try:
                ka = max(0, min(120, int(keepalive)))
            except (TypeError, ValueError):
                ka = peer.get("keepalive") or 0
            peer["keepalive"] = ka
            meta["keepalive"] = ka

        if mode:
            if mode not in ("split", "full"):
                raise ApiError("mode 只能是 split/full")
            meta["mode"] = mode
        if dns:
            meta["dns"] = dns

        write_conf(iface_lines, peers)
        wg_set_peer(peer["pubkey"], allowed_ips=", ".join(peer["allowed_ips"]),
                    keepalive=peer.get("keepalive") or 0)
        if priv:
            cfg_run = dict(cfg)
            if meta.get("dns"):
                cfg_run["client_dns"] = meta["dns"]
            conf_text = build_client_conf(priv, meta.get("ip") or cur_ip, cfg_run,
                                          meta.get("mode", "split"),
                                          server_pubkey(),
                                          meta.get("keepalive", 25), peers, name)
            save_client(name, conf_text, meta)
        else:
            # 无客户端私钥备份(手工对等端): 仍持久化 meta, 否则改名即失联
            Path(CLIENTS).mkdir(parents=True, exist_ok=True)
            _, meta_p = client_paths(name)
            meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                              encoding="utf-8")
            meta_p.chmod(0o600)
        if old_name != name:
            drop_client(old_name)
        refresh_client_confs(cfg, peers)
        return meta


def live_status():
    try:
        r = subprocess.run(["wg", "show", WG_IFACE, "dump"],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return {}, 0
    info, listen_port = {}, 0
    if r.returncode != 0 or not r.stdout.strip():
        return info, listen_port
    lines = r.stdout.splitlines()
    head = lines[0].split("\t")
    if len(head) >= 3:
        try:
            listen_port = int(head[2])
        except ValueError:
            pass
    for line in lines[1:]:
        f = line.split("\t")
        if len(f) < 8:
            continue
        try:
            info[f[0]] = {"handshake": int(f[4]), "rx": int(f[5]), "tx": int(f[6])}
        except ValueError:
            continue
    return info, listen_port


# 读路径不持锁。写路径用 wg_lock，避免 CLI 和面板两个进程互相覆盖。
def list_peers(live=None):
    if live is None:
        live, _ = live_status()
    now = int(time.time())
    rows = []
    for p in parse_conf()[1]:
        st = live.get(p["pubkey"], {})
        hs = st.get("handshake", 0)
        if hs == 0:
            state = "off"
        elif now - hs < 180:
            state = "ok"
        elif now - hs < 86400:
            state = "stale"
        else:
            state = "off"
        ip = p["allowed_ips"][0].split("/")[0] if p["allowed_ips"] else ""
        has_client = False
        meta = None
        if p["name"]:
            try:
                has_client = client_paths(p["name"])[0].exists()
                meta = load_client_meta(p["name"])
            except ApiError:
                has_client = False  # 手工对等端名字不合法时也不能炸列表
        rows.append({
            "name": p["name"] or p["pubkey"][:8],
            "pubkey": p["pubkey"],
            "ip": ip,
            "allowed_ips": p["allowed_ips"],
            "keepalive": p.get("keepalive", 0),
            "last_handshake": hs,
            "rx": st.get("rx", 0),
            "tx": st.get("tx", 0),
            "state": state,
            "is_gateway": is_gateway(p, ip),
            "has_client": has_client,
            "mode": (meta or {}).get("mode", "split"),
        })
    return rows


# 读路径不持锁。写路径用 wg_lock，避免 CLI 和面板两个进程互相覆盖。
def show_conf(name):
    conf_p, _ = client_paths(name)
    if not conf_p.exists():
        raise ApiError("找不到该设备的配置(手工创建的对等端没有备份)", 404)
    return conf_p.read_text(encoding="utf-8")


def full_status(cfg):
    live, listen_port = live_status()
    try:
        nip = next_ip(cfg)
    except ApiError:
        nip = None
    return {
        "ok": True,
        "iface": {"name": WG_IFACE, "up": bool(live) or listen_port > 0,
                  "listen_port": listen_port or cfg.get("wg_port", 51820),
                  "public_key": server_pubkey()},
        "endpoint": cfg.get("endpoint", ""),
        "panel_bind": cfg.get("panel_bind") or "",
        "default_allowed": ", ".join(
            [cfg["vpn_cidr"]] + list(cfg.get("lan_cidrs") or [])),
        "next_ip": nip,
        "peers": list_peers(live=live),
        "now": int(time.time()),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(prog="wgaio", description="WireGuard 设备管理核心")
    parser.add_argument("--serve", action="store_true", help="启动面板 HTTP 服务")
    sub = parser.add_subparsers(dest="cmd", required=False)
    user = sub.add_parser("user", help="设备管理")
    usub = user.add_subparsers(dest="ucmd", required=True)

    p_add = usub.add_parser("add", help="生成设备")
    p_add.add_argument("name")
    p_add.add_argument("--ip", default=None)
    p_add.add_argument("--dns", default=None)
    p_add.add_argument("--ka", default=None, help="保活秒数 0-120")
    p_add.add_argument("--mode", choices=["split", "full"], default=None)
    p_add.add_argument("--routes", default=None,
                       help="网关路由段, 逗号分隔(如 192.168.1.0/24)")

    p_del = usub.add_parser("del", help="删除设备")
    p_del.add_argument("name")
    p_del.add_argument("--force", action="store_true")

    p_edit = usub.add_parser("edit", help="修改设备")
    p_edit.add_argument("name")
    p_edit.add_argument("--rename", default=None)
    p_edit.add_argument("--ip", default=None)
    p_edit.add_argument("--dns", default=None)
    p_edit.add_argument("--ka", default=None)
    p_edit.add_argument("--mode", choices=["split", "full"], default=None)
    p_edit.add_argument("--routes", default=None)

    usub.add_parser("list", help="设备列表")
    p_show = usub.add_parser("show", help="导出 .conf")
    p_show.add_argument("name")

    args = parser.parse_args(argv)
    if args.serve:
        try:
            serve()
        except ApiError as e:
            print("错误: %s" % e, file=sys.stderr)
            return 1
        return 0
    if not args.cmd:
        parser.error("需要子命令或 --serve")
    try:
        cfg = load_config()
        if args.ucmd == "add":
            meta, _ = add_peer(args.name, args.ip, args.dns, args.ka,
                               args.mode, args.routes, cfg)
            print("已生成 %s (%s/%s)" % (meta["name"], meta["ip"], meta["mode"]))
        elif args.ucmd == "del":
            remove_peer(args.name, force=args.force, cfg=cfg)
            print("已删除 %s" % args.name)
        elif args.ucmd == "edit":
            meta = update_peer(args.name, new_name=args.rename, ip=args.ip,
                               dns=args.dns, keepalive=args.ka, mode=args.mode,
                               routes=args.routes, cfg=cfg)
            print("已更新 %s" % meta["name"])
        elif args.ucmd == "list":
            for r in list_peers():
                gw = " 网关" if r["is_gateway"] else ""
                print("%-15s %-14s %-5s%s" % (r["name"], r["ip"], r["state"], gw))
        elif args.ucmd == "show":
            print(show_conf(args.name), end="")
        return 0
    except ApiError as e:
        print("错误: %s" % e, file=sys.stderr)
        return 1
    except Exception as e:
        print("内部错误: %s" % e, file=sys.stderr)
        return 2


def session_cookie(value, max_age):
    return "wgaio_sess=%s; HttpOnly; SameSite=Strict; Path=/; Max-Age=%d" % (value, max_age)


def hash_token(plain):
    return hashlib.sha256((plain or "").encode("utf-8")).hexdigest()


def verify_token(plain, token_hash):
    if not token_hash:
        return False
    try:
        # 非字符串哈希值(如 JSON 里的数字)先转 str, 比较失败即拒绝
        return hmac.compare_digest(hash_token(plain), str(token_hash))
    except Exception:
        return False


PAGES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/static/style.css": ("style.css", "text/css; charset=utf-8"),
    "/static/app.js": ("app.js", "application/javascript; charset=utf-8"),
}
MAX_BODY = 64 * 1024


class PanelHandler(BaseHTTPRequestHandler):
    server_version = "wgaio/1.0"
    sys_version = ""

    def log_message(self, fmt, *args):
        pass  # 不落访问日志: 防止令牌/私钥随日志外泄

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response_only(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         "script-src 'self'; object-src 'none'; base-uri 'self'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
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
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except (ValueError, TypeError):
            raise ApiError("请求体不合法", 400)
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
        now = time.time()
        with _sessions_lock:
            exp = _sessions.get(s)
            if not exp or exp <= now:
                _sessions.pop(s, None)
                return False
            _sessions[s] = now + SESSION_TTL
            _sessions.move_to_end(s)
            return True

    def _cfg(self):
        path = getattr(self.server, "app_cfg_path", None)
        if path:
            try:
                cfg = load_config(path)
            except ApiError:
                return self.server.app_cfg
            self.server.app_cfg = cfg
            return cfg
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
                    time.sleep(0.5)  # 迟滞暴力破解
                    self._json({"ok": False, "error": "令牌错误"}, 401)
                    return
                sess = secrets.token_hex(32)
                with _sessions_lock:
                    now = time.time()
                    dead = [k for k, exp in _sessions.items() if not exp or exp <= now]
                    for k in dead:
                        _sessions.pop(k, None)
                    _sessions[sess] = now + SESSION_TTL
                    while len(_sessions) > 1000:
                        _sessions.popitem(last=False)
                self._send(200, json.dumps({"ok": True}, ensure_ascii=False),
                           extra={"Set-Cookie": session_cookie(sess, SESSION_TTL)})
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
        except Exception:
            print("[wgaio] 面板内部错误", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            self._json({"ok": False, "error": "内部错误"}, 500)

    # API 契约(T5 前端消费):
    # GET    /api/status                 -> {ok, iface, peers[], next_ip, ...}
    # POST   /api/peers                  <- {name, ip?, dns?, keepalive?, mode?, routes?} -> {ok, peer, conf}
    # PATCH  /api/peers/<name>           <- {new_name?, ip?, dns?, keepalive?, mode?, routes?} -> {ok, peer}
    # DELETE /api/peers/<name>[?force=1] -> {ok, peer{name}, removed}   (网关无 force 时 409)
    # GET    /api/peers/<name>/conf      -> text/plain attachment
    def _api(self, method, parts, u):
        cfg = self._cfg()
        if parts == ["api", "logout"] and method == "POST":
            if self.headers.get("Content-Length"):
                self._body()
            s = self._session()
            with _sessions_lock:
                if s:
                    _sessions.pop(s, None)
            self._send(200, json.dumps({"ok": True}, ensure_ascii=False),
                       extra={"Set-Cookie": session_cookie("", 0)})
            return
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
            self._json({"ok": True, "peer": {"name": parts[2]},
                        **remove_peer(parts[2], force=force, cfg=cfg)})
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
            fname = parts[2].replace('"', "").replace("\r", "").replace("\n", "")
            self._send(200, text, "text/plain; charset=utf-8",
                       {"Content-Disposition":
                        'attachment; filename="%s.conf"' % fname})
            return
        raise ApiError("接口不存在", 404)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_HEAD(self):
        self._handle("GET")

    def do_DELETE(self):
        self._handle("DELETE")


def start_server(cfg):
    bind = cfg.get("panel_bind") or "127.0.0.1"
    port = cfg.get("panel_port")
    port = 8888 if port is None else int(port)
    httpd = ThreadingHTTPServer((bind, port), PanelHandler)
    httpd.app_cfg = cfg
    return httpd


def serve(cfg=None):
    cfg = cfg or load_config()
    httpd = start_server(cfg)
    httpd.app_cfg_path = CONFIG_PATH
    tls_cert = cfg.get("tls_cert", "")
    tls_key = cfg.get("tls_key", "")
    if tls_cert and tls_key and Path(tls_cert).is_file() and Path(tls_key).is_file():
        import ssl
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(tls_cert, tls_key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    scheme = "https" if (tls_cert and tls_key and Path(tls_cert).is_file()) else "http"
    host, port = httpd.server_address[0], httpd.server_address[1]
    tls_cn = cfg.get("tls_cn", "")
    if tls_cn:
        print("wgaio 面板已启动: %s://%s:%d (令牌登录)" % (scheme, tls_cn, port), flush=True)
    else:
        print("wgaio 面板已启动: %s://%s:%d (令牌登录)" % (scheme, host, port), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    sys.exit(main())
