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
                        cur["keepalive"] = int(v)
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
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(conf)
