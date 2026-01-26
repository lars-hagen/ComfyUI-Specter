/**
 * Specter Dialogs & Browser Popup
 */
import { api } from "../../scripts/api.js";

// Global styles
const css = `
.specter-loading::after { content: ''; animation: specter-dots 1.5s steps(4, end) infinite; }
@keyframes specter-dots { 0%,25%{content:''} 26%,50%{content:'.'} 51%,75%{content:'..'} 76%,100%{content:'...'} }
.specter-dialog { display: none; position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 10000; background: var(--comfy-menu-bg, #353535); border-radius: 12px; box-shadow: 0 8px 32px rgba(0,0,0,0.4); border: 1px solid var(--border-color, #4e4e4e); min-width: 400px; }
.specter-dialog-header { display: flex; justify-content: space-between; align-items: center; padding: 12px 16px; cursor: move; }
.specter-dialog-title { color: var(--fg-color, #fff); font-weight: 600; font-size: 16px; display: flex; align-items: center; gap: 8px; }
.specter-dialog-content { padding: 0 16px 16px; position: relative; }
.specter-dialog textarea { width: 100%; height: 150px; resize: vertical; background: var(--comfy-input-bg, #222); border: 1px solid var(--border-color, #4e4e4e); border-radius: 6px; color: var(--fg-color, #ddd); padding: 8px; font-family: monospace; font-size: 12px; }
.specter-dialog-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 12px; }
.specter-dropzone { border: 2px dashed var(--border-color, #4e4e4e); border-radius: 6px; padding: 16px; text-align: center; color: var(--p-text-muted-color, #888); margin-bottom: 8px; transition: border-color 0.2s; cursor: pointer; }
.specter-dropzone.dragover { border-color: var(--p-primary-color, #6366f1); background: rgba(99, 102, 241, 0.1); }
.specter-subtitle { color: var(--p-text-muted-color, #888); font-size: 12px; }
.specter-canvas { cursor: pointer; display: block; border-radius: 6px; outline: none; }
.specter-loading-overlay { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); color: #888; font: 16px system-ui, sans-serif; display: none; }
.specter-close { font-size: 20px; line-height: 1; padding: 4px 8px; background: none; border: none; color: var(--fg-color, #fff); cursor: pointer; opacity: 0.7; }
.specter-close:hover { opacity: 1; }
`;
document.head.appendChild(Object.assign(document.createElement("style"), { textContent: css }));

// Make element draggable
function makeDraggable(el, handle) {
    let drag = null;
    handle.onmousedown = e => { if (e.target.tagName !== "BUTTON") drag = { x: e.clientX - el.offsetLeft, y: e.clientY - el.offsetTop }; };
    document.addEventListener("mousemove", e => { if (drag) { el.style.left = `${e.clientX - drag.x}px`; el.style.top = `${e.clientY - drag.y}px`; el.style.transform = "none"; } });
    document.addEventListener("mouseup", () => drag = null);
}

// Dialog factory
export function createDialog(title, icon, { subtitle = "", draggable = true } = {}) {
    const el = Object.assign(document.createElement("div"), {
        className: "specter-dialog p-dialog p-component",
        innerHTML: `<div class="specter-dialog-header"><div><span class="specter-dialog-title"><i class="pi pi-${icon}"></i>${title}</span>${subtitle ? `<div class="specter-subtitle">${subtitle}</div>` : ""}</div><button class="specter-close" type="button">×</button></div><div class="specter-dialog-content"></div>`
    });
    const hide = () => el.style.display = "none";
    el.querySelector("button").onclick = hide;
    if (draggable) makeDraggable(el, el.querySelector(".specter-dialog-header"));
    document.body.appendChild(el);
    return { el, content: el.querySelector(".specter-dialog-content"), show: () => el.style.display = "block", hide, setTitle: (t, i) => el.querySelector(".specter-dialog-title").innerHTML = `<i class="pi pi-${i}"></i>${t}`, setSubtitle: s => { const sub = el.querySelector(".specter-subtitle"); if (sub) sub.style.display = s ? "block" : "none"; } };
}

// Browser popup singleton
export const browserPopup = {
    dialog: null, canvas: null, ctx: null, ws: null, active: false, onClose: null, loadingOverlay: null,

    init() {
        if (this.dialog) return;
        this.dialog = createDialog("Browser", "cog", { subtitle: "Closes automatically when logged in" });
        this.dialog.el.querySelector("button").onclick = () => this.stop();
        this.canvas = Object.assign(document.createElement("canvas"), { className: "specter-canvas", tabIndex: 0 });
        this.ctx = this.canvas.getContext("2d");
        this.loadingOverlay = Object.assign(document.createElement("div"), { className: "specter-loading-overlay", innerHTML: `Launching browser<span class="specter-loading"></span>` });
        this.dialog.content.append(this.canvas, this.loadingOverlay);
        this.setupEvents();
    },

    setupEvents() {
        let mouseDown = null, lastMove = 0;
        const coords = e => { const r = this.canvas.getBoundingClientRect(); return { x: Math.round((e.clientX - r.left) * this.canvas.width / r.width), y: Math.round((e.clientY - r.top) * this.canvas.height / r.height) }; };
        const send = ev => this.ws?.readyState === 1 && this.ws.send(JSON.stringify(ev));

        this.canvas.onmousedown = e => { this.canvas.focus(); e.preventDefault(); mouseDown = coords(e); send({ type: "mousedown", ...mouseDown }); };
        this.canvas.onmouseup = e => { e.preventDefault(); const c = coords(e); send({ type: "mouseup", ...(mouseDown && Math.abs(c.x - mouseDown.x) <= 5 ? mouseDown : c) }); mouseDown = null; };
        this.canvas.onmousemove = e => { if (Date.now() - lastMove > 33) { lastMove = Date.now(); send({ type: "mousemove", ...coords(e) }); } };
        this.canvas.onwheel = e => { e.preventDefault(); send({ type: "scroll", ...coords(e), dy: e.deltaY }); };
        this.canvas.onkeydown = async e => {
            e.preventDefault(); e.stopPropagation();
            const ctrl = e.ctrlKey || e.metaKey;
            if (ctrl && e.key.toLowerCase() === "v") { try { const t = await navigator.clipboard.readText(); if (t) send({ type: "type", text: t }); } catch {} return; }
            send(e.key.length === 1 && !ctrl && !e.altKey ? { type: "type", text: e.key } : { type: "keydown", key: e.key });
        };
        this.canvas.onkeyup = this.canvas.onkeypress = e => { e.preventDefault(); e.stopPropagation(); };
    },

    async start(endpoint, title, onClose = null) {
        this.init();
        const isLogin = title.includes("Login");
        this.dialog.setTitle(title, isLogin ? "sign-in" : "cog");
        this.dialog.setSubtitle(isLogin);
        this.onClose = onClose;
        this.dialog.show();
        this.canvas.width = 600; this.canvas.height = 800;
        this.loadingOverlay.style.display = "block";

        try {
            const data = await fetch(endpoint, { method: "POST" }).then(r => r.json());
            if (data.status === "error") { this.loadingOverlay.style.display = "none"; this.showError(data.message); return; }
            this.active = true;
            this.connectWS();
            this.canvas.focus();
        } catch { this.loadingOverlay.style.display = "none"; this.showError("Failed to connect to server"); }
    },

    showError(msg) {
        this.canvas.width = 450; this.canvas.height = 80;
        this.ctx.fillStyle = "#2a2a2a"; this.ctx.fillRect(0, 0, 450, 80);
        this.ctx.fillStyle = "#f87171"; this.ctx.font = "14px system-ui, sans-serif";
        this.ctx.fillText("⚠ " + msg.split("\n")[0], 16, 45);
    },

    connectWS() {
        const protocol = location.protocol === "https:" ? "wss:" : "ws:";
        this.ws = new WebSocket(`${protocol}//${location.host}/specter/browser/ws`);
        this.ws.onmessage = async e => {
            if (typeof e.data === "string") {
                const msg = JSON.parse(e.data);
                if (msg.type === "logged_in") { this.active = false; this.loadingOverlay.style.display = "none"; this.ws?.close(); this.ws = null; this.dialog?.hide(); this.onClose?.(); }
                return;
            }
            this.loadingOverlay.style.display = "none";
            const bmp = await createImageBitmap(e.data);
            this.canvas.width = bmp.width; this.canvas.height = bmp.height;
            this.ctx.drawImage(bmp, 0, 0); bmp.close();
        };
        this.ws.onclose = () => this.active && setTimeout(() => this.connectWS(), 1000);
    },

    stop() {
        this.active = false;
        this.loadingOverlay.style.display = "none";
        this.ws?.close(); this.ws = null;
        this.dialog?.hide();
        this.onClose?.();
        fetch("/specter/browser/stop", { method: "POST" }).catch(() => {});
    }
};

// Login notification
let loginNotification = null;

export function showLoginNotification(service) {
    if (loginNotification) return;
    const close = () => { loginNotification?.remove(); loginNotification = null; };

    loginNotification = Object.assign(document.createElement("div"), {
        style: "position:fixed;top:10px;left:50%;transform:translateX(-50%);background:var(--error-bg,#c53030);color:var(--fg-color,white);padding:12px 16px;border-radius:8px;z-index:10001;box-shadow:0 4px 16px rgba(0,0,0,0.3);display:flex;align-items:center;gap:12px;font-size:14px",
        innerHTML: `<span>Login required for <strong>${service}</strong>. <a href="#" style="color:inherit;text-decoration:underline">Go to Settings</a></span><button style="background:none;border:none;color:inherit;font-size:20px;cursor:pointer;opacity:0.8">×</button>`
    });

    loginNotification.querySelector("a").onclick = e => {
        e.preventDefault();
        document.querySelector(".comfy-settings-btn").click();
        const poll = setInterval(() => {
            const btn = [...document.querySelectorAll(".p-button-label")].find(b => b.textContent === "Specter");
            if (btn) { btn.parentElement.click(); clearInterval(poll); }
        }, 100);
        close();
    };
    loginNotification.querySelector("button").onclick = close;

    document.body.appendChild(loginNotification);
    setTimeout(close, 8000);
}

// Event listeners
api.addEventListener("specter-login-required", () => showLoginNotification("ChatGPT"));
api.addEventListener("specter-grok-login-required", () => showLoginNotification("Grok"));
api.addEventListener("specter-gemini-login-required", () => showLoginNotification("Gemini"));
