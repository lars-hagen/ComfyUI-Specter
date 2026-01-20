/**
 * Specter Node Appearance Configuration
 */

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// Inject global CSS once
const style = document.createElement("style");
style.textContent = `
    .specter-loading::after { content: ''; animation: specter-dots 1.5s steps(4, end) infinite; }
    @keyframes specter-dots { 0%,25%{content:''} 26%,50%{content:'.'} 51%,75%{content:'..'} 76%,100%{content:'...'} }
    .specter-dialog { display: none; position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); z-index: 10000; background: var(--comfy-menu-bg, #353535); border-radius: 12px; box-shadow: 0 8px 32px rgba(0,0,0,0.4); border: 1px solid var(--border-color, #4e4e4e); min-width: 400px; }
    .specter-dialog-header { display: flex; justify-content: space-between; align-items: center; padding: 12px 12px 8px 16px; cursor: move; }
    .specter-dialog-title { color: var(--fg-color, #fff); font-weight: 600; font-size: 16px; display: flex; align-items: center; gap: 8px; }
    .specter-dialog-content { padding: 0 16px 16px; position: relative; }
    .specter-dialog textarea { width: 100%; height: 150px; resize: vertical; background: var(--comfy-input-bg, #222); border: 1px solid var(--border-color, #4e4e4e); border-radius: 6px; color: var(--fg-color, #ddd); padding: 8px; font-family: monospace; font-size: 12px; }
    .specter-dialog-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 12px; }
    .specter-dropzone { border: 2px dashed var(--border-color, #4e4e4e); border-radius: 6px; padding: 16px; text-align: center; color: var(--p-text-muted-color, #888); margin-bottom: 8px; transition: border-color 0.2s; }
    .specter-dropzone.dragover { border-color: var(--p-primary-color, #6366f1); background: rgba(99, 102, 241, 0.1); }
    .specter-subtitle { color: var(--p-text-muted-color, #888); font-size: 12px; }
    .specter-canvas { cursor: pointer; display: block; border-radius: 6px; outline: none; }
    .specter-loading-overlay { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); color: #888; font: 16px system-ui, sans-serif; display: none; }
    .specter-close { font-size: 20px; line-height: 1; padding: 4px 8px; background: none; border: none; color: var(--fg-color, #fff); cursor: pointer; opacity: 0.7; }
    .specter-close:hover { opacity: 1; }
`;
document.head.appendChild(style);

// Simple reusable dialog with dragging support
function createDialog(title, icon, { subtitle = "", draggable = true } = {}) {
    const el = document.createElement("div");
    el.className = "specter-dialog p-dialog p-component";
    el.innerHTML = `
        <div class="specter-dialog-header">
            <div><span class="specter-dialog-title"><i class="pi pi-${icon}"></i>${title}</span>${subtitle ? `<div class="specter-subtitle">${subtitle}</div>` : ""}</div>
            <button class="specter-close" type="button">×</button>
        </div>
        <div class="specter-dialog-content"></div>
    `;
    const hide = () => el.style.display = "none";
    el.querySelector("button").onclick = hide;
    if (draggable) makeDraggable(el, el.querySelector(".specter-dialog-header"));
    document.body.appendChild(el);
    return { el, content: el.querySelector(".specter-dialog-content"), show: () => el.style.display = "block", hide, setTitle: (t, i) => el.querySelector(".specter-dialog-title").innerHTML = `<i class="pi pi-${i}"></i>${t}`, setSubtitle: (s) => { const sub = el.querySelector(".specter-subtitle"); if (sub) sub.style.display = s ? "block" : "none"; } };
}

// Make element draggable by header
function makeDraggable(el, handle) {
    let dragging = false, dragX, dragY;
    handle.onmousedown = (e) => { if (e.target.tagName === "BUTTON") return; dragging = true; dragX = e.clientX - el.offsetLeft; dragY = e.clientY - el.offsetTop; };
    document.addEventListener("mousemove", (e) => { if (!dragging) return; el.style.left = e.clientX - dragX + "px"; el.style.top = e.clientY - dragY + "px"; el.style.transform = "none"; });
    document.addEventListener("mouseup", () => dragging = false);
}

// Single reusable browser popup
const browserPopup = {
    dialog: null, canvas: null, ctx: null, ws: null, active: false, onClose: null, loadingOverlay: null,

    createEl() {
        if (this.dialog) return;
        this.dialog = createDialog("Browser", "cog", { subtitle: "Closes automatically when logged in" });
        this.dialog.el.querySelector("button").onclick = () => this.stop();

        this.canvas = document.createElement("canvas");
        this.canvas.className = "specter-canvas";
        this.canvas.tabIndex = 0;
        this.ctx = this.canvas.getContext("2d");

        this.loadingOverlay = document.createElement("div");
        this.loadingOverlay.className = "specter-loading-overlay";
        this.loadingOverlay.innerHTML = `Launching browser<span class="specter-loading"></span>`;

        this.dialog.content.append(this.canvas, this.loadingOverlay);
        this.setupCanvasEvents();
    },

    setupCanvasEvents() {
        let mouseDownPos = null, lastMove = 0;
        const coords = (e) => {
            const r = this.canvas.getBoundingClientRect();
            return { x: Math.round((e.clientX - r.left) * this.canvas.width / r.width), y: Math.round((e.clientY - r.top) * this.canvas.height / r.height) };
        };
        const send = (ev) => { if (this.ws?.readyState === 1) this.ws.send(JSON.stringify(ev)); };

        this.canvas.onmousedown = (e) => { this.canvas.focus(); e.preventDefault(); mouseDownPos = coords(e); send({ type: "mousedown", ...mouseDownPos }); };
        this.canvas.onmouseup = (e) => { e.preventDefault(); const c = coords(e); send({ type: "mouseup", ...(mouseDownPos && Math.abs(c.x - mouseDownPos.x) <= 5 ? mouseDownPos : c) }); mouseDownPos = null; };
        this.canvas.onmousemove = (e) => { if (Date.now() - lastMove > 33) { lastMove = Date.now(); send({ type: "mousemove", ...coords(e) }); } };
        this.canvas.onwheel = (e) => { e.preventDefault(); send({ type: "scroll", ...coords(e), dy: e.deltaY }); };

        this.canvas.onkeydown = async (e) => {
            e.preventDefault(); e.stopPropagation();
            const ctrl = e.ctrlKey || e.metaKey;
            if (ctrl && e.key.toLowerCase() === "v") { try { const t = await navigator.clipboard.readText(); if (t) send({ type: "type", text: t }); } catch {} return; }
            if (e.key.length === 1 && !ctrl && !e.altKey) send({ type: "type", text: e.key });
            else send({ type: "keydown", key: e.key });
        };
        this.canvas.onkeyup = this.canvas.onkeypress = (e) => { e.preventDefault(); e.stopPropagation(); };
    },

    async start(endpoint, title, onClose = null) {
        this.createEl();
        const isLogin = title.includes("Login");
        this.dialog.setTitle(title, isLogin ? "sign-in" : "cog");
        this.dialog.setSubtitle(isLogin);
        this.onClose = onClose;
        this.dialog.show();
        this.canvas.width = 600;
        this.canvas.height = 800;
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
        const clean = msg.split("\n")[0];
        this.canvas.width = 450; this.canvas.height = 80;
        this.ctx.fillStyle = "#2a2a2a"; this.ctx.fillRect(0, 0, 450, 80);
        this.ctx.fillStyle = "#f87171"; this.ctx.font = "14px system-ui, sans-serif"; this.ctx.fillText("⚠ " + clean, 16, 45);
    },

    connectWS() {
        const protocol = location.protocol === "https:" ? "wss:" : "ws:";
        this.ws = new WebSocket(`${protocol}//${location.host}/specter/browser/ws`);
        this.ws.onmessage = async (e) => {
            if (typeof e.data === "string") {
                const msg = JSON.parse(e.data);
                console.log("[Specter] WS message received:", msg);
                if (msg.type === "logged_in") {
                    console.log("[Specter] Login detected, closing dialog");
                    this.active = false;
                    this.loadingOverlay.style.display = "none";
                    if (this.ws) { this.ws.close(); this.ws = null; }
                    this.dialog?.hide();
                    if (this.onClose) this.onClose();
                }
                return;
            }
            this.loadingOverlay.style.display = "none";
            const bmp = await createImageBitmap(e.data);
            this.canvas.width = bmp.width; this.canvas.height = bmp.height;
            this.ctx.drawImage(bmp, 0, 0); bmp.close();
        };
        this.ws.onclose = () => { if (this.active) setTimeout(() => this.connectWS(), 1000); };
    },

    async stop() {
        console.log("[Specter] Manual stop requested");
        this.active = false;
        this.loadingOverlay.style.display = "none";
        if (this.ws) { this.ws.close(); this.ws = null; }
        this.dialog?.hide();
        if (this.onClose) this.onClose();

        // Tell backend to stop (only for manual close)
        fetch("/specter/browser/stop", { method: "POST" })
            .then(r => r.json())
            .then(data => console.log("[Specter] Stop response:", data))
            .catch(err => console.error("[Specter] Stop failed:", err));
    },
};

// Factory for login button settings
function createLoginSetting(id, service, sortOrder = 0) {
    const svc = service.toLowerCase();
    const endpoints = { status: `/specter/${svc}/status`, logout: `/specter/${svc}/logout`, login: `/specter/${svc}/browser/start`, import: `/specter/${svc}/import` };

    return {
        id, category: ["Specter", "Authentication", service], name: service, tooltip: `Log in to ${service} via embedded browser`, sortOrder,
        type: () => {
            const container = document.createElement("div");
            container.className = "flex items-center gap-4";
            container.innerHTML = `<span class="text-muted">Checking...</span><button type="button" class="p-button p-component" style="min-width:116px"></button><button type="button" class="p-button p-component p-button-icon-only p-button-secondary" style="border:none" title="Import cookies"><span class="p-button-icon pi pi-file-import"></span></button>`;
            const [statusText, loginBtn, importBtn] = container.children;
            let isLoggedIn = false, importDialog = null;

            const checkStatus = async () => {
                try {
                    const data = await fetch(endpoints.status).then(r => r.json());
                    isLoggedIn = data.logged_in;
                    statusText.textContent = isLoggedIn ? "Logged in" : "Not logged in";
                    loginBtn.innerHTML = `<span class="p-button-icon p-button-icon-left pi pi-${isLoggedIn ? "sign-out" : "user"}"></span><span class="p-button-label">${isLoggedIn ? "Sign Out" : "Sign In"}</span>`;
                } catch { statusText.textContent = "Status unknown"; }
            };

            loginBtn.onclick = async () => { if (isLoggedIn) { await fetch(endpoints.logout, { method: "POST" }); await checkStatus(); } else { await browserPopup.start(endpoints.login, `${service} Login`, checkStatus); } };

            importBtn.onclick = () => {
                if (!importDialog) {
                    importDialog = createDialog(`Import ${service} Cookies`, "file-import");
                    importDialog.content.innerHTML = `<div class="specter-dropzone">Drop cookies.txt or cookies.json here, or click to browse</div><input type="file" accept=".txt,.json" style="display:none"><textarea placeholder="Or paste cookie content here (JSON or Netscape TXT format)..."></textarea><div class="specter-dialog-actions"><button class="p-button p-component" type="button"><span class="p-button-label">Import</span></button></div>`;
                    const [dropzone, fileInput, textarea] = importDialog.content.querySelectorAll(".specter-dropzone, input, textarea");
                    const importAction = importDialog.content.querySelector(".specter-dialog-actions button");
                    const handleFile = (f) => { const r = new FileReader(); r.onload = (e) => textarea.value = e.target.result; r.readAsText(f); };

                    dropzone.onclick = () => fileInput.click();
                    fileInput.onchange = (e) => e.target.files[0] && handleFile(e.target.files[0]);
                    dropzone.ondragover = (e) => { e.preventDefault(); dropzone.classList.add("dragover"); };
                    dropzone.ondragleave = () => dropzone.classList.remove("dragover");
                    dropzone.ondrop = (e) => { e.preventDefault(); dropzone.classList.remove("dragover"); e.dataTransfer.files[0] && handleFile(e.dataTransfer.files[0]); };

                    importAction.onclick = async () => {
                        const cookies = textarea.value.trim();
                        if (!cookies) return;
                        importAction.disabled = true;
                        importAction.querySelector(".p-button-label").textContent = "Importing...";
                        try {
                            const data = await fetch(endpoints.import, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cookies }) }).then(r => r.json());
                            if (data.status === "ok") { importDialog.hide(); textarea.value = ""; await checkStatus(); }
                            else alert(`Import failed: ${data.message}`);
                        } catch (e) { alert(`Import failed: ${e.message}`); }
                        importAction.disabled = false;
                        importAction.querySelector(".p-button-label").textContent = "Import";
                    };
                }
                importDialog.show();
            };

            checkStatus();
            return container;
        },
        defaultValue: "",
    };
}

// Factory for settings button
function createSettingsButton(id, service, sortOrder = 0) {
    return {
        id, category: ["Specter", "Provider Settings", service], name: `${service} Settings`, tooltip: `Open ${service} settings in embedded browser`, sortOrder,
        type: () => {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "p-button p-component";
            btn.innerHTML = `<span class="p-button-icon p-button-icon-left pi pi-cog"></span><span class="p-button-label">Open Settings</span>`;
            btn.onclick = () => browserPopup.start(`/specter/${service.toLowerCase()}/settings/start`, `${service} Settings`);
            return btn;
        },
        defaultValue: "",
    };
}

// Factory for toggle settings
function createToggleSetting(id, category, name, tooltip, settingKey, defaultValue = false, sortOrder = 0) {
    return {
        id, category, name, tooltip, sortOrder,
        type: () => {
            const wrapper = document.createElement("div");
            wrapper.className = "p-toggleswitch p-component";
            wrapper.setAttribute("data-pc-name", "toggleswitch");
            wrapper.setAttribute("data-pc-section", "root");
            wrapper.style.position = "relative";

            const input = document.createElement("input");
            input.type = "checkbox";
            input.role = "switch";
            input.className = "p-toggleswitch-input";
            input.setAttribute("aria-labelledby", id + "-label");
            input.setAttribute("data-pc-section", "input");

            const slider = document.createElement("div");
            slider.className = "p-toggleswitch-slider";
            slider.setAttribute("data-pc-section", "slider");

            const handle = document.createElement("div");
            handle.className = "p-toggleswitch-handle";
            handle.setAttribute("data-pc-section", "handle");
            slider.appendChild(handle);

            wrapper.appendChild(input);
            wrapper.appendChild(slider);

            fetch("/specter/settings").then(r => r.json()).then(s => {
                const checked = defaultValue ? s[settingKey] !== false : s[settingKey] || false;
                input.checked = checked;
                input.setAttribute("aria-checked", checked);
                wrapper.setAttribute("data-p-checked", checked);
                if (checked) wrapper.classList.add("p-toggleswitch-checked");
            }).catch(() => {
                input.checked = defaultValue;
                input.setAttribute("aria-checked", defaultValue);
                wrapper.setAttribute("data-p-checked", defaultValue);
                if (defaultValue) wrapper.classList.add("p-toggleswitch-checked");
            });

            input.onchange = () => {
                const checked = input.checked;
                input.setAttribute("aria-checked", checked);
                wrapper.setAttribute("data-p-checked", checked);
                wrapper.classList.toggle("p-toggleswitch-checked", checked);
                fetch("/specter/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ [settingKey]: checked }) }).catch(e => console.error("[Specter] Failed to save setting:", e));
            };

            return wrapper;
        },
        defaultValue,
    };
}

// Factory for text input settings
function createTextSetting(id, category, name, tooltip, settingKey, placeholder = "", defaultValue = "", sortOrder = 0) {
    return {
        id, category, name, tooltip, sortOrder,
        type: () => {
            const input = document.createElement("input");
            input.type = "text";
            input.className = "p-inputtext p-component";
            input.placeholder = placeholder;
            input.setAttribute("aria-labelledby", id + "-label");
            input.setAttribute("data-pc-name", "pcinputtext");
            input.setAttribute("data-pc-section", "root");

            fetch("/specter/settings").then(r => r.json()).then(s => input.value = s[settingKey] || defaultValue).catch(() => input.value = defaultValue);
            input.onchange = () => fetch("/specter/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ [settingKey]: input.value }) }).catch(e => console.error("[Specter] Failed to save setting:", e));
            return input;
        },
        defaultValue,
    };
}

// Factory for number input settings
function createNumberSetting(id, category, name, tooltip, settingKey, min = 0, max = 65535, defaultValue = 0, sortOrder = 0) {
    return {
        id, category, name, tooltip, sortOrder,
        type: () => {
            const wrapper = document.createElement("span");
            wrapper.className = "p-inputnumber p-component p-inputwrapper p-inputwrapper-filled";
            wrapper.setAttribute("data-pc-name", "inputnumber");
            wrapper.setAttribute("data-pc-section", "root");

            const input = document.createElement("input");
            input.type = "text";
            input.className = "p-inputtext p-component p-inputnumber-input";
            input.role = "spinbutton";
            input.setAttribute("aria-valuemin", min);
            input.setAttribute("aria-valuemax", max);
            input.setAttribute("inputmode", "numeric");
            input.setAttribute("aria-labelledby", id + "-label");
            input.setAttribute("data-pc-name", "pcinputtext");
            input.setAttribute("data-pc-extend", "inputtext");
            input.setAttribute("data-pc-section", "root");

            wrapper.appendChild(input);

            fetch("/specter/settings").then(r => r.json()).then(s => {
                const val = s[settingKey] || defaultValue;
                input.value = val;
                input.setAttribute("aria-valuenow", val);
            }).catch(() => {
                input.value = defaultValue;
                input.setAttribute("aria-valuenow", defaultValue);
            });

            input.onchange = () => {
                const val = parseInt(input.value) || defaultValue;
                input.setAttribute("aria-valuenow", val);
                fetch("/specter/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ [settingKey]: val }) }).catch(e => console.error("[Specter] Failed to save setting:", e));
            };

            return wrapper;
        },
        defaultValue,
    };
}

app.registerExtension({
    name: "Specter.Appearance",
    settings: [
        { id: "Specter.BrowserStatus", category: ["Specter", "Status"], name: "Browser Status", tooltip: "Shows if the browser automation is ready", sortOrder: 10,
            type: () => { const el = document.createElement("span"); el.textContent = "Checking..."; el.style.fontWeight = "500"; fetch("/specter/health").then(r => r.json()).then(d => { el.textContent = d.ready ? "Ready" : "⚠ " + (d.error || "Not ready"); el.style.color = d.ready ? "#4ade80" : "#f87171"; }).catch(() => { el.textContent = "⚠ Unable to check"; el.style.color = "#fbbf24"; }); return el; }, defaultValue: "" },
        { id: "Specter.GoogleWarning", category: ["Specter", "Status"], name: "Google Services", tooltip: "Google connectivity check", sortOrder: 15,
            type: () => { const el = document.createElement("div"); el.innerHTML = '<span style="color: #888;">Checking...</span>'; fetch("/specter/health").then(r => r.json()).then(d => { if (d.google_blocked) { el.innerHTML = '<span style="color: #fbbf24; font-weight: 500;">⚠ Google services blocked from server</span><br><span style="color: var(--p-text-muted-color, #888); font-size: 12px;">The ComfyUI server cannot reach www.gstatic.com. Gemini will not work. Google login on Grok/ChatGPT won\'t work - export cookies from your local browser and use cookie import instead.</span>'; } else { el.innerHTML = '<span style="color: #4ade80; font-weight: 500;">✓ Google services accessible</span>'; } }).catch(() => { el.innerHTML = '<span style="color: #888;">Unable to check</span>'; }); return el; }, defaultValue: "" },
        createLoginSetting("Specter.GeminiLoginButton", "Gemini", 50),
        createLoginSetting("Specter.GrokLoginButton", "Grok", 50),
        createLoginSetting("Specter.LoginButton", "ChatGPT", 50),
        createSettingsButton("Specter.GeminiSettings", "Gemini", 45),
        createSettingsButton("Specter.GrokSettings", "Grok", 45),
        createSettingsButton("Specter.ChatGPTSettings", "ChatGPT", 45),
        createTextSetting("Specter.ProxyServer", ["Specter", "Proxy", "Server"], "Proxy Server", "Proxy server address (e.g. 127.0.0.1 or proxy.example.com)", "proxy_server", "127.0.0.1", "", 40),
        createNumberSetting("Specter.ProxyPort", ["Specter", "Proxy", "Port"], "Proxy Port", "Proxy server port", "proxy_port", 1, 65535, 8080, 40),
        createToggleSetting("Specter.ProxyEnabled", ["Specter", "Proxy", "Enable"], "Enable Proxy", "Route browser traffic through proxy", "proxy_enabled", false, 40),
        { id: "Specter.ResetData", category: ["Specter", "Advanced", "Data"], name: "Reset All Data", tooltip: "Clear all saved sessions and browser profiles", sortOrder: 20,
            type: () => {
                const btn = document.createElement("button");
                btn.type = "button";
                btn.className = "p-button p-component p-button-danger";
                btn.innerHTML = `<span class="p-button-icon p-button-icon-left pi pi-trash"></span><span class="p-button-label">Reset All Data</span>`;
                btn.onclick = async () => {
                    if (!confirm("This will clear all saved sessions and browser profiles for ChatGPT, Grok, and Gemini. Continue?")) return;
                    btn.disabled = true;
                    const label = btn.querySelector(".p-button-label");
                    label.textContent = "Clearing...";
                    try { await fetch("/specter/reset", { method: "POST" }); label.textContent = "Done!"; } catch { label.textContent = "Failed"; }
                    setTimeout(() => { label.textContent = "Reset All Data"; btn.disabled = false; }, 2000);
                };
                return btn;
            }, defaultValue: "" },
        createToggleSetting("Specter.DebugDumps", ["Specter", "Advanced", "Debugging"], "Debug Dumps", "Save debug info on error", "debug_dumps", true, 20),
        createToggleSetting("Specter.HeadedBrowser", ["Specter", "Advanced", "Browser"], "Show Browser Window", "Run browser visibly for debugging", "headed_browser", false, 20),
    ],
    async nodeCreated(node) {
        if (!node.comfyClass.startsWith("Specter_")) return;
        // Deep sexy colors: Grok=purple, Gemini=blue, ChatGPT=red
        const cls = node.comfyClass;
        node.color = cls.includes("Grok") ? "#4c1d95" : cls.includes("Gemini") || cls.includes("NanoBanana") ? "#1e3a8a" : "#991b1b";
        node.bgcolor = "#2a2a2a";
        node.size[0] = Math.max(node.size[0], 300);
    },
});

// Listen for login required events
api.addEventListener("specter-login-required", () => browserPopup.start("/specter/chatgpt/browser/start", "ChatGPT Login"));
api.addEventListener("specter-grok-login-required", () => browserPopup.start("/specter/grok/browser/start", "Grok Login"));
api.addEventListener("specter-gemini-login-required", () => browserPopup.start("/specter/gemini/browser/start", "Gemini Login"));
