/**
 * Graph manager — handles graph loading, graph list fetching, graph switching,
 * breadcrumb navigation, dynamic node registration, and the init sequence.
 */

import { LiteGraph } from 'litegraph.js';
import { addLog } from './logging.js';
import { setHistoryBlock, clearHistory, cancelPendingSaves } from './history.js';
import { openJSONEditor } from './json_editor.js';
import { openFileBrowser } from './file_browser.js';
import { getSessionId } from './session.js';
import { setAvailableGraphs, revalidateSubgraphNodes } from './nodes.js';
import { reloadLogs, updateActiveSessions } from './execution.js';

const API_BASE = "http://localhost:8000";

/**
 * Fit the viewport so all nodes are visible, centred with padding.
 * Used as fallback when a graph has no saved _viewport.
 */
export function fitViewportToNodes(canvas, graph) {
    const nodes = graph._nodes || [];
    if (!nodes.length) {
        canvas.ds.offset[0] = 0;
        canvas.ds.offset[1] = 0;
        canvas.ds.scale = 1;
        return;
    }
    const PAD = 80;
    let bx0 = Infinity, by0 = Infinity, bx1 = -Infinity, by1 = -Infinity;
    for (const n of nodes) {
        const w = n.size?.[0] || 210;
        const h = n.size?.[1] || 100;
        bx0 = Math.min(bx0, n.pos[0]);
        by0 = Math.min(by0, n.pos[1]);
        bx1 = Math.max(bx1, n.pos[0] + w);
        by1 = Math.max(by1, n.pos[1] + h);
    }
    for (const g of (graph._groups || [])) {
        bx0 = Math.min(bx0, g.pos[0]);
        by0 = Math.min(by0, g.pos[1]);
        bx1 = Math.max(bx1, g.pos[0] + g.size[0]);
        by1 = Math.max(by1, g.pos[1] + g.size[1]);
    }
    const gw = bx1 - bx0 + PAD * 2;
    const gh = by1 - by0 + PAD * 2;
    const bodyZoom = parseFloat(getComputedStyle(document.body).zoom) || 1;
    const visW = window.innerWidth / bodyZoom;
    const visH = window.innerHeight / bodyZoom;
    const scale = Math.min(visW / gw, visH / gh, 1.0);
    canvas.ds.scale = scale;
    canvas.ds.offset[0] = visW / (2 * scale) - (bx0 + bx1) / 2;
    canvas.ds.offset[1] = visH / (2 * scale) - (by0 + by1) / 2;
}

/**
 * Per-type origin metadata populated by fetchNodes().
 * Keys are full LiteGraph type strings (e.g. "github/ImageTagExtractor").
 * Values: { origin: "core"|"builtin"|"external", source_url: string|null, module_id: string }
 */
export const nodeTypeMetadata = {};

/**
 * Show the global "restart required" banner.
 * Persists across page refreshes via localStorage — only cleared by
 * clicking "Restart Now" (which restarts the server then reloads).
 */
const _RESTART_KEY = "stackflow_restart_required";
let _restartRequired = localStorage.getItem(_RESTART_KEY) === "1";

export function showRestartRequired() {
    if (_restartRequired) return; // already showing
    _restartRequired = true;
    localStorage.setItem(_RESTART_KEY, "1");
    const banner = document.getElementById("restart-required-banner");
    if (banner) banner.style.display = "flex";
}

function _clearRestartRequired() {
    _restartRequired = false;
    localStorage.removeItem(_RESTART_KEY);
    const banner = document.getElementById("restart-required-banner");
    if (banner) banner.style.display = "none";
}

// Wire up the global restart banner button + restore persisted state
document.addEventListener("DOMContentLoaded", () => {
    // Restore banner visibility from localStorage
    if (_restartRequired) {
        const banner = document.getElementById("restart-required-banner");
        if (banner) banner.style.display = "flex";
    }

    const btn = document.getElementById("restart-required-btn");
    if (btn) {
        btn.addEventListener("click", async () => {
            btn.disabled = true;
            btn.textContent = "Restarting…";
            try {
                await fetch(`${API_BASE}/restart`, { method: "POST" });
                btn.textContent = "Reloading…";
                _clearRestartRequired();
                setTimeout(() => location.reload(), 3000);
            } catch {
                _clearRestartRequired();
                setTimeout(() => location.reload(), 5000);
            }
        });
    }
});

export function isRestartRequired() { return _restartRequired; }

/**
 * Show a dialog suggesting modules to install when a graph has missing node types.
 * Cross-references missing types against the /modules endpoint AND the graph's
 * embedded _module_deps manifest (which stores origin + source_url for each module).
 *
 * @param {Set<string>} missingTypes - e.g. {"github/ImageTagExtractor", "slack/SlackDMNotifier"}
 * @param {Object|null} moduleDeps - the graph's _module_deps object, if present
 */
async function suggestMissingModules(missingTypes, moduleDeps) {
    if (!missingTypes || missingTypes.size === 0) return;

    // Map missing type → {moduleId, nodeName}
    const needed = new Map(); // moduleId → Set<nodeName>
    for (const fullType of missingTypes) {
        const slash = fullType.indexOf("/");
        if (slash < 0) continue;
        const moduleId = fullType.slice(0, slash);
        const nodeName = fullType.slice(slash + 1);
        if (!needed.has(moduleId)) needed.set(moduleId, new Set());
        needed.get(moduleId).add(nodeName);
    }
    if (needed.size === 0) return;

    // Fetch all modules to cross-reference
    let allModules;
    try {
        const res = await fetch(`${API_BASE}/modules`);
        if (!res.ok) return;
        const data = await res.json();
        allModules = data.modules || [];
    } catch { return; }

    // Categorise missing modules:
    // - installable: known module in registry, not installed → "Install" button
    // - broken: known module in registry, installed but nodes still missing → error badge
    // - installableFromUrl: NOT in registry, but _module_deps has a source_url → "Install from GitHub" button
    // - unknown: not in registry, no source_url → grey unknown badge
    const installable = [];       // {module, missingNodes}
    const broken = [];            // {module, missingNodes}
    const installableFromUrl = []; // {moduleId, sourceUrl, missingNodes}
    const unknown = [];           // {moduleId, missingNodes}

    for (const [moduleId, nodeNames] of needed) {
        const mod = allModules.find(m => m.id === moduleId);
        if (!mod) {
            // Not in registry — check if graph has the source URL
            const dep = moduleDeps?.[moduleId];
            if (dep?.source_url) {
                installableFromUrl.push({ moduleId, sourceUrl: dep.source_url, missingNodes: [...nodeNames] });
            } else {
                unknown.push({ moduleId, missingNodes: [...nodeNames] });
            }
            continue;
        }
        const matched = [...nodeNames].filter(n => (mod.nodes || []).includes(n));
        const nodeList = matched.length > 0 ? matched : [...nodeNames];
        if (!mod.installed) {
            installable.push({ module: mod, missingNodes: nodeList });
        } else {
            // Installed but nodes still missing → load error or registration failure
            broken.push({ module: mod, missingNodes: nodeList });
        }
    }

    const totalIssues = installable.length + broken.length + installableFromUrl.length + unknown.length;
    if (totalIssues === 0) return;

    // ── Build the dialog ─────────────────────────────────────────────
    const overlay = document.createElement("div");
    overlay.className = "missing-modules-overlay";

    const dialog = document.createElement("div");
    dialog.className = "missing-modules-dialog";

    // Header
    const header = document.createElement("div");
    header.className = "missing-modules-header";

    const canInstall = installable.length > 0 || installableFromUrl.length > 0;
    const subtitle = canInstall
        ? "This graph requires modules that are not installed."
        : "This graph has nodes that could not be loaded.";

    header.innerHTML = `<span class="missing-modules-icon">📦</span>
        <div>
            <div class="missing-modules-title">Missing Nodes</div>
            <div class="missing-modules-subtitle">${subtitle}</div>
        </div>`;

    const closeBtn = document.createElement("button");
    closeBtn.className = "missing-modules-close";
    closeBtn.textContent = "✕";
    closeBtn.addEventListener("click", () => overlay.remove());
    header.appendChild(closeBtn);

    // Module list
    const list = document.createElement("div");
    list.className = "missing-modules-list";

    // Helper to create a row
    function addRow({ color, name, nodes, url, actionEl }) {
        const row = document.createElement("div");
        row.className = "missing-modules-row";

        const colorDot = document.createElement("span");
        colorDot.className = "missing-modules-dot";
        colorDot.style.background = color || "#666";

        const info = document.createElement("div");
        info.className = "missing-modules-info";

        const modName = document.createElement("div");
        modName.className = "missing-modules-name";
        modName.textContent = name;

        info.appendChild(modName);

        if (url) {
            const urlEl = document.createElement("a");
            urlEl.className = "missing-modules-url";
            urlEl.href = url;
            urlEl.target = "_blank";
            urlEl.rel = "noopener";
            urlEl.textContent = url.replace(/^https?:\/\//, "");
            info.appendChild(urlEl);
        }

        const nodeChips = document.createElement("div");
        nodeChips.className = "missing-modules-chips";
        nodes.forEach(n => {
            const chip = document.createElement("span");
            chip.className = "missing-modules-chip";
            chip.textContent = n;
            nodeChips.appendChild(chip);
        });

        info.appendChild(nodeChips);
        row.appendChild(colorDot);
        row.appendChild(info);
        if (actionEl) row.appendChild(actionEl);
        list.appendChild(row);
    }

    // ── Installable modules (blue Install button) ──
    for (const { module: mod, missingNodes } of installable) {
        const installBtn = document.createElement("button");
        installBtn.className = "missing-modules-install";
        installBtn.textContent = "Install";
        installBtn.addEventListener("click", async () => {
            installBtn.disabled = true;
            installBtn.textContent = "Installing…";
            try {
                const res = await fetch(`${API_BASE}/modules/${mod.id}/install`, { method: "POST" });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
                installBtn.textContent = "✓ Installed";
                installBtn.classList.add("installed");
                if (data.needs_restart) showRestartRequired();
            } catch (e) {
                installBtn.textContent = "✗ Failed";
                installBtn.title = e.message;
                setTimeout(() => { installBtn.textContent = "Retry"; installBtn.disabled = false; }, 2000);
            }
        });
        addRow({ color: mod.color, name: mod.name, nodes: missingNodes, url: mod.source_url, actionEl: installBtn });
    }

    // ── Broken modules (installed but nodes missing — show error badge) ──
    for (const { module: mod, missingNodes } of broken) {
        const badge = document.createElement("span");
        badge.className = "missing-modules-badge-error";
        badge.textContent = mod.load_error ? "load error" : "not loaded";
        badge.title = mod.load_error || "Module is installed but these nodes were not registered";
        addRow({ color: mod.color, name: mod.name, nodes: missingNodes, url: mod.source_url, actionEl: badge });
    }

    // ── External modules with source URL from graph manifest (Install from GitHub) ──
    for (const { moduleId, sourceUrl, missingNodes } of installableFromUrl) {
        const installBtn = document.createElement("button");
        installBtn.className = "missing-modules-install";
        installBtn.textContent = "Install";
        installBtn.title = sourceUrl;
        installBtn.addEventListener("click", async () => {
            installBtn.disabled = true;
            installBtn.textContent = "Cloning…";
            try {
                const res = await fetch(`${API_BASE}/modules/install-from-github`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ url: sourceUrl }),
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
                installBtn.textContent = "✓ Installed";
                installBtn.classList.add("installed");
                if (data.needs_restart) showRestartRequired();
            } catch (e) {
                installBtn.textContent = "✗ Failed";
                installBtn.title = e.message;
                setTimeout(() => { installBtn.textContent = "Retry"; installBtn.disabled = false; }, 2000);
            }
        });
        addRow({ color: "#666", name: moduleId, nodes: missingNodes, url: sourceUrl, actionEl: installBtn });
    }

    // ── Unknown modules (no matching module in registry, no source URL) ──
    for (const { moduleId, missingNodes } of unknown) {
        const badge = document.createElement("span");
        badge.className = "missing-modules-badge-unknown";
        badge.textContent = "unknown";
        badge.title = `No module "${moduleId}" found in the registry`;
        addRow({ color: "#666", name: moduleId, nodes: missingNodes, actionEl: badge });
    }

    // Footer
    const footer = document.createElement("div");
    footer.className = "missing-modules-footer";
    const dismissBtn = document.createElement("button");
    dismissBtn.className = "missing-modules-dismiss";
    dismissBtn.textContent = "Dismiss";
    dismissBtn.addEventListener("click", () => overlay.remove());
    footer.appendChild(dismissBtn);

    dialog.appendChild(header);
    dialog.appendChild(list);
    dialog.appendChild(footer);
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);

    // ESC to close
    const onKey = (e) => {
        if (e.key === "Escape") { overlay.remove(); document.removeEventListener("keydown", onKey); }
    };
    document.addEventListener("keydown", onKey);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
}

/**
 * Fetches and registers dynamic nodes from the backend.
 */
export async function fetchNodes(canvas, isDirty, retries = 10) {
    try {
        const res = await fetch(`${API_BASE}/list_nodes`);
        if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
        const data = await res.json();

        data.nodes.forEach(nodeData => {
            function CustomNode() {
                this.addInput("In", "state");
                this.addProperty("name", nodeData.name);
                this.addProperty("type", nodeData.type); // standard or router
                this.title = nodeData.name;

                // Dynamic properties and widgets from server metadata
                if (nodeData.properties) {
                    Object.entries(nodeData.properties).forEach(([pName, pInfo]) => {
                        // Skip system properties
                        if (pName === "name" || pName === "type") return;

                        // Register the property with its default value from Python
                        this.addProperty(pName, pInfo.default);

                        let widgetType = "text";
                        const options = { property: pName }; // Bind widget to property

                        if (pInfo.type === "number") {
                            widgetType = "number";
                            options.precision = 2;
                        } else if (pInfo.type === "boolean") {
                            widgetType = "toggle";
                        } else if (pInfo.type === "enum") {
                            widgetType = "combo";
                            options.values = [...pInfo.options, "✏️ Custom..."];

                        } else if (pInfo.type === "multi_select") {
                            // Multi-select implementation
                            const btnLabel = pName === "tool_sets" ? "Select Tools" : "Select " + pName;

                            if (!Array.isArray(this.properties[pName])) {
                                if (this.properties[pName]) {
                                    this.properties[pName] = Array.isArray(this.properties[pName]) ? this.properties[pName] : [this.properties[pName]];
                                } else {
                                    this.properties[pName] = [];
                                }
                            }
                            if (pInfo.options) {
                                this.properties[pName] = this.properties[pName].filter(opt => pInfo.options.includes(opt));
                            }

                            // 1. The Select Button
                            const w = this.addWidget("button", btnLabel, "Click to toggle", (v, e) => {
                                LiteGraph.closeAllContextMenus();

                                const mouseEvent = e || window.event;
                                let x = mouseEvent ? (mouseEvent.clientX || mouseEvent.pageX) : 0;
                                let y = mouseEvent ? (mouseEvent.clientY || mouseEvent.pageY) : 0;

                                if (!x && !y && canvas) {
                                    x = canvas.mouse[0];
                                    y = canvas.mouse[1];
                                }

                                const menuItems = pInfo.options.map(opt => {
                                    const currentVals = this.properties[pName] || [];
                                    const isSelected = currentVals.includes(opt);
                                    return {
                                        content: (isSelected ? "✓ " : "  ") + opt,
                                        callback: (item, event) => {
                                            if (!Array.isArray(this.properties[pName])) this.properties[pName] = [];
                                            const idx = this.properties[pName].indexOf(opt);

                                            if (idx === -1) {
                                                this.properties[pName].push(opt);
                                            } else {
                                                this.properties[pName].splice(idx, 1);
                                            }

                                            updateDisplay(this.properties[pName]);

                                            const hasSelected = this.properties[pName].includes(opt);
                                            item.content = (hasSelected ? "✓ " : "  ") + opt;

                                            const allEntries = document.querySelectorAll(".litemenu-entry");
                                            allEntries.forEach(entry => {
                                                if (entry.innerText.includes(opt)) {
                                                    const targetEl = entry.querySelector(".content") || entry;
                                                    targetEl.innerText = (hasSelected ? "✓ " : "  ") + opt;
                                                }
                                            });

                                            if (this.setDirtyCanvas) this.setDirtyCanvas(true, true);
                                            if (canvas && canvas.draw) canvas.draw(true, true);

                                            isDirty.value = true;
                                            return true; // Keep menu open
                                        }
                                    };
                                });

                                new LiteGraph.ContextMenu(menuItems, {
                                    event: mouseEvent,
                                    left: x,
                                    top: y,
                                    scale: canvas ? (canvas.ds ? canvas.ds.scale : 1) : 1
                                });
                            }, { property: pName });

                            // 2. The Display Widget
                            const displayWidget = {
                                type: "info_list",
                                name: "Selected",
                                value: [],
                                is_display_for: pName,
                                draw: function (ctx, node, widget_width, y, margin) {
                                    const vals = this.value || [];
                                    const totalHeight = this.computeSize(widget_width)[1];

                                    const rectX = 14;
                                    const rectW = widget_width - 28;

                                    ctx.fillStyle = "#222";
                                    ctx.strokeStyle = "#444";
                                    ctx.beginPath();
                                    ctx.roundRect(rectX, y, rectW, totalHeight, 4);
                                    ctx.fill();
                                    ctx.stroke();

                                    ctx.font = "10px coding-font, monospace";

                                    if (vals.length === 0) {
                                        ctx.textAlign = "left";
                                        ctx.fillStyle = "#666";
                                        ctx.fillText("None selected", rectX + 8, y + 12);
                                        return;
                                    }

                                    let tx = rectX + 6;
                                    let ty = y + 4;
                                    const boxHeight = 14;
                                    const spacing = 5;

                                    vals.forEach(v => {
                                        const textWidth = ctx.measureText(v).width;
                                        const boxWidth = textWidth + 10;

                                        if (tx + boxWidth > rectX + rectW - 6) {
                                            tx = rectX + 6;
                                            ty += 18;
                                        }

                                        const bx = tx;
                                        ctx.fillStyle = "#000";
                                        ctx.beginPath();
                                        ctx.roundRect(bx, ty, boxWidth, boxHeight, 3);
                                        ctx.fill();

                                        ctx.textAlign = "center";
                                        ctx.fillStyle = "#aaa";
                                        ctx.fillText(v, bx + boxWidth / 2, ty + 10);

                                        tx += (boxWidth + spacing);
                                    });
                                },
                                computeSize: function (width) {
                                    if (!this.value || this.value.length === 0) return [width, 22];
                                    const ctx = document.createElement("canvas").getContext("2d");
                                    ctx.font = "10px coding-font, monospace";
                                    const wMargin = 10;
                                    let tx = wMargin + 6;
                                    let lines = 1;

                                    this.value.forEach(v => {
                                        const boxWidth = ctx.measureText(v).width + 10;
                                        if (tx + boxWidth > width - wMargin - 6) {
                                            tx = wMargin + 6;
                                            lines++;
                                        }
                                        tx += (boxWidth + 5);
                                    });

                                    // 18px per line matches ty += 18 in draw(); +4 ensures
                                    // 1-line height (22) equals the empty-value return above.
                                    return [width, lines * 18 + 4];
                                },
                                mouse: function (event, pos, node) {
                                    return false;
                                }
                            };
                            this.addCustomWidget(displayWidget);

                            const updateDisplay = (vals) => {
                                displayWidget.value = Array.isArray(vals) ? vals : [];
                                if (this.setDirtyCanvas) this.setDirtyCanvas(true, true);
                            };

                            updateDisplay(this.properties[pName]);
                            return; // Skip standard addWidget below
                        } else if (pInfo.type === "json") {
                            const w = this.addWidget("text", pName, this.properties[pName], (v) => {
                                this.properties[pName] = v;
                                isDirty.value = true;
                            }, options);

                            this.addWidget("button", "Edit", "Click to open full editor", () => {
                                openJSONEditor(this, w, pName, canvas, isDirty);
                            });
                            return;
                        } else if (pInfo.type === "slack") {
                            const w = this.addWidget("text", pName, this.properties[pName], (v) => {
                                this.properties[pName] = v;
                                isDirty.value = true;
                            }, options);

                            this.addWidget("button", "Edit Message", "Open multi-line editor", () => {
                                openJSONEditor(this, w, pName, canvas, isDirty, null, "text");
                            });
                            return;
                        } else if (pInfo.type === "template") {
                            const w = this.addWidget("text", pName, this.properties[pName], (v) => {
                                this.properties[pName] = v;
                                isDirty.value = true;
                            }, options);

                            this.addWidget("button", "Edit Template", "Open Jinja2 template editor", () => {
                                openJSONEditor(this, w, pName, canvas, isDirty, null, "template");
                            });
                            return;
                        } else if (pInfo.type === "file") {
                            const w = this.addWidget("text", pName, this.properties[pName], (v) => {
                                this.properties[pName] = v;
                                isDirty.value = true;
                            }, options);

                            const self = this;
                            this.addWidget("button", "Browse", "Select a file", () => {
                                openFileBrowser(self.properties[pName] || "", (selectedPath) => {
                                    self.properties[pName] = selectedPath;
                                    w.value = selectedPath;
                                    isDirty.value = true;
                                    if (self.setDirtyCanvas) self.setDirtyCanvas(true, true);
                                });
                            });
                            return;
                        }

                        // Add widget - property binding handles sync
                        const w = this.addWidget(widgetType, pName, this.properties[pName], (v) => {
                            if (v === "✏️ Custom...") {
                                const newVal = prompt(`Enter custom value for ${pName}:`, "");
                                if (newVal !== null && newVal !== "") {
                                    this.properties[pName] = newVal;
                                    w.value = newVal;
                                } else {
                                    w.value = this.properties[pName];
                                }
                            } else {
                                this.properties[pName] = v;
                            }
                            isDirty.value = true;
                        }, options);

                        if (pInfo.link) {
                            const isPrompt = pName === "prompt_name" || pName === "prompt";
                            const btnLabel = isPrompt ? "Open Prompt" : "Open " + pName;
                            const self = this;
                            this.addWidget("button", btnLabel, "", () => {
                                let url = pInfo.link;
                                if (isPrompt) {
                                    const val = self.properties[pName];
                                    if (val) url = url.replace(/\/+$/, "") + "/" + encodeURIComponent(val);
                                }
                                window.open(url, "_blank");
                            });
                        }
                    });
                }

                // Ensure widgets stay in sync with properties when loading/configuring
                this.onPropertyChanged = function (name, value) {
                    if (this.widgets) {
                        const pInfo = nodeData.properties ? nodeData.properties[name] : null;

                        if (pInfo && pInfo.options) {
                            if (pInfo.type === "multi_select" || name === "tool_sets") {
                                const prevValue = Array.isArray(value) ? value : [];
                                const filteredValue = prevValue.filter(v => pInfo.options.includes(v));
                                if (filteredValue.length !== prevValue.length) {
                                    this.properties[name] = filteredValue;
                                    value = filteredValue;
                                }
                            } else if (pInfo.type === "enum" || pInfo.type === "combo") {
                                // Allow custom/arbitrary values
                            }
                        }

                        this.widgets.forEach(w => {
                            if (w.name === name || (w.options && w.options.property === name)) {
                                w.value = value;
                            }
                            if (w.is_display_for === name) {
                                if (w.type === "info_list") {
                                    w.value = Array.isArray(value) ? value : [];
                                } else if (w.type === "json_display" || w.type === "json_editor") {
                                    w.value = value;
                                } else {
                                    const list = (Array.isArray(value) && value.length > 0) ? value.join(", ") : "None";
                                    w.value = "[" + list + "]";
                                }
                            }
                        });
                    }

                    // Rebuild outputs dynamically when route_options change (DynamicRouter)
                    if (name === "route_options" && nodeData.type === "router") {
                        let opts = [];
                        if (Array.isArray(value)) {
                            opts = value;
                        } else if (typeof value === "string") {
                            try { opts = JSON.parse(value); } catch (e) { opts = []; }
                        }
                        if (!opts.includes("OTHER")) opts = [...opts, "OTHER"];

                        // Skip rebuild if the current outputs already have the same set of
                        // names — this happens during graph.configure() where LiteGraph has
                        // already restored the correct outputs (with link data) before firing
                        // onPropertyChanged. Rebuilding here would destroy those link associations.
                        const currentNames = new Set((this.outputs || []).map(o => o.name));
                        const expectedNames = new Set(opts);
                        const sameSet = currentNames.size === expectedNames.size &&
                            [...expectedNames].every(n => currentNames.has(n));
                        if (sameSet) return true;

                        while (this.outputs && this.outputs.length > 0) {
                            this.removeOutput(0);
                        }
                        opts.forEach(opt => {
                            if (typeof opt === "string" && opt.trim()) {
                                this.addOutput(opt.trim(), "state");
                            }
                        });
                        this.size[1] = Math.max(80, 80 + opts.length * 20);
                        this.setDirtyCanvas && this.setDirtyCanvas(true, true);
                    }

                    isDirty.value = true;
                    return true;
                };

                if (nodeData.type === "router") {
                    let initOpts = (nodeData.route_options && nodeData.route_options.length > 0)
                        ? [...nodeData.route_options] : [];
                    // Only add OTHER fallback for routers that have a user-editable
                    // route_options property (e.g. DynamicRouter). Routers with fixed
                    // outputs (e.g. SubgraphNodeCompletionRouter) already include
                    // their full set of options from get_route_options().
                    const hasDynamicRouteOptions = nodeData.properties && "route_options" in nodeData.properties;
                    if (hasDynamicRouteOptions && !initOpts.includes("OTHER")) initOpts.push("OTHER");
                    initOpts.forEach(opt => {
                        this.addOutput(opt, "state");
                    });
                    this.size = [240, 80 + (initOpts.length * 20)];
                } else {
                    this.addOutput("Out", "state");
                    const propCount = Object.keys(nodeData.properties || {}).length;
                    const linkCount = Object.values(nodeData.properties || {}).filter(p => p.link).length;
                    let outputKeysHeight = 0;
                    if (nodeData.output_keys && nodeData.output_keys.length > 0) {
                        const tmpCtx = document.createElement("canvas").getContext("2d");
                        tmpCtx.font = "9px monospace";
                        const margin = 10;
                        const gap = 4;
                        let x = margin;
                        let lines = 1;
                        nodeData.output_keys.forEach(key => {
                            const pillW = tmpCtx.measureText(key).width + 10;
                            if (x + pillW > 260 - margin) { x = margin; lines++; }
                            x += pillW + gap;
                        });
                        outputKeysHeight = lines * 17 + 4;
                    }
                    this.size = [260, 80 + (propCount * 30) + (linkCount * 30) + outputKeysHeight];
                }

                if (nodeData.output_keys && nodeData.output_keys.length > 0) {
                    const keys = nodeData.output_keys;
                    this.addCustomWidget({
                        type: "output_keys",
                        name: "output_keys",
                        value: keys,
                        draw(ctx, node, widget_width, y) {
                            ctx.save();
                            const margin = 10;
                            const pillH = 13;
                            const lineH = 17;
                            const gap = 4;

                            ctx.font = "9px monospace";

                            let x = margin;
                            let currentY = y + 4;
                            this.value.forEach(key => {
                                const pillW = ctx.measureText(key).width + 10;
                                if (x + pillW > widget_width - margin) {
                                    x = margin;
                                    currentY += lineH;
                                }
                                ctx.fillStyle = "#0f2a1a";
                                ctx.strokeStyle = "#2a5a3a";
                                ctx.lineWidth = 0.75;
                                ctx.beginPath();
                                ctx.roundRect(x, currentY, pillW, pillH, 3);
                                ctx.fill();
                                ctx.stroke();

                                ctx.fillStyle = "#5aaa70";
                                ctx.textAlign = "center";
                                ctx.fillText(key, x + pillW / 2, currentY + 9);
                                x += pillW + gap;
                            });
                            ctx.restore();
                        },
                        computeSize(width) {
                            const offscreen = document.createElement("canvas").getContext("2d");
                            offscreen.font = "9px monospace";
                            const margin = 10;
                            const gap = 4;
                            let x = margin;
                            let lines = 1;
                            (this.value || []).forEach(key => {
                                const pillW = offscreen.measureText(key).width + 10;
                                if (x + pillW > width - margin) { x = margin; lines++; }
                                x += pillW + gap;
                            });
                            // Exact fit: 4px top offset + 13px pill = 17px per line.
                            // LiteGraph adds its own 4px inter-widget gap on top of this,
                            // so the pill still has visual breathing room at the node bottom.
                            return [width, lines * 17];
                        },
                        mouse() { return false; },
                        serialize: false,
                    });
                }
            }
            const fullType = `${nodeData.category}/${nodeData.name}`;
            LiteGraph.registerNodeType(fullType, CustomNode);
            nodeTypeMetadata[fullType] = {
                origin: nodeData.origin || "builtin",
                source_url: nodeData.source_url || null,
                module_id: nodeData.module_id || null,
            };
        });
        console.log(`[Init] Registered ${data.nodes.length} dynamic nodes.`);
    } catch (e) {
        console.error("Failed to fetch nodes", e);
        if (retries > 0) {
            addLog(`System: Failed to fetch nodes. Retrying in 1s... (${retries} left)`, "warning");
            await new Promise(resolve => setTimeout(resolve, 1000));
            return fetchNodes(canvas, isDirty, retries - 1);
        }
        addLog("System: Failed to load node definitions. Please check if the server is running.", "error");
    }
}

/**
 * Fetches the list of available graphs from the backend and populates the selector.
 */
export async function fetchGraphList(graph, canvas, isDirty, loadGraphFn, retries = 5, reloadCurrent = true) {
    try {
        const res = await fetch(`${API_BASE}/list_graphs`);
        if (!res.ok) throw new Error(`HTTP error! status: ${res.status}`);
        const data = await res.json();

        // Pass folder tree to the explorer panel (if initialized)
        window.__graphExplorer?.setTree(data.tree || []);

        // Feed flat list to the subgraph node widget, then re-validate all subgraph nodes
        setAvailableGraphs(data.graphs);
        revalidateSubgraphNodes(graph);

        const select = document.getElementById("graph-selector");
        if (!select) return;

        const currentVal = select.value;

        const urlParams = new URLSearchParams(window.location.search);
        const urlGraph = urlParams.get('graph');
        const lastGraph = localStorage.getItem("stackflow_last_graph");

        let graphToLoad = null;
        if (urlGraph && data.graphs.includes(urlGraph)) {
            graphToLoad = urlGraph;
        } else if (currentVal && data.graphs.includes(currentVal)) {
            graphToLoad = currentVal;
        } else if (lastGraph && data.graphs.includes(lastGraph)) {
            graphToLoad = lastGraph;
        } else if (data.graphs.length > 0) {
            graphToLoad = data.graphs[0];
        }

        if (graphToLoad) {
            const inSubgraph = new URLSearchParams(window.location.search).get('subgraph_node');
            if (inSubgraph) {
                // Inside a subgraph — load the subgraph file, not the root graph
                const segments = inSubgraph.split('@@');
                const lastSeg = segments[segments.length - 1];
                const match = lastSeg.match(/^.*?\((.*?)\)$/);
                const subgraphFile = match ? match[1] : graphToLoad;
                select.value = subgraphFile;
                if (reloadCurrent) {
                    await loadGraphFn(subgraphFile);
                }
            } else {
                select.value = graphToLoad;
                localStorage.setItem("stackflow_last_graph", graphToLoad);
                if (reloadCurrent) {
                    await loadGraphFn(graphToLoad);
                }
            }
        }

    } catch (e) {
        console.error("Failed to fetch graphs", e);
        if (retries > 0) {
            await new Promise(resolve => setTimeout(resolve, 1000));
            return fetchGraphList(graph, canvas, isDirty, loadGraphFn, retries - 1, reloadCurrent);
        }
    }
}

/**
 * Loads a specific graph from the backend.
 */
export async function loadGraph(graph, isDirty, graphName, canvas = null) {
    if (!graphName) return;
    try {
        const res = await fetch(`${API_BASE}/get_graph/${graphName}`);
        if (!res.ok) return;
        const data = await res.json();
        if (data) {
            cancelPendingSaves();
            setHistoryBlock(true);
            graph.configure(data);
            setHistoryBlock(false);

            // Restore saved viewport (pan + zoom), or fit-to-content if absent
            if (canvas?.ds) {
                const vp = data._viewport;
                if (vp && Array.isArray(vp.offset) && typeof vp.scale === "number") {
                    canvas.ds.offset[0] = vp.offset[0];
                    canvas.ds.offset[1] = vp.offset[1];
                    canvas.ds.scale     = vp.scale;
                } else {
                    fitViewportToNodes(canvas, graph);
                }
                canvas.setDirty(true, true);
            }

            clearHistory();
            cancelPendingSaves();

            // Validate properties.name matches node type
            if (graph._nodes) {
                // Collect missing node types (unregistered in LiteGraph)
                const missingTypes = new Set();
                graph._nodes.forEach(node => {
                    if (node.type && !node.type.startsWith('langgraph/')) {
                        if (!LiteGraph.registered_node_types[node.type]) {
                            missingTypes.add(node.type);
                        }
                    }
                });

                if (missingTypes.size > 0) {
                    const missingList = [...missingTypes].sort().join(", ");
                    const warnMsg = `⚠️ Missing node types not in registry: ${missingList}`;
                    console.warn(warnMsg);
                    addLog(warnMsg, "warning");
                    // Suggest installing missing modules (async, non-blocking)
                    suggestMissingModules(missingTypes, data._module_deps || null);
                }

                graph._nodes.forEach(node => {
                    if (node.type && node.properties && node.properties.name) {
                        const typeParts = node.type.split('/');
                        const expectedName = typeParts[typeParts.length - 1];

                        if (node.type.startsWith('langgraph/')) return;

                        if (node.properties.name !== expectedName) {
                            const errorMsg = `❌ Node validation error: Node "${node.title}" (ID: ${node.id}) has type "${node.type}" but properties.name is "${node.properties.name}". Expected "${expectedName}".`;
                            console.error(errorMsg);
                            addLog(errorMsg, "error");
                            node.color = "#dc2626";
                        }
                    }
                });
            }

            // Final cleanup: cancel any timers that LiteGraph hooks may have
            // scheduled during node validation / configure, and ensure the
            // freshly-loaded graph is not marked dirty.
            cancelPendingSaves();
            isDirty.value = false;
        } else {
            graph.clear();
        }
    } catch (e) {
        console.error("Failed to load graph", e);
    }
}

/**
 * Switches to a different graph, managing URL state and breadcrumbs.
 */
export async function switchGraph(graph, canvas, isDirty, graphName, pushHistory = true, subgraphNode = null, fullPathOverride = null, isInline = false) {
    const selector = document.getElementById("graph-selector");
    const sessionId = getSessionId();

    if (isDirty.value && !confirm("You have unsaved changes. Discard and load anyway?")) {
        return;
    }

    if (!subgraphNode && fullPathOverride === null) {
        // Root-level graph switch — update last-graph bookmark
        localStorage.setItem("stackflow_last_graph", graphName);
    }

    // Always update the selector to reflect the currently displayed graph
    // so that saving targets the correct file.
    if (selector) selector.value = graphName;

    if (pushHistory) {
        const url = new URL(window.location);

        const currentThreadId = new URLSearchParams(window.location.search).get('thread_id');
        if (subgraphNode || fullPathOverride) {
            // Navigating into/within subgraphs — keep root graph unchanged
            if (currentThreadId) {
                url.searchParams.set('thread_id', currentThreadId);
            }
        } else {
            // Switching to a new root graph
            url.searchParams.set('graph', graphName);
            const newGraphBase = graphName.replace(".json", "");
            const newThreadId = `${newGraphBase}_${sessionId}`;
            url.searchParams.set('thread_id', newThreadId);
        }

        if (fullPathOverride !== null) {
            if (fullPathOverride) {
                url.searchParams.set('subgraph_node', fullPathOverride);
            } else {
                url.searchParams.delete('subgraph_node');
            }
        } else if (subgraphNode) {
            const nsSegment = `${subgraphNode}(${graphName})`;
            const currentPath = url.searchParams.get('subgraph_node');
            if (currentPath) {
                const sep = "@@";
                const segments = currentPath.split('@@');
                if (segments[segments.length - 1] !== nsSegment) {
                    url.searchParams.set('subgraph_node', `${currentPath}${sep}${nsSegment}`);
                }
            } else {
                url.searchParams.set('subgraph_node', nsSegment);
            }
        } else {
            url.searchParams.delete('subgraph_node');
        }

        window.history.pushState({ graphName }, "", url);
    }

    await loadGraph(graph, isDirty, graphName, canvas);
    updateBreadcrumbs(graph, canvas, isDirty);
    reloadLogs(graph, canvas);

    // Safety net: after all graph-switching operations complete, ensure the
    // freshly-loaded graph starts with a clean dirty state.  Stale timers from
    // onPropertyChanged / afterChange hooks during configure() can otherwise
    // race and flip isDirty back to true.
    cancelPendingSaves();
    isDirty.value = false;
}

/**
 * Updates the breadcrumb navigation bar.
 */
export function updateBreadcrumbs(graph, canvas, isDirty) {
    const bar = document.getElementById("breadcrumb-bar");
    const container = document.getElementById("breadcrumbs");
    const toolbar = document.querySelector(".toolbar");
    if (!container || !bar || !toolbar) return;

    const urlParams = new URLSearchParams(window.location.search);
    const threadId = urlParams.get('thread_id');
    const nsPath = urlParams.get('subgraph_node');

    container.innerHTML = "";

    const currentGraph = urlParams.get('graph') || localStorage.getItem("stackflow_last_graph");

    if (!threadId && !currentGraph) {
        bar.style.display = "none";
        toolbar.style.borderRadius = "8px";
        return;
    }

    let rootFullName = "";
    if (currentGraph) {
        rootFullName = currentGraph;
    } else if (threadId) {
        const rootName = threadId.split('_').slice(0, -1).join('_') || threadId;
        rootFullName = rootName.endsWith('.json') ? rootName : `${rootName}.json`;
    }

    const createCrumb = (text, isLast, onClick) => {
        const span = document.createElement("span");
        span.textContent = text;
        span.className = "breadcrumb-segment";
        span.style.cursor = onClick ? "pointer" : "default";
        span.style.color = isLast ? "#eee" : "#3b82f6";
        if (onClick) span.onclick = onClick;
        return span;
    };

    // 1. Root Crumb
    const isRootLast = !nsPath;
    container.appendChild(createCrumb(rootFullName, isRootLast, isRootLast ? null : () => {
        switchGraph(graph, canvas, isDirty, rootFullName, true, null, "");
    }));

    // 2. Subgraph segments
    if (nsPath) {
        const segments = nsPath.split('@@');
        segments.forEach((seg, i) => {
            const sep = document.createElement("span");
            sep.textContent = " > ";
            sep.style.margin = "0 4px";
            sep.style.opacity = "0.5";
            container.appendChild(sep);

            const isLast = (i === segments.length - 1);

            const match = seg.match(/^(.*?)\((.*?)\)$/);
            const levelGraph = match ? match[2] : rootFullName;
            const levelPath = segments.slice(0, i + 1).join('@@');

            let displayText = seg;
            if (match) {
                const ns = match[1];
                const gBase = match[2].replace(".json", "");
                displayText = `${ns} (${gBase})`;
            }

            container.appendChild(createCrumb(displayText, isLast, isLast ? null : () => {
                switchGraph(graph, canvas, isDirty, levelGraph, true, null, levelPath);
            }));
        });
    }

    // Toggle visibility
    if (container.children.length > 0) {
        bar.style.display = "flex";
        toolbar.style.borderRadius = "8px 8px 0 0";
    } else {
        bar.style.display = "none";
        toolbar.style.borderRadius = "8px";
    }
}

/**
 * Opens the full Package Manager overlay.
 */
export async function openPackageManager(refreshNodes) {
    // Overlay
    const overlay = document.createElement("div");
    overlay.className = "pm-overlay";

    const modal = document.createElement("div");
    modal.className = "pm-modal";

    // ── Header ──────────────────────────────────────────────────────
    const header = document.createElement("div");
    header.className = "pm-header";

    const title = document.createElement("h2");
    title.textContent = "📦 Package Manager";

    const searchWrap = document.createElement("div");
    searchWrap.className = "pm-search-wrap";
    searchWrap.innerHTML = `<span>🔍</span>`;
    const searchInput = document.createElement("input");
    searchInput.className = "pm-search";
    searchInput.type = "text";
    searchInput.placeholder = "Search modules…";
    searchWrap.appendChild(searchInput);

    const closeBtn = document.createElement("button");
    closeBtn.className = "pm-close";
    closeBtn.textContent = "✕";
    closeBtn.addEventListener("click", () => overlay.remove());

    header.appendChild(title);
    header.appendChild(searchWrap);
    header.appendChild(closeBtn);

    // ── Body ─────────────────────────────────────────────────────────
    const body = document.createElement("div");
    body.className = "pm-body";

    const stats = document.createElement("div");
    stats.className = "pm-stats";
    stats.textContent = "Loading…";

    const grid = document.createElement("div");
    grid.className = "pm-grid";

    // ── Install from GitHub bar ──────────────────────────────────────
    const ghRow = document.createElement("div");
    ghRow.style.cssText = "display:flex;align-items:center;gap:8px;margin-bottom:10px;";

    const ghInput = document.createElement("input");
    ghInput.type = "text";
    ghInput.className = "pm-search";
    ghInput.placeholder = "🔗 Install from GitHub URL…";
    ghInput.style.cssText = "flex:1;font-size:11px;";

    const ghBtn = document.createElement("button");
    ghBtn.textContent = "Install";
    ghBtn.style.cssText = "padding:4px 12px;background:#3b82f6;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:11px;white-space:nowrap;";

    const ghMsg = document.createElement("span");
    ghMsg.style.cssText = "font-size:10px;min-width:0;flex-shrink:1;";

    ghRow.appendChild(ghInput);
    ghRow.appendChild(ghBtn);
    ghRow.appendChild(ghMsg);

    const doGhInstall = async () => {
        const url = ghInput.value.trim();
        if (!url) return;
        ghBtn.disabled = true;
        ghBtn.textContent = "Installing…";
        ghMsg.style.color = "#aaa";
        ghMsg.textContent = "";
        try {
            const res = await fetch(`${API_BASE}/modules/install-from-github`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
            ghMsg.style.color = "#10b981";
            ghMsg.textContent = `✓ ${data.name || data.id} installed`;
            ghInput.value = "";
            // Refresh module list
            const listRes = await fetch(`${API_BASE}/modules`);
            const listData = await listRes.json();
            allModules = listData.modules || [];
            updateStats();
            renderGrid(filterModules(searchInput.value));
            if (data.needs_restart) showRestartBanner();
        } catch (e) {
            ghMsg.style.color = "#ef4444";
            ghMsg.textContent = `✗ ${e.message}`;
        } finally {
            ghBtn.disabled = false;
            ghBtn.textContent = "Install";
        }
    };

    ghBtn.addEventListener("click", doGhInstall);
    ghInput.addEventListener("keydown", (e) => { if (e.key === "Enter") doGhInstall(); });

    // ── Restart notice (mirrors the global banner state) ───────────
    const pmRestartNotice = document.createElement("div");
    pmRestartNotice.className = "pm-restart-banner";
    pmRestartNotice.style.display = _restartRequired ? "flex" : "none";

    const pmRestartSpan = document.createElement("span");
    pmRestartSpan.textContent = "⚠ Server restart required for changes to take effect.";

    const pmRestartBtn = document.createElement("button");
    pmRestartBtn.textContent = "Restart Now";
    pmRestartBtn.addEventListener("click", async () => {
        pmRestartBtn.disabled = true;
        pmRestartBtn.textContent = "Restarting…";
        try {
            await fetch(`${API_BASE}/restart`, { method: "POST" });
            pmRestartBtn.textContent = "Reloading…";
            _clearRestartRequired();
            setTimeout(() => location.reload(), 3000);
        } catch {
            _clearRestartRequired();
            setTimeout(() => location.reload(), 5000);
        }
    });

    pmRestartNotice.appendChild(pmRestartSpan);
    pmRestartNotice.appendChild(pmRestartBtn);

    function showRestartBanner() {
        showRestartRequired();  // show the global toolbar banner
        pmRestartNotice.style.display = "flex";  // also show inside PM
    }

    body.appendChild(stats);
    body.appendChild(pmRestartNotice);
    body.appendChild(ghRow);
    body.appendChild(grid);

    // ── Footer — Reinstall button ───────────────────────────────────
    const footer = document.createElement("div");
    footer.style.cssText = "display:flex;justify-content:flex-end;padding:10px 16px;border-top:1px solid #333;";

    const reinstallBtn = document.createElement("button");
    reinstallBtn.textContent = "📦 Reinstall Dependencies";
    reinstallBtn.style.cssText = "padding:6px 16px;background:#444;color:#ccc;border:1px solid #555;border-radius:5px;cursor:pointer;font-size:11px;font-weight:600;";
    reinstallBtn.addEventListener("click", async () => {
        if (!confirm("Reinstall all pip dependencies and restart the server?")) return;
        reinstallBtn.disabled = true;
        reinstallBtn.textContent = "Installing…";
        try {
            const res = await fetch(`${API_BASE}/reinstall`, { method: "POST" });
            if (res.ok) {
                const data = await res.json();
                reinstallBtn.textContent = "Restarting…";
                if (data.errors?.length) {
                    console.warn("Reinstall errors:", data.errors);
                }
                setTimeout(() => location.reload(), 5000);
            } else {
                const errData = await res.json().catch(() => ({}));
                alert(`Reinstall failed: ${errData.detail || res.status}`);
                reinstallBtn.disabled = false;
                reinstallBtn.textContent = "📦 Reinstall Dependencies";
            }
        } catch {
            // Server restarting — connection lost is expected
            setTimeout(() => location.reload(), 8000);
        }
    });
    footer.appendChild(reinstallBtn);

    modal.appendChild(header);
    modal.appendChild(body);
    modal.appendChild(footer);
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    // ── Loading skeleton ────────────────────────────────────────────
    function showSkeleton() {
        grid.innerHTML = "";
        for (let i = 0; i < 6; i++) {
            const card = document.createElement("div");
            card.className = "pm-card pm-skeleton-card";
            card.innerHTML = `
                <div class="pm-skeleton-line" style="width:60%;height:14px;margin-bottom:10px;"></div>
                <div class="pm-skeleton-line" style="width:90%;height:10px;margin-bottom:6px;"></div>
                <div class="pm-skeleton-line" style="width:75%;height:10px;margin-bottom:12px;"></div>
                <div style="display:flex;gap:4px;">
                    <div class="pm-skeleton-line" style="width:50px;height:16px;border-radius:3px;"></div>
                    <div class="pm-skeleton-line" style="width:60px;height:16px;border-radius:3px;"></div>
                </div>
            `;
            grid.appendChild(card);
        }
    }
    showSkeleton();

    // ── Fetch & render ───────────────────────────────────────────────
    const _updateCache = {};  // moduleId → {remote_sha, update_available}
    let allModules = [];
    try {
        const res = await fetch(`${API_BASE}/modules`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        allModules = data.modules || [];
    } catch (e) {
        grid.innerHTML = `<div class="pm-empty">Could not load modules.</div>`;
        stats.textContent = "";
        return;
    }

    function renderGrid(modules) {
        grid.innerHTML = "";
        if (modules.length === 0) {
            grid.innerHTML = `<div class="pm-empty">No modules match your search.</div>`;
            return;
        }
        modules.forEach(mod => {
            const card = document.createElement("div");
            card.className = "pm-card";
            card.style.borderLeftColor = mod.color || "#444";

            const top = document.createElement("div");
            top.className = "pm-card-top";

            const name = document.createElement("span");
            name.className = "pm-card-name";
            name.textContent = mod.name;

            const originBadge = document.createElement("span");
            originBadge.className = `pm-card-badge pm-card-badge-origin ${mod.origin === "external" ? "external" : "builtin"}`;
            originBadge.textContent = mod.origin === "external" ? "external" : "built-in";

            const badge = document.createElement("span");
            badge.className = `pm-card-badge ${mod.installed ? "installed" : "not-installed"}`;
            badge.textContent = mod.installed ? "installed" : "not installed";

            top.appendChild(name);
            top.appendChild(originBadge);

            if (mod.installed && mod.load_error) {
                const lerr = document.createElement("span");
                lerr.className = "pm-card-badge pm-card-badge-load-error";
                lerr.title = "Module failed to load (Python import error) — click View for details";
                lerr.textContent = "load error";
                top.appendChild(lerr);
            } else if (mod.installed && mod.has_warnings) {
                const warn = document.createElement("span");
                warn.className = "pm-card-badge pm-card-badge-error";
                warn.title = "Some setup requirements are not met — click View for details";
                warn.textContent = "error";
                top.appendChild(warn);
            }

            top.appendChild(badge);

            // Version row — SHA for external, semver for built-in
            const versionRow = document.createElement("div");
            versionRow.className = "pm-card-version-row";
            if (mod.git_sha) {
                const localLabel = document.createElement("span");
                localLabel.className = "pm-card-version-label";
                localLabel.textContent = "installed";
                const localSha = document.createElement("span");
                localSha.className = "pm-card-sha";
                localSha.title = mod.git_sha;
                localSha.textContent = mod.git_sha.slice(0, 7);

                const cached = _updateCache[mod.id];
                const latestLabel = document.createElement("span");
                latestLabel.className = "pm-card-version-label";
                latestLabel.textContent = "latest";
                const remoteSha = document.createElement("span");
                remoteSha.className = cached?.update_available ? "pm-card-sha pm-card-sha-new" : "pm-card-sha";
                remoteSha.textContent = cached?.remote_sha || "…";
                remoteSha.dataset.latestSha = mod.id;

                versionRow.appendChild(localLabel);
                versionRow.appendChild(localSha);
                versionRow.appendChild(latestLabel);
                versionRow.appendChild(remoteSha);
            } else {
                const ver = document.createElement("span");
                ver.className = "pm-card-version";
                ver.textContent = `v${mod.version}`;
                versionRow.appendChild(ver);
            }

            const desc = document.createElement("div");
            desc.className = "pm-card-desc";
            desc.textContent = mod.description || "";

            // Node chips (max 3 in card view)
            const nodes = mod.nodes || [];
            card.appendChild(top);
            card.appendChild(versionRow);
            card.appendChild(desc);
            if (nodes.length > 0) {
                const chipsWrap = document.createElement("div");
                chipsWrap.className = "pm-card-chips";
                const visible = nodes.slice(0, 3);
                visible.forEach(nodeName => {
                    const chip = document.createElement("span");
                    chip.className = "pm-card-chip";
                    chip.textContent = nodeName;
                    chipsWrap.appendChild(chip);
                });
                if (nodes.length > 3) {
                    const more = document.createElement("span");
                    more.className = "pm-card-chip pm-card-chip-more";
                    more.textContent = `+${nodes.length - 3} more`;
                    chipsWrap.appendChild(more);
                }
                card.appendChild(chipsWrap);
            }

            const footer = document.createElement("div");
            footer.className = "pm-card-footer";

            const nodeCount = document.createElement("span");
            nodeCount.className = "pm-card-nodes";
            const n = nodes.length;
            nodeCount.textContent = `${n} node${n !== 1 ? "s" : ""}`;

            const viewBtn = document.createElement("button");
            viewBtn.className = "pm-card-view";
            viewBtn.textContent = "View";
            viewBtn.addEventListener("click", () => {
                openModuleModal(mod.id, () => {
                    // Refresh PM grid
                    fetch(`${API_BASE}/modules`)
                        .then(r => r.json())
                        .then(d => {
                            allModules = d.modules || [];
                            renderGrid(filterModules(searchInput.value));
                            updateStats();
                        })
                        .catch(() => {});
                }, showRestartBanner);
            });

            footer.appendChild(nodeCount);
            footer.appendChild(viewBtn);

            card.appendChild(footer);
            grid.appendChild(card);
        });
    }

    function filterModules(query) {
        const q = query.toLowerCase().trim();
        if (!q) return allModules;
        return allModules.filter(m =>
            m.name.toLowerCase().includes(q) ||
            (m.description || "").toLowerCase().includes(q)
        );
    }

    function updateStats() {
        const installed = allModules.filter(m => m.installed).length;
        stats.textContent = `${allModules.length} modules · ${installed} installed`;
    }

    updateStats();
    renderGrid(allModules);

    // ── Async update check — writes to cache, patches DOM in-place ──
    const hasExternal = allModules.some(m => m.origin === "external" && m.installed);
    if (hasExternal) {
        fetch(`${API_BASE}/modules/check-updates`)
            .then(r => r.ok ? r.json() : null)
            .then(data => {
                if (!data?.updates) return;
                for (const [id, info] of Object.entries(data.updates)) {
                    if (info.error) continue;
                    _updateCache[id] = info;
                    // Patch the element in-place — no grid re-render
                    const el = grid.querySelector(`[data-latest-sha="${id}"]`);
                    if (el) {
                        el.textContent = info.remote_sha || "?";
                        el.className = info.update_available ? "pm-card-sha pm-card-sha-new" : "pm-card-sha";
                    }
                }
            })
            .catch(() => {}); // silent — update check is best-effort
    }

    searchInput.addEventListener("input", () => renderGrid(filterModules(searchInput.value)));
    searchInput.focus();

    // ── Close behaviour ──────────────────────────────────────────────
    const onKey = (e) => {
        if (e.key === "Escape") { overlay.remove(); document.removeEventListener("keydown", onKey); }
    };
    document.addEventListener("keydown", onKey);
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
}

/**
 * Opens the module detail modal for a given module ID.
 */
async function openModuleModal(moduleId, onDone, onNeedsRestart) {
    let mod;
    try {
        const res = await fetch(`${API_BASE}/modules/${moduleId}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        mod = await res.json();
    } catch (e) {
        console.warn("Failed to fetch module detail", e);
        return;
    }

    // Overlay
    const overlay = document.createElement("div");
    overlay.className = "mod-modal-overlay";

    const card = document.createElement("div");
    card.className = "mod-modal";
    card.style.borderTopColor = mod.color || "#666";

    // ── Header ──────────────────────────────────────────────────────
    const header = document.createElement("div");
    header.className = "mod-modal-header";
    header.style.borderTopColor = mod.color || "#666";
    const versionChip = mod.git_sha
        ? `<span class="mod-modal-sha" title="Installed commit: ${mod.git_sha}">${mod.git_sha.slice(0, 7)}</span>`
        : `<span class="mod-modal-version">v${mod.version}</span>`;
    header.innerHTML = `
        <h3>${mod.name}</h3>
        ${versionChip}
    `;
    // Shared close — always refreshes the grid so badges update
    function closeModal() {
        overlay.remove();
        document.removeEventListener("keydown", onKey, { capture: true });
        onDone?.();
    }

    const closeBtn = document.createElement("button");
    closeBtn.className = "mod-modal-close";
    closeBtn.textContent = "✕";
    closeBtn.addEventListener("click", closeModal);
    header.appendChild(closeBtn);

    // ── Body ─────────────────────────────────────────────────────────
    const body = document.createElement("div");
    body.className = "mod-modal-body";

    // Load error — shown prominently before anything else
    if (mod.load_error) {
        const errBox = document.createElement("div");
        errBox.className = "mod-load-error-box";

        const errTitle = document.createElement("div");
        errTitle.className = "mod-load-error-title";
        errTitle.textContent = "⚠ Module failed to load (Python import error)";

        const errPre = document.createElement("pre");
        errPre.className = "mod-load-error-pre";
        errPre.textContent = mod.load_error;

        errBox.appendChild(errTitle);
        errBox.appendChild(errPre);
        body.appendChild(errBox);
    }

    // Description
    const desc = document.createElement("p");
    desc.className = "mod-modal-desc";
    desc.textContent = mod.description || "";
    body.appendChild(desc);

    // Source URL for external modules
    if (mod.source_url) {
        const srcRow = document.createElement("div");
        srcRow.className = "mod-source-url";
        srcRow.innerHTML = `<span class="mod-section-title">Source</span> <a href="${mod.source_url}" target="_blank" rel="noopener">${mod.source_url}</a>`;
        body.appendChild(srcRow);
    }

    // Nodes
    if (mod.nodes && mod.nodes.length > 0) {
        const sec = document.createElement("div");
        sec.innerHTML = `<div class="mod-section-title">Nodes</div>`;
        const list = document.createElement("div");
        list.className = "mod-nodes-list";
        mod.nodes.forEach(n => {
            const chip = document.createElement("span");
            chip.className = "mod-node-chip";
            chip.textContent = n;
            list.appendChild(chip);
        });
        sec.appendChild(list);
        body.appendChild(sec);
    }

    // Env vars
    const envVars = mod.setup?.env_vars || [];
    const envInputs = {}; // key → <input>
    if (envVars.length > 0) {
        const sec = document.createElement("div");
        sec.innerHTML = `<div class="mod-section-title">Environment Variables</div>`;
        envVars.forEach(ev => {
            const row = document.createElement("div");
            row.className = "mod-env-row";

            const label = document.createElement("div");
            label.className = "mod-env-label";
            label.textContent = ev.name;

            const input = document.createElement("input");
            input.type = "text";
            input.className = "mod-env-input";
            input.placeholder = ev.set ? "already set — leave blank to keep" : (ev.optional ? "optional" : "required");
            envInputs[ev.name] = input;

            const status = document.createElement("span");
            status.className = `mod-env-status ${ev.set ? "set" : "unset"}`;
            status.textContent = ev.set ? "✓" : "○";

            row.appendChild(label);
            row.appendChild(input);
            row.appendChild(status);
            sec.appendChild(row);
        });
        body.appendChild(sec);
    }

    // Auth notes
    const authNotes = mod.setup?.auth_notes;
    if (authNotes) {
        const note = document.createElement("div");
        note.style.cssText = "font-size:11px;color:#94a3b8;background:#1e293b;border:1px solid #334155;border-radius:4px;padding:6px 8px;margin:4px 0 8px 0;";
        note.textContent = authNotes;
        body.appendChild(note);
    }

    // Setup steps
    const steps = mod.setup?.steps || [];
    if (steps.length > 0) {
        const sec = document.createElement("div");
        sec.innerHTML = `<div class="mod-section-title">Setup Steps</div>`;
        steps.forEach(step => {
            if (step.type === "check_command") {
                const row = document.createElement("div");
                row.className = "mod-step-row";
                const icon = document.createElement("span");
                icon.className = "mod-step-icon";
                icon.textContent = step.available ? "✓" : "✗";
                icon.style.color = step.available ? "#10b981" : "#ef4444";

                const text = document.createElement("div");
                text.className = "mod-step-text";
                const cmdLine = `<div class="mod-step-cmd">$ ${step.command}</div>`;
                const avail = step.available
                    ? `<div class="mod-step-available">Found</div>`
                    : `<div class="mod-step-missing">Not found — ${step.message || ""}</div>`;
                let hint = "";
                if (!step.available && step.install_hint) {
                    const h = step.install_hint;
                    if (h.macos) hint += `<div class="mod-step-hint">macOS: ${h.macos}</div>`;
                    if (h.linux) hint += `<div class="mod-step-hint">Linux: ${h.linux}</div>`;
                    if (h.windows) hint += `<div class="mod-step-hint">Windows: ${h.windows}</div>`;
                }
                text.innerHTML = cmdLine + avail + hint;

                row.appendChild(icon);
                row.appendChild(text);
                sec.appendChild(row);

            } else if (step.type === "check_connectivity") {
                const row = document.createElement("div");
                row.className = "mod-step-row";
                const icon = document.createElement("span");
                icon.className = "mod-step-icon";
                icon.textContent = step.available ? "✓" : "✗";
                icon.style.color = step.available ? "#10b981" : "#ef4444";

                const text = document.createElement("div");
                text.className = "mod-step-text";
                const cmdLine = `<div class="mod-step-cmd">$ ${step.command}</div>`;
                const status = step.available
                    ? `<div class="mod-step-available">Connected</div>`
                    : `<div class="mod-step-missing">Not connected — ${step.message || ""}</div>`;
                let extra = "";
                if (step.output) {
                    extra += `<div class="mod-step-hint" style="font-family:monospace;white-space:pre-wrap;">${step.output}</div>`;
                }
                if (!step.available && step.error_hint) {
                    extra += `<div class="mod-step-hint">${step.error_hint}</div>`;
                }
                text.innerHTML = cmdLine + status + extra;

                row.appendChild(icon);
                row.appendChild(text);
                sec.appendChild(row);

            } else if (step.type === "run_command" && step.interactive) {
                const row = document.createElement("div");
                row.className = "mod-manual-step";

                const lbl = document.createElement("div");
                lbl.className = "mod-manual-step-label";
                lbl.textContent = "Manual step — run in terminal";

                const cmdRow = document.createElement("div");
                cmdRow.className = "mod-manual-step-cmd";
                const cmdSpan = document.createElement("span");
                cmdSpan.textContent = `$ ${step.command}`;
                const copyBtn = document.createElement("button");
                copyBtn.className = "mod-copy-btn";
                copyBtn.textContent = "Copy";
                copyBtn.addEventListener("click", () => {
                    navigator.clipboard.writeText(step.command).catch(() => {});
                    copyBtn.textContent = "Copied!";
                    setTimeout(() => { copyBtn.textContent = "Copy"; }, 1500);
                });
                cmdRow.appendChild(cmdSpan);
                cmdRow.appendChild(copyBtn);

                row.appendChild(lbl);
                row.appendChild(cmdRow);
                if (step.message) {
                    const msg = document.createElement("div");
                    msg.style.cssText = "font-size:9px;color:#888;margin-top:3px;";
                    msg.textContent = step.message;
                    row.appendChild(msg);
                }
                sec.appendChild(row);
            }
        });
        body.appendChild(sec);
    }

    // Install notes
    const installNotes = mod.setup?.install_notes;
    if (installNotes) {
        const sec = document.createElement("div");
        sec.innerHTML = `<div class="mod-section-title">Install Notes</div>`;

        if (installNotes.summary) {
            const sum = document.createElement("div");
            sum.style.cssText = "font-size:11px;color:#ccc;margin-bottom:8px;";
            sum.textContent = installNotes.summary;
            sec.appendChild(sum);
        }

        if (installNotes.common?.length) {
            const lbl = document.createElement("div");
            lbl.style.cssText = "font-size:10px;color:#888;margin-bottom:4px;";
            lbl.textContent = "Common:";
            sec.appendChild(lbl);
            installNotes.common.forEach(cmd => {
                const row = document.createElement("div");
                row.className = "mod-manual-step-cmd";
                const cmdSpan = document.createElement("span");
                cmdSpan.textContent = `$ ${cmd}`;
                const copyBtn = document.createElement("button");
                copyBtn.className = "mod-copy-btn";
                copyBtn.textContent = "Copy";
                copyBtn.addEventListener("click", () => {
                    navigator.clipboard.writeText(cmd).catch(() => {});
                    copyBtn.textContent = "Copied!";
                    setTimeout(() => { copyBtn.textContent = "Copy"; }, 1500);
                });
                row.appendChild(cmdSpan);
                row.appendChild(copyBtn);
                sec.appendChild(row);
            });
        }

        // Render each sub-section (e.g. natten)
        for (const [key, val] of Object.entries(installNotes)) {
            if (key === "summary" || key === "common") continue;
            if (typeof val !== "object") continue;

            const sub = document.createElement("div");
            sub.style.cssText = "margin-top:10px;";

            const title = document.createElement("div");
            title.style.cssText = "font-size:11px;font-weight:600;color:#ddd;margin-bottom:4px;";
            title.textContent = key;
            sub.appendChild(title);

            if (val.description) {
                const desc = document.createElement("div");
                desc.style.cssText = "font-size:10px;color:#999;margin-bottom:6px;";
                desc.textContent = val.description;
                sub.appendChild(desc);
            }

            for (const platform of ["macos", "linux", "windows"]) {
                const instructions = val[platform];
                if (!instructions) continue;
                const platLbl = document.createElement("div");
                platLbl.style.cssText = "font-size:10px;color:#888;margin-top:4px;margin-bottom:2px;";
                platLbl.textContent = platform === "macos" ? "macOS:" : platform === "linux" ? "Linux:" : "Windows:";
                sub.appendChild(platLbl);

                const lines = Array.isArray(instructions) ? instructions : [instructions];
                lines.forEach(line => {
                    const row = document.createElement("div");
                    row.style.cssText = "font-size:10px;color:#bbb;font-family:monospace;white-space:pre-wrap;padding:3px 6px;background:#1a1a2e;border-radius:3px;margin-bottom:2px;";
                    row.textContent = line;
                    sub.appendChild(row);
                });
            }

            sec.appendChild(sub);
        }

        body.appendChild(sec);
    }

    // ── Configurations (generic, manifest-driven) ───────────────────
    if (mod.has_configurations) {
        let configsData = [];         // [{name, type, options, status}]
        let configTypesSchema = {};   // {type_key: {label, options:[...]}}
        let configLabel = "Configurations";

        const provSec = document.createElement("div");
        provSec.className = "mod-prov-section";

        // Header row
        const provHeader = document.createElement("div");
        provHeader.className = "mod-prov-header";

        const provTitle = document.createElement("div");
        provTitle.className = "mod-section-title";

        const provActions = document.createElement("div");
        provActions.className = "mod-prov-actions";

        const addBtn = document.createElement("button");
        addBtn.className = "mod-prov-add-btn";
        addBtn.textContent = "+ Add";

        const saveBtn = document.createElement("button");
        saveBtn.className = "mod-prov-save-btn";
        saveBtn.textContent = "Save";
        saveBtn.style.display = "none";

        provActions.appendChild(addBtn);
        provActions.appendChild(saveBtn);
        provHeader.appendChild(provTitle);
        provHeader.appendChild(provActions);
        provSec.appendChild(provHeader);

        const provList = document.createElement("div");
        provList.className = "mod-prov-list";
        provSec.appendChild(provList);

        let provDirty = false;

        function markProvDirty() {
            provDirty = true;
            saveBtn.style.display = "inline-block";
        }

        function buildConfigRow(p) {
            const row = document.createElement("div");
            row.className = "mod-prov-row";

            // Top line: name, type select, delete
            const topLine = document.createElement("div");
            topLine.className = "mod-prov-top";

            const nameInput = document.createElement("input");
            nameInput.type = "text";
            nameInput.className = "mod-prov-name";
            nameInput.value = p.name || "";
            nameInput.placeholder = "name";
            nameInput.addEventListener("input", markProvDirty);

            const typeSelect = document.createElement("select");
            typeSelect.className = "mod-prov-type";
            Object.entries(configTypesSchema).forEach(([key, tmeta]) => {
                const opt = document.createElement("option");
                opt.value = key;
                opt.textContent = tmeta.label || key;
                if (key === p.type) opt.selected = true;
                typeSelect.appendChild(opt);
            });

            const delBtn = document.createElement("button");
            delBtn.className = "mod-prov-del";
            delBtn.textContent = "✕";
            delBtn.addEventListener("click", () => { row.remove(); markProvDirty(); });

            topLine.appendChild(nameInput);
            topLine.appendChild(typeSelect);
            topLine.appendChild(delBtn);
            row.appendChild(topLine);

            // Status block — same mod-step-row layout as setup steps
            if (p.status) {
                const stepRow = document.createElement("div");
                stepRow.className = "mod-step-row mod-prov-status-detail";

                const icon = document.createElement("span");
                icon.className = "mod-step-icon";
                icon.textContent = p.status.available ? "✓" : "✗";
                icon.style.color = p.status.available ? "#10b981" : "#ef4444";

                const text = document.createElement("div");
                text.className = "mod-step-text";
                if (p.status.command) {
                    const cmdEl = document.createElement("div");
                    cmdEl.className = "mod-step-cmd";
                    cmdEl.textContent = `$ ${p.status.command}`;
                    text.appendChild(cmdEl);
                }
                const msgEl = document.createElement("div");
                msgEl.className = p.status.available ? "mod-step-available" : "mod-step-missing";
                msgEl.textContent = p.status.message || "";
                text.appendChild(msgEl);

                stepRow.appendChild(icon);
                stepRow.appendChild(text);
                row.appendChild(stepRow);
            }

            // Options area — rebuilt when type changes
            const optsDiv = document.createElement("div");
            optsDiv.className = "mod-prov-opts";
            row.appendChild(optsDiv);

            const SECRET_SENTINEL = "__SET__";

            function rebuildOptions() {
                optsDiv.innerHTML = "";
                const typeMeta = configTypesSchema[typeSelect.value] || {};
                (typeMeta.options || []).forEach(optDef => {
                    const optRow = document.createElement("div");
                    optRow.className = "mod-prov-opt-row";
                    const lbl = document.createElement("label");
                    lbl.className = "mod-prov-opt-label";
                    lbl.textContent = optDef.label;
                    const inp = document.createElement("input");
                    inp.type = optDef.secret ? "password" : "text";
                    inp.className = "mod-prov-opt-input";
                    inp.dataset.optKey = optDef.key;

                    const rawVal = (p.options || {})[optDef.key] || "";
                    if (optDef.secret && rawVal === SECRET_SENTINEL) {
                        inp.value = "";
                        inp.placeholder = "Key is set — paste to replace";
                        inp.dataset.secretSet = "1";
                    } else {
                        inp.value = rawVal;
                        inp.placeholder = optDef.placeholder || "";
                    }

                    inp.addEventListener("input", () => {
                        delete inp.dataset.secretSet;
                        markProvDirty();
                    });
                    optRow.appendChild(lbl);
                    optRow.appendChild(inp);
                    optsDiv.appendChild(optRow);
                });
            }

            typeSelect.addEventListener("change", () => { markProvDirty(); rebuildOptions(); });
            rebuildOptions();

            row._collect = () => {
                const opts = {};
                optsDiv.querySelectorAll("[data-opt-key]").forEach(inp => {
                    const val = inp.value.trim();
                    if (inp.dataset.secretSet === "1" && !val) {
                        opts[inp.dataset.optKey] = SECRET_SENTINEL;
                    } else if (val) {
                        opts[inp.dataset.optKey] = val;
                    }
                });
                return { name: nameInput.value.trim(), type: typeSelect.value, options: opts };
            };

            return row;
        }

        function renderConfigs() {
            provList.innerHTML = "";
            configsData.forEach(p => provList.appendChild(buildConfigRow(p)));
        }

        async function fetchAndRenderConfigs() {
            try {
                const res = await fetch(`${API_BASE}/modules/${mod.id}/configurations`);
                if (res.ok) {
                    const data = await res.json();
                    configsData = data.items || [];
                    configTypesSchema = data.types || {};
                    configLabel = data.label || "Configurations";
                    provTitle.textContent = configLabel;
                }
            } catch (e) {
                console.warn("Failed to fetch configurations", e);
            }
            renderConfigs();
        }

        // Fetch configurations from API on open
        await fetchAndRenderConfigs();

        addBtn.addEventListener("click", () => {
            const firstType = Object.keys(configTypesSchema)[0] || "";
            const newRow = buildConfigRow({ name: "", type: firstType, options: {}, status: null });
            provList.appendChild(newRow);
            markProvDirty();
            newRow.querySelector(".mod-prov-name")?.focus();
        });

        function validateRows(rows) {
            rows.forEach(row => {
                row.querySelectorAll(".mod-prov-error").forEach(el => el.remove());
                row.querySelectorAll(".mod-prov-field-error").forEach(el => el.classList.remove("mod-prov-field-error"));
            });

            let valid = true;
            const seenNames = new Set();

            rows.forEach(row => {
                if (!row._collect) return;
                const data = row._collect();
                const rowErrors = [];

                if (!data.name) {
                    row.querySelector(".mod-prov-name")?.classList.add("mod-prov-field-error");
                    rowErrors.push("Name is required");
                } else if (seenNames.has(data.name)) {
                    row.querySelector(".mod-prov-name")?.classList.add("mod-prov-field-error");
                    rowErrors.push(`Duplicate name "${data.name}"`);
                } else {
                    seenNames.add(data.name);
                }

                const typeMeta = configTypesSchema[data.type] || {};
                (typeMeta.options || []).forEach(optDef => {
                    if (optDef.required && !data.options[optDef.key]) {
                        const inp = row.querySelector(`[data-opt-key="${optDef.key}"]`);
                        inp?.classList.add("mod-prov-field-error");
                        rowErrors.push(`${optDef.label} is required`);
                    }
                });

                if (rowErrors.length > 0) {
                    valid = false;
                    const errEl = document.createElement("div");
                    errEl.className = "mod-prov-error";
                    errEl.textContent = rowErrors.join(" · ");
                    row.appendChild(errEl);
                }
            });

            return valid;
        }

        saveBtn.addEventListener("click", async () => {
            const rows = [...provList.querySelectorAll(".mod-prov-row")];

            if (!validateRows(rows)) {
                saveBtn.textContent = "Fix errors";
                setTimeout(() => { saveBtn.textContent = "Save"; }, 2000);
                return;
            }

            saveBtn.disabled = true;
            saveBtn.textContent = "Saving…";
            const items = rows.map(row => row._collect ? row._collect() : null).filter(p => p?.name);
            try {
                const res = await fetch(`${API_BASE}/modules/${mod.id}/configurations`, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ items }),
                });
                if (!res.ok) {
                    const err = await res.json().catch(() => ({}));
                    throw new Error(err.detail || `HTTP ${res.status}`);
                }
                saveBtn.textContent = "Saved!";
                provDirty = false;
                await fetchAndRenderConfigs();
                setTimeout(() => { saveBtn.textContent = "Save"; saveBtn.style.display = "none"; }, 1500);
            } catch (e) {
                console.warn("Failed to save configurations", e);
                saveBtn.textContent = "Error";
                const errEl = document.createElement("div");
                errEl.className = "mod-prov-save-error";
                errEl.textContent = e.message;
                saveBtn.parentElement?.after(errEl);
                setTimeout(() => { errEl.remove(); saveBtn.textContent = "Save"; }, 4000);
            }
            saveBtn.disabled = false;
        });

        body.appendChild(provSec);
    }

    // ── Footer ───────────────────────────────────────────────────────
    const footer = document.createElement("div");
    footer.className = "mod-modal-footer";

    function showSuccess(msg) {
        const el = document.createElement("div");
        el.className = "mod-success-msg";
        el.style.flex = "1";
        el.textContent = msg;
        footer.prepend(el);
    }

    if (mod.installed) {
        const uninstallBtn = document.createElement("button");
        uninstallBtn.className = "mod-btn mod-btn-danger";
        uninstallBtn.textContent = "Uninstall";
        uninstallBtn.addEventListener("click", async () => {
            uninstallBtn.disabled = true;
            uninstallBtn.textContent = "Uninstalling…";
            try {
                const res = await fetch(`${API_BASE}/modules/${mod.id}/uninstall`, { method: "POST" });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                if (data.needs_restart) onNeedsRestart?.();
            } catch (e) {
                console.warn("Uninstall failed", e);
            }
            closeModal();
        });

        // Save button for updating env vars on installed modules
        if (envVars.length > 0) {
            const saveBtn = document.createElement("button");
            saveBtn.className = "mod-btn mod-btn-primary";
            saveBtn.textContent = "Save";
            saveBtn.addEventListener("click", async () => {
                const payload = {};
                for (const [k, input] of Object.entries(envInputs)) {
                    if (input.value.trim()) payload[k] = input.value.trim();
                }
                if (Object.keys(payload).length === 0) return;
                saveBtn.disabled = true;
                saveBtn.textContent = "Saving…";
                try {
                    const res = await fetch(`${API_BASE}/modules/${mod.id}/env`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ env_vars: payload }),
                    });
                    if (!res.ok) throw new Error(`HTTP ${res.status}`);
                    showSuccess("Environment updated.");
                    // Update status indicators
                    for (const [k, input] of Object.entries(envInputs)) {
                        if (input.value.trim()) {
                            const statusEl = input.parentElement.querySelector(".mod-env-status");
                            if (statusEl) {
                                statusEl.className = "mod-env-status set";
                                statusEl.textContent = "✓";
                            }
                            input.value = "";
                            input.placeholder = "already set — leave blank to keep";
                        }
                    }
                } catch (e) {
                    console.warn("Save env failed", e);
                }
                saveBtn.disabled = false;
                saveBtn.textContent = "Save";
            });
            footer.appendChild(saveBtn);
        }

        // Update button — only for externally installed modules (have a source URL)
        if (mod.source_url) {
            const updateBtn = document.createElement("button");
            updateBtn.className = "mod-btn mod-btn-update";
            updateBtn.textContent = "↑ Update";
            updateBtn.addEventListener("click", async () => {
                updateBtn.disabled = true;
                updateBtn.textContent = "Checking…";
                try {
                    // Step 1: lightweight check via ls-remote (~1s)
                    const checkRes = await fetch(`${API_BASE}/modules/${mod.id}/check-update`);
                    const checkData = await checkRes.json();
                    if (!checkRes.ok) throw new Error(checkData.detail || `HTTP ${checkRes.status}`);

                    if (!checkData.update_available) {
                        updateBtn.textContent = "✓ Up to date";
                        setTimeout(() => { updateBtn.textContent = "↑ Update"; updateBtn.disabled = false; }, 2500);
                        return;
                    }

                    // Step 2: update available — do the full clone + replace
                    updateBtn.textContent = "Updating…";
                    const res = await fetch(`${API_BASE}/modules/${mod.id}/update`, { method: "POST" });
                    const data = await res.json();
                    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

                    if (data.status === "up_to_date") {
                        updateBtn.textContent = "✓ Up to date";
                        setTimeout(() => { updateBtn.textContent = "↑ Update"; updateBtn.disabled = false; }, 2500);
                    } else {
                        // Updated — refresh SHA chip and show message
                        const shaEl = header.querySelector(".mod-modal-sha");
                        if (shaEl && data.to_sha) shaEl.textContent = data.to_sha;
                        showSuccess(`Updated ${data.from_sha || "?"} → ${data.to_sha || "?"}`);
                        updateBtn.textContent = "✓ Updated";
                        onDone?.();
                        if (data.needs_restart) onNeedsRestart?.();
                        setTimeout(() => { updateBtn.textContent = "↑ Update"; updateBtn.disabled = false; }, 2500);
                    }
                } catch (e) {
                    console.warn("Update failed", e);
                    updateBtn.textContent = "✗ Failed";
                    showSuccess(`Update failed: ${e.message}`);
                    setTimeout(() => { updateBtn.textContent = "↑ Update"; updateBtn.disabled = false; }, 3000);
                }
            });
            footer.appendChild(updateBtn);
        }

        const closeFooterBtn = document.createElement("button");
        closeFooterBtn.className = "mod-btn mod-btn-cancel";
        closeFooterBtn.textContent = "Close";
        closeFooterBtn.addEventListener("click", closeModal);

        footer.appendChild(uninstallBtn);
        footer.appendChild(closeFooterBtn);
    } else {
        const installBtn = document.createElement("button");
        installBtn.className = "mod-btn mod-btn-primary";
        installBtn.textContent = "Install";
        installBtn.addEventListener("click", async () => {
            installBtn.disabled = true;
            installBtn.textContent = "Installing…";

            const payload = {};
            for (const [k, input] of Object.entries(envInputs)) {
                if (input.value.trim()) payload[k] = input.value.trim();
            }

            try {
                const res = await fetch(`${API_BASE}/modules/${mod.id}/install`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ env_vars: payload }),
                });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();

                // Clear footer actions
                installBtn.remove();
                cancelBtn.remove();

                let msg = `${mod.name} installed.`;
                if (data.manual_steps && data.manual_steps.length > 0) {
                    msg += " Complete the manual steps above in your terminal.";
                }
                showSuccess(msg);

                const doneBtn = document.createElement("button");
                doneBtn.className = "mod-btn mod-btn-cancel";
                doneBtn.textContent = "Close";
                doneBtn.addEventListener("click", closeModal);
                footer.appendChild(doneBtn);

                onDone?.();  // refresh grid immediately so install badge appears
                if (data.needs_restart) onNeedsRestart?.();
            } catch (e) {
                console.warn("Install failed", e);
                installBtn.disabled = false;
                installBtn.textContent = "Install";
            }
        });

        const cancelBtn = document.createElement("button");
        cancelBtn.className = "mod-btn mod-btn-cancel";
        cancelBtn.textContent = "Cancel";
        cancelBtn.addEventListener("click", () => overlay.remove());

        footer.appendChild(installBtn);
        footer.appendChild(cancelBtn);
    }

    // ── Assemble ─────────────────────────────────────────────────────
    card.appendChild(header);
    card.appendChild(body);
    card.appendChild(footer);
    overlay.appendChild(card);
    document.body.appendChild(overlay);

    // Close on Escape — use capture so this fires before the PM's bubble listener,
    // then stopImmediatePropagation to prevent the PM from also closing.
    const onKey = (e) => {
        if (e.key === "Escape") {
            e.stopImmediatePropagation();
            closeModal();
        }
    };
    document.addEventListener("keydown", onKey, { capture: true });
    overlay.addEventListener("click", (e) => { if (e.target === overlay) closeModal(); });
}

/**
 * Initializes the graph selector and popstate handler.
 */
export function initGraphSelector(graph, canvas, isDirty, loadGraphFn) {
    const selector = document.getElementById("graph-selector");
    const nameBtn  = document.getElementById("graph-name-btn");
    const folderEl = nameBtn?.querySelector(".graph-name-folder");
    const baseEl   = nameBtn?.querySelector(".graph-name-base");

    // Keep the display button in sync with every selector.value assignment
    if (selector) {
        const proto = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value");
        Object.defineProperty(selector, "value", {
            get() { return proto.get.call(this); },
            set(v) {
                proto.set.call(this, v);
                if (!baseEl) return;
                if (!v || v === "unsaved_graph.json") {
                    if (folderEl) folderEl.textContent = "";
                    baseEl.textContent = v === "unsaved_graph.json" ? "Unsaved Graph*" : "No graph loaded";
                    nameBtn?.setAttribute("data-unsaved", v === "unsaved_graph.json" ? "1" : "");
                } else {
                    const clean = v.replace(/\.json$/, "");
                    const slash = clean.lastIndexOf("/");
                    const folder = slash >= 0 ? clean.slice(0, slash) : "";
                    const base   = slash >= 0 ? clean.slice(slash + 1) : clean;
                    if (folderEl) folderEl.textContent = folder;
                    baseEl.textContent = base;
                    nameBtn?.removeAttribute("data-unsaved");
                    if (nameBtn) nameBtn.title = folder ? `${folder} / ${base}` : base;
                }
            },
            configurable: true,
        });
    }

    window.addEventListener("popstate", (e) => {
        const graphName = e.state?.graphName || new URL(window.location).searchParams.get('graph');
        if (graphName) {
            switchGraph(graph, canvas, isDirty, graphName, false);
        }
        updateBreadcrumbs(graph, canvas, isDirty);
    });
}
