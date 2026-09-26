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
import tarfile
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
    "tls_mode": "",
    "panel_path": "",
}
PANEL_PATH_RE = re.compile(r"^[A-Za-z0-9_-]{4,80}$")


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
    _host, eport = parse_endpoint(cfg.get("endpoint"))
    if eport != port:
        raise ApiError("endpoint 端口必须和服务端口 %s 一致" % port)
    if str(cfg.get("client_dns") or "").strip():
        normalize_dns(cfg.get("client_dns"))
    if cfg.get("default_mode") not in ("split", "full"):
        raise ApiError("default_mode 只能是 split/full")
    for c in cfg.get("lan_cidrs") or []:
        if not CIDR_RE.match(str(c)):
            raise ApiError("lan_cidrs 含非法网段: %s" % c)
        try:
            cidr_bounds(str(c))
        except ValueError:
            raise ApiError("lan_cidrs 含非法网段: %s" % c)
    pp = cfg.get("panel_port")
    if type(pp) is not int or not 1 <= pp <= 65535:
        raise ApiError("panel_port 必须是 1-65535 的整数")
    pb = str(cfg.get("panel_bind") or "").strip()
    if pb:
        if pb != "0.0.0.0" and not IPV4_RE.match(pb):
            raise ApiError("panel_bind 必须是 IPv4 或 0.0.0.0: %s" % pb)
        if pb != "0.0.0.0":
            try:
                _ip_to_int(pb)
            except ValueError:
                raise ApiError("panel_bind 必须是 IPv4 或 0.0.0.0: %s" % pb)
    mode_tls = str(cfg.get("tls_mode") or "").strip()
    if mode_tls and mode_tls not in ("acme", "self"):
        raise ApiError("tls_mode 只能是 acme 或 self")
    ppath = str(cfg.get("panel_path") or "").strip().strip("/")
    if ppath and not PANEL_PATH_RE.match(ppath):
        raise ApiError("panel_path 不合法: %s" % ppath)


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


def cidrs_overlap(a, b):
    a0, a1 = cidr_bounds(a)
    b0, b1 = cidr_bounds(b)
    return a0 <= b1 and b0 <= a1


def parse_endpoint(endpoint):
    text = str(endpoint or "").strip()
    host, sep, port_s = text.rpartition(":")
    if not sep or not host or not port_s.isdigit():
        raise ApiError("endpoint 必须是 IP或域名:端口")
    port = int(port_s)
    if not 1 <= port <= 65535:
        raise ApiError("endpoint 端口必须是 1-65535")
    if IPV4_RE.match(host):
        try:
            _ip_to_int(host)
        except ValueError:
            raise ApiError("endpoint 地址不合法: %s" % host)
    elif ".." in host or not re.match(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$", host):
        raise ApiError("endpoint 必须是 IP或域名:端口")
    return host, port


def normalize_dns(dns):
    text = ("" if dns is None else str(dns)).strip()
    if not text:
        return ""
    parts = []
    for part in text.split(","):
        part = part.strip()
        if not part or not IPV4_RE.match(part):
            raise ApiError("DNS 必须是 IPv4, 多个用逗号分隔: %s" % text)
        try:
            _ip_to_int(part)
        except ValueError:
            raise ApiError("DNS 不合法: %s" % part)
        parts.append(part)
    return ", ".join(parts)


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
    base, last = cidr_bounds(vpn_cidr)
    prefix = int(str(vpn_cidr).partition("/")[2] or 32)
    # /31 及以上没有传统的网络地址和广播地址
    if prefix <= 30 and (n == base or n == last):
        raise ApiError("该地址是网段地址或广播地址, 不能分给设备")
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
            plen = int(prefix or "32")
            if plen == 0:
                raise ApiError("网关路由不能是默认路由，全隧道请改流量模式")
            base, _last = cidr_bounds(r)  # 拒绝 /33 之类越界前缀
        except ApiError:
            raise
        except ValueError:
            raise ApiError("路由段不合法(要形如 192.168.1.0/24): %s" % r)
        # 主机位清零，否则 wg 会拒绝这条路由
        out.append("%s/%d" % (int_to_ip(base), plen))
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
                            cur["name"] = comment[len(pfx):].strip()
                            break
                    # 其他注释不当作设备名，避免手工注释误识别
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
    prefix = int(str(cfg["vpn_cidr"]).partition("/")[2] or 32)
    # 服务器占 base+1；/30 及以下再跳过网络/广播；/31 没有网络/广播，只剩 base 可分
    if prefix <= 30:
        candidates = range(base + 2, last)
    elif prefix == 31:
        candidates = [base]
    else:
        candidates = []
    for n in candidates:
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


def default_allowed(cfg, peers=None):
    """状态展示用的默认分流网段：与 split_allowed 同源，含其他设备宣布的网关路由。"""
    return split_allowed(cfg, peers or [], None)


def build_client_conf(priv, ip, cfg, mode, server_pub, keepalive, peers=None, self_name=None):
    try:
        keepalive = int(keepalive)
    except (TypeError, ValueError):
        keepalive = 25
    endpoint = cfg.get("endpoint")
    if not endpoint:
        raise ApiError("配置缺少 endpoint", 500)
    if not server_pub:
        raise ApiError("读不到服务端公钥, 请确认 wg0 已配置私钥", 500)
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
        use_dns = normalize_dns(dns) or normalize_dns(cfg.get("client_dns")) or "1.1.1.1"
        cfg_run = dict(cfg)
        cfg_run["client_dns"] = use_dns
        conf_text = build_client_conf(priv, ip, cfg_run, mode, server_pubkey(), keepalive, peers, name)
        meta = {"name": name, "pubkey": pub, "ip": ip, "mode": mode,
                "dns": use_dns, "keepalive": keepalive, "routes": extra_routes,
                "disabled": False}
        # 先落私钥。写 wg0.conf 失败就删掉，避免对等端已写入但私钥丢失。
        save_client(name, conf_text, meta)
        try:
            write_conf(iface_lines, peers)
        except Exception:
            drop_client(name)
            raise
        wg_set_peer(pub, allowed_ips=", ".join(peer["allowed_ips"]), keepalive=keepalive)
        refresh_client_confs(cfg, peers)
        conf_text = client_paths(name)[0].read_text(encoding="utf-8")
        return meta, conf_text


def remove_peer(name, force=False, cfg=None):
    with wg_lock():
        iface_lines, peers = parse_conf()
        peer = find_peer(peers, name)
        if not peer:
            meta = load_client_meta(name)
            if not meta:
                raise ApiError("找不到设备: %s" % name, 404)
            routes = [str(r) for r in (meta.get("routes") or [])]
            if routes and not force:
                raise ApiError("该设备是内网网关(带路由 %s), 删除会断掉进内网; 确认请加 --force"
                               % ", ".join(routes), 409)
            drop_client(name)
            return {"removed": name}
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


def write_meta(name, meta):
    Path(CLIENTS).mkdir(parents=True, exist_ok=True)
    _, meta_p = client_paths(name)
    meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    meta_p.chmod(0o600)


def _update_disabled_peer(name, new_name, ip, dns, keepalive, mode, routes, cfg):
    """改一份已经从 wg0 拿掉的设备。地址和密钥不动接口，只改备份。"""
    meta = load_client_meta(name)
    if not meta:
        raise ApiError("找不到设备: %s" % name, 404)
    priv = read_priv(name)
    old_name = name
    if new_name and new_name != name:
        new_name = normalize_name(new_name)
        if find_peer(parse_conf()[1], new_name) or load_client_meta(new_name):
            raise ApiError("设备名已存在: %s" % new_name)
        meta["name"] = new_name
        name = new_name
    cur_ip = str(meta.get("ip") or "")
    if ip:
        new_ip = normalize_ip(ip, cfg, used_ips(), current=cur_ip)
        meta["ip"] = new_ip
        cur_ip = new_ip
    if routes is not None:
        meta["routes"] = normalize_routes(routes)
    if keepalive is not None:
        try:
            meta["keepalive"] = max(0, min(120, int(keepalive)))
        except (TypeError, ValueError):
            pass
    if mode:
        if mode not in ("split", "full"):
            raise ApiError("mode 只能是 split/full")
        meta["mode"] = mode
    if dns:
        meta["dns"] = normalize_dns(dns)
    meta["disabled"] = True
    if priv and cur_ip:
        cfg_run = dict(cfg)
        if meta.get("dns"):
            cfg_run["client_dns"] = meta["dns"]
        text = build_client_conf(
            priv, cur_ip, cfg_run, meta.get("mode") or "split", server_pubkey(),
            meta.get("keepalive", 25), parse_conf()[1], name)
        save_client(name, text, meta)
    else:
        write_meta(name, meta)
    if old_name != name:
        drop_client(old_name)
    return meta


def update_peer(name, new_name=None, ip=None, dns=None, keepalive=None,
                mode=None, routes=None, cfg=None):
    cfg = cfg or load_config()
    with wg_lock():
        iface_lines, peers = parse_conf()
        peer = find_peer(peers, name)
        if not peer:
            return _update_disabled_peer(name, new_name, ip, dns, keepalive,
                                         mode, routes, cfg)
        meta = load_client_meta(name) or {"name": name, "pubkey": peer["pubkey"],
                                          "routes": [], "ip": "", "disabled": False}
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
            meta["dns"] = normalize_dns(dns)
        meta["disabled"] = False

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


def set_peer_active(name, active, cfg, force=False):
    """停用只从隧道拿掉，IP 和私钥都留着。启用按原地址加回去。"""
    name = normalize_name(name)
    cfg = cfg or load_config()
    with wg_lock():
        iface_lines, peers = parse_conf()
        peer = find_peer(peers, name)
        meta = load_client_meta(name)
        if active:
            if peer:
                if meta and meta.get("disabled"):
                    meta["disabled"] = False
                    write_meta(name, meta)
                return meta or {"name": name, "disabled": False}
            if not meta:
                raise ApiError("找不到设备: %s" % name, 404)
            ip = str(meta.get("ip") or "")
            pub = str(meta.get("pubkey") or "")
            if not ip or not pub:
                raise ApiError("停用记录不完整，不能启用")
            routes = [str(r) for r in (meta.get("routes") or [])]
            try:
                ka = max(0, min(120, int(meta.get("keepalive") or 0)))
            except (TypeError, ValueError):
                ka = 0
            peer = {"name": name, "pubkey": pub,
                    "allowed_ips": ["%s/32" % ip] + routes,
                    "keepalive": ka, "extra": []}
            priv = read_priv(name)
            text = None
            if priv:
                cfg_run = dict(cfg)
                if meta.get("dns"):
                    cfg_run["client_dns"] = meta["dns"]
                text = build_client_conf(
                    priv, ip, cfg_run, meta.get("mode") or "split", server_pubkey(),
                    meta.get("keepalive", 25), peers + [peer], name)
            peers.append(peer)
            meta["disabled"] = False
            write_conf(iface_lines, peers)
            wg_set_peer(pub, allowed_ips=", ".join(peer["allowed_ips"]), keepalive=ka)
            if text:
                save_client(name, text, meta)
            else:
                write_meta(name, meta)
            refresh_client_confs(cfg, peers)
            return meta
        if not peer:
            if meta and meta.get("disabled"):
                return meta
            raise ApiError("找不到设备: %s" % name, 404)
        ip = peer["allowed_ips"][0].split("/")[0] if peer["allowed_ips"] else ""
        if is_gateway(peer, ip) and not force:
            extras = [a for a in peer["allowed_ips"] if a != "%s/32" % ip]
            raise ApiError("该设备是内网网关(带路由 %s), 停用会断掉进内网; 确认请加 --force"
                           % ", ".join(extras), 409)
        routes = [a for a in peer["allowed_ips"] if a != "%s/32" % ip]
        if not meta:
            meta = {"name": name, "pubkey": peer["pubkey"], "ip": ip,
                    "mode": "split", "routes": routes,
                    "keepalive": peer.get("keepalive") or 0, "dns": ""}
        meta["pubkey"] = peer["pubkey"]
        meta["ip"] = ip
        meta["routes"] = routes
        meta["keepalive"] = peer.get("keepalive") or meta.get("keepalive") or 0
        meta["disabled"] = True
        peers.remove(peer)
        write_conf(iface_lines, peers)
        wg_set_peer(peer["pubkey"], remove=True)
        write_meta(name, meta)
        refresh_client_confs(cfg, peers)
        return meta


def rotate_peer(name, cfg):
    """换一对密钥，IP、流量模式和路由都不改。停用中的设备不会被重新加回隧道。"""
    name = normalize_name(name)
    cfg = cfg or load_config()
    with wg_lock():
        iface_lines, peers = parse_conf()
        peer = find_peer(peers, name)
        meta = load_client_meta(name)
        priv = read_priv(name)
        if not peer and not meta:
            raise ApiError("找不到设备: %s" % name, 404)
        if not priv:
            raise ApiError("没有这份设备的私钥，不能更换密钥")
        old_pub = peer["pubkey"] if peer else str(meta.get("pubkey") or "")
        new_priv, new_pub = gen_keypair()
        if not meta:
            ip = peer["allowed_ips"][0].split("/")[0] if peer["allowed_ips"] else ""
            routes = [a for a in peer["allowed_ips"] if a != "%s/32" % ip]
            meta = {"name": name, "ip": ip, "mode": cfg.get("default_mode") or "split",
                    "dns": cfg.get("client_dns") or "1.1.1.1",
                    "keepalive": peer.get("keepalive") or 25, "routes": routes,
                    "disabled": False}
        meta["pubkey"] = new_pub
        meta["disabled"] = not bool(peer)
        ip = str(meta.get("ip") or "")
        if peer and peer.get("allowed_ips"):
            ip = peer["allowed_ips"][0].split("/")[0]
            meta["ip"] = ip
            meta["routes"] = [a for a in peer["allowed_ips"] if a != "%s/32" % ip]
            meta["keepalive"] = peer.get("keepalive") or meta.get("keepalive") or 0
        cfg_run = dict(cfg)
        if meta.get("dns"):
            cfg_run["client_dns"] = meta["dns"]
        preview = list(peers)
        if peer:
            preview = [dict(p, pubkey=new_pub) if p is peer else p for p in peers]
        text = build_client_conf(
            new_priv, ip, cfg_run, meta.get("mode") or "split", server_pubkey(),
            meta.get("keepalive", 25), preview, name)
        if peer:
            peer["pubkey"] = new_pub
            write_conf(iface_lines, peers)
            if old_pub:
                wg_set_peer(old_pub, remove=True)
            wg_set_peer(new_pub, allowed_ips=", ".join(peer["allowed_ips"]),
                        keepalive=peer.get("keepalive") or 0)
        save_client(name, text, meta)
        if peer:
            refresh_client_confs(cfg, peers)
            text = client_paths(name)[0].read_text(encoding="utf-8")
        return meta, text


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
            "disabled": False,
        })
    seen = {row["name"] for row in rows}
    if Path(CLIENTS).exists():
        for meta_p in sorted(Path(CLIENTS).glob("*.json")):
            if meta_p.is_symlink():
                continue
            try:
                stored = json.loads(meta_p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeError):
                continue
            if not isinstance(stored, dict) or not stored.get("disabled"):
                continue
            name = str(stored.get("name") or meta_p.stem)
            try:
                normalize_name(name)
            except ApiError:
                continue
            if name in seen:
                continue
            ip = str(stored.get("ip") or "")
            routes = [str(r) for r in (stored.get("routes") or [])]
            try:
                has_client = client_paths(name)[0].exists()
            except ApiError:
                has_client = False
            rows.append({
                "name": name,
                "pubkey": str(stored.get("pubkey") or ""),
                "ip": ip,
                "allowed_ips": (["%s/32" % ip] if ip else []) + routes,
                "keepalive": stored.get("keepalive") or 0,
                "last_handshake": 0,
                "rx": 0,
                "tx": 0,
                "state": "disabled",
                "is_gateway": bool(routes),
                "has_client": has_client,
                "mode": stored.get("mode") or "split",
                "disabled": True,
            })
            seen.add(name)
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
    peers = parse_conf()[1]
    return {
        "ok": True,
        "iface": {"name": WG_IFACE, "up": bool(live) or listen_port > 0,
                  "listen_port": listen_port or cfg.get("wg_port", 51820),
                  "public_key": server_pubkey()},
        "endpoint": cfg.get("endpoint", ""),
        "panel_bind": cfg.get("panel_bind") or "",
        "default_allowed": default_allowed(cfg, peers),
        "next_ip": nip,
        "peers": list_peers(live=live),
        "now": int(time.time()),
    }


def backup_root():
    return CONFIG_PATH.parent / "backups"


def _add_backup_file(tar, src, arcname):
    src = Path(src)
    if not src.is_file() or src.is_symlink():
        return
    tar.add(src, arcname=arcname, recursive=False)


def create_backup(keep_days=None):
    """打包 config.json、clients 和 wg0.conf。默认只留 14 天。"""
    if keep_days is None:
        raw = os.environ.get("WGAIO_BACKUP_DAYS", "14")
        try:
            keep_days = int(raw)
        except (TypeError, ValueError):
            keep_days = 14
    keep_days = max(0, int(keep_days))
    dest_dir = backup_root()
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = dest_dir / ("wgaio-data-%s.tar.gz" % stamp)
    n = 1
    while path.exists():
        n += 1
        path = dest_dir / ("wgaio-data-%s-%d.tar.gz" % (stamp, n))
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tarfile.open(tmp, "w:gz") as tar:
            _add_backup_file(tar, CONFIG_PATH, "config.json")
            _add_backup_file(tar, Path(WG_CONF), "wg0.conf")
            clients = Path(CLIENTS)
            if clients.is_dir() and not clients.is_symlink():
                info = tarfile.TarInfo("clients")
                info.type = tarfile.DIRTYPE
                info.mode = 0o700
                tar.addfile(info)
                for child in sorted(clients.iterdir()):
                    if child.is_symlink() or not child.is_file():
                        continue
                    if child.suffix not in (".conf", ".json"):
                        continue
                    stem = child.name[:-len(child.suffix)]
                    try:
                        normalize_name(stem)
                    except ApiError:
                        continue
                    _add_backup_file(tar, child, "clients/%s" % child.name)
        os.chmod(tmp, 0o600)
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    if keep_days > 0:
        cutoff = time.time() - keep_days * 86400
        for old in dest_dir.glob("wgaio-data-*.tar.gz"):
            try:
                if old.resolve() == path.resolve():
                    continue
                if old.stat().st_mtime < cutoff:
                    old.unlink()
            except OSError:
                pass
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _backup_members(tar):
    found = []
    saw_clients = False
    for member in tar.getmembers():
        raw = member.name.replace("\\", "/")
        if not raw or raw.startswith("/") or ".." in raw.split("/"):
            raise ApiError("备份里有非法路径: %s" % member.name)
        parts = [p for p in raw.split("/") if p and p != "."]
        name = "/".join(parts)
        if not name:
            raise ApiError("备份里有非法路径: %s" % member.name)
        if member.issym() or member.islnk():
            raise ApiError("备份里有链接，已拒绝: %s" % member.name)
        if member.isdir():
            if name == "clients":
                saw_clients = True
                continue
            raise ApiError("备份里有不认识的目录: %s" % member.name)
        if not member.isfile():
            raise ApiError("备份里有不支持的条目: %s" % member.name)
        if name in ("config.json", "wg0.conf"):
            found.append((member, name))
            continue
        if len(parts) == 2 and parts[0] == "clients" and parts[1].endswith((".conf", ".json")):
            stem = parts[1].rsplit(".", 1)[0]
            try:
                normalize_name(stem)
            except ApiError:
                raise ApiError("备份里的设备名不合法: %s" % name)
            saw_clients = True
            found.append((member, name))
            continue
        raise ApiError("备份里有不认识的文件: %s" % name)
    if not found and not saw_clients:
        raise ApiError("备份是空的")
    return found, saw_clients


def _write_private(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(path)


def _try_restart_units():
    if os.name != "posix":
        return
    for unit in ("wg-quick@wg0", "wgaio-panel"):
        try:
            subprocess.run(["systemctl", "try-restart", unit],
                           capture_output=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            pass


def restore_backup(archive, restart=True):
    archive = Path(archive)
    if not archive.is_file() or archive.is_symlink():
        raise ApiError("找不到备份: %s" % archive, 404)
    try:
        tar = tarfile.open(archive, "r:gz")
    except (tarfile.TarError, OSError):
        raise ApiError("不是有效的备份包")
    with tar:
        members, saw_clients = _backup_members(tar)
        blobs = {}
        for member, name in members:
            handle = tar.extractfile(member)
            if handle is None:
                raise ApiError("读不到备份条目: %s" % name)
            data = handle.read(2 * 1024 * 1024 + 1)
            if len(data) > 2 * 1024 * 1024:
                raise ApiError("备份条目过大: %s" % name)
            blobs[name] = data
    if "config.json" in blobs:
        _write_private(CONFIG_PATH, blobs["config.json"])
    if "wg0.conf" in blobs:
        _write_private(Path(WG_CONF), blobs["wg0.conf"])
    if saw_clients:
        Path(CLIENTS).mkdir(parents=True, exist_ok=True)
        keep = {name.split("/", 1)[1] for name in blobs if name.startswith("clients/")}
        if Path(CLIENTS).is_dir() and not Path(CLIENTS).is_symlink():
            for child in list(Path(CLIENTS).iterdir()):
                if child.is_symlink() or not child.is_file():
                    continue
                if child.suffix not in (".conf", ".json"):
                    continue
                try:
                    normalize_name(child.name[:-len(child.suffix)])
                except ApiError:
                    continue
                if child.name not in keep:
                    child.unlink()
        for name, data in blobs.items():
            if name.startswith("clients/"):
                _write_private(Path(CLIENTS) / name.split("/", 1)[1], data)
    if restart:
        _try_restart_units()
    return archive


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

    p_off = usub.add_parser("disable", help="停用设备，保留地址和私钥")
    p_off.add_argument("name")
    p_off.add_argument("--force", action="store_true")
    p_on = usub.add_parser("enable", help="启用已停用的设备")
    p_on.add_argument("name")
    p_rot = usub.add_parser("rotate", help="更换密钥，IP 不变")
    p_rot.add_argument("name")

    bak = sub.add_parser("backup", help="备份或恢复 config.json、clients 和 wg0.conf")
    bsub = bak.add_subparsers(dest="bcmd", required=False)
    bsub.add_parser("create", help="打一个数据包")
    p_res = bsub.add_parser("restore", help="从数据包恢复")
    p_res.add_argument("path")

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
        if args.cmd == "backup":
            if getattr(args, "bcmd", None) == "restore":
                restore_backup(args.path)
                print("已从备份恢复: %s" % args.path)
            else:
                print(create_backup())
            return 0
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
        elif args.ucmd == "disable":
            set_peer_active(args.name, False, cfg, force=args.force)
            print("已停用 %s（地址和私钥都还在）" % args.name)
        elif args.ucmd == "enable":
            set_peer_active(args.name, True, cfg)
            print("已启用 %s" % args.name)
        elif args.ucmd == "rotate":
            meta, _conf = rotate_peer(args.name, cfg)
            print("已更换 %s 的密钥，旧配置不能再用" % meta["name"])
        return 0
    except ApiError as e:
        print("错误: %s" % e, file=sys.stderr)
        return 1
    except Exception as e:
        print("内部错误: %s" % e, file=sys.stderr)
        return 2


def session_cookie(value, max_age, secure=False, path="/"):
    flag = "; Secure" if secure else ""
    return "wgaio_sess=%s; HttpOnly; SameSite=Strict; Path=%s; Max-Age=%d%s" % (
        value, path or "/", max_age, flag)


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


JOIN_TTL = 60
JOIN_OS = ("linux", "mac")
_JOIN_MARK = "WGAIO_CONF"
_JOIN_DENIED = "加入命令已失效\n"
_HOST_RE = re.compile(r"^[A-Za-z0-9.:_\-\[\]]{1,253}$")


def join_mac(token_hash, name, os_name, ts):
    """用面板令牌的哈希给「设备 + 系统 + 时间戳」签名。令牌原文不进命令。"""
    key = str(token_hash or "").encode("utf-8")
    msg = ("%s\n%s\n%d" % (name, os_name, int(ts))).encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def join_fresh(ts, now=None):
    now = int(time.time()) if now is None else int(now)
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return False
    age = now - ts
    return 0 <= age <= JOIN_TTL


def host_is_loopback(host):
    text = str(host or "")
    if text.startswith("["):
        name = text[1:].split("]", 1)[0]
    elif text.count(":") == 1:
        name = text.split(":", 1)[0]
    else:
        name = text
    return name in ("localhost", "::1") or name.startswith("127.")


def public_origin(host, tls):
    host = (host or "").strip()
    if not _HOST_RE.fullmatch(host) or ".." in host or host.startswith(".") or host.endswith("."):
        raise ApiError("无法确定面板地址", 500)
    return "%s://%s" % ("https" if tls else "http", host)


def join_shell(url):
    if not re.fullmatch(
            r"https?://[A-Za-z0-9.:_\-\[\]]+/wgaio-join/[A-Za-z0-9_-]+\?t=\d+&k=[0-9a-f]{64}&os=(linux|mac)",
            url):
        raise ApiError("加入地址无法放进命令", 500)
    # 外层单引号交给用户的 shell。签名只活 60 秒，私钥在临时文件里，脚本结束就删。
    return (
        "sudo sh -c 'umask 077; set -eu; f=$(mktemp); trap \"rm -f $f\" EXIT; "
        "curl -fsSL \"%s\" -o \"$f\"; sh \"$f\"'"
    ) % url


def _join_warn(conf):
    if "0.0.0.0/0" not in conf:
        return ""
    return ('echo "这是全隧道：默认路由会改走 VPN。如果这是远程 SSH 的机器，先确认还能连上。" >&2\n')


_LINUX_JOIN = """#!/bin/sh
set -eu
if [ "$(id -u)" -ne 0 ]; then
  echo "请用 sudo 执行这条命令" >&2
  exit 1
fi
__WARN__if command -v wg >/dev/null 2>&1 && command -v wg-quick >/dev/null 2>&1; then
  :
else
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y wireguard
    if ! command -v resolvconf >/dev/null 2>&1 && ! command -v resolvectl >/dev/null 2>&1; then
      apt-get install -y openresolv || apt-get install -y resolvconf || true
    fi
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y wireguard-tools
  elif command -v pacman >/dev/null 2>&1; then
    pacman -Sy --noconfirm wireguard-tools openresolv
  elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache wireguard-tools openresolv
  else
    echo "认不出包管理器。请先安装 wireguard-tools，再重新复制一条命令。" >&2
    exit 1
  fi
fi
install -d -m 700 /etc/wireguard
umask 077
cat > /etc/wireguard/wgaio.conf <<'WGAIO_CONF'
__CONF__WGAIO_CONF
chmod 600 /etc/wireguard/wgaio.conf
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  systemctl enable wg-quick@wgaio
  systemctl restart wg-quick@wgaio
else
  if wg show wgaio >/dev/null 2>&1; then
    wg-quick down wgaio || true
  fi
  wg-quick up wgaio
fi
echo "已加入内网，接口 wgaio"
"""

_MAC_JOIN = """#!/bin/sh
set -eu
if [ "$(id -u)" -ne 0 ]; then
  echo "请用 sudo 执行这条命令" >&2
  exit 1
fi
__WARN__BREW=""
for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
  if [ -x "$b" ]; then
    BREW=$b
    break
  fi
done
if [ -z "$BREW" ]; then
  echo "没有找到 Homebrew。请到 App Store 安装 WireGuard，再用面板里的下载配置导入。" >&2
  exit 1
fi
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
  sudo -u "$SUDO_USER" -H "$BREW" install wireguard-tools
else
  echo "请用普通用户的 sudo 执行，Homebrew 不适合直接用 root 跑。" >&2
  exit 1
fi
install -d -m 700 /etc/wireguard
umask 077
cat > /etc/wireguard/wgaio.conf <<'WGAIO_CONF'
__CONF__WGAIO_CONF
chmod 600 /etc/wireguard/wgaio.conf
WGQ=""
for q in /opt/homebrew/bin/wg-quick /usr/local/bin/wg-quick; do
  if [ -x "$q" ]; then
    WGQ=$q
    break
  fi
done
if [ -z "$WGQ" ]; then
  echo "已写好配置，但找不到 wg-quick。" >&2
  exit 1
fi
WG=""
for w in /opt/homebrew/bin/wg /usr/local/bin/wg; do
  if [ -x "$w" ]; then
    WG=$w
    break
  fi
done
if [ -n "$WG" ] && "$WG" show wgaio >/dev/null 2>&1; then
  "$WGQ" down wgaio || true
fi
"$WGQ" up wgaio
echo "已加入内网，接口 wgaio。Mac 重启后不会自动连接，需要再执行一次，或改用 App Store 的 WireGuard。"
"""


def build_join_script(os_name, conf):
    if os_name not in JOIN_OS:
        raise ApiError("系统只能是 linux 或 mac", 400)
    text = str(conf or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.endswith("\n"):
        text += "\n"
    if (_JOIN_MARK in text.splitlines()) or ("__CONF__" in text) or ("__WARN__" in text):
        raise ApiError("配置内容无法放进加入脚本", 500)
    tmpl = _LINUX_JOIN if os_name == "linux" else _MAC_JOIN
    tmpl = tmpl.replace("\r\n", "\n").replace("\r", "\n")
    return tmpl.replace("__WARN__", _join_warn(text)).replace("__CONF__", text)


def issue_join(name, os_name, cfg, origin, now=None):
    name = normalize_name(name)
    if os_name not in JOIN_OS:
        raise ApiError("系统只能是 linux 或 mac", 400)
    meta = load_client_meta(name)
    if not meta:
        raise ApiError("找不到该设备的配置", 404)
    if meta.get("disabled"):
        raise ApiError("设备已停用，先启用再生成加入命令", 400)
    conf = show_conf(name)
    if "PrivateKey" not in conf:
        raise ApiError("没有这份设备的私钥，不能生成加入命令", 400)
    token_hash = str(cfg.get("panel_token_hash") or "")
    if not token_hash:
        raise ApiError("面板令牌未设置", 500)
    now = int(time.time()) if now is None else int(now)
    mac = join_mac(token_hash, name, os_name, now)
    url = "%s/wgaio-join/%s?t=%d&k=%s&os=%s" % (str(origin).rstrip("/"), name, now, mac, os_name)
    build_join_script(os_name, conf)
    return {
        "ok": True,
        "command": join_shell(url),
        "expires_in": JOIN_TTL,
        "os": os_name,
        "full": "0.0.0.0/0" in conf,
        "plain_http": str(origin).startswith("http://"),
        "loopback": host_is_loopback(str(origin).split("://", 1)[-1]),
    }


def render_join_script(name, os_name, ts, key, cfg, now=None):
    """验签通过才返回脚本。失败一律当失效，不区分过期、签名错或没有这台设备。"""
    try:
        name = normalize_name(name)
    except ApiError:
        return None
    if os_name not in JOIN_OS or not re.fullmatch(r"\d{1,12}", str(ts or "")):
        return None
    token_hash = str((cfg or {}).get("panel_token_hash") or "")
    if not token_hash:
        return None
    ts_i = int(ts)
    expected = join_mac(token_hash, name, os_name, ts_i)
    given = str(key or "")
    if len(given) == len(expected):
        signed = hmac.compare_digest(expected, given)
    else:
        hmac.compare_digest(expected, expected)
        signed = False
    if not signed:
        return None
    if not join_fresh(ts_i, now):
        return None
    try:
        meta = load_client_meta(name)
        conf = show_conf(name)
    except ApiError:
        return None
    if not meta or meta.get("disabled") or "PrivateKey" not in conf:
        return None
    try:
        return build_join_script(os_name, conf)
    except ApiError:
        return None


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
        extra = dict(extra or {})
        if getattr(self, "_refresh_sess", None) and "Set-Cookie" not in extra:
            extra["Set-Cookie"] = session_cookie(
                self._refresh_sess, SESSION_TTL, self._cookie_secure(), self._cookie_path())
        self.send_response_only(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         "script-src 'self'; object-src 'none'; base-uri 'self'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        if not getattr(self, "_head_only", False):
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

    def _cookie_secure(self):
        import ssl
        return isinstance(getattr(self, "connection", None), ssl.SSLSocket)

    def _panel_prefix(self):
        raw = str(self._cfg().get("panel_path") or "").strip().strip("/")
        if not PANEL_PATH_RE.match(raw):
            return ""
        return "/" + raw

    def _cookie_path(self):
        prefix = self._panel_prefix()
        return (prefix + "/") if prefix else "/"

    def _route_path(self):
        """面板实际路径。没带对入口码时返回 None，调用方回 404。"""
        path = urlparse(self.path).path or "/"
        prefix = self._panel_prefix()
        if not prefix:
            return path
        if path == prefix:
            return "REDIRECT"
        if path.startswith(prefix + "/"):
            rest = path[len(prefix):]
            return rest if rest.startswith("/") else "/" + rest
        return None

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
            # 滑动续期时同步刷新 Cookie Max-Age，避免会话还在但 Cookie 先过期
            self._refresh_sess = s
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

    def _request_origin(self):
        return public_origin(self.headers.get("Host"), self._cookie_secure())

    def _serve_join(self, method):
        path = urlparse(self.path).path or "/"
        if path != "/wgaio-join" and not path.startswith("/wgaio-join/"):
            return False
        if method != "GET" or len(self.path) > 512:
            self._send(403, _JOIN_DENIED, "text/plain; charset=utf-8")
            return True
        u = urlparse(self.path)
        name = unquote(path[len("/wgaio-join/"):].strip("/"))
        q = parse_qs(u.query, keep_blank_values=True)
        script = None
        if name and "/" not in name:
            script = render_join_script(
                name,
                (q.get("os") or [""])[0],
                (q.get("t") or [""])[0],
                (q.get("k") or [""])[0],
                self._cfg())
        if not script:
            self._send(403, _JOIN_DENIED, "text/plain; charset=utf-8")
            return True
        self._send(200, script, "text/x-shellscript; charset=utf-8")
        return True

    def _handle(self, method):
        try:
            if self._serve_join(method):
                return
            route = self._route_path()
            if route is None:
                self._send(404, "Not Found\n", "text/plain; charset=utf-8")
                return
            if route == "REDIRECT":
                self.send_response(308)
                self.send_header("Location", self._panel_prefix() + "/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            u = urlparse(self.path)
            parts = [unquote(x) for x in route.split("/") if x]
            if route in PAGES and method == "GET":
                fname, ctype = PAGES[route]
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
                           extra={"Set-Cookie": session_cookie(
                               sess, SESSION_TTL, self._cookie_secure(), self._cookie_path())})
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
    # GET    /api/peers/<name>/qr        -> image/svg+xml
    # POST   /api/peers/<name>/disable[?force=1]
    # POST   /api/peers/<name>/enable
    # POST   /api/peers/<name>/rotate    -> {ok, peer, conf}
    # POST   /api/peers/<name>/join?os=  -> {ok, command, expires_in}  命令 60 秒内有效
    # GET    /wgaio-join/<name>?t&k&os=  -> 安装脚本（不经过面板入口，只认 60 秒签名）
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
                       extra={"Set-Cookie": session_cookie(
                           "", 0, self._cookie_secure(), self._cookie_path())})
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
        if len(parts) == 4 and parts[:2] == ["api", "peers"] and parts[3] == "qr" \
                and method == "GET":
            import qr
            svg = qr.qr_svg(show_conf(parts[2]))
            self._send(200, svg, "image/svg+xml")
            return
        if len(parts) == 4 and parts[:2] == ["api", "peers"] and method == "POST" \
                and parts[3] in ("disable", "enable", "rotate"):
            if parts[3] == "disable":
                force = parse_qs(u.query).get("force", ["0"])[0] == "1"
                meta = set_peer_active(parts[2], False, cfg, force=force)
                self._json({"ok": True, "peer": meta})
                return
            if parts[3] == "enable":
                meta = set_peer_active(parts[2], True, cfg)
                self._json({"ok": True, "peer": meta})
                return
            meta, conf_text = rotate_peer(parts[2], cfg)
            self._json({"ok": True, "peer": meta, "conf": conf_text})
            return
        if len(parts) == 4 and parts[:2] == ["api", "peers"] and parts[3] == "join" \
                and method == "POST":
            os_name = parse_qs(u.query).get("os", ["linux"])[0]
            self._json(issue_join(parts[2], os_name, cfg, self._request_origin()))
            return
        raise ApiError("接口不存在", 404)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_HEAD(self):
        self._head_only = True
        try:
            self._handle("GET")
        finally:
            self._head_only = False

    def do_DELETE(self):
        self._handle("DELETE")


def start_server(cfg):
    bind = cfg.get("panel_bind") or "127.0.0.1"
    port = cfg.get("panel_port")
    port = 8888 if port is None else int(port)
    httpd = ThreadingHTTPServer((bind, port), PanelHandler)
    httpd.app_cfg = cfg
    return httpd


def ensure_panel_path(cfg):
    """旧配置没有入口码时补一个并写回，避免升级后面板还裸露在端口根上。"""
    raw = str(cfg.get("panel_path") or "").strip().strip("/")
    if PANEL_PATH_RE.match(raw):
        return cfg
    cfg = dict(cfg)
    cfg["panel_path"] = "wgaio-" + secrets.token_hex(6)
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        data["panel_path"] = cfg["panel_path"]
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        print("[wgaio] 警告: 面板入口码没能写入配置: %s" % exc, file=sys.stderr)
    else:
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except OSError:
            pass
    print("[wgaio] 已生成面板入口码。只开完整地址，单独打开端口会看到 404", flush=True)
    return cfg


def panel_url(cfg, scheme, host, port):
    path = str(cfg.get("panel_path") or "").strip().strip("/")
    suffix = ("/" + path + "/") if path else "/"
    name = cfg.get("tls_cn") or host
    return "%s://%s:%d%s" % (scheme, name, port, suffix)


def serve(cfg=None):
    cfg = ensure_panel_path(cfg or load_config())
    httpd = start_server(cfg)
    httpd.app_cfg = cfg
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
    print("wgaio 面板已启动: %s (令牌登录)" % panel_url(cfg, scheme, host, port), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    sys.exit(main())
