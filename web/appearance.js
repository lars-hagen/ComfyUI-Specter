/**
 * Specter Node Appearance Configuration
 * Custom styling for Specter nodes in ComfyUI
 */

import { app } from "../../scripts/app.js";

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

                // Status text
                const statusText = document.createElement("span");
                statusText.className = "text-muted";
                statusText.textContent = "Checking...";

                // Login button - matches PrimeVue button styling
                const loginBtn = document.createElement("button");
                loginBtn.type = "button";
                loginBtn.className = "p-button p-component";
                loginBtn.innerHTML = `<span class="p-button-icon p-button-icon-left pi pi-user"></span><span class="p-button-label">Sign In</span>`;

                container.append(statusText, loginBtn);

                // Floating popup for browser
                const popup = document.createElement("div");
                popup.style.cssText = `
                    display: none; position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%);
                    z-index: 10000; background: var(--comfy-menu-bg, #353535); border-radius: 8px;
                    box-shadow: 0 8px 32px rgba(0,0,0,0.4);
                    padding: 12px; border: 1px solid var(--border-color, #4e4e4e);
                `;

                const popupHeader = document.createElement("div");
                popupHeader.style.cssText = "display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; cursor: move;";
                popupHeader.innerHTML = `<span style="color: var(--fg-color, #fff); font-weight: 500;">ChatGPT Login</span>`;

                const closeBtn = document.createElement("button");
                closeBtn.className = "p-button p-component p-button-primary";
                closeBtn.innerHTML = `<span class="p-button-label">Save & Close</span>`;
                popupHeader.appendChild(closeBtn);

                const canvas = document.createElement("canvas");
                canvas.style.cssText = "cursor: pointer; display: block; border-radius: 4px;";
                canvas.tabIndex = 0;

                popup.append(popupHeader, canvas);
                document.body.appendChild(popup);

                // Draggable popup
                let dragging = false, dragX, dragY;
                popupHeader.addEventListener("mousedown", (e) => {
                    if (e.target === closeBtn || closeBtn.contains(e.target)) return;
                    dragging = true;
                    dragX = e.clientX - popup.offsetLeft;
                    dragY = e.clientY - popup.offsetTop;
                });
                document.addEventListener("mousemove", (e) => {
                    if (!dragging) return;
                    popup.style.left = e.clientX - dragX + "px";
                    popup.style.top = e.clientY - dragY + "px";
                    popup.style.transform = "none";
                });
                document.addEventListener("mouseup", () => dragging = false);

                // State
                let isLoggedIn = false;
                let ws = null;
                let browserActive = false;
                const ctx = canvas.getContext("2d");

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

                const startBrowser = async () => {
                    popup.style.display = "block";
                    loginBtn.style.display = "none";
                    statusText.textContent = "Starting...";

                    try {
                        await fetch("/specter/browser/start", { method: "POST" });
                        connectWS();
                        browserActive = true;
                        canvas.focus();
                        statusText.textContent = "Complete the login in the popup window";
                    } catch (e) {
                        statusText.textContent = "Failed";
                        loginBtn.style.display = "inline-flex";
                    }
                };

                const stopBrowser = async () => {
                    browserActive = false;
                    if (ws) { ws.close(); ws = null; }
                    popup.style.display = "none";
                    loginBtn.style.display = "inline-flex";
                    statusText.textContent = "Saving...";

                    try {
                        await fetch("/specter/browser/stop", { method: "POST" });
                        await checkStatus();
                    } catch (e) {
                        statusText.textContent = "Error";
                    }
                };

                const connectWS = () => {
                    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
                    ws = new WebSocket(`${protocol}//${location.host}/specter/browser/ws`);

                    ws.onmessage = (e) => {
                        // Handle JSON messages (login status)
                        if (typeof e.data === "string") {
                            const msg = JSON.parse(e.data);
                            if (msg.type === "logged_in") {
                                console.log("[Specter] Login detected, auto-closing");
                                stopBrowser();
                            }
                            return;
                        }

                        // Handle binary screenshots
                        const blob = new Blob([e.data], { type: "image/png" });
                        const img = new Image();
                        img.onload = () => {
                            if (canvas.width !== img.width || canvas.height !== img.height) {
                                canvas.width = img.width;
                                canvas.height = img.height;
                            }
                            ctx.drawImage(img, 0, 0);
                            URL.revokeObjectURL(img.src);
                        };
                        img.src = URL.createObjectURL(blob);
                    };

                    ws.onclose = () => { if (browserActive) setTimeout(connectWS, 1000); };
                };

                const send = (event) => {
                    if (ws?.readyState === WebSocket.OPEN) {
                        ws.send(JSON.stringify(event));
                    }
                };

                const getCoords = (e) => {
                    const rect = canvas.getBoundingClientRect();
                    // Scale from CSS display size to internal canvas size
                    const scaleX = canvas.width / rect.width;
                    const scaleY = canvas.height / rect.height;
                    return {
                        x: (e.clientX - rect.left) * scaleX,
                        y: (e.clientY - rect.top) * scaleY
                    };
                };

                // Event handlers - use mousedown/mouseup for reliability
                canvas.addEventListener("mousedown", (e) => {
                    canvas.focus();
                    e.preventDefault();
                    e.stopPropagation();
                    send({ type: "mousedown", ...getCoords(e) });
                });
                canvas.addEventListener("mouseup", (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    send({ type: "mouseup", ...getCoords(e) });
                });
                canvas.addEventListener("mousemove", (e) => {
                    if (e.buttons === 1) send({ type: "mousemove", ...getCoords(e) });
                });
                canvas.addEventListener("wheel", (e) => {
                    canvas.focus();
                    e.preventDefault();
                    e.stopPropagation();
                    send({ type: "scroll", dx: e.deltaX, dy: e.deltaY });
                }, { passive: false });

                const stopKey = (e) => { e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation(); };
                canvas.addEventListener("keydown", async (e) => {
                    stopKey(e);
                    const hasCtrl = e.ctrlKey || e.metaKey;

                    // Handle paste specially - read clipboard and type it
                    if (hasCtrl && e.key.toLowerCase() === "v") {
                        try {
                            const text = await navigator.clipboard.readText();
                            if (text) send({ type: "type", text });
                        } catch (err) { console.warn("Clipboard access denied"); }
                        return;
                    }

                    const mods = [];
                    if (hasCtrl) mods.push("Control");
                    if (e.altKey) mods.push("Alt");
                    if (e.shiftKey) mods.push("Shift");

                    if (mods.length > 0) {
                        send({ type: "keydown", key: [...mods, e.key].join("+") });
                    } else if (e.key.length === 1) {
                        send({ type: "type", text: e.key });
                    } else {
                        send({ type: "keydown", key: e.key });
                    }
                }, true);
                canvas.addEventListener("keyup", stopKey, true);
                canvas.addEventListener("keypress", stopKey, true);

                loginBtn.addEventListener("click", async () => {
                    if (isLoggedIn) {
                        await fetch("/specter/logout", { method: "POST" });
                        await checkStatus();
                    } else {
                        await startBrowser();
                    }
                });

                closeBtn.addEventListener("click", stopBrowser);
                checkStatus();

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

console.log("[Specter] Appearance extension loaded");
