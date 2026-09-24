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
    cfg = dict(DEFAULTS)
    for k, v in data.items():
        if k in DEFAULTS:
            cfg[k] = v
    validate_config(cfg)
    return cfg


def validate_config(cfg):
    if not CIDR_RE.match(str(cfg.get("vpn_cidr", ""))):
        raise ApiError("vpn_cidr 不是合法网段: %s" % cfg.get("vpn_cidr"))
    port = cfg.get("wg_port")
    if not isinstance(port, int) or not 1 <= port <= 65535:
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
    if not isinstance(pp, int) or not 1 <= pp <= 65535:
        raise ApiError("panel_port 必须是 1-65535 的整数")