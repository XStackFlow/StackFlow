/**
 * File browser modal — allows selecting files from the server filesystem.
 */

const API_BASE = "http://localhost:8000";

let modal = null;
let lastDir = "";

function ensureModal() {
    if (modal) return modal;

    modal = document.createElement("div");
    modal.id = "file-browser-modal";
    modal.innerHTML = `
        <div class="fb-backdrop"></div>
        <div class="fb-dialog">
            <div class="fb-header">
                <span class="fb-title">Select File</span>
                <button class="fb-close">&times;</button>
            </div>
            <div class="fb-path-bar">
                <button class="fb-up" title="Go up">&#8593;</button>
                <input class="fb-path-input" type="text" spellcheck="false" />
                <button class="fb-go">Go</button>
            </div>
            <div class="fb-list"></div>
            <div class="fb-footer">
                <span class="fb-selected-path"></span>
                <div class="fb-actions">
                    <button class="fb-cancel">Cancel</button>
                    <button class="fb-select" disabled>Select</button>
                </div>
            </div>
        </div>
    `;
    document.body.appendChild(modal);

    // Inject styles once
    if (!document.getElementById("fb-styles")) {
        const style = document.createElement("style");
        style.id = "fb-styles";
        style.textContent = `
            #file-browser-modal { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: 10000; }
            #file-browser-modal.open { display: flex; align-items: center; justify-content: center; }
            .fb-backdrop { position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.6); }
            .fb-dialog { position: relative; width: 550px; max-height: 70vh; background: #1e1e1e; border: 1px solid #444; border-radius: 8px; display: flex; flex-direction: column; box-shadow: 0 8px 32px rgba(0,0,0,0.5); }
            .fb-header { display: flex; justify-content: space-between; align-items: center; padding: 10px 14px; border-bottom: 1px solid #333; }
            .fb-title { color: #eee; font-size: 13px; font-weight: 600; }
            .fb-close { background: none; border: none; color: #888; font-size: 18px; cursor: pointer; padding: 0 4px; }
            .fb-close:hover { color: #fff; }
            .fb-path-bar { display: flex; gap: 4px; padding: 8px 14px; border-bottom: 1px solid #333; }
            .fb-up { background: #2a2a2a; border: 1px solid #444; color: #ccc; padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 14px; }
            .fb-up:hover { background: #333; }
            .fb-path-input { flex: 1; background: #111; border: 1px solid #444; border-radius: 4px; color: #ccc; padding: 4px 8px; font-size: 11px; font-family: 'Fira Code', monospace; outline: none; }
            .fb-path-input:focus { border-color: #3b82f6; }
            .fb-go { background: #2a2a2a; border: 1px solid #444; color: #ccc; padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 11px; }
            .fb-go:hover { background: #333; }
            .fb-list { flex: 1; overflow-y: auto; padding: 4px 0; min-height: 200px; max-height: 50vh; scrollbar-width: thin; scrollbar-color: #555 transparent; }
            .fb-list::-webkit-scrollbar { width: 6px; }
            .fb-list::-webkit-scrollbar-track { background: transparent; }
            .fb-list::-webkit-scrollbar-thumb { background: #555; border-radius: 3px; }
            .fb-item { display: flex; align-items: center; gap: 8px; padding: 5px 14px; cursor: pointer; color: #bbb; font-size: 12px; }
            .fb-item:hover { background: #2a2a2a; }
            .fb-item.selected { background: #1e3a5f; color: #60a5fa; }
            .fb-item-icon { font-size: 14px; width: 18px; text-align: center; flex-shrink: 0; }
            .fb-item-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
            .fb-empty { padding: 20px; color: #666; text-align: center; font-style: italic; font-size: 12px; }
            .fb-footer { display: flex; justify-content: space-between; align-items: center; padding: 8px 14px; border-top: 1px solid #333; gap: 8px; }
            .fb-selected-path { flex: 1; color: #888; font-size: 10px; font-family: 'Fira Code', monospace; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
            .fb-actions { display: flex; gap: 6px; }
            .fb-cancel, .fb-select { padding: 5px 14px; border-radius: 4px; border: 1px solid #444; cursor: pointer; font-size: 11px; }
            .fb-cancel { background: #2a2a2a; color: #ccc; }
            .fb-cancel:hover { background: #333; }
            .fb-select { background: #3b82f6; color: #fff; border-color: #3b82f6; }
            .fb-select:hover { background: #2563eb; }
            .fb-select:disabled { background: #333; color: #666; border-color: #444; cursor: default; }
        `;
        document.head.appendChild(style);
    }

    return modal;
}

async function fetchDir(dirPath, extensions) {
    const params = new URLSearchParams();
    if (dirPath) params.set("path", dirPath);
    if (extensions) params.set("extensions", extensions);
    const res = await fetch(`${API_BASE}/browse_files?${params}`);
    if (!res.ok) throw new Error(`Failed to browse: ${res.status}`);
    return res.json();
}

export function openFileBrowser(currentValue, onSelect, extensions) {
    const m = ensureModal();
    const list = m.querySelector(".fb-list");
    const pathInput = m.querySelector(".fb-path-input");
    const upBtn = m.querySelector(".fb-up");
    const goBtn = m.querySelector(".fb-go");
    const selectBtn = m.querySelector(".fb-select");
    const cancelBtn = m.querySelector(".fb-cancel");
    const closeBtn = m.querySelector(".fb-close");
    const backdrop = m.querySelector(".fb-backdrop");
    const selectedLabel = m.querySelector(".fb-selected-path");

    let currentDir = "";
    let parentDir = null;
    let selectedFile = null;

    // Determine start dir: use dir of currentValue if set, else fall back to lastDir
    if (currentValue && !currentValue.startsWith("{{")) {
        const sep = currentValue.includes("/") ? "/" : "\\";
        const parts = currentValue.split(sep);
        parts.pop();
        currentDir = parts.join(sep) || lastDir;
    } else {
        currentDir = lastDir;
    }

    function close() {
        m.classList.remove("open");
    }

    function selectFile(path) {
        selectedFile = path;
        selectedLabel.textContent = path;
        selectBtn.disabled = false;
        // Highlight
        list.querySelectorAll(".fb-item").forEach(el => {
            el.classList.toggle("selected", el.dataset.path === path);
        });
    }

    async function loadDir(dirPath) {
        list.innerHTML = '<div class="fb-empty">Loading...</div>';
        selectedFile = null;
        selectBtn.disabled = true;
        selectedLabel.textContent = "";

        try {
            const data = await fetchDir(dirPath, extensions || "");
            currentDir = data.path;
            lastDir = data.path;  // persist for next open
            parentDir = data.parent;
            pathInput.value = data.path;

            if (data.items.length === 0) {
                list.innerHTML = '<div class="fb-empty">Empty directory</div>';
                return;
            }

            list.innerHTML = "";
            data.items.forEach(item => {
                const el = document.createElement("div");
                el.className = "fb-item";
                el.dataset.path = item.path;
                el.innerHTML = `
                    <span class="fb-item-icon">${item.is_dir ? "📁" : "📄"}</span>
                    <span class="fb-item-name">${item.name}</span>
                `;

                if (item.is_dir) {
                    el.addEventListener("dblclick", () => loadDir(item.path));
                    el.addEventListener("click", () => {
                        // Single click on dir just highlights; double-click navigates
                        list.querySelectorAll(".fb-item").forEach(e => e.classList.remove("selected"));
                        el.classList.add("selected");
                    });
                } else {
                    el.addEventListener("click", () => selectFile(item.path));
                    el.addEventListener("dblclick", () => {
                        onSelect(item.path);
                        close();
                    });
                }

                list.appendChild(el);
            });
        } catch (e) {
            list.innerHTML = `<div class="fb-empty">Error: ${e.message}</div>`;
        }
    }

    // Wire up buttons
    upBtn.onclick = () => { if (parentDir) loadDir(parentDir); };
    goBtn.onclick = () => loadDir(pathInput.value);
    pathInput.addEventListener("keydown", (e) => { if (e.key === "Enter") loadDir(pathInput.value); });
    selectBtn.onclick = () => { if (selectedFile) { onSelect(selectedFile); close(); } };
    cancelBtn.onclick = close;
    closeBtn.onclick = close;
    backdrop.onclick = close;

    m.classList.add("open");
    loadDir(currentDir);
}
