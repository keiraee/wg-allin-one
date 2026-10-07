# wg-allin-one

一句话简介: 一键部署 WireGuard 中转 + 管理面板 + 设备管理 CLI。

当前正式版是 [v0.3.3](https://github.com/keiraee/wg-allin-one/releases/tag/v0.3.3)。

更新有两条轨道：

- **正式版**：跟 [Releases](https://github.com/keiraee/wg-allin-one/releases) 里的最新 tag。新装、以及没记住别的轨道时，安装和 `wgaio upgrade` 都走这里。
- **main**：仓库里还没打进 Release 的提交。菜单「轨道」显示 `main` 的机器，普通升级会继续拉 `main`。要试用这条，见 [试用 main](#试用-main)。

## 快速开始

安装当前正式版：

```bash
curl -fsSL https://raw.githubusercontent.com/keiraee/wg-allin-one/v0.3.3/wgaio.sh -o wgaio.sh
sudo bash wgaio.sh install
```

按中文向导回车即可; 装完把令牌保存好(只显示一次)。

这条安装脚本会下载 GitHub 最新 Release，不会去拉 `main`。装好后看菜单第一行，轨道应是 `latest`。

**安装会做什么**: 自动安装 wireguard/python3 依赖、初始化 WireGuard 中枢 wg0(已有 wg0.conf 则不动)、
把套件落到 `/opt/wgaio`(可用 `WGAIO_DIR` 改)、写入 `/usr/local/bin/wgaio` 包装、
创建并启动两个 systemd 服务: `wgaio-panel.service`(Python 面板，只监听 127.0.0.1)
和 `wgaio-caddy.service`(面板 HTTPS 前置，自动申请并续期证书)。
(卸载: `wgaio uninstall` 会清掉服务/配置/命令，并删除**本工具生成的** wg0.conf；用户自有的 /etc/wireguard 配置不动；程序文件保留便于重装)

## 重要: 安全组

安装结束会提示放行 UDP 端口——必须到云控制台安全组放行, 否则设备连不上。
公网面板还要放行 TCP 面板端口(默认 8443)；申请证书要 80(HTTP-01) 或 443(TLS-ALPN-01) 里有一个空闲。

## 管理菜单

装好后在终端里直接执行 `wgaio`（不要带子命令）进入菜单，里面可以升级、管设备、开关面板、看日志、回滚和卸载。`wgaio --help` 仍是命令说明。

安装会顺带写一个短命令 `wg`：敲 `wg` 直接进菜单，`wg user add …`、`wg upgrade` 这些也和 `wgaio` 等价；
`wg show`、`wg set`、`wg genkey` 等 WireGuard 自己的子命令仍然原样转交给真正的 `/usr/bin/wg`，不会抢。
（`/usr/local/bin/wg` 已经存在且不是本工具写的时，安装会跳过短命令，只保留 `wgaio`。）

## 已经装好了，怎么更新

先看本机记的是哪条轨道：终端里执行 `wgaio`，菜单第一行的「轨道」就是。第 3 项也能看到轨道和哈希。

- 轨道是 `latest`，或者还没有轨道记录：`wgaio upgrade` 拉 GitHub 最新 Release。菜单第 1 项「升级稳定版」相同。现在会得到 `v0.3.3`。
- 轨道是 `main`：`wgaio upgrade` 继续拉 `main`。它不会自己改去正式版。

从 `main` 改回正式版，执行一次下面这句。执行后轨道改成 `latest`，程序换成当前最新 tag（现在是 `v0.3.3`）：

```bash
WGAIO_REF=latest wgaio upgrade
```

升级只覆盖程序文件，不动 `config.json` 和 `clients/`。每次先向 GitHub 解析当前提交，再按该提交下载并校验 `SHA256SUMS`。日志会打出上次哈希和本次哈希；提交没变，或者哈希没变，就跳过覆盖。本地文件被改坏时用 `wgaio verify --fix` 按当前轨道修复。

## 试用 main

`main` 是仓库最新提交，还没有对应的 Release。第一次必须带 `WGAIO_REF=main`，本机会把轨道写成 `main`。之后普通 `wgaio upgrade` 继续跟 `main`，后续发出的正式版 tag 不会把它带走。

已安装，改跟 `main`：

```bash
WGAIO_REF=main wgaio upgrade
```

全新安装也直接用 `main`：

```bash
curl -fsSL https://raw.githubusercontent.com/keiraee/wg-allin-one/main/wgaio.sh -o wgaio.sh
sudo WGAIO_REF=main bash wgaio.sh install
```

要回到正式版，再执行一次 `WGAIO_REF=latest wgaio upgrade`。

## 国内网络

GitHub 在国内经常连不上（`curl: (7)`、卡住或 429）。wgaio **默认就会自动兜底**：先直连，连不上再依次走公共加速站（`gh-proxy.com`、`ghfast.top`、`ghproxy.net`），走镜像时输出里会写一行「直连失败, 改走镜像: …」。套件下载、升级、装 Caddy 都走这条路。

换成自己的加速前缀（多个用空格分开）：

```bash
WGAIO_MIRROR=https://你的加速站/ wgaio upgrade
```

不想走第三方加速（比如内网已有代理）：

```bash
WGAIO_NO_MIRROR=1 wgaio upgrade
```

第一次装如果 `raw.githubusercontent.com` 打不开，可以借加速站取脚本：

```bash
curl -fsSL https://gh-proxy.com/https://raw.githubusercontent.com/keiraee/wg-allin-one/v0.3.3/wgaio.sh -o wgaio.sh
sudo bash wgaio.sh install
```

### 系统包源也会自动换

`apt/dnf/yum/apk` 装依赖如果失败，wgaio 会自动探测国内镜像站（阿里云 → 腾讯云 → 清华 → 中科大 → 华为云，取第一个能通的），**把系统包源替换掉再重试一次**：

- Debian / Ubuntu：重写 `/etc/apt/sources.list`，原文件备份为 `sources.list.wgaio.bak`，`/etc/apt/sources.list.d` 里的旧源挪到 `sources.list.d.wgaio-saved/`
- Alpine：重写 `/etc/apk/repositories`，备份为 `repositories.wgaio.bak`
- 想手动执行或还原：

```bash
wgaio mirror            # 立刻把系统包源换成国内镜像
wgaio mirror restore    # 还原成原来的源
```

dnf/yum 的源文件格式各家不同，没做自动替换，失败时会提示你手动换。

镜像只换下载通道，套件内容仍按 `SHA256SUMS` 校验；加速站是第三方，介意就设 `WGAIO_NO_MIRROR=1`。

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

面板里可以复制 Linux 或 Mac 的加入命令。命令用时间戳签名，**60 秒后服务器拒绝**。命令行里没有私钥；执行时才把安装脚本取下来，写到临时文件，跑完就删。脚本安装 WireGuard 工具，配置写到 `/etc/wireguard/wgaio.conf`，不会覆盖这台电脑上已有的 `wg0`。Windows 和 iOS 仍用官方客户端扫二维码或导入下载的配置。

## 面板 HTTPS

面板的证书交给 wgaio 自己的 Caddy 实例：**选「公网直接访问」就自动申请、自动续期**，不用挑证书方式。
域名默认 `你的-IP.sslip.io`（点换成横线，例如 `156-231-141-104.sslip.io`），有自己的域名就在向导里填。

申请证书需要 **80(HTTP-01) 或 443(TLS-ALPN-01) 里有一个空闲**，域名也要能解析到本机：

- 80 空闲 → 用 HTTP-01，不碰 443；
- 80 被占、443 空闲 → 用 TLS-ALPN-01，不碰 80；
- 两个都被占（比如同机 HY2 的 Caddy 正在跑）→ 先用自签证书，面板照常能用；腾出其中一个后执行 `wgaio cert`。

面板 HTTPS 端口默认 **8443**，不占 443，方便和同机 HY2 共存。地址不是只开端口，后面还有一串随机入口，
例如 `https://156-231-141-104.sslip.io:8443/wgaio-a1b2c3d4e5f6/`；只打开 `:8443` 会看到 404。
完整地址在安装结束和 `wgaio status` 里。

wgaio 不碰系统的 `/etc/caddy/Caddyfile` 和 `caddy.service`：自己的配置在 `/etc/wgaio/Caddyfile`，
服务是 `wgaio-caddy.service`，证书数据在 `/var/lib/wgaio-caddy`。同机装了 HY2 也互不影响
（Caddy 二进制共用，配置和服务各管各的）。确实要自签或明文时才用环境变量：`WGAIO_TLS=internal` / `WGAIO_TLS=off`。
密码仍是分段随机格式。

## 安全面说明

- 仅 VPN 内网使用时: 面板只在 VPN 内网监听 + 令牌登录(哈希存储), 请勿把面板端口暴露公网;
- 公网使用时: 面板在 `wgaio-caddy` 后面，证书自动申请和续期；80/443 都被占时降级自签并提示。确实要走纯 HTTP 时才用 `WGAIO_TLS=off`，此时令牌明文传输有被窃听风险。
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
