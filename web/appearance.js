/**
 * Specter Node Appearance Configuration
 * Custom styling for Specter nodes in ComfyUI
 */

import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

// Standalone login popup that can be triggered anytime
const specterLogin = {
    popup: null,
    canvas: null,
    ctx: null,
    ws: null,
    active: false,

    createPopup() {
        if (this.popup) return;

        this.popup = document.createElement("div");
        this.popup.style.cssText = `
            display: none; position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
            z-index: 10000; background: var(--comfy-menu-bg, #353535); border-radius: 8px;
            box-shadow: 0 8px 32px rgba(0,0,0,0.4);
            padding: 12px; border: 1px solid var(--border-color, #4e4e4e);
        `;

        const header = document.createElement("div");
        header.style.cssText = "display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; cursor: move;";
        header.innerHTML = `<span style="color: var(--fg-color, #fff); font-weight: 500;">ChatGPT Login</span>`;

        const closeBtn = document.createElement("button");
        closeBtn.className = "p-button p-component p-button-primary";
        closeBtn.innerHTML = `<span class="p-button-label">Save & Close</span>`;
        closeBtn.onclick = () => this.stop();
        header.appendChild(closeBtn);

        this.canvas = document.createElement("canvas");
        this.canvas.style.cssText = "cursor: pointer; display: block; border-radius: 4px;";
        this.canvas.tabIndex = 0;
        this.ctx = this.canvas.getContext("2d");

        this.popup.append(header, this.canvas);
        document.body.appendChild(this.popup);

        // Draggable
        let dragging = false, dragX, dragY;
        header.addEventListener("mousedown", (e) => {
            if (e.target === closeBtn || closeBtn.contains(e.target)) return;
            dragging = true;
            dragX = e.clientX - this.popup.offsetLeft;
            dragY = e.clientY - this.popup.offsetTop;
        });
        document.addEventListener("mousemove", (e) => {
            if (!dragging) return;
            this.popup.style.left = e.clientX - dragX + "px";
            this.popup.style.top = e.clientY - dragY + "px";
            this.popup.style.transform = "none";
        });
        document.addEventListener("mouseup", () => dragging = false);

        // Canvas events
        const getCoords = (e) => {
            const rect = this.canvas.getBoundingClientRect();
            return {
                x: (e.clientX - rect.left) * (this.canvas.width / rect.width),
                y: (e.clientY - rect.top) * (this.canvas.height / rect.height)
            };
        };
        const send = (event) => {
            if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(event));
        };
        this.canvas.addEventListener("mousedown", (e) => { this.canvas.focus(); e.preventDefault(); send({ type: "mousedown", ...getCoords(e) }); });
        this.canvas.addEventListener("mouseup", (e) => { e.preventDefault(); send({ type: "mouseup", ...getCoords(e) }); });
        this.canvas.addEventListener("mousemove", (e) => { if (e.buttons === 1) send({ type: "mousemove", ...getCoords(e) }); });
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
        this.createPopup();
        this.popup.style.display = "block";
        this.active = true;

        try {
            await fetch("/specter/browser/start", { method: "POST" });
            this.connectWS();
            this.canvas.focus();
        } catch (e) {
            console.error("[Specter] Failed to start browser:", e);
            this.popup.style.display = "none";
        }
    },

    connectWS() {
        const protocol = location.protocol === "https:" ? "wss:" : "ws:";
        this.ws = new WebSocket(`${protocol}//${location.host}/specter/browser/ws`);

        this.ws.onmessage = (e) => {
            if (typeof e.data === "string") {
                const msg = JSON.parse(e.data);
                if (msg.type === "logged_in") {
                    console.log("[Specter] Login detected, auto-closing");
                    this.stop();
                }
                return;
            }
            const blob = new Blob([e.data], { type: "image/png" });
            const img = new Image();
            img.onload = () => {
                if (this.canvas.width !== img.width || this.canvas.height !== img.height) {
                    this.canvas.width = img.width;
                    this.canvas.height = img.height;
                }
                this.ctx.drawImage(img, 0, 0);
                URL.revokeObjectURL(img.src);
            };
            img.src = URL.createObjectURL(blob);
        };

        this.ws.onclose = () => { if (this.active) setTimeout(() => this.connectWS(), 1000); };
    },

    async stop() {
        this.active = false;
        if (this.ws) { this.ws.close(); this.ws = null; }
        if (this.popup) this.popup.style.display = "none";
        try { await fetch("/specter/browser/stop", { method: "POST" }); } catch {}
    }
};

// Color scheme - darker headers for light title text visibility
const SPECTER_COLORS = {
    // ChatGPT nodes - Dark red
    chatgpt: {
        nodeColor: "#991b1b",      // Dark red
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

// Node to color mapping
const NODE_COLORS = {
    // ChatGPT nodes - Red
    "Specter_ChatGPT": SPECTER_COLORS.chatgpt,
    "Specter_ChatGPT_Text": SPECTER_COLORS.chatgpt,
    "Specter_ChatGPT_Image": SPECTER_COLORS.chatgpt,

    // Tool nodes - Teal
    "Specter_PromptEnhancer": SPECTER_COLORS.tools,
    "Specter_ImageDescriber": SPECTER_COLORS.tools,

    // Processor nodes - Purple
};

app.registerExtension({
    name: "Specter.Appearance",

    // Settings for Specter
    settings: [
        {
            id: "Specter.LoginButton",
            category: ["Specter", "Authentication"],
            name: "ChatGPT Login",
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
                const origStop = specterLogin.stop.bind(specterLogin);
                specterLogin.stop = async () => { await origStop(); await checkStatus(); };

                return container;
            },
            defaultValue: "",
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

// Listen for login required event from backend
api.addEventListener("specter-login-required", () => {
    console.log("[Specter] Login required - opening authentication popup");
    specterLogin.start();
});

console.log("[Specter] Appearance extension loaded");
