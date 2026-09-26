# wg-allin-one 设计文档

- 日期：2026-09-24
- 状态：已批准（用户逐节审阅后批准）
- 第一期范围：VPS 侧一键部署（WireGuard 中转 hub + wg-panel 管理面板 + 客户端配置生成）

## 1. 背景与目标

把手工完成的「阿里云 VPS WireGuard 中转 + 管理面板」整理成可公开发布的一键脚本项目，
形态对齐 `hy2-allin-one`：单入口 + lib 模块 + 中文问答向导 + tests + SHA256SUMS + Releases 升级通道。

目标：

1. 一台全新 VPS 上 `curl | bash` 一键装好：WireGuard 中转（hub 拓扑）+ Web 管理面板 + 客户端配置生成。
2. 装完后用 `wgaio` 命令（SSH）或 Web 面板管理设备，双入口共享同一份核心逻辑。
3. 公开发布标准：零硬编码、零私密入库、有测试、有升级通道、中文文档。

## 2. 关键决策（用户已确认）

| 决策点 | 结论 |
|---|---|
| 用途 | 公开发布（GitHub + Releases + 升级命令） |
| 管理入口 | 命令 + 面板都要，共享同一份 Python 核心 |
| 命名 | 仓库 `wg-allin-one`，装完命令 `wgaio`（不撞官方 `wg`/`wg-quick`） |
| 客户端流量模式 | 分流（默认）+ 全隧道（可选）都支持 |
| 架构 | 方案 A：bash 外壳（安装/向导/升级）+ Python 核心（密钥/wg/HTTP/面板） |
| git 提交粒度 | 一个功能/修复 = 一个中文 commit（`feat:`/`fix:`/`chore:`/`docs:` 前缀） |
| 第二期 | OpenWrt 路由器端（ADG/ShellClash 联动）单独成模块，本期不做 |

## 3. 架构与布局

### 3.1 仓库布局

```
wg-allin-one/
├── wgaio.sh            # 入口（对齐 hy2.sh：curl 引导 + install/upgrade/uninstall 子命令）
├── bin/wgaio           # 套件树内薄包装（exec ../wgaio.sh）；生产入口是 install 写入的 /usr/local/bin/wgaio
├── lib/
│   ├── core.sh         # 日志/公共函数/校验
│   ├── wizard.sh       # 中文问答向导
│   ├── install.sh      # 装 wireguard/python3/systemd 单元（落盘到 /opt/wgaio 或 WGAIO_DIR）
│   ├── user.sh         # wgaio user add/del/edit/list/show 包装
│   ├── upgrade.sh      # 升级（提交哈希 + SHA256SUMS）+ 快照/回滚 + verify 校验修复
│   ├── uninstall.sh    # 清理服务/配置/命令（保留程序文件，可选保留 clients）
│   ├── panel.sh        # 面板 systemd 单元
│   └── core.py         # Python 核心：密钥/wg0.conf/HTTP API/面板服务
├── panel/              # index.html / style.css / app.js（浅色复古终端风）
├── tests/              # Python 单元测试（Windows 语义不兼容的用 skipIf 跳过）
├── docs/
│   └── superpowers/specs/   # 设计文档
├── config.example.json # 配置样例（无任何真实值）
├── README.md           # 中文
├── SHA256SUMS          # 发布校验
└── LICENSE
```

### 3.2 VPS 安装布局

```
/opt/wgaio/
├── core.py             # 核心（含 --serve 入口）
├── panel/              # 静态文件
├── clients/            # 客户端 .conf 备份（600 权限，含私钥）
└── config.json         # 安装参数（600 权限）
/etc/wireguard/wg0.conf # WireGuard 配置（600）
/usr/local/bin/wgaio    # 命令
/etc/systemd/system/wgaio-panel.service
```

## 4. 功能面（双入口共享 core.py）

### 4.1 CLI

| 命令 | 作用 |
|---|---|
| `wgaio install` | 中文向导安装（网段/端口/endpoint/转发模式/令牌），装完醒目提示「去安全组放行 UDP xxxx」 |
| `wgaio user add <名>` | 生成设备（自动分配 IP，可选分流/全隧道、DNS、keepalive） |
| `wgaio user del <名>` | 删除设备（网关设备二次确认） |
| `wgaio user edit <名>` | 改名/IP/DNS/keepalive/流量模式 |
| `wgaio user list` | 状态表（握手/流量/状态灯） |
| `wgaio user show <名>` | 重新导出 .conf |
| `wgaio status` / `logs` | 查看状态 / 日志 |
| `wgaio upgrade` / `verify` / `rollback` | 升级 / 校验并修复本地文件 / 回滚快照（不动 config.json） |
| `wgaio uninstall` | 清理服务/配置/命令（保留程序文件；用户自有 wg0 不动） |

### 4.2 面板（wg-panel）

- 浅色复古终端风 UI（纯白底、等宽字、细灰边框、`#8B5CF6` 主调、小彩色状态灯、全中文）。
- 登录页：安装时随机生成访问令牌（不进 argv，hash 后存储校验）。
- 功能：设备列表（握手/流量/状态灯）、生成配置（自动 IP、下载 .conf）、修改（名称/IP/DNS/keepalive/模式）、删除（网关二次警告）。
- core.py 双形态：`--serve` 起 HTTP；不带参数时当 CLI 后端被 bash 调用——CLI 与面板写同一份 wg0.conf，永不打架。

## 5. 配置参数化（零硬编码）

`config.json`（600）由向导收集，仓库只有 `config.example.json`：

| 键 | 说明 | 默认 |
|---|---|---|
| `vpn_cidr` | VPN 网段 | `10.66.66.0/24` |
| `wg_port` | WireGuard 监听端口（UDP） | `51820` |
| `endpoint` | 客户端接入点（IP 或域名:端口） | 向导必填 |
| `client_dns` | 客户端 DNS | `1.1.1.1` |
| `lan_cidrs` | 内网路由段（可选，多个） | 空 |
| `panel_bind` | 面板绑定地址 | VPN 网段首 IP（仅隧道内可达） |
| `panel_port` | 面板端口 | `8888` |
| `panel_token_hash` | 面板令牌哈希 | 随机生成 |
| `default_mode` | 新设备默认流量模式 | `split` |

**私钥/令牌明文/真实 IP 永不进 git**：.gitignore 挡 `clients/`、`config.json`、`*.key`、`*.conf`；测试断言扫描仓库无可疑内容兜底。

## 6. 数据流与热更新

- 写：CLI/面板 → core.py → wg0.conf 原子写（临时文件 + rename）+ `wg set` 热更新（不掉隧道）+ `clients/<名>.conf` 备份 + 元数据 `<名>.json`。
- 读：`wg show wg0 dump` 与 conf 注释合并 → 列表状态（ok/stale/off 状态灯）。
- 重启：wg0.conf 已落盘，开机由 wg-quick 拉起；面板服务 Restart=always。

## 7. 流量模式与全隧道防坑

- **分流（默认）**：客户端 `AllowedIPs = vpn_cidr + lan_cidrs`，其余流量自理。
- **全隧道（可选）**：客户端 `AllowedIPs = 0.0.0.0/0`。本期不做 IPv6，不写 `::/0`。
  服务端 wg0 继续用 wg-quick 的默认路由表，只为对等端网段装路由。拒绝把 `0.0.0.0/0` 写进服务端对等端，避免默认路由吸走 SSH。
  新建的 wg0.conf 用 PostUp/PostDown 持久化：进出 wg0 的转发、仅 VPN 源地址的 MASQUERADE、TCPMSS 钳制。已有 wg0.conf 不覆盖。

## 8. 错误处理与安全（吸收 hy2 血泪教训）

- 向导校验端口占用、网段冲突、endpoint 格式。
- install/upgrade 幂等；升级前快照，`wgaio rollback` 可回退（默认不覆盖 config.json）。
- `wgaio verify [--fix]` 校验本地程序文件；损坏时按当前轨道重新下载修复。
- 面板 API 只收 `application/json`（防 CSRF）；令牌校验用哈希比较；**令牌不进进程 argv**（hy2 8a09904 教训）。
- `clients/`、`config.json`、wg0.conf 权限 600；进程 umask 077。
- uninstall 清理服务/配置/备份/命令，保留程序文件便于重装；可选保留 clients；只删本工具生成的 wg0.conf。
- 下载 .conf 的 URL 路径做 percent-decode（公钥 base64 含 `/` `=` `+`，历史 bug）。

## 9. 测试 · CI · 发版

- `tests/`：unittest；Windows 语义不兼容用 `skipIf(os.name == "nt")`（对齐 hy2 模式，WSL 跑全套）。
- GitHub Actions：Linux 上跑单元测试。
- `SHA256SUMS` 校验下载文件；`wgaio upgrade` 对比提交哈希与 SHA256SUMS 内容哈希（相同则跳过覆盖）。
- 发版：Releases tag（v0.1.0 起）；main 轨道可用 `WGAIO_REF=main` 环境变量试用（`latest` 跟 Release）。
- commit 粒度：一个功能/修复一个中文 commit。

## 10. 实机验证计划（用户 VPS）

1. 用**备用端口**（面板 8899 / wg 复用现有 wg0 不动）装一套并存测试；
2. 测完卸掉旧 wg-panel（/opt/wg-panel），新面板换正式端口接管；
3. **现有 wg0.conf、密钥、对等端原样不动**——只接管面板与新增设备的管理面。

## 11. 非目标（本期明确不做）

- OpenWrt 路由器端（ADG/ShellClash 联动）——第二期单独设计。
- 反向代理/公网正式 CA 证书（向导支持公网监听 + 可选自签 HTTPS；正式证书由用户自理）。
- 多用户/配额/流量套餐（hy2 的订阅体系不搬）。
- IPv6 组网（用户已主动关闭 v6）。

### 11.1 范围变更（相对初稿）

初稿曾把「面板公网 HTTPS」列为非目标；产品已扩展为向导可选「公网直接访问」+ 自签证书，README 与向导均已覆盖。以本节为准。

## 12. 历史踩坑存档（写进测试与文档的根据）

| 坑 | 对策（已入设计） |
|---|---|
| VPS wg-quick 配 `0.0.0.0/0` 吸走 SSH 断连 | 服务端拒绝默认路由；全隧道只写在客户端，并只对 VPN 源地址做 NAT |
| WireGuard App 隧道名中文/空格报「名称无效」 | 设备名校验 `^[A-Za-z0-9_-]{1,15}$` |
| 公钥 base64 的 `/` `=` 在 URL 被编码导致 404 | 路径 percent-decode + 测试用例覆盖 |
| 回退时误删 NAT（masq）导致全家断网 | 卸载/回退操作白名单化，不动系统既有 NAT |
| 单文件过大撑爆输出无法完成开发 | 多模块拆分（本设计的仓库布局即为此） |
