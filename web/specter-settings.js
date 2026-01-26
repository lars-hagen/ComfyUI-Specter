/**
 * Specter Settings - Uses ComfyUI native settings pattern
 */
import { app } from "../../scripts/app.js";
import { createDialog, browserPopup } from "./specter-dialogs.js";

// Minimal CSS
const css = `
.specter-provider-controls { display: flex; align-items: center; gap: 8px; }
.specter-provider-controls .status { font-size: 12px; padding: 2px 8px; border-radius: 4px; min-width: 70px; text-align: center; }
.specter-provider-controls .status.in { color: #4ade80; background: rgba(74,222,128,0.15); }
.specter-provider-controls .status.out { color: var(--p-text-muted-color, #888); background: rgba(136,136,136,0.15); }
.specter-proxy-row { display: flex; align-items: center; gap: 8px; }
.specter-proxy-row input { width: 120px !important; }
.specter-proxy-row input.narrow { width: 60px !important; }
`;
document.head.appendChild(Object.assign(document.createElement("style"), { textContent: css }));

// Helpers
const fetch$ = (url, opts) => fetch(url, opts).then(r => r.json());
const el = (tag, props = {}, children = []) => {
    const e = Object.assign(document.createElement(tag), props);
    children.forEach(c => e.append(c));
    return e;
};

// Simple button helpers
const iconBtn = (icon, title, onClick) => {
    const btn = el("button", {
        type: "button", title,
        className: "p-button p-component p-button-icon-only p-button-secondary",
        innerHTML: `<span class="p-button-icon pi pi-${icon}"></span>`,
        style: "border: none; align-self: stretch;"
    });
    btn.onclick = onClick;
    return btn;
};

const textBtn = (icon, label, onClick, cls = "") => {
    const btn = el("button", {
        type: "button",
        className: `p-button p-component ${cls}`.trim(),
        innerHTML: `<span class="p-button-icon p-button-icon-left pi pi-${icon}"></span><span class="p-button-label">${label}</span>`
    });
    btn.onclick = onClick;
    return btn;
};

// Provider setting factory - renders status + sign in/out + settings + import
const createProviderSetting = (service, order, displayName = null) => {
    const svc = service.toLowerCase();
    const name = displayName || service;
    const endpoints = {
        status: `/specter/${svc}/status`,
        logout: `/specter/${svc}/logout`,
        login: `/specter/${svc}/browser/start`,
        import: `/specter/${svc}/import`,
        settings: `/specter/${svc}/settings/start`
    };

    return {
        id: `Specter.${service}`,
        category: ["Specter", "Providers", name],
        name: name,
        tooltip: `Manage ${name} authentication and settings`,
        sortOrder: order,
        type: () => {
            const container = el("div", { className: "specter-provider-controls" });
            const status = el("span", { className: "status out", textContent: "..." });
            const loginBtn = textBtn("sign-in", "Sign In", null);
            const settingsBtn = iconBtn("cog", "Settings", () => browserPopup.start(endpoints.settings, `${name} Settings`));
            const importBtn = iconBtn("download", "Import", null);

            container.append(status, loginBtn, settingsBtn, importBtn);

            let loggedIn = false;
            const check = async () => {
                try {
                    const data = await fetch$(endpoints.status);
                    loggedIn = data.logged_in;
                    status.textContent = loggedIn ? "Logged in" : "Not logged in";
                    status.className = `status ${loggedIn ? "in" : "out"}`;
                    loginBtn.querySelector(".p-button-icon").className = `p-button-icon p-button-icon-left pi pi-${loggedIn ? "sign-out" : "sign-in"}`;
                    loginBtn.querySelector(".p-button-label").textContent = loggedIn ? "Sign Out" : "Sign In";
                } catch { status.textContent = "Unknown"; status.className = "status out"; }
            };

            loginBtn.onclick = async () => {
                if (loggedIn) { await fetch$(endpoints.logout, { method: "POST" }); check(); }
                else browserPopup.start(endpoints.login, `${name} Login`, check);
            };

            // Import dialog
            let importDialog;
            importBtn.onclick = () => {
                if (!importDialog) {
                    importDialog = createDialog(`Import ${name} Cookies`, "download");
                    importDialog.content.innerHTML = `<div class="specter-dropzone">Drop cookies file or click to browse</div><input type="file" accept=".txt,.json" style="display:none"><textarea placeholder="Or paste cookie content..."></textarea><div class="specter-dialog-actions"><button class="p-button p-component"><span class="p-button-label">Import</span></button></div>`;
                    const [dropzone, fileInput, textarea] = importDialog.content.querySelectorAll(".specter-dropzone, input, textarea");
                    const importAction = importDialog.content.querySelector(".specter-dialog-actions button");
                    const handleFile = f => { const r = new FileReader(); r.onload = e => textarea.value = e.target.result; r.readAsText(f); };
                    dropzone.onclick = () => fileInput.click();
                    fileInput.onchange = e => e.target.files[0] && handleFile(e.target.files[0]);
                    dropzone.ondragover = e => { e.preventDefault(); dropzone.classList.add("dragover"); };
                    dropzone.ondragleave = () => dropzone.classList.remove("dragover");
                    dropzone.ondrop = e => { e.preventDefault(); dropzone.classList.remove("dragover"); e.dataTransfer.files[0] && handleFile(e.dataTransfer.files[0]); };
                    importAction.onclick = async () => {
                        const cookies = textarea.value.trim();
                        if (!cookies) return;
                        importAction.disabled = true;
                        importAction.querySelector(".p-button-label").textContent = "Importing...";
                        try {
                            const data = await fetch$(endpoints.import, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cookies }) });
                            if (data.status === "ok") { importDialog.hide(); textarea.value = ""; check(); }
                            else alert(`Import failed: ${data.message}`);
                        } catch (e) { alert(`Import failed: ${e.message}`); }
                        importAction.disabled = false;
                        importAction.querySelector(".p-button-label").textContent = "Import";
                    };
                }
                importDialog.show();
            };

            check();
            return container;
        },
        defaultValue: ""
    };
};

// Toggle setting factory
const createToggleSetting = (id, category, name, tooltip, settingKey, defaultVal = false, order = 0) => ({
    id, category, name, tooltip, sortOrder: order,
    type: () => {
        const wrapper = el("div", { className: "p-toggleswitch p-component", style: "position:relative" });
        const input = el("input", { type: "checkbox", role: "switch", className: "p-toggleswitch-input" });
        const slider = el("div", { className: "p-toggleswitch-slider", innerHTML: '<div class="p-toggleswitch-handle"></div>' });
        wrapper.append(input, slider);
        const update = checked => { input.checked = checked; wrapper.classList.toggle("p-toggleswitch-checked", checked); };
        fetch$("/specter/settings").then(s => update(defaultVal ? s[settingKey] !== false : !!s[settingKey])).catch(() => update(defaultVal));
        input.onchange = () => { update(input.checked); fetch$("/specter/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ [settingKey]: input.checked }) }); };
        return wrapper;
    },
    defaultValue: defaultVal
});

// Text setting factory
const createTextSetting = (id, category, name, tooltip, settingKey, placeholder = "", defaultVal = "", order = 0) => ({
    id, category, name, tooltip, sortOrder: order,
    type: () => {
        const input = el("input", { type: "text", className: "p-inputtext p-component", placeholder });
        fetch$("/specter/settings").then(s => input.value = s[settingKey] || defaultVal).catch(() => input.value = defaultVal);
        input.onchange = () => fetch$("/specter/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ [settingKey]: input.value }) });
        return input;
    },
    defaultValue: defaultVal
});

// Number setting factory
const createNumberSetting = (id, category, name, tooltip, settingKey, defaultVal = 0, order = 0) => ({
    id, category, name, tooltip, sortOrder: order,
    type: () => {
        const input = el("input", { type: "text", className: "p-inputtext p-component", inputMode: "numeric", style: "width: 80px" });
        fetch$("/specter/settings").then(s => input.value = s[settingKey] || defaultVal).catch(() => input.value = defaultVal);
        input.onchange = () => fetch$("/specter/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ [settingKey]: parseInt(input.value) || defaultVal }) });
        return input;
    },
    defaultValue: defaultVal
});

// Register extension with settings
app.registerExtension({
    name: "Specter.Settings",
    settings: [
        // Status
        {
            id: "Specter.GoogleStatus", category: ["Specter", "Status", "Google"], name: "Google Services", tooltip: "Google connectivity check", sortOrder: 100,
            type: () => {
                const el = document.createElement("span");
                el.style.fontWeight = "500";
                el.textContent = "Checking...";
                el.style.color = "#888";
                fetch$("/specter/health").then(d => {
                    el.textContent = d.google_blocked ? "⚠ Blocked" : "✓ Accessible";
                    el.style.color = d.google_blocked ? "#fbbf24" : "#4ade80";
                }).catch(() => { el.textContent = "Unknown"; });
                return el;
            },
            defaultValue: ""
        },
        {
            id: "Specter.BrowserStatus", category: ["Specter", "Status", "Browser"], name: "Browser", tooltip: "Browser automation status", sortOrder: 99,
            type: () => {
                const el = document.createElement("span");
                el.style.fontWeight = "500";
                el.textContent = "Checking...";
                el.style.color = "#888";
                fetch$("/specter/health").then(d => {
                    el.textContent = d.ready ? "Ready" : "⚠ " + (d.error || "Not ready");
                    el.style.color = d.ready ? "#4ade80" : "#f87171";
                }).catch(() => { el.textContent = "Unknown"; el.style.color = "#fbbf24"; });
                return el;
            },
            defaultValue: ""
        },

        // Providers
        createProviderSetting("ChatGPT", 50),
        createProviderSetting("Grok", 49),
        createProviderSetting("Gemini", 48, "Google Gemini"),
        createProviderSetting("Flow", 47, "Google Flow"),

        // Proxy
        createToggleSetting("Specter.ProxyEnabled", ["Specter", "Proxy", "Enable"], "Enable Proxy", "Route browser traffic through proxy", "proxy_enabled", false, 40),
        createTextSetting("Specter.ProxyServer", ["Specter", "Proxy", "Server"], "Server", "Proxy server address", "proxy_server", "127.0.0.1", "", 39),
        createNumberSetting("Specter.ProxyPort", ["Specter", "Proxy", "Port"], "Port", "Proxy server port", "proxy_port", 8080, 38),

        // Advanced
        createToggleSetting("Specter.DebugDumps", ["Specter", "Advanced", "DebugDumps"], "Debug Dumps", "Save debug info on error", "debug_dumps", true, 20),
        createToggleSetting("Specter.HeadedBrowser", ["Specter", "Advanced", "ShowBrowser"], "Show Browser", "Run browser visibly for debugging", "headed_browser", false, 19),
        {
            id: "Specter.ResetData", category: ["Specter", "Advanced", "Reset"], name: "Reset All Data", tooltip: "Clear all saved sessions and browser profiles", sortOrder: 10,
            type: () => {
                const btn = textBtn("trash", "Reset", null, "p-button-danger");
                btn.onclick = async () => {
                    if (!confirm("Clear all saved sessions and browser profiles?")) return;
                    btn.disabled = true;
                    btn.querySelector(".p-button-label").textContent = "Clearing...";
                    try { await fetch$("/specter/reset", { method: "POST" }); btn.querySelector(".p-button-label").textContent = "Done!"; }
                    catch { btn.querySelector(".p-button-label").textContent = "Failed"; }
                    setTimeout(() => { btn.querySelector(".p-button-label").textContent = "Reset"; btn.disabled = false; }, 2000);
                };
                return btn;
            },
            defaultValue: ""
        }
    ],

    async nodeCreated(node) {
        if (!node.comfyClass.startsWith("Specter_")) return;
        const cls = node.comfyClass;
        node.color = cls.includes("Grok") ? "#4c1d95" : cls.includes("Gemini") || cls.includes("NanoBanana") || cls.includes("Flow") ? "#1e3a8a" : "#991b1b";
        node.bgcolor = "#2a2a2a";
        node.size[0] = Math.max(node.size[0], 300);
    }
});
