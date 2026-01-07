/**
 * Specter Node Appearance Configuration
 * Custom styling for Specter nodes in ComfyUI
 */

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// Reusable login popup for any service (ChatGPT, Grok, etc.)
function createLoginPopup(service, startEndpoint, title) {
    const popup = {
        el: null,
        canvas: null,
        ctx: null,
        ws: null,
        active: false,
        onClose: null,
        cursorX: null,
        cursorY: null,
        lastScreenshot: null,
        loadingOverlay: null,

        createEl() {
            if (this.el) return;

            this.el = document.createElement("div");
            this.el.style.cssText = `
                display: none; position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
                z-index: 10000; background: var(--comfy-menu-bg, #353535); border-radius: 8px;
                box-shadow: 0 8px 32px rgba(0,0,0,0.4);
                padding: 12px; border: 1px solid var(--border-color, #4e4e4e);
            `;

            const header = document.createElement("div");
            header.style.cssText = "display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; cursor: move;";
            header.innerHTML = `<span style="color: var(--fg-color, #fff); font-weight: 500;">${title}</span>`;

            const closeBtn = document.createElement("button");
            closeBtn.className = "p-button p-component p-button-primary";
            closeBtn.innerHTML = `<span class="p-button-label">Save & Close</span>`;
            closeBtn.onclick = () => this.stop();
            header.appendChild(closeBtn);

            this.canvas = document.createElement("canvas");
            this.canvas.style.cssText = "cursor: pointer; display: block; border-radius: 4px; min-width: 600px; min-height: 800px;";
            this.canvas.tabIndex = 0;
            this.ctx = this.canvas.getContext("2d");

            // Loading overlay with CSS animation
            this.loadingOverlay = document.createElement("div");
            this.loadingOverlay.style.cssText = `
                position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%);
                color: #888; font: 16px system-ui, sans-serif; display: none;
            `;
            this.loadingOverlay.innerHTML = `
                Launching browser<span class="loading-dots"></span>
                <style>
                    .loading-dots::after {
                        content: '';
                        animation: loading-dots 1.5s steps(4, end) infinite;
                    }
                    @keyframes loading-dots {
                        0%, 25% { content: ''; }
                        26%, 50% { content: '.'; }
                        51%, 75% { content: '..'; }
                        76%, 100% { content: '...'; }
                    }
                </style>
            `;

            this.el.append(header, this.canvas, this.loadingOverlay);
            document.body.appendChild(this.el);

            // Draggable
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

            // Canvas events - track mousedown position for click detection
            let mouseDownPos = null;
            const getCoords = (e) => {
                const rect = this.canvas.getBoundingClientRect();
                // IMPORTANT: Round to integers for pixel-perfect consistency (fixes reCAPTCHA tile selection)
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
                const coords = getCoords(e);
                mouseDownPos = coords;
                send({ type: "mousedown", ...coords });
            });
            this.canvas.addEventListener("mouseup", (e) => {
                e.preventDefault();
                // CRITICAL FIX: Reuse mousedown coordinates if mouse barely moved
                // This ensures reCAPTCHA sees identical mousedown/mouseup coords
                const rawCoords = getCoords(e);
                const coords = (mouseDownPos && Math.abs(rawCoords.x - mouseDownPos.x) <= 5 && Math.abs(rawCoords.y - mouseDownPos.y) <= 5)
                    ? mouseDownPos  // Reuse exact mousedown coords
                    : rawCoords;     // Use new coords if mouse moved significantly

                console.log(`[Specter] Click at (${coords.x}, ${coords.y})`);
                send({ type: "mouseup", ...coords });
                // If mouseup is close to mousedown, also send atomic click (for iframes like Cloudflare)
                if (mouseDownPos && Math.abs(coords.x - mouseDownPos.x) <= 5 && Math.abs(coords.y - mouseDownPos.y) <= 5) {
                    send({ type: "click", ...coords });
                }
                mouseDownPos = null;
            });
            this.canvas.addEventListener("mousemove", (e) => {
                const coords = getCoords(e);
                this.cursorX = coords.x;
                this.cursorY = coords.y;
                this.redrawWithCursor();
                if (e.buttons === 1) send({ type: "mousemove", ...coords });
            });
            this.canvas.addEventListener("wheel", (e) => { this.canvas.focus(); e.preventDefault(); send({ type: "scroll", dx: e.deltaX, dy: e.deltaY }); }, { passive: false });

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

        async start() {
            this.createEl();
            this.el.style.display = "block";

            // Set canvas size for loading state (before first screenshot)
            this.canvas.width = 600;
            this.canvas.height = 800;
            this.loadingOverlay.style.display = "block";

            try {
                const resp = await fetch(startEndpoint, { method: "POST" });
                const data = await resp.json();
                if (data.status === "error") {
                    this.loadingOverlay.style.display = "none";
                    this.showError(data.message);
                    return;
                }
                this.active = true;
                this.connectWS();
                this.canvas.focus();
            } catch (e) {
                this.loadingOverlay.style.display = "none";
                this.showError("Failed to connect to server");
            }
        },

        showError(msg) {
            // Parse clean error from verbose Playwright output
            const clean = msg.includes("install-deps")
                ? "Missing dependencies. Run: sudo playwright install-deps"
                : msg.split("\n")[0];
            // Show error in canvas
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

            this.ws.onmessage = (e) => {
                if (typeof e.data === "string") {
                    const msg = JSON.parse(e.data);
                    if (msg.type === "logged_in") {
                        console.log(`[Specter] ${service} login detected, auto-closing`);
                        this.stop();
                    }
                    return;
                }

                // Hide loading overlay on first screenshot
                this.loadingOverlay.style.display = "none";

                const blob = new Blob([e.data], { type: "image/png" });
                const img = new Image();
                img.onload = () => {
                    if (this.canvas.width !== img.width || this.canvas.height !== img.height) {
                        this.canvas.width = img.width;
                        this.canvas.height = img.height;
                    }
                    this.ctx.drawImage(img, 0, 0);
                    this.lastScreenshot = img;
                    this.redrawWithCursor();
                    URL.revokeObjectURL(img.src);
                };
                img.src = URL.createObjectURL(blob);
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

        redrawWithCursor() {
            if (!this.lastScreenshot) return;

            // Redraw screenshot
            this.ctx.drawImage(this.lastScreenshot, 0, 0);

            // Draw cursor if position is set
            if (this.cursorX !== null && this.cursorY !== null) {
                // Draw crosshair
                this.ctx.strokeStyle = "#00ff00";
                this.ctx.lineWidth = 2;
                this.ctx.beginPath();
                this.ctx.moveTo(this.cursorX - 10, this.cursorY);
                this.ctx.lineTo(this.cursorX + 10, this.cursorY);
                this.ctx.moveTo(this.cursorX, this.cursorY - 10);
                this.ctx.lineTo(this.cursorX, this.cursorY + 10);
                this.ctx.stroke();

                // Draw circle
                this.ctx.beginPath();
                this.ctx.arc(this.cursorX, this.cursorY, 8, 0, 2 * Math.PI);
                this.ctx.stroke();

                // Draw coordinates
                this.ctx.fillStyle = "#00ff00";
                this.ctx.font = "12px monospace";
                this.ctx.fillText(`(${Math.round(this.cursorX)}, ${Math.round(this.cursorY)})`, this.cursorX + 15, this.cursorY - 10);
            }
        }
    };
    return popup;
}

// Login popups for each service
const specterLogin = createLoginPopup("ChatGPT", "/specter/browser/start", "ChatGPT Login");
const grokLogin = createLoginPopup("Grok", "/specter/grok/browser/start", "Grok Login");

// Settings popups (reuse login popup with different title)
const chatgptSettings = createLoginPopup("ChatGPT", "/specter/chatgpt/settings/start", "ChatGPT Settings");
const grokSettings = createLoginPopup("Grok", "/specter/grok/settings/start", "Grok Settings");

// Color scheme - darker headers for light title text visibility
const SPECTER_COLORS = {
    // ChatGPT nodes - Dark red
    chatgpt: {
        nodeColor: "#991b1b",      // Dark red
        nodeBgColor: "#2a2a2a",
    },
    // Grok nodes - Dark blue
    grok: {
        nodeColor: "#1e3a8a",      // Dark blue
        nodeBgColor: "#2a2a2a",
    },
    // Tool nodes - Dark teal
    tools: {
        nodeColor: "#065f46",      // Dark teal
        nodeBgColor: "#2a2a2a",
    },
    // Processor nodes - Dark purple
    processors: {
        nodeColor: "#5b21b6",      // Dark purple
        nodeBgColor: "#2a2a2a",
    },
};

// Node to color mapping (keys match Specter_{ClassName minus "Node"})
const NODE_COLORS = {
    // ChatGPT nodes - Red
    "Specter_ChatGPTText": SPECTER_COLORS.chatgpt,
    "Specter_ChatGPTImage": SPECTER_COLORS.chatgpt,

    // Grok nodes - Blue
    "Specter_GrokText": SPECTER_COLORS.grok,
    "Specter_GrokImage": SPECTER_COLORS.grok,
    "Specter_GrokImageEdit": SPECTER_COLORS.grok,
    "Specter_GrokTextToVideo": SPECTER_COLORS.grok,
    "Specter_GrokImageToVideo": SPECTER_COLORS.grok,

    // ChatGPT Tool nodes - Red (uses ChatGPT)
    "Specter_PromptEnhancer": SPECTER_COLORS.chatgpt,
    "Specter_ImageDescriber": SPECTER_COLORS.chatgpt,

    // Grok Tool nodes - Blue (uses Grok)
    "Specter_GrokPromptEnhancer": SPECTER_COLORS.grok,
    "Specter_GrokImageDescriber": SPECTER_COLORS.grok,
};

app.registerExtension({
    name: "Specter.Appearance",

    // Settings for Specter
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
                fetch("/specter/health")
                    .then(r => r.json())
                    .then(data => {
                        if (data.ready) {
                            el.textContent = "✓ Ready";
                            el.style.color = "#4ade80";
                        } else {
                            el.textContent = "⚠ " + (data.error || "Not ready");
                            el.style.color = "#f87171";
                        }
                    })
                    .catch(() => {
                        el.textContent = "⚠ Unable to check";
                        el.style.color = "#fbbf24";
                    });
                return el;
            },
            defaultValue: "",
        },
        {
            id: "Specter.LoginButton",
            category: ["Specter", " Authentication", "ChatGPT"],
            name: "ChatGPT",
            tooltip: "Log in to ChatGPT via embedded browser",
            type: (name, setter, value, attrs) => {
                const container = document.createElement("div");
                container.className = "flex items-center gap-4";

                const statusText = document.createElement("span");
                statusText.className = "text-muted";
                statusText.textContent = "Checking...";

                const loginBtn = document.createElement("button");
                loginBtn.type = "button";
                loginBtn.className = "p-button p-component";
                loginBtn.style.minWidth = "116px";

                container.append(statusText, loginBtn);

                let isLoggedIn = false;

                const checkStatus = async () => {
                    try {
                        const resp = await fetch("/specter/status");
                        const data = await resp.json();
                        isLoggedIn = data.logged_in;
                        statusText.textContent = isLoggedIn ? "Logged in" : "Not logged in";
                        loginBtn.innerHTML = `<span class="p-button-icon p-button-icon-left pi pi-${isLoggedIn ? "sign-out" : "user"}"></span><span class="p-button-label">${isLoggedIn ? "Sign Out" : "Sign In"}</span>`;
                    } catch (e) {
                        statusText.textContent = "Status unknown";
                    }
                };

                loginBtn.addEventListener("click", async () => {
                    if (isLoggedIn) {
                        await fetch("/specter/logout", { method: "POST" });
                        await checkStatus();
                    } else {
                        await specterLogin.start();
                    }
                });

                checkStatus();
                // Re-check status when login popup closes
                specterLogin.onClose = checkStatus;

                return container;
            },
            defaultValue: "",
        },
        {
            id: "Specter.GrokLoginButton",
            category: ["Specter", " Authentication", "Grok"],
            name: "Grok",
            tooltip: "Log in to Grok via embedded browser",
            type: (name, setter, value, attrs) => {
                const container = document.createElement("div");
                container.className = "flex items-center gap-4";

                const statusText = document.createElement("span");
                statusText.className = "text-muted";
                statusText.textContent = "Checking...";

                const loginBtn = document.createElement("button");
                loginBtn.type = "button";
                loginBtn.className = "p-button p-component";
                loginBtn.style.minWidth = "116px";

                container.append(statusText, loginBtn);

                let isLoggedIn = false;

                const checkStatus = async () => {
                    try {
                        const resp = await fetch("/specter/grok/status");
                        const data = await resp.json();
                        isLoggedIn = data.logged_in;
                        statusText.textContent = isLoggedIn ? "Logged in" : "Not logged in";
                        loginBtn.innerHTML = `<span class="p-button-icon p-button-icon-left pi pi-${isLoggedIn ? "sign-out" : "user"}"></span><span class="p-button-label">${isLoggedIn ? "Sign Out" : "Sign In"}</span>`;
                    } catch (e) {
                        statusText.textContent = "Status unknown";
                    }
                };

                loginBtn.addEventListener("click", async () => {
                    if (isLoggedIn) {
                        await fetch("/specter/grok/logout", { method: "POST" });
                        await checkStatus();
                    } else {
                        await grokLogin.start();
                    }
                });

                checkStatus();
                // Re-check status when login popup closes
                grokLogin.onClose = checkStatus;

                return container;
            },
            defaultValue: "",
        },
        {
            id: "Specter.ChatGPTSettings",
            category: ["Specter", "Provider Settings", "ChatGPT"],
            name: "ChatGPT Settings",
            tooltip: "Open ChatGPT settings in embedded browser",
            type: () => {
                const btn = document.createElement("button");
                btn.type = "button";
                btn.className = "p-button p-component";
                btn.innerHTML = `<span class="p-button-icon p-button-icon-left pi pi-cog"></span><span class="p-button-label">Open Settings</span>`;
                btn.addEventListener("click", () => chatgptSettings.start());
                return btn;
            },
            defaultValue: "",
        },
        {
            id: "Specter.GrokSettings",
            category: ["Specter", "Provider Settings", "Grok"],
            name: "Grok Settings",
            tooltip: "Open Grok Imagine settings in embedded browser",
            type: () => {
                const btn = document.createElement("button");
                btn.type = "button";
                btn.className = "p-button p-component";
                btn.innerHTML = `<span class="p-button-icon p-button-icon-left pi pi-cog"></span><span class="p-button-label">Open Settings</span>`;
                btn.addEventListener("click", () => grokSettings.start());
                return btn;
            },
            defaultValue: "",
        },
        {
            id: "Specter.HeadedBrowser",
            category: ["Specter", "Advanced", "Browser"],
            name: "Show Browser Window",
            tooltip: "Run browser visibly for debugging (useful when login or automation fails)",
            type: (name, setter, value, attrs) => {
                const container = document.createElement("div");
                container.className = "flex items-center gap-4";

                const toggle = document.createElement("input");
                toggle.type = "checkbox";
                toggle.style.cssText = "width: 18px; height: 18px; cursor: pointer;";

                const label = document.createElement("span");
                label.textContent = "Enable headed mode";
                label.style.cssText = "color: var(--fg-color, #ccc);";

                container.append(toggle, label);

                // Load current setting
                fetch("/specter/settings")
                    .then(r => r.json())
                    .then(settings => {
                        toggle.checked = settings.headed_browser || false;
                    })
                    .catch(() => {});

                toggle.addEventListener("change", async () => {
                    try {
                        await fetch("/specter/settings", {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ headed_browser: toggle.checked }),
                        });
                    } catch (e) {
                        console.error("[Specter] Failed to save setting:", e);
                    }
                });

                return container;
            },
            defaultValue: false,
        },
        {
            id: "Specter.DebugDumps",
            category: ["Specter", "Advanced", "Debugging"],
            name: "Debug Dumps",
            tooltip: "Save debug info (screenshots, traces, logs) when errors occur",
            type: (name, setter, value, attrs) => {
                const container = document.createElement("div");
                container.className = "flex items-center gap-4";

                const toggle = document.createElement("input");
                toggle.type = "checkbox";
                toggle.style.cssText = "width: 18px; height: 18px; cursor: pointer;";

                const label = document.createElement("span");
                label.textContent = "Enable debug dumps on error";
                label.style.cssText = "color: var(--fg-color, #ccc);";

                container.append(toggle, label);

                // Load current setting (default true)
                fetch("/specter/settings")
                    .then(r => r.json())
                    .then(settings => {
                        toggle.checked = settings.debug_dumps !== false;
                    })
                    .catch(() => { toggle.checked = true; });

                toggle.addEventListener("change", async () => {
                    try {
                        await fetch("/specter/settings", {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ debug_dumps: toggle.checked }),
                        });
                    } catch (e) {
                        console.error("[Specter] Failed to save setting:", e);
                    }
                });

                return container;
            },
            defaultValue: true,
        },
    ],

    async beforeRegisterNodeDef(nodeType, nodeData, app) {
        const colors = NODE_COLORS[nodeData.name];
        if (!colors) return;

        const origOnNodeCreated = nodeType.prototype.onNodeCreated;

        nodeType.prototype.onNodeCreated = function () {
            if (origOnNodeCreated) {
                origOnNodeCreated.apply(this, arguments);
            }

            // Apply colors
            this.color = colors.nodeColor;
            this.bgcolor = colors.nodeBgColor;

            // Ensure minimum width
            this.size = this.computeSize();
            this.size[0] = Math.max(this.size[0], 300);
        };
    },

    async nodeCreated(node) {
        const colors = NODE_COLORS[node.comfyClass];
        if (colors) {
            node.color = colors.nodeColor;
            node.bgcolor = colors.nodeBgColor;
        }
    },
});

// Listen for login required events from backend
api.addEventListener("specter-login-required", () => {
    console.log("[Specter] ChatGPT login required - opening authentication popup");
    specterLogin.start();
});

api.addEventListener("specter-grok-login-required", () => {
    console.log("[Specter] Grok login required - opening authentication popup");
    grokLogin.start();
});

console.log("[Specter] Appearance extension loaded");
