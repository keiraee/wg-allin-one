# wg-allin-one

一句话简介: 一键部署 WireGuard 中转 + 管理面板 + 设备管理 CLI(hy2-allin-one 同款体验)。

更新有两条轨道：

- **正式版**：跟 [Releases](https://github.com/keiraee/wg-allin-one/releases) 里的最新 tag。现在这个 tag 是 [v0.1.0](https://github.com/keiraee/wg-allin-one/releases/tag/v0.1.0)。新装、以及没记住别的轨道时，安装和 `wgaio upgrade` 都走这里。
- **main**：仓库里还没打 tag 的提交。菜单「轨道」显示 `main` 的机器，普通升级会继续拉 `main`。要试用这条，见 [试用 main](#试用-main)。

`main` 比 `v0.1.0` 多这些还没发版的功能：面板随机入口、Let's Encrypt 正式证书、设备停用和换密钥、配置二维码、隧道配置备份、Linux / Mac 一分钟加入命令。现在执行 `WGAIO_REF=latest wgaio upgrade`，程序会换成 `v0.1.0`，这些功能不在那个 tag 里。`config.json` 和 `clients/` 不会被升级删掉。

## 快速开始

安装当前正式版：

```bash
curl -fsSL https://raw.githubusercontent.com/keiraee/wg-allin-one/v0.1.0/wgaio.sh -o wgaio.sh
sudo WGAIO_REF=latest bash wgaio.sh install
```

按中文向导回车即可; 装完把令牌保存好(只显示一次)。

`v0.1.0` 这份安装脚本在下载其余文件时，默认仍会去拉 `main`。命令里的 `WGAIO_REF=latest` 让它改拉最新 Release，并在本机记下「以后跟正式版」。装好后看菜单第一行，轨道应是 `latest`。

**安装会做什么**: 自动安装 wireguard/python3 依赖、初始化 WireGuard 中枢 wg0(已有 wg0.conf 则不动)、
把套件落到 `/opt/wgaio`(可用 `WGAIO_DIR` 改)、写入 `/usr/local/bin/wgaio` 包装、
创建并启动 systemd 服务 `wgaio-panel.service`。
(卸载: `wgaio uninstall` 会清掉服务/配置/命令，并删除**本工具生成的** wg0.conf；用户自有的 /etc/wireguard 配置不动；程序文件保留便于重装)

## 重要: 安全组

安装结束会提示放行 UDP 端口——必须到云控制台安全组放行, 否则设备连不上。

## 管理菜单

装好后在终端里直接执行 `wgaio`（不要带子命令）进入菜单，里面可以升级、管设备、开关面板、看日志、回滚和卸载。`wgaio --help` 仍是命令说明。

## 已经装好了，怎么更新

先看本机记的是哪条轨道：终端里执行 `wgaio`，菜单第一行的「轨道」就是。第 3 项也能看到轨道和哈希。

- 轨道是 `latest`，或者还没有轨道记录：`wgaio upgrade` 拉 GitHub 最新 Release。菜单第 1 项「升级稳定版」相同。现在会得到 `v0.1.0`。
- 轨道是 `main`：`wgaio upgrade` 继续拉 `main`。它不会自己改去正式版。

从 `main` 改回正式版，执行一次下面这句。执行后轨道改成 `latest`，程序换成当前最新 tag（现在是 `v0.1.0`）：

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
