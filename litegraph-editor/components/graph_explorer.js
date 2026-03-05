/**
 * Graph Explorer — floating panel for browsing, organizing, and installing graphs.
 *
 * Triggered by the 📁 button in the toolbar.  The panel shows a collapsible
 * folder tree, an inline search filter, and a GitHub URL install form.
 *
 * Cross-module handle:  window.__graphExplorer = { setTree, getFolders }
 * Used by graph_manager.js (setTree on every list refresh) and
 * toolbar.js (getFolders to populate the save-modal folder dropdown).
 */

const API_BASE = "http://localhost:8000";

export function initGraphExplorer(_graph, _canvas, _isDirty, { switchGraphFn, fetchGraphListFn }) {
    let tree = [];
    let isOpen = false;

    // Folder collapse state — persists while the page is open
    const collapsedFolders = new Set();

    // ── Panel DOM ────────────────────────────────────────────────────────────

    const panel = document.createElement("div");
    panel.id = "graph-explorer-panel";
    document.body.appendChild(panel);

    // Search bar
    const searchWrap = document.createElement("div");
    searchWrap.className = "ge-search-wrap";
    const searchInput = document.createElement("input");
    searchInput.type = "text";
    searchInput.placeholder = "Filter graphs…";
    searchInput.className = "ge-search";
    searchWrap.appendChild(searchInput);

    // Tree area
    const treeArea = document.createElement("div");
    treeArea.className = "ge-tree";

    // GitHub install bar
    const ghBar = document.createElement("div");
    ghBar.className = "ge-gh-bar";

    const ghLabel = document.createElement("div");
    ghLabel.className = "ge-gh-label";
    ghLabel.textContent = "Install from GitHub";

    const ghRow = document.createElement("div");
    ghRow.className = "ge-gh-row";

    const ghInput = document.createElement("input");
    ghInput.type = "text";
    ghInput.placeholder = "https://github.com/…/graph.json";
    ghInput.className = "ge-gh-input";

    const ghBtn = document.createElement("button");
    ghBtn.textContent = "Install";
    ghBtn.className = "ge-gh-btn";

    const ghMsg = document.createElement("div");
    ghMsg.className = "ge-gh-msg";

    ghRow.appendChild(ghInput);
    ghRow.appendChild(ghBtn);
    ghBar.appendChild(ghLabel);
    ghBar.appendChild(ghRow);
    ghBar.appendChild(ghMsg);

    panel.appendChild(searchWrap);
    panel.appendChild(treeArea);
    panel.appendChild(ghBar);

    // ── Tree rendering ───────────────────────────────────────────────────────

    function renderTree(nodes = tree, query = searchInput.value) {
        treeArea.innerHTML = "";
        const q = query.trim().toLowerCase();
        const anyRendered = renderNodes(nodes, treeArea, 0, q);
        if (!anyRendered) {
            const empty = document.createElement("div");
            empty.className = "ge-empty";
            empty.textContent = q ? "No graphs match filter" : "No graphs";
            treeArea.appendChild(empty);
        }
    }

    function nodeMatches(node, q) {
        if (!q) return true;
        if (node.type === "graph") return node.path.toLowerCase().includes(q);
        return node.children.some(c => nodeMatches(c, q));
    }

    /** Returns true if at least one item was rendered. */
    function renderNodes(nodes, container, depth, q) {
        let rendered = false;
        nodes.forEach(node => {
            if (!nodeMatches(node, q)) return;
            rendered = true;

            if (node.type === "folder") {
                const forceOpen = !!q;
                const isCollapsed = !forceOpen && collapsedFolders.has(node.path);

                const folderEl = document.createElement("div");
                folderEl.className = "ge-folder";
                folderEl.style.paddingLeft = `${8 + depth * 14}px`;

                const arrow = document.createElement("span");
                arrow.className = "ge-arrow";
                arrow.textContent = isCollapsed ? "▶" : "▼";

                const icon = document.createElement("span");
                icon.textContent = "📁";
                icon.style.marginRight = "4px";

                const name = document.createElement("span");
                name.textContent = node.name;

                folderEl.appendChild(arrow);
                folderEl.appendChild(icon);
                folderEl.appendChild(name);
                container.appendChild(folderEl);

                folderEl.addEventListener("click", () => {
                    if (collapsedFolders.has(node.path)) {
                        collapsedFolders.delete(node.path);
                    } else {
                        collapsedFolders.add(node.path);
                    }
                    renderTree();
                });

                if (!isCollapsed) {
                    renderNodes(node.children, container, depth + 1, q);
                }
            } else {
                // Graph leaf
                const currentPath = document.getElementById("graph-selector")?.value;
                const isActive = currentPath === node.path;

                const graphEl = document.createElement("div");
                graphEl.className = "ge-graph" + (isActive ? " ge-graph-active" : "");
                graphEl.style.paddingLeft = `${8 + depth * 14 + 16}px`;
                graphEl.title = node.path;

                const nameEl = document.createElement("span");
                nameEl.className = "ge-graph-name";
                nameEl.textContent = node.name.replace(/\.json$/, "");

                const isModule = node.path.startsWith("module@@");

                if (!isModule) {
                    const trashBtn = document.createElement("button");
                    trashBtn.className = "ge-trash";
                    trashBtn.textContent = "🗑";
                    trashBtn.title = "Delete graph";
                    trashBtn.addEventListener("click", (e) => {
                        e.stopPropagation();
                        confirmDelete(node);
                    });
                    graphEl.appendChild(nameEl);
                    graphEl.appendChild(trashBtn);
                } else {
                    graphEl.appendChild(nameEl);
                }
                container.appendChild(graphEl);

                graphEl.addEventListener("click", () => {
                    switchGraphFn(node.path);
                    close();
                });

                if (!isModule) {
                    graphEl.addEventListener("contextmenu", (e) => {
                        e.preventDefault();
                        showContextMenu(e, node);
                    });
                }
            }
        });
        return rendered;
    }

    // ── Context menu ─────────────────────────────────────────────────────────

    function showContextMenu(e, node) {
        document.getElementById("ge-ctx")?.remove();

        const menu = document.createElement("div");
        menu.id = "ge-ctx";
        menu.className = "ge-ctx-menu";
        menu.style.left = `${e.clientX}px`;
        menu.style.top = `${e.clientY}px`;

        const delItem = document.createElement("div");
        delItem.className = "ge-ctx-item ge-ctx-danger";
        delItem.textContent = "🗑 Delete";
        delItem.addEventListener("click", () => { menu.remove(); confirmDelete(node); });
        menu.appendChild(delItem);

        document.body.appendChild(menu);

        const dismiss = (ev) => {
            if (!menu.contains(ev.target)) {
                menu.remove();
                document.removeEventListener("mousedown", dismiss);
            }
        };
        setTimeout(() => document.addEventListener("mousedown", dismiss), 0);
    }

    // ── Delete ───────────────────────────────────────────────────────────────

    async function confirmDelete(node) {
        if (!confirm(`Delete "${node.path}"?\nThis cannot be undone.`)) return;
        try {
            const res = await fetch(`${API_BASE}/delete_graph/${node.path}`, { method: "DELETE" });
            if (!res.ok) {
                const d = await res.json().catch(() => ({}));
                alert(`Delete failed: ${d.detail || res.status}`);
                return;
            }
            await fetchGraphListFn(3, false);
        } catch (err) {
            alert("Delete request failed: " + err.message);
        }
    }

    // ── GitHub install ───────────────────────────────────────────────────────

    async function doInstall() {
        const url = ghInput.value.trim();
        if (!url) return;

        ghBtn.disabled = true;
        ghBtn.textContent = "Fetching…";
        ghMsg.className = "ge-gh-msg";
        ghMsg.textContent = "";

        try {
            // Convert GitHub blob URL → raw.githubusercontent.com URL
            let rawUrl = url;
            const blobMatch = url.match(
                /^https:\/\/github\.com\/([^/]+)\/([^/]+)\/blob\/([^/]+)\/(.+)$/
            );
            if (blobMatch) {
                rawUrl = `https://raw.githubusercontent.com/${blobMatch[1]}/${blobMatch[2]}/${blobMatch[3]}/${blobMatch[4]}`;
            }

            const fetchRes = await fetch(rawUrl);
            if (!fetchRes.ok) throw new Error(`HTTP ${fetchRes.status} from ${rawUrl}`);
            const graphData = await fetchRes.json();

            // Derive default filename
            const baseName = rawUrl.split("/").pop().replace(/\.json$/i, "");

            const dest = prompt(
                "Save as (e.g. github/my_graph):",
                `github/${baseName}`
            );
            if (!dest) return;

            const savePath = dest.trim().replace(/\.json$/i, "");
            const saveRes = await fetch(`${API_BASE}/save_graph/${savePath}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(graphData),
            });
            if (!saveRes.ok) throw new Error(`Save failed: HTTP ${saveRes.status}`);

            ghMsg.className = "ge-gh-msg ge-gh-ok";
            ghMsg.textContent = `✓ Saved as ${savePath}.json`;
            ghInput.value = "";
            await fetchGraphListFn(3, false);
        } catch (err) {
            ghMsg.className = "ge-gh-msg ge-gh-err";
            ghMsg.textContent = err.message;
        } finally {
            ghBtn.disabled = false;
            ghBtn.textContent = "Install";
        }
    }

    ghBtn.addEventListener("click", doInstall);
    ghInput.addEventListener("keydown", (e) => { if (e.key === "Enter") doInstall(); });

    // ── Search ───────────────────────────────────────────────────────────────

    searchInput.addEventListener("input", () => renderTree());

    // ── Open / close ─────────────────────────────────────────────────────────

    const toggleBtn = document.getElementById("graph-name-btn");

    function open() {
        panel.style.display = "flex";
        isOpen = true;
        toggleBtn?.classList.add("open");
        renderTree();
        searchInput.focus();
    }

    function close() {
        panel.style.display = "none";
        isOpen = false;
        toggleBtn?.classList.remove("open");
    }

    toggleBtn?.addEventListener("click", () => { isOpen ? close() : open(); });

    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && isOpen) close();
    });

    // Use capture:true so this runs before LiteGraph's canvas mousedown handler,
    // which calls stopPropagation() and would otherwise block bubble-phase listeners.
    document.addEventListener("mousedown", (e) => {
        if (isOpen && !panel.contains(e.target) && e.target !== toggleBtn && !toggleBtn?.contains(e.target)) close();
    }, true);

    // ── Public API ───────────────────────────────────────────────────────────

    const api = {
        setTree(newTree) {
            tree = newTree;
            if (isOpen) renderTree();
        },
        getFolders() {
            const folders = [];
            function walk(nodes) {
                nodes.forEach(n => {
                    if (n.type === "folder") {
                        folders.push(n.path);
                        walk(n.children);
                    }
                });
            }
            walk(tree);
            return folders;
        },
    };

    window.__graphExplorer = api;
    return api;
}
