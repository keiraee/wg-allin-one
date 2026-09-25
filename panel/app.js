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

function openModal(title, bodyHtml) {
  $("modal-title").textContent = title;
  $("modal-body").innerHTML = bodyHtml;
  $("modal-mask").classList.add("show");
}
function closeModal() { $("modal-mask").classList.remove("show"); }
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
const STATE_TXT = { ok: "已连接", stale: "掉线", off: "离线" };

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function showLogin() { $("login-mask").style.display = "flex"; }

async function api(path, opts) {
  const res = await fetch(path, opts);
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
  else if (act === "close") closeModal();
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
    return `<tr>
      <td><span class="dot ${esc(p.state)}" title="${esc(STATE_TXT[p.state] || p.state)}"></span>${STATE_TXT[p.state] || p.state}</td>
      <td>${esc(p.name)}${gw}</td>
      <td class="mono">${esc(p.ip)}</td>
      <td class="mono muted">${esc(routes)}</td>
      <td class="mono">${fmtAgo(p.last_handshake, st.now)}</td>
      <td class="mono">${fmtBytes(p.rx)}</td>
      <td class="mono">${fmtBytes(p.tx)}</td>
      <td class="ops">
        ${p.has_client ? `<button class="mini" data-action="dl" data-name="${esc(p.name)}">下载</button>` : ""}
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
    openModal("✓ 已生成: " + d.peer.name + " (" + d.peer.ip + "/" + d.peer.mode + ")",
      `<p class="hint">把下面内容保存为 <b>${esc(d.peer.name)}.conf</b> 发到设备上, 在 WireGuard 里「从文件导入」即可。</p>
       <pre id="conf-text">${esc(d.conf)}</pre>
       <div class="modal-ops">
         <button data-action="copy">复制内容</button>
         <button class="primary" data-action="dl" data-name="${esc(d.peer.name)}">下载 .conf</button>
         <button data-action="close">关闭</button>
       </div>`);
    showMsg("设备 " + d.peer.name + " 已创建");
    refresh();
  } catch (e) { showMsg("创建失败: " + e.message, true); }
});

function copyConf() {
  const t = $("conf-text");
  if (!t) return;
  navigator.clipboard.writeText(t.textContent).then(
    () => showMsg("已复制到剪贴板"),
    () => showMsg("复制失败, 请手动全选复制", true));
}

function dlConf(name) {
  const a = document.createElement("a");
  a.href = "/api/peers/" + encodeURIComponent(name) + "/conf";
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
       <p class="hint">改 IP/名称后请重新下载 .conf 导入到设备。全隧道只走 IPv4，安装时会在 wg0 上打开转发和 NAT。</p>
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
