#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wg-allin-one 核心：WireGuard 设备管理（CLI 后端）。"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

BASE = Path(os.environ.get("WGAIO_BASE", "/opt/wgaio"))
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
            cidr_bounds(r)  # 拒绝 /33 之类越界前缀
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


def build_client_conf(priv, ip, cfg, mode, server_pub, keepalive):
    try:
        keepalive = int(keepalive)
    except (TypeError, ValueError):
        keepalive = 25
    endpoint = cfg.get("endpoint")
    if not endpoint:
        raise ApiError("配置缺少 endpoint", 500)
    allowed = "0.0.0.0/0, ::/0" if mode == "full" else ", ".join(
        [cfg["vpn_cidr"]] + list(cfg.get("lan_cidrs") or []))
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
            return run_wg(["pubkey"], input_text=m.group(1) + "\n").strip()
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


def wg_set_peer(pubkey, allowed_ips=None, keepalive=None, remove=False):
    try:
        r = subprocess.run(["ip", "link", "show", WG_IFACE], capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return
    if r.returncode != 0:
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
    conf_text = build_client_conf(priv, ip, cfg_run, mode, server_pubkey(), keepalive)
    meta = {"name": name, "pubkey": pub, "ip": ip, "mode": mode,
            "dns": use_dns, "keepalive": keepalive, "routes": extra_routes}
    save_client(name, conf_text, meta)
    return meta, conf_text


def remove_peer(name, force=False):
    iface_lines, peers = parse_conf()
    peer = find_peer(peers, name)
    if not peer:
        raise ApiError("找不到设备: %s" % name, 404)
    ip = peer["allowed_ips"][0].split("/")[0] if peer["allowed_ips"] else ""
    extras = [a for a in peer["allowed_ips"] if a != "%s/32" % ip]
    if extras and not force:
        raise ApiError("该设备是内网网关(带路由 %s), 删除会断掉进内网; 确认请加 --force"
                       % ", ".join(extras), 409)
    peers.remove(peer)
    write_conf(iface_lines, peers)
    wg_set_peer(peer["pubkey"], remove=True)
    drop_client(name)
    return {"removed": name}


def update_peer(name, new_name=None, ip=None, dns=None, keepalive=None,
                mode=None, routes=None, cfg=None):
    cfg = cfg or load_config()
    iface_lines, peers = parse_conf()
    peer = find_peer(peers, name)
    if not peer:
        raise ApiError("找不到设备: %s" % name, 404)
    meta = load_client_meta(name) or {"name": name, "pubkey": peer["pubkey"],
                                      "routes": [], "ip": ""}
    priv = read_priv(name)  # 先读私钥 —— 改名会删旧文件, 之后就读不到了

    if new_name and new_name != name:
        new_name = normalize_name(new_name)
        if find_peer(peers, new_name) or load_client_meta(new_name):
            raise ApiError("设备名已存在: %s" % new_name)
        drop_client(name)
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
                                      meta.get("keepalive", 25))
        save_client(name, conf_text, meta)
    else:
        # 无客户端私钥备份(手工对等端): 仍持久化 meta, 否则改名即失联
        Path(CLIENTS).mkdir(parents=True, exist_ok=True)
        _, meta_p = client_paths(name)
        meta_p.write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                          encoding="utf-8")
        meta_p.chmod(0o600)
    return meta


import time


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


def list_peers():
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
        if p["name"]:
            try:
                has_client = client_paths(p["name"])[0].exists()
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
        })
    return rows


def show_conf(name):
    conf_p, _ = client_paths(name)
    if not conf_p.exists():
        raise ApiError("找不到该设备的配置(手工创建的对等端没有备份)", 404)
    return conf_p.read_text(encoding="utf-8")


def full_status(cfg):
    live, listen_port = live_status()
    return {
        "ok": True,
        "iface": {"name": WG_IFACE, "up": bool(live) or listen_port > 0,
                  "listen_port": listen_port or cfg.get("wg_port", 51820),
                  "public_key": server_pubkey()},
        "endpoint": cfg.get("endpoint", ""),
        "default_allowed": ", ".join(
            [cfg["vpn_cidr"]] + list(cfg.get("lan_cidrs") or [])),
        "next_ip": next_ip(cfg),
        "peers": list_peers(),
        "now": int(time.time()),
    }
