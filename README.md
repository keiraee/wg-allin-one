# wg-allin-one

一句话简介: 一键部署 WireGuard 中转 + 管理面板 + 设备管理 CLI(hy2-allin-one 同款体验)。

## 快速开始

```bash
curl -fsSL https://raw.githubusercontent.com/keiraee/wg-allin-one/main/wgaio.sh -o wgaio.sh && sudo bash wgaio.sh install
```

按中文向导回车即可; 装完把令牌保存好(只显示一次)。

**安装会做什么**: 自动安装 wireguard/python3 依赖、初始化 WireGuard 中枢 wg0(已有 wg0.conf 则不动)、
把套件落到 `/opt/wgaio`(可用 `WGAIO_DIR` 改)、写入 `/usr/local/bin/wgaio` 包装、
创建并启动 systemd 服务 `wgaio-panel.service`。(卸载: `wgaio uninstall`, 不动用户自有的 /etc/wireguard 配置)

## 重要: 安全组

安装结束会提示放行 UDP 端口——必须到云控制台安全组放行, 否则设备连不上。

## 管理菜单

装好后在终端里直接执行 `wgaio`（不要带子命令）进入菜单，里面可以升级、管设备、开关面板、看日志、回滚和卸载。`wgaio --help` 仍是命令说明。

## 升级轨道

升级只覆盖程序文件，不动 `config.json` 和 `clients/`。每次先向 GitHub 解析当前提交，再按该提交下载并校验 `SHA256SUMS`。日志会打出上次哈希和本次哈希；提交没变，或者哈希没变，就跳过覆盖。

- **稳定版**：菜单里选「升级稳定版」，或 `WGAIO_REF=latest wgaio upgrade`。跟 GitHub Release 的最新 tag。
- **抢先试用**：菜单里选「抢先试用 main」，或 `WGAIO_REF=main wgaio upgrade`。本机会记住 `main`，之后普通 `wgaio upgrade` 继续跟 `main`，不会被正式版带走。
- 用仓库里的 `main` 安装脚本装好的机器，轨道默认就是 `main`。

## 子命令

```
wgaio                  管理菜单
wgaio install
wgaio version
wgaio user add|del|edit|list|show
wgaio panel start|stop|restart|status|install
wgaio status
wgaio logs
wgaio upgrade
wgaio rollback
wgaio uninstall
```

## 公网管理

面板选"公网直接访问"后自动使用 `你的-IP.sslip.io:端口` 形式的自动域名(IP 地址的点变为横线, 如 `156-231-141-104.sslip.io:8888`)。

- 可配合 HTTPS 自签证书(浏览器会提示证书不受信, 点继续即可);
- 与 hy2 等服务共存靠端口隔离(面板默认 8888, hy2 占用 80/443);
- 密码采用分段随机格式, 强度高于普通 hex token。

## 安全面说明

- 仅 VPN 内网使用时: 面板只在 VPN 内网监听 + 令牌登录(哈希存储), 请勿把面板端口暴露公网;
- 公网使用时: 建议开启 HTTPS 自签证书(向导会引导); 纯 HTTP 公网下令牌明文传输有被窃听风险。
- `wgaio user show` 的输出含设备私钥, 禁止落日志。

## 开发

```bash
python -m unittest discover tests -v   # 需要 bash 与 sha256sum
```

改动发版文件(wgaio.sh/bin/lib/panel)后需重新生成 SHA256SUMS:
```bash
python -c "from pathlib import Path; ns='wgaio.sh bin/wgaio lib/core.sh lib/core.py lib/wizard.sh lib/install.sh lib/user.sh lib/panel.sh lib/upgrade.sh lib/uninstall.sh lib/status.sh lib/logs.sh lib/menu.sh panel/index.html panel/style.css panel/app.js'.split(); [Path(n).write_bytes(Path(n).read_bytes().replace(b'\r\n', b'\n').replace(b'\r', b'\n')) for n in ns]"
sha256sum wgaio.sh bin/wgaio lib/*.sh lib/core.py panel/index.html panel/style.css panel/app.js > SHA256SUMS
```
