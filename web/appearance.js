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
    .specter-dialog-header { display: flex; justify-content: space-between; align-items: center; padding: 12px 12px 8px 16px; }
    .specter-dialog-title { color: var(--fg-color, #fff); font-weight: 600; font-size: 16px; display: flex; align-items: center; gap: 8px; }
    .specter-dialog-content { padding: 0 16px 16px; }
    .specter-dialog textarea { width: 100%; height: 150px; resize: vertical; background: var(--comfy-input-bg, #222); border: 1px solid var(--border-color, #4e4e4e); border-radius: 6px; color: var(--fg-color, #ddd); padding: 8px; font-family: monospace; font-size: 12px; }
    .specter-dialog-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 12px; }
    .specter-dropzone { border: 2px dashed var(--border-color, #4e4e4e); border-radius: 6px; padding: 16px; text-align: center; color: var(--p-text-muted-color, #888); margin-bottom: 8px; transition: border-color 0.2s; }
    .specter-dropzone.dragover { border-color: var(--p-primary-color, #6366f1); background: rgba(99, 102, 241, 0.1); }
`;
document.head.appendChild(style);

// Shared close button SVG
const CLOSE_ICON = `<svg width="14" height="14" viewBox="0 0 14 14" fill="none" xmlns="http://www.w3.org/2000/svg" class="p-icon" aria-hidden="true"><path d="M8.01186 7.00933L12.27 2.75116C12.341 2.68501 12.398 2.60524 12.4375 2.51661C12.4769 2.42798 12.4982 2.3323 12.4999 2.23529C12.5016 2.13827 12.4838 2.0419 12.4474 1.95194C12.4111 1.86197 12.357 1.78024 12.2884 1.71163C12.2198 1.64302 12.138 1.58893 12.0481 1.55259C11.9581 1.51625 11.8617 1.4984 11.7647 1.50011C11.6677 1.50182 11.572 1.52306 11.4834 1.56255C11.3948 1.60204 11.315 1.65898 11.2488 1.72997L6.99067 5.98814L2.7325 1.72997C2.59553 1.60234 2.41437 1.53286 2.22718 1.53616C2.03999 1.53946 1.8614 1.61529 1.72901 1.74767C1.59663 1.88006 1.5208 2.05865 1.5175 2.24584C1.5142 2.43303 1.58368 2.61419 1.71131 2.75116L5.96948 7.00933L1.71131 11.2675C1.576 11.403 1.5 11.5866 1.5 11.7781C1.5 11.9696 1.576 12.1532 1.71131 12.2887C1.84679 12.424 2.03043 12.5 2.2219 12.5C2.41338 12.5 2.59702 12.424 2.7325 12.2887L6.99067 8.03052L11.2488 12.2887C11.3843 12.424 11.568 12.5 11.7594 12.5C11.9509 12.5 12.1346 12.424 12.27 12.2887C12.4053 12.1532 12.4813 11.9696 12.4813 11.7781C12.4813 11.5866 12.4053 11.403 12.27 11.2675L8.01186 7.00933Z" fill="currentColor"></path></svg>`;

// Simple reusable dialog
function createDialog(title, icon) {
    const el = document.createElement("div");
    el.className = "specter-dialog p-dialog p-component";
    el.innerHTML = `
        <div class="specter-dialog-header">
            <span class="specter-dialog-title"><i class="pi pi-${icon}"></i>${title}</span>
            <button class="p-button p-component p-button-icon-only p-button-rounded p-button-text p-button-secondary" type="button">${CLOSE_ICON}</button>
        </div>
        <div class="specter-dialog-content"></div>
    `;
    el.querySelector("button").onclick = () => el.style.display = "none";
    document.body.appendChild(el);
    return { el, content: el.querySelector(".specter-dialog-content"), show: () => el.style.display = "block", hide: () => el.style.display = "none" };
}

// Single reusable browser popup
const browserPopup = {
    el: null,
    canvas: null,
    ctx: null,
    ws: null,
    active: false,
    onClose: null,
    titleEl: null,
    subtitleEl: null,
    loadingOverlay: null,

    createEl() {
        if (this.el) return;

        this.el = document.createElement("div");
        this.el.className = "specter-dialog p-dialog p-component";

        const header = document.createElement("div");
        header.className = "specter-dialog-header";
        header.style.cursor = "move";

        const titleContainer = document.createElement("div");
        titleContainer.style.cssText = "display: flex; flex-direction: column; gap: 2px;";

        this.titleEl = document.createElement("span");
        this.titleEl.className = "specter-dialog-title";
        this.titleEl.style.fontSize = "18px";

        this.subtitleEl = document.createElement("span");
        this.subtitleEl.style.cssText = "color: var(--p-text-muted-color, #888); font-size: 12px; display: none;";

        titleContainer.append(this.titleEl, this.subtitleEl);
        header.appendChild(titleContainer);

        const closeBtn = document.createElement("button");
        closeBtn.className = "p-button p-component p-button-icon-only p-button-rounded p-button-text p-button-secondary";
        closeBtn.type = "button";
        closeBtn.innerHTML = CLOSE_ICON;
        closeBtn.onclick = () => this.stop();
        header.appendChild(closeBtn);

        const content = document.createElement("div");
        content.className = "specter-dialog-content";
        content.style.position = "relative";

        this.canvas = document.createElement("canvas");
        this.canvas.style.cssText = "cursor: pointer; display: block; border-radius: 6px; outline: none;";
        this.canvas.tabIndex = 0;
        this.ctx = this.canvas.getContext("2d");

        this.loadingOverlay = document.createElement("div");
        this.loadingOverlay.style.cssText = `
            position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
            color: #888; font: 16px system-ui, sans-serif; display: none;
        `;
        this.loadingOverlay.innerHTML = `Launching browser<span class="specter-loading"></span>`;

        content.append(this.canvas, this.loadingOverlay);
        this.el.append(header, content);
        document.body.appendChild(this.el);

        // Draggable header
        let dragging = false, dragX, dragY;
        header.addEventListener("mousedown", (e) => {
            if (e.target === closeBtn || closeBtn.contains(e.target)) return;
            dragging = true;
            dragX = e.clientX - this.el.offsetLeft;
            dragY = e.clientY - this.el.offsetTop;
        });
        document.addEventListener("mousemove", (e) => {
            if (!dragging) return;
            this.el.style.left = e.clientX - dragX + "px";
            this.el.style.top = e.clientY - dragY + "px";
            this.el.style.transform = "none";
        });
        document.addEventListener("mouseup", () => dragging = false);

        // Canvas input handling
        let mouseDownPos = null;
        const getCoords = (e) => {
            const rect = this.canvas.getBoundingClientRect();
            return {
                x: Math.round((e.clientX - rect.left) * (this.canvas.width / rect.width)),
                y: Math.round((e.clientY - rect.top) * (this.canvas.height / rect.height))
            };
        };
        const send = (event) => {
            if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(event));
        };

        this.canvas.addEventListener("mousedown", (e) => {
            this.canvas.focus();
            e.preventDefault();
            mouseDownPos = getCoords(e);
            send({ type: "mousedown", ...mouseDownPos });
        });

        this.canvas.addEventListener("mouseup", (e) => {
            e.preventDefault();
            const raw = getCoords(e);
            // Reuse mousedown coords if barely moved (for precise clicking)
            const isClick = mouseDownPos && Math.abs(raw.x - mouseDownPos.x) <= 5 && Math.abs(raw.y - mouseDownPos.y) <= 5;
            send({ type: "mouseup", ...(isClick ? mouseDownPos : raw) });
            // Note: mousedown + mouseup = click, no separate click event needed
            mouseDownPos = null;
        });

        // Throttled mousemove for hover support (~30fps to match stream)
        let lastMove = 0;
        this.canvas.addEventListener("mousemove", (e) => {
            const now = Date.now();
            if (now - lastMove < 33) return;
            lastMove = now;
            send({ type: "mousemove", ...getCoords(e) });
        });

        this.canvas.addEventListener("wheel", (e) => {
            this.canvas.focus();
            e.preventDefault();
            send({ type: "scroll", dx: e.deltaX, dy: e.deltaY });
        }, { passive: false });

        const stopKey = (e) => { e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation(); };
        this.canvas.addEventListener("keydown", async (e) => {
            stopKey(e);
            const hasCtrl = e.ctrlKey || e.metaKey;
            if (hasCtrl && e.key.toLowerCase() === "v") {
                try { const text = await navigator.clipboard.readText(); if (text) send({ type: "type", text }); } catch {}
                return;
            }
            const mods = [];
            if (hasCtrl) mods.push("Control");
            if (e.altKey) mods.push("Alt");
            if (e.shiftKey) mods.push("Shift");
            if (mods.length > 0) send({ type: "keydown", key: [...mods, e.key].join("+") });
            else if (e.key.length === 1) send({ type: "type", text: e.key });
            else send({ type: "keydown", key: e.key });
        }, true);
        this.canvas.addEventListener("keyup", stopKey, true);
        this.canvas.addEventListener("keypress", stopKey, true);
    },

    async start(endpoint, title, onClose = null) {
        this.createEl();
        const isLogin = title.includes("Login");
        this.titleEl.innerHTML = `<i class="pi pi-${isLogin ? "sign-in" : "cog"}"></i>${title}`;
        this.subtitleEl.textContent = "Closes automatically when logged in";
        this.subtitleEl.style.display = isLogin ? "block" : "none";
        this.onClose = onClose;
        this.el.style.display = "block";
        this.canvas.width = 600;
        this.canvas.height = 800;
        this.loadingOverlay.innerHTML = `Launching browser<span class="specter-loading"></span>`;
        this.loadingOverlay.style.display = "block";

        try {
            const resp = await fetch(endpoint, { method: "POST" });
            const data = await resp.json();
            if (data.status === "error") {
                this.loadingOverlay.style.display = "none";
                this.showError(data.message);
                return;
            }
            this.active = true;
            this.connectWS();
            this.canvas.focus();
        } catch {
            this.loadingOverlay.style.display = "none";
            this.showError("Failed to connect to server");
        }
    },

    showError(msg) {
        const clean = msg.includes("install-deps") ? "Missing dependencies. Run: sudo playwright install-deps" : msg.split("\n")[0];
        this.canvas.width = 450;
        this.canvas.height = 80;
        this.ctx.fillStyle = "#2a2a2a";
        this.ctx.fillRect(0, 0, 450, 80);
        this.ctx.fillStyle = "#f87171";
        this.ctx.font = "14px system-ui, sans-serif";
        this.ctx.fillText("⚠ " + clean, 16, 45);
    },

    connectWS() {
        const protocol = location.protocol === "https:" ? "wss:" : "ws:";
        this.ws = new WebSocket(`${protocol}//${location.host}/specter/browser/ws`);

        this.ws.onmessage = async (e) => {
            if (typeof e.data === "string") {
                if (JSON.parse(e.data).type === "logged_in") {
                    this.stop();
                }
                return;
            }
            this.loadingOverlay.style.display = "none";
            // createImageBitmap is faster than Image() for decoding
            const bitmap = await createImageBitmap(new Blob([e.data], { type: "image/jpeg" }));
            if (this.canvas.width !== bitmap.width || this.canvas.height !== bitmap.height) {
                this.canvas.width = bitmap.width;
                this.canvas.height = bitmap.height;
            }
            this.ctx.drawImage(bitmap, 0, 0);
            bitmap.close();
        };

        this.ws.onclose = () => { if (this.active) setTimeout(() => this.connectWS(), 1000); };
    },

    async stop() {
        this.active = false;
        this.loadingOverlay.style.display = "none";
        if (this.ws) { this.ws.close(); this.ws = null; }
        if (this.el) this.el.style.display = "none";
        try { await fetch("/specter/browser/stop", { method: "POST" }); } catch {}
        if (this.onClose) this.onClose();
    },
};

// Factory for login button settings
function createLoginSetting(id, service) {
    const serviceLower = service.toLowerCase();
    const statusEndpoint = `/specter/${serviceLower}/status`;
    const logoutEndpoint = `/specter/${serviceLower}/logout`;
    const loginEndpoint = `/specter/${serviceLower}/browser/start`;
    const importEndpoint = `/specter/${serviceLower}/import`;

    return {
        id,
        category: ["Specter", " Authentication", service],
        name: service,
        tooltip: `Log in to ${service} via embedded browser`,
        type: () => {
            const container = document.createElement("div");
            container.className = "flex items-center gap-4";

            const statusText = document.createElement("span");
            statusText.className = "text-muted";
            statusText.textContent = "Checking...";

            const loginBtn = document.createElement("button");
            loginBtn.type = "button";
            loginBtn.className = "p-button p-component";
            loginBtn.style.minWidth = "116px";

            const importBtn = document.createElement("button");
            importBtn.type = "button";
            importBtn.className = "p-button p-component p-button-icon-only p-button-text";
            importBtn.title = "Import cookies from browser extension";
            importBtn.innerHTML = `<span class="p-button-icon pi pi-file-import"></span>`;

            container.append(statusText, loginBtn, importBtn);

            let isLoggedIn = false;

            const checkStatus = async () => {
                try {
                    const data = await fetch(statusEndpoint).then(r => r.json());
                    isLoggedIn = data.logged_in;
                    statusText.textContent = isLoggedIn ? "Logged in" : "Not logged in";
                    loginBtn.innerHTML = `<span class="p-button-icon p-button-icon-left pi pi-${isLoggedIn ? "sign-out" : "user"}"></span><span class="p-button-label">${isLoggedIn ? "Sign Out" : "Sign In"}</span>`;
                } catch {
                    statusText.textContent = "Status unknown";
                }
            };

            loginBtn.addEventListener("click", async () => {
                if (isLoggedIn) {
                    await fetch(logoutEndpoint, { method: "POST" });
                    await checkStatus();
                } else {
                    await browserPopup.start(loginEndpoint, `${service} Login`, checkStatus);
                }
            });

            // Import dialog (created once, reused)
            let importDialog = null;
            importBtn.addEventListener("click", () => {
                if (!importDialog) {
                    importDialog = createDialog(`Import ${service} Cookies`, "file-import");
                    importDialog.content.innerHTML = `
                        <div class="specter-dropzone">Drop cookies.txt or cookies.json here, or click to browse</div>
                        <input type="file" accept=".txt,.json" style="display: none">
                        <textarea placeholder="Or paste cookie content here (JSON or Netscape TXT format)..."></textarea>
                        <div class="specter-dialog-actions">
                            <button class="p-button p-component" type="button"><span class="p-button-label">Import</span></button>
                        </div>
                    `;
                    const dropzone = importDialog.content.querySelector(".specter-dropzone");
                    const fileInput = importDialog.content.querySelector("input[type=file]");
                    const textarea = importDialog.content.querySelector("textarea");
                    const importAction = importDialog.content.querySelector(".specter-dialog-actions button");

                    const handleFile = (file) => {
                        const reader = new FileReader();
                        reader.onload = (e) => { textarea.value = e.target.result; };
                        reader.readAsText(file);
                    };

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
                            const resp = await fetch(importEndpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cookies }) });
                            const data = await resp.json();
                            if (data.status === "ok") {
                                importDialog.hide();
                                textarea.value = "";
                                await checkStatus();
                            } else {
                                alert(`Import failed: ${data.message}`);
                            }
                        } catch (e) {
                            alert(`Import failed: ${e.message}`);
                        }
                        importAction.disabled = false;
                        importAction.querySelector(".p-button-label").textContent = "Import";
                    };
                }
                importDialog.show();
            });

            checkStatus();
            return container;
        },
        defaultValue: "",
    };
}

// Factory for settings button
function createSettingsButton(id, service) {
    return {
        id,
        category: ["Specter", "Provider Settings", service],
        name: `${service} Settings`,
        tooltip: `Open ${service} settings in embedded browser`,
        type: () => {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "p-button p-component";
            btn.innerHTML = `<span class="p-button-icon p-button-icon-left pi pi-cog"></span><span class="p-button-label">Open Settings</span>`;
            btn.addEventListener("click", () => browserPopup.start(`/specter/${service.toLowerCase()}/settings/start`, `${service} Settings`));
            return btn;
        },
        defaultValue: "",
    };
}

// Factory for toggle settings
function createToggleSetting(id, category, name, tooltip, settingKey, label, defaultValue = false) {
    return {
        id, category, name, tooltip,
        type: () => {
            const container = document.createElement("div");
            container.className = "flex items-center gap-4";

            const toggle = document.createElement("input");
            toggle.type = "checkbox";
            toggle.style.cssText = "width: 18px; height: 18px; cursor: pointer;";

            const labelEl = document.createElement("span");
            labelEl.textContent = label;
            labelEl.style.cssText = "color: var(--fg-color, #ccc);";

            container.append(toggle, labelEl);

            fetch("/specter/settings").then(r => r.json()).then(s => {
                toggle.checked = defaultValue ? s[settingKey] !== false : s[settingKey] || false;
            }).catch(() => { toggle.checked = defaultValue; });

            toggle.addEventListener("change", () => {
                fetch("/specter/settings", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ [settingKey]: toggle.checked }),
                }).catch(e => console.error("[Specter] Failed to save setting:", e));
            });

            return container;
        },
        defaultValue,
    };
}

// Get node colors by pattern matching
function getNodeColors(name) {
    if (!name.startsWith("Specter_")) return null;
    const isGrok = name.includes("Grok");
    return { nodeColor: isGrok ? "#1e3a8a" : "#991b1b", nodeBgColor: "#2a2a2a" };
}

app.registerExtension({
    name: "Specter.Appearance",

    settings: [
        {
            id: "Specter.BrowserStatus",
            category: ["Specter", "Status"],
            name: "Browser Status",
            tooltip: "Shows if the browser automation is ready",
            type: () => {
                const el = document.createElement("span");
                el.textContent = "Checking...";
                el.style.fontWeight = "500";
                fetch("/specter/health").then(r => r.json()).then(data => {
                    el.textContent = data.ready ? "Ready" : "⚠ " + (data.error || "Not ready");
                    el.style.color = data.ready ? "#4ade80" : "#f87171";
                }).catch(() => { el.textContent = "⚠ Unable to check"; el.style.color = "#fbbf24"; });
                return el;
            },
            defaultValue: "",
        },
        createLoginSetting("Specter.LoginButton", "ChatGPT"),
        createLoginSetting("Specter.GrokLoginButton", "Grok"),
        createSettingsButton("Specter.ChatGPTSettings", "ChatGPT"),
        createSettingsButton("Specter.GrokSettings", "Grok"),
        createToggleSetting("Specter.HeadedBrowser", ["Specter", "Advanced", "Browser"], "Show Browser Window", "Run browser visibly for debugging", "headed_browser", "Enable headed mode"),
        createToggleSetting("Specter.DebugDumps", ["Specter", "Advanced", "Debugging"], "Debug Dumps", "Save debug info on error", "debug_dumps", "Enable debug dumps on error", true),
        {
            id: "Specter.ResetData",
            category: ["Specter", "Advanced", "Data"],
            name: "Reset All Data",
            tooltip: "Clear all saved sessions and browser profiles",
            type: () => {
                const btn = document.createElement("button");
                btn.type = "button";
                btn.className = "p-button p-component p-button-danger";
                btn.innerHTML = `<span class="p-button-icon p-button-icon-left pi pi-trash"></span><span class="p-button-label">Reset All Data</span>`;
                btn.addEventListener("click", async () => {
                    if (!confirm("This will clear all saved sessions and browser profiles for ChatGPT and Grok. Continue?")) return;
                    btn.disabled = true;
                    btn.querySelector(".p-button-label").textContent = "Clearing...";
                    try {
                        await fetch("/specter/reset", { method: "POST" });
                        btn.querySelector(".p-button-label").textContent = "Done!";
                        setTimeout(() => { btn.querySelector(".p-button-label").textContent = "Reset All Data"; btn.disabled = false; }, 2000);
                    } catch {
                        btn.querySelector(".p-button-label").textContent = "Failed";
                        setTimeout(() => { btn.querySelector(".p-button-label").textContent = "Reset All Data"; btn.disabled = false; }, 2000);
                    }
                });
                return btn;
            },
            defaultValue: "",
        },
    ],

    async nodeCreated(node) {
        const colors = getNodeColors(node.comfyClass);
        if (colors) {
            node.color = colors.nodeColor;
            node.bgcolor = colors.nodeBgColor;
            node.size[0] = Math.max(node.size[0], 300);
        }
    },
});

// Listen for login required events
api.addEventListener("specter-login-required", () => browserPopup.start("/specter/chatgpt/browser/start", "ChatGPT Login"));
api.addEventListener("specter-grok-login-required", () => browserPopup.start("/specter/grok/browser/start", "Grok Login"));
