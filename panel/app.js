/* wg-allin-one 面板 - 交互逻辑(登录门 + name 键 API) */
"use strict";

const $ = (id) => document.getElementById(id);

function showMsg(text, isErr) {
  const el = $("msg");
  el.textContent = text;
  el.className = isErr ? "err" : "";
  el.style.display = "block";
  if (!isErr) setTimeout(() => { el.style.display = "none"; }, 5000);
}

let joinTimer = null;
let joinBusy = false;

function clearJoinTimer() {
  if (joinTimer) {
    clearInterval(joinTimer);
    joinTimer = null;
  }
}

function openModal(title, bodyHtml) {
  clearJoinTimer();
  $("modal-title").textContent = title;
  $("modal-body").innerHTML = bodyHtml;
  $("modal-mask").classList.add("show");
}
function closeModal() {
  clearJoinTimer();
  $("modal-mask").classList.remove("show");
}
$("modal-mask").addEventListener("click", (e) => {
  if (e.target === $("modal-mask")) closeModal();
});

function fmtBytes(n) {
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
  if (n < 1073741824) return (n / 1048576).toFixed(1) + " MB";
  return (n / 1073741824).toFixed(2) + " GB";
}
function fmtAgo(ts, now) {
  if (!ts) return "从未";
  const d = Math.max(0, now - ts);
  if (d < 60) return d + " 秒前";
  if (d < 3600) return Math.floor(d / 60) + " 分钟前";
  if (d < 86400) return Math.floor(d / 3600) + " 小时前";
  return Math.floor(d / 86400) + " 天前";
}
const STATE_TXT = { ok: "已连接", stale: "掉线", off: "离线", disabled: "已停用" };

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function showLogin() { $("login-mask").style.display = "flex"; }

function panelUrl(path) {
  const rel = String(path).replace(/^\//, "");
  return new URL(rel, new URL(".", window.location.href)).href;
}

async function api(path, opts) {
  const res = await fetch(panelUrl(path), opts);
  if (res.status === 401) {
    showLogin();
    throw new Error("未登录或会话过期");
  }
  let data = null;
  try { data = await res.json(); } catch (e) { /* 非 JSON */ }
  if (!res.ok) {
    const err = (data && data.error) ? data.error : ("请求失败 HTTP " + res.status);
    const e = new Error(err);
    e.code = res.status;
    throw e;
  }
  return data;
}

async function tryLogin() {
  const token = $("login-token").value.trim();
  if (!token) { $("login-err").textContent = "请输入令牌"; return; }
  try {
    await api("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: token }),
    });
    $("login-mask").style.display = "none";
    $("login-err").textContent = "";
    $("login-token").value = "";
    refresh();
  } catch (e) {
    $("login-err").textContent = "登录失败: " + e.message;
  }
}
$("btn-logout").addEventListener("click", async () => {
  try {
    await api("/api/logout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
  } catch (e) { /* 未登录时接口会返回 401 */ }
  showLogin();
});
$("btn-login").addEventListener("click", tryLogin);
$("login-token").addEventListener("keydown", (e) => {
  if (e.key === "Enter") tryLogin();
});

// ---- delegated click handler (replaces all inline onclick) ----
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-action]");
  if (!btn) return;
  const name = btn.dataset.name || "";
  const act = btn.dataset.action;
  if (act === "dl") dlConf(name);
  else if (act === "edit") editPeer(name);
  else if (act === "del") delPeer(name, btn.dataset.gw === "1");
  else if (act === "del-force") doDel(name, true);
  else if (act === "del-noconf") doDel(name, false);
  else if (act === "save") saveEdit(name);
  else if (act === "copy") copyConf();
  else if (act === "join") copyJoin(name, btn.dataset.os || "linux");
  else if (act === "close") closeModal();
  else if (act === "qr") showPeerQr(name);
  else if (act === "disable") disablePeer(name, btn.dataset.gw === "1");
  else if (act === "disable-force") doDisable(name, true);
  else if (act === "enable") doEnable(name);
  else if (act === "rotate") confirmRotate(name);
  else if (act === "rotate-go") doRotate(name);
});

function extraRoutesOf(p) {
  return p.allowed_ips.filter((a) => a !== p.ip + "/32").join(", ");
}

function renderRows(st) {
  const tb = $("peer-rows");
  if (!st.peers.length) {
    tb.innerHTML = '<tr><td colspan="8" class="muted">还没有设备, 先在上面生成一个</td></tr>';
    return;
  }
  tb.innerHTML = st.peers.map((p) => {
    const gw = p.is_gateway ? ' <span class="dot accent" title="内网网关"></span>' : "";
    const routes = extraRoutesOf(p) || "—";
    const activeBtn = p.disabled
      ? `<button class="mini" data-action="enable" data-name="${esc(p.name)}">启用</button>`
      : `<button class="mini" data-action="disable" data-name="${esc(p.name)}" data-gw="${p.is_gateway ? "1" : "0"}">停用</button>`;
    return `<tr class="${p.disabled ? "disabled" : ""}">
      <td><span class="dot ${esc(p.state)}" title="${esc(STATE_TXT[p.state] || p.state)}"></span>${STATE_TXT[p.state] || p.state}</td>
      <td>${esc(p.name)}${gw}</td>
      <td class="mono">${esc(p.ip)}</td>
      <td class="mono muted">${esc(routes)}</td>
      <td class="mono">${fmtAgo(p.last_handshake, st.now)}</td>
      <td class="mono">${fmtBytes(p.rx)}</td>
      <td class="mono">${fmtBytes(p.tx)}</td>
      <td class="ops">
        ${p.has_client ? `<button class="mini" data-action="qr" data-name="${esc(p.name)}">二维码</button>` : ""}
        ${p.has_client ? `<button class="mini" data-action="dl" data-name="${esc(p.name)}">下载</button>` : ""}
        ${activeBtn}
        ${p.has_client ? `<button class="mini" data-action="rotate" data-name="${esc(p.name)}">换密钥</button>` : ""}
        <button class="mini" data-action="edit" data-name="${esc(p.name)}">修改</button>
        <button class="mini danger" data-action="del" data-name="${esc(p.name)}" data-gw="${p.is_gateway ? "1" : "0"}">删除</button>
      </td>
    </tr>`;
  }).join("");
}

async function refresh() {
  try {
    const st = await api("/api/status");
    $("st-iface").innerHTML = `<span class="dot ${st.iface.up ? "ok" : "off"}"></span>${st.iface.up ? "运行中" : "未启动"}`;
    $("st-port").textContent = st.iface.listen_port + "/udp";
    $("st-ep").textContent = st.endpoint;
    $("st-pub").textContent = st.iface.public_key ? st.iface.public_key.slice(0, 16) + "…" : "(未配置)";
    $("st-count").textContent = st.peers.length + " 个";
    const bind = st.panel_bind || "";
    $("access-tag").textContent = bind === "0.0.0.0"
      ? "公网可访问 · 建议使用 HTTPS"
      : (bind ? "只监听 " + bind : "管理面板");
    if (!$("f-ip").value) $("f-ip").placeholder = st.next_ip || "地址池已满";
    renderRows(st);
  } catch (e) {
    if (e.message !== "未登录或会话过期") showMsg("加载状态失败: " + e.message, true);
  }
}

$("btn-add").addEventListener("click", async () => {
  const name = $("f-name").value.trim();
  if (!name) { showMsg("请填设备名(1-15 位字母/数字/_/-)", true); return; }
  try {
    const d = await api("/api/peers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: name,
        ip: $("f-ip").value.trim(),
        dns: $("f-dns").value.trim(),
        keepalive: $("f-ka").value.trim(),
        mode: $("f-mode").value,
        routes: $("f-routes").value.trim(),
      }),
    });
    $("f-name").value = ""; $("f-ip").value = ""; $("f-dns").value = "";
    $("f-ka").value = "25"; $("f-routes").value = "";
    openConfModal(
      "✓ 已生成: " + d.peer.name + " (" + d.peer.ip + "/" + d.peer.mode + ")",
      d.peer.name, d.conf,
      "手机打开 WireGuard，点「从二维码创建」，或把文本存成 " + d.peer.name + ".conf 再导入。");
    showMsg("设备 " + d.peer.name + " 已创建");
    refresh();
  } catch (e) { showMsg("创建失败: " + e.message, true); }
});

function openConfModal(title, name, conf, hint) {
  const full = String(conf).indexOf("0.0.0.0/0") !== -1;
  const httpWarn = location.protocol === "http:"
    ? `<p class="warn-text">面板现在是 HTTP。执行加入命令时，私钥会明文经过网络。</p>` : "";
  openModal(title,
    `<p class="hint">${esc(hint)}</p>
     <img id="qr-img" alt="配置二维码" width="220" height="220">
     <p class="warn-text">二维码和下面的文本都含私钥。不要截图，不要转发。</p>
     <pre id="conf-text">${esc(conf)}</pre>
     <div class="join-box">
       <p class="hint">Linux 或 Mac 加入这台内网：复制命令，到那台电脑的终端粘贴。命令 1 分钟内有效，里面没有私钥。私钥只在这一分钟里随脚本下来，用完就删。接口名是 wgaio，不会动那台电脑上已有的 wg0。电脑上需要已有 curl。</p>
       ${full ? `<p class="warn-text">这是全隧道。在远程 Linux 上执行会把默认路由改走 VPN，SSH 可能中断。</p>` : ""}
       ${httpWarn}
       <p class="hint">Mac 没有 Homebrew 时，用 App Store 的 WireGuard，再点「下载 .conf」导入。Windows 和 iOS 用官方客户端扫上面的二维码。</p>
       <pre id="join-cmd" hidden></pre>
       <p class="hint" id="join-exp" hidden></p>
       <div class="modal-ops start">
         <button data-action="join" data-os="linux" data-name="${esc(name)}">复制 Linux 加入命令</button>
         <button data-action="join" data-os="mac" data-name="${esc(name)}">复制 Mac 命令</button>
       </div>
     </div>
     <div class="modal-ops">
       <button data-action="copy">复制内容</button>
       <button class="primary" data-action="dl" data-name="${esc(name)}">下载 .conf</button>
       <button data-action="close">关闭</button>
     </div>`);
  attachQr(name);
}

function armJoinExpiry(seconds) {
  clearJoinTimer();
  const end = Date.now() + seconds * 1000;
  const tick = () => {
    const node = $("join-exp");
    if (!node) {
      clearJoinTimer();
      return;
    }
    const left = Math.max(0, Math.ceil((end - Date.now()) / 1000));
    node.hidden = false;
    if (left <= 0) {
      node.textContent = "这条命令已超过 1 分钟，不能再用。需要的话重新复制。";
      clearJoinTimer();
      return;
    }
    node.textContent = "还剩 " + left + " 秒。超时后服务器会拒绝这条命令。";
  };
  tick();
  joinTimer = setInterval(tick, 1000);
}

async function copyJoin(name, osName) {
  if (joinBusy) return;
  joinBusy = true;
  try {
    const d = await api("/api/peers/" + encodeURIComponent(name) + "/join?os=" + encodeURIComponent(osName),
                         { method: "POST" });
    const pre = $("join-cmd");
    if (pre) {
      pre.hidden = false;
      pre.textContent = d.command;
    }
    armJoinExpiry(d.expires_in || 60);
    const notes = [];
    if (d.loopback) notes.push("命令里的地址只有本机能打开。要加入的电脑必须能访问这个面板地址。");
    if (d.plain_http) notes.push("当前是 HTTP，私钥会明文传输。");
    try {
      await navigator.clipboard.writeText(d.command);
      showMsg("已复制，" + (d.expires_in || 60) + " 秒内有效。" + (notes.length ? " " + notes.join("") : ""));
    } catch (err) {
      showMsg("没能写入剪贴板，请从框里手动复制。" + (notes.length ? " " + notes.join("") : ""), true);
    }
  } catch (e) {
    showMsg("生成加入命令失败: " + e.message, true);
  } finally {
    joinBusy = false;
  }
}

async function attachQr(name) {
  const img = $("qr-img");
  if (!img) return;
  try {
    const res = await fetch(panelUrl("/api/peers/" + encodeURIComponent(name) + "/qr"));
    if (res.status === 401) { showLogin(); return; }
    if (!res.ok) throw new Error("HTTP " + res.status);
    const blob = await res.blob();
    if (img._url) URL.revokeObjectURL(img._url);
    img._url = URL.createObjectURL(blob);
    img.src = img._url;
  } catch (e) {
    img.alt = "二维码生成失败";
  }
}

async function showPeerQr(name) {
  let conf = "";
  try {
    const res = await fetch(panelUrl("/api/peers/" + encodeURIComponent(name) + "/conf"));
    if (res.status === 401) { showLogin(); return; }
    if (!res.ok) throw new Error("HTTP " + res.status);
    conf = await res.text();
  } catch (e) {
    showMsg("读配置失败: " + e.message, true);
    return;
  }
  openConfModal("二维码: " + name, name, conf, "用 WireGuard 扫这个码即可导入。");
}

function disablePeer(name, isGateway) {
  if (!isGateway) { doDisable(name, false); return; }
  openModal("停用网关: " + name,
    `<p class="warn-text">这是内网网关。停用后，其他设备暂时进不了它后面的内网。地址和密钥都还在，可以再启用。</p>
     <div class="modal-ops">
       <button class="danger" data-action="disable-force" data-name="${esc(name)}">仍要停用</button>
       <button data-action="close">取消</button>
     </div>`);
}

async function doDisable(name, force) {
  try {
    await api("/api/peers/" + encodeURIComponent(name) + "/disable" + (force ? "?force=1" : ""),
              { method: "POST" });
    closeModal();
    showMsg("已停用 " + name + "，地址还留着");
    refresh();
  } catch (e) {
    if (e.code === 409) {
      openModal("需要二次确认",
        `<p class="warn-text">${esc(e.message)}</p>
         <div class="modal-ops">
           <button class="danger" data-action="disable-force" data-name="${esc(name)}">仍要停用</button>
           <button data-action="close">取消</button>
         </div>`);
    } else {
      showMsg("停用失败: " + e.message, true);
    }
  }
}

async function doEnable(name) {
  try {
    await api("/api/peers/" + encodeURIComponent(name) + "/enable", { method: "POST" });
    showMsg("已启用 " + name);
    refresh();
  } catch (e) { showMsg("启用失败: " + e.message, true); }
}

function confirmRotate(name) {
  openModal("更换密钥: " + name,
    `<p>确定更换 <b>${esc(name)}</b> 的密钥？</p>
     <p class="hint">IP 不变。手机上的旧配置和旧二维码会立刻失效，需要重新导入。</p>
     <div class="modal-ops">
       <button class="danger" data-action="rotate-go" data-name="${esc(name)}">更换</button>
       <button data-action="close">取消</button>
     </div>`);
}

async function doRotate(name) {
  try {
    const d = await api("/api/peers/" + encodeURIComponent(name) + "/rotate", { method: "POST" });
    openConfModal("已更换密钥: " + d.peer.name, d.peer.name, d.conf,
      "旧配置已经作废。请用新的二维码或文本重新导入，IP 没有变。");
    showMsg("已更换 " + d.peer.name + " 的密钥");
    refresh();
  } catch (e) { showMsg("更换失败: " + e.message, true); }
}

function copyConf() {
  const t = $("conf-text");
  if (!t) return;
  navigator.clipboard.writeText(t.textContent).then(
    () => showMsg("已复制到剪贴板"),
    () => showMsg("复制失败, 请手动全选复制", true));
}

function dlConf(name) {
  const a = document.createElement("a");
  a.href = panelUrl("/api/peers/" + encodeURIComponent(name) + "/conf");
  a.download = name + ".conf";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function editPeer(name) {
  api("/api/status").then((st) => {
    const p = st.peers.find((x) => x.name === name);
    if (!p) { showMsg("设备不存在", true); return; }
    openModal("修改设备: " + p.name,
      `<label class="field">设备名<input id="e-name" maxlength="15" value="${esc(p.name)}"></label>
       <label class="field">内网 IP<input id="e-ip" class="mono" value="${esc(p.ip)}"></label>
       <label class="field">DNS(重下载配置生效)<input id="e-dns" class="mono" value=""></label>
       <label class="field">保活(秒)<input id="e-ka" class="mono" value="${esc(String(p.keepalive != null ? p.keepalive : 25))}"></label>
       <label class="field">流量模式
         <select id="e-mode">
           <option value="split">分流(只进内网)</option>
           <option value="full">全隧道(0.0.0.0/0)</option>
         </select>
       </label>
       <label class="field">网关路由段(逗号分隔, 空=无)<input id="e-routes" class="mono" value="${esc(extraRoutesOf(p))}"></label>
       ${p.is_gateway ? '<p class="warn-text">⚠ 这是内网网关, 改动请确认无误。</p>' : ""}
       <p class="hint">改 IP、名称或网关路由后，请重新下载相关设备的 .conf。网关自己的配置不会把这段内网再送回隧道。</p>
       <div class="modal-ops">
         <button class="primary" data-action="save" data-name="${esc(p.name)}">保存</button>
         <button data-action="close">取消</button>
       </div>`);
    $("e-mode").value = p.mode || "split";
    $("e-mode").dataset.dirty = "0";
    $("e-mode").addEventListener("change", () => { $("e-mode").dataset.dirty = "1"; });
  }).catch((e) => showMsg("读取设备失败: " + e.message, true));
}

async function saveEdit(name) {
  try {
    await api("/api/peers/" + encodeURIComponent(name), {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        new_name: $("e-name").value.trim(),
        ip: $("e-ip").value.trim(),
        dns: $("e-dns").value.trim() || undefined,
        keepalive: $("e-ka").value.trim(),
        mode: $("e-mode").dataset.dirty === "1" ? $("e-mode").value : undefined,
        routes: $("e-routes").value.trim(),
      }),
    });
    closeModal();
    showMsg("已保存修改");
    refresh();
  } catch (e) { showMsg("保存失败: " + e.message, true); }
}

function delPeer(name, isGateway) {
  openModal("删除设备: " + name,
    `<p>确定删除 <b>${esc(name)}</b> ?</p>
     <p class="hint">会同时移除 wg0 里的对等端和已保存的 .conf, 不可恢复。</p>
     ${isGateway ? '<p class="warn-text">⚠ 这是内网网关! 删除后 VPN 进内网会断! 若确定, 再点一次「仍要删除」。</p>' : ""}
     <div class="modal-ops">
       ${isGateway
         ? `<button class="danger" data-action="del-force" data-name="${esc(name)}">仍要删除</button>`
         : `<button class="danger" data-action="del-noconf" data-name="${esc(name)}">删除</button>`}
       <button data-action="close">取消</button>
     </div>`);
}

async function doDel(name, force) {
  try {
    await api("/api/peers/" + encodeURIComponent(name) + (force ? "?force=1" : ""),
              { method: "DELETE" });
    closeModal();
    showMsg("设备已删除");
    refresh();
  } catch (e) {
    if (e.code === 409) {
      openModal("需要二次确认",
        `<p class="warn-text">${esc(e.message)}</p>
         <div class="modal-ops">
           <button class="danger" data-action="del-force" data-name="${esc(name)}">仍要删除</button>
           <button data-action="close">取消</button>
         </div>`);
    } else {
      showMsg("删除失败: " + e.message, true);
    }
  }
}

refresh();
setInterval(refresh, 10000);
