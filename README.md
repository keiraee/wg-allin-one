# wg-allin-one

一句话简介: 一键部署 WireGuard 中转 + 管理面板 + 设备管理 CLI(hy2-allin-one 同款体验)。

## 快速开始

```bash
curl -fsSL <仓库地址>/wgaio.sh -o wgaio.sh && sudo bash wgaio.sh install
```

按中文向导回车即可; 装完把显示的访问令牌保存好——只显示一次。

**安装会做什么**: 写入 `/usr/local/bin/wgaio` 包装、部署文件到 `/opt/wgaio`、
创建并启动 systemd 服务 `wgaio-panel.service`。(卸载: `wgaio uninstall`, 不动 /etc/wireguard)

## 重要: 安全组

安装结束会提示放行 UDP 端口——必须到云控制台安全组放行, 否则设备连不上。

## 子命令

```
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

## 安全面说明

- 面板只在 VPN 内网监听 + 令牌登录(哈希存储); 请勿把面板端口暴露公网;
- `wgaio user show` 的输出含设备私钥, 禁止落日志。
- (面板默认绑定 VPN 隧道地址; 若自定义绑定, 请勿填 0.0.0.0)

## 开发

```bash
python -m unittest discover tests -v   # 需要 bash 与 sha256sum
```

改动发版文件(wgaio.sh/bin/lib/panel)后需重新生成 SHA256SUMS:
```bash
sha256sum wgaio.sh bin/wgaio lib/*.sh lib/core.py panel/index.html panel/style.css panel/app.js > SHA256SUMS
```
