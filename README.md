# wg-allin-one

一句话简介: 一键部署 WireGuard 中转 + 管理面板 + 设备管理 CLI(hy2-allin-one 同款体验)。

## 快速开始

```bash
curl -fsSL https://raw.githubusercontent.com/keiraee/wg-allin-one/main/wgaio.sh -o wgaio.sh && sudo bash wgaio.sh install
```

按中文向导回车即可; 装完把令牌保存好(只显示一次)。

**安装会做什么**: 自动安装 wireguard/python3 依赖、初始化 WireGuard 中枢 wg0(已有 wg0.conf 则不动)、
把套件落到 `/opt/wgaio`(可用 `WGAIO_DIR` 改)、写入 `/usr/local/bin/wgaio` 包装、
创建并启动 systemd 服务 `wgaio-panel.service`。
(卸载: `wgaio uninstall` 会清掉服务/配置/命令，并删除**本工具生成的** wg0.conf；用户自有的 /etc/wireguard 配置不动；程序文件保留便于重装)

## 重要: 安全组

安装结束会提示放行 UDP 端口——必须到云控制台安全组放行, 否则设备连不上。

## 管理菜单

装好后在终端里直接执行 `wgaio`（不要带子命令）进入菜单，里面可以升级、管设备、开关面板、看日志、回滚和卸载。`wgaio --help` 仍是命令说明。

## 升级轨道

升级只覆盖程序文件，不动 `config.json` 和 `clients/`。每次先向 GitHub 解析当前提交，再按该提交下载并校验 `SHA256SUMS`。日志会打出上次哈希和本次哈希；提交没变，或者哈希没变，就跳过覆盖。本地文件被改坏时用 `wgaio verify --fix` 按当前轨道修复。

- **稳定版**：菜单里选「升级稳定版」，或 `WGAIO_REF=latest wgaio upgrade`。跟 GitHub Release 的最新 tag。
- **抢先试用**：菜单里选「抢先试用 main」，或 `WGAIO_REF=main wgaio upgrade`。本机会记住 `main`，之后普通 `wgaio upgrade` 继续跟 `main`，不会被正式版带走。
- 用仓库里的 `main` 安装脚本装好的机器，轨道默认就是 `main`。

## 子命令

```
wgaio                  管理菜单
wgaio install
wgaio version
wgaio user add|del|edit|list|show|disable|enable|rotate
wgaio panel start|stop|restart|status|install
wgaio status
wgaio cert
wgaio logs
wgaio backup
wgaio backup restore 文件
wgaio upgrade
wgaio verify [--fix]
wgaio rollback
wgaio uninstall
```

`verify` 校验本地程序文件是否被改动；`verify --fix` 按当前轨道重新下载修复。
`rollback` 恢复程序文件到最近快照，**不会覆盖 `config.json`**（升级也从不改配置）。
`backup` 打包的是 `config.json`、`clients/` 和 `wg0.conf`，和升级快照分开。停用设备会留着原来的地址和私钥；更换密钥不改 IP，旧的客户端配置随之作废。面板里的二维码含私钥，不要截图外传。

## 公网管理

面板选「公网直接访问」后，域名是 `你的-IP.sslip.io`（点换成横线，例如 `156-231-141-104.sslip.io`）。默认向 Let's Encrypt 申请正式证书，浏览器可以直接打开。申请时本机 **TCP 80** 必须能从公网访问到，并且 80 没有被别的程序占用。

地址不是只开端口。后面还有一串随机入口，例如 `https://156-231-141-104.sslip.io:8888/wgaio-a1b2c3d4e5f6/`。只打开 `:8888` 会看到 404。完整地址在安装结束和 `wgaio status` 里。

证书没申请到时会先用自签证书（浏览器提示不受信），放行 80 之后执行 `wgaio cert` 再申请。向导里也可以改选自签或纯 HTTP。密码仍是分段随机格式。

## 安全面说明

- 仅 VPN 内网使用时: 面板只在 VPN 内网监听 + 令牌登录(哈希存储), 请勿把面板端口暴露公网;
- 公网使用时: 默认申请 Let's Encrypt。纯 HTTP 公网下令牌明文传输有被窃听风险。
- `wgaio user show` 的输出含设备私钥, 禁止落日志。

## 开发

```bash
python -m unittest discover tests -v   # 需要 bash 与 sha256sum
```

改动发版文件(wgaio.sh/bin/lib/panel)后需重新生成 SHA256SUMS:
```bash
python -c "from pathlib import Path; ns='wgaio.sh bin/wgaio lib/core.sh lib/core.py lib/qr.py lib/wizard.sh lib/install.sh lib/user.sh lib/panel.sh lib/upgrade.sh lib/uninstall.sh lib/status.sh lib/logs.sh lib/menu.sh lib/backup.sh panel/index.html panel/style.css panel/app.js'.split(); [Path(n).write_bytes(Path(n).read_bytes().replace(b'\r\n', b'\n').replace(b'\r', b'\n')) for n in ns]"
sha256sum wgaio.sh bin/wgaio lib/*.sh lib/*.py panel/index.html panel/style.css panel/app.js > SHA256SUMS
```
