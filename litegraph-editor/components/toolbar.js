/**
 * Toolbar — wires up all toolbar buttons, keyboard shortcuts,
 * and the save/load/new-graph workflows.
 */

import { LiteGraph } from 'litegraph.js';
import { addLog, pushLog } from './logging.js';
import { getCleanPath } from './utils.js';
import { clearHistory, undo, redo, canUndo, canRedo } from './history.js';
import { getSessionId, updateSessionUI } from './session.js';
import {
    executeGraph,
    stopGraph,
    clearGraphState,
    getThreadId,
    reloadLogs,
    updateStateWidget,
    applyNodeGlows,
    getDebugMode,
    setDebugMode,
} from './execution.js';
import { nodeTypeMetadata } from './graph_manager.js';

const API_BASE = "http://localhost:8000";

/**
 * Saves the current graph to the server.
 * Shows a modal with a folder selector and name field (replacing the old bare prompt).
 * Returns true on success, false otherwise.
 */
export async function saveGraphToServer(graph, isDirty, fetchGraphListFn, updateBreadcrumbsFn, canvas = null) {
    const selector = document.getElementById("graph-selector");
    // currentSavedPath is the path already on disk (may be empty for a brand-new graph)
    const currentSavedPath = (selector?.value || "").replace(/\.json$/, "");
    const slashIdx = currentSavedPath.lastIndexOf("/");
    const defaultFolder = slashIdx >= 0 ? currentSavedPath.slice(0, slashIdx) : "";
    const defaultName   = slashIdx >= 0 ? currentSavedPath.slice(slashIdx + 1)
                                        : (currentSavedPath || "my_graph");
    const folders = window.__graphExplorer?.getFolders() ?? [];

    // Unique id for the datalist so multiple opens don't collide
    const dlId = "save-modal-folder-dl-" + Date.now();

    return new Promise((resolve) => {
        // ── Modal overlay ──────────────────────────────────────────────────
        const overlay = document.createElement("div");
        overlay.className = "save-modal-overlay";

        const modal = document.createElement("div");
        modal.className = "save-modal";

        const title = document.createElement("div");
        title.className = "save-modal-title";
        title.textContent = "Save Graph";

        // Folder row — free-text input with datalist autocomplete
        const folderRow = document.createElement("div");
        folderRow.className = "save-modal-row";
        const folderLabel = document.createElement("label");
        folderLabel.className = "save-modal-label";
        folderLabel.textContent = "Folder";

        const folderInput = document.createElement("input");
        folderInput.type = "text";
        folderInput.className = "save-modal-input";
        folderInput.value = defaultFolder;
        folderInput.placeholder = "root (leave blank) or type/new/folder";
        folderInput.setAttribute("list", dlId);

        const datalist = document.createElement("datalist");
        datalist.id = dlId;
        folders.forEach(f => {
            const o = document.createElement("option");
            o.value = f;
            datalist.appendChild(o);
        });

        folderRow.appendChild(folderLabel);
        folderRow.appendChild(folderInput);
        folderRow.appendChild(datalist);

        // Name row
        const nameRow = document.createElement("div");
        nameRow.className = "save-modal-row";
        const nameLabel = document.createElement("label");
        nameLabel.className = "save-modal-label";
        nameLabel.textContent = "Name";
        const nameInput = document.createElement("input");
        nameInput.type = "text";
        nameInput.className = "save-modal-input";
        nameInput.value = defaultName;
        nameInput.placeholder = "graph_name";
        nameRow.appendChild(nameLabel);
        nameRow.appendChild(nameInput);

        // Move row — shown only when destination ≠ current saved path
        const moveRow = document.createElement("div");
        moveRow.className = "save-modal-move-row";
        moveRow.style.display = "none";
        const moveChk = document.createElement("input");
        moveChk.type = "checkbox";
        moveChk.id = "save-modal-move-chk";
        moveChk.checked = true;
        const moveLabel = document.createElement("label");
        moveLabel.htmlFor = "save-modal-move-chk";
        moveLabel.className = "save-modal-move-label";
        moveLabel.textContent = "Delete original (move instead of copy)";
        moveRow.appendChild(moveChk);
        moveRow.appendChild(moveLabel);

        function getComposedPath() {
            const f = folderInput.value.trim().replace(/\/+$/, "");
            const n = nameInput.value.trim().replace(/\.json$/, "");
            return f ? `${f}/${n}` : n;
        }

        function updateMoveRow() {
            // Hide move option if there's no current path or if the source is a read-only module graph
            if (!currentSavedPath || currentSavedPath.startsWith("module@@")) { moveRow.style.display = "none"; return; }
            const differs = getComposedPath() !== currentSavedPath;
            moveRow.style.display = differs ? "flex" : "none";
        }

        folderInput.addEventListener("input", updateMoveRow);
        nameInput.addEventListener("input", updateMoveRow);
        updateMoveRow();

        // Buttons
        const btnRow = document.createElement("div");
        btnRow.className = "save-modal-btns";
        const cancelBtn = document.createElement("button");
        cancelBtn.className = "save-modal-btn";
        cancelBtn.textContent = "Cancel";
        const saveBtn = document.createElement("button");
        saveBtn.className = "save-modal-btn save-modal-btn-primary";
        saveBtn.textContent = "Save";

        btnRow.appendChild(cancelBtn);
        btnRow.appendChild(saveBtn);

        modal.appendChild(title);
        modal.appendChild(folderRow);
        modal.appendChild(nameRow);
        modal.appendChild(moveRow);
        modal.appendChild(btnRow);
        overlay.appendChild(modal);
        document.body.appendChild(overlay);

        setTimeout(() => { nameInput.focus(); nameInput.select(); }, 30);

        const dismiss = (result) => { overlay.remove(); resolve(result); };

        cancelBtn.addEventListener("click", () => dismiss(false));
        overlay.addEventListener("click", (e) => { if (e.target === overlay) dismiss(false); });

        const doSave = async () => {
            const rawName = nameInput.value.trim().replace(/\.json$/, "");
            if (!rawName) return;

            const composedPath = getComposedPath();
            const shouldMove   = currentSavedPath && composedPath !== currentSavedPath && moveChk.checked;
            overlay.remove();

            try {
                const data = JSON.parse(JSON.stringify(graph.serialize()));
                if (data.nodes) {
                    data.nodes.forEach(node => {
                        delete node.boxcolor;
                        delete node._last_exec_time;
                        delete node.color;
                    });
                }

                // Persist current viewport so it's restored on next load
                if (canvas?.ds) {
                    data._viewport = {
                        offset: [...canvas.ds.offset],
                        scale: canvas.ds.scale,
                    };
                }

                // Persist module dependency manifest so missing modules can be
                // identified (and installed) when this graph is loaded elsewhere.
                // Format: { "module_id": { origin, source_url } }
                if (data.nodes) {
                    const deps = {};
                    data.nodes.forEach(node => {
                        if (!node.type || node.type.startsWith("langgraph/")) return;
                        const meta = nodeTypeMetadata[node.type];
                        if (!meta || !meta.module_id) return;
                        if (deps[meta.module_id]) return; // already recorded
                        deps[meta.module_id] = {
                            origin: meta.origin || "builtin",
                            source_url: meta.source_url || null,
                        };
                    });
                    if (Object.keys(deps).length > 0) {
                        data._module_deps = deps;
                    }
                }

                const response = await fetch(`${API_BASE}/save_graph/${composedPath}`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(data),
                });

                if (!response.ok) {
                    const errData = await response.json().catch(() => ({}));
                    const detail = errData.detail || `HTTP ${response.status}`;
                    addLog(`System: Failed to save graph — ${detail}`, "error");
                    resolve(false);
                    return;
                }

                const savedName = composedPath.endsWith(".json") ? composedPath : `${composedPath}.json`;

                // Delete original if moving
                if (shouldMove) {
                    try {
                        await fetch(`${API_BASE}/delete_graph/${currentSavedPath}`, { method: "DELETE" });
                    } catch (_) { /* non-fatal */ }
                }

                isDirty.value = false;
                if (selector) selector.value = savedName;
                localStorage.setItem("stackflow_last_graph", savedName);

                const url = new URL(window.location);
                if (url.searchParams.get("graph") !== savedName) {
                    url.searchParams.set("graph", savedName);
                    window.history.replaceState({ graphName: savedName }, "", url);
                    if (updateBreadcrumbsFn) updateBreadcrumbsFn();
                }

                if (fetchGraphListFn) await fetchGraphListFn(5, false);
                const action = shouldMove ? "moved to" : "saved as";
                addLog(`System: Graph ${action} ${savedName} successfully.`, "completion");
                resolve(true);
            } catch (err) {
                console.error("Save failed:", err);
                addLog("System: Save error. Check console.", "error");
                resolve(false);
            }
        };

        saveBtn.addEventListener("click", doSave);
        nameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") doSave(); });
        folderInput.addEventListener("keydown", (e) => { if (e.key === "Enter") nameInput.focus(); });
    });
}

/**
 * Auto-layout: hierarchical left-to-right layout (group-aware).
 *
 * When the graph contains LiteGraph groups the layout runs in two levels:
 *   1. Each group's nodes are laid out internally (local column-based layout).
 *   2. Groups (as "super-nodes") and ungrouped nodes are laid out together in
 *      a top-level meta-graph so inter-group flow reads left-to-right.
 *   3. Group bounding boxes are resized to wrap their contents.
 *
 * Graphs without groups fall through to the same algorithm applied once.
 */
function autoLayout(graph, canvas) {
    const allNodes = graph._nodes;
    if (!allNodes || allNodes.length === 0) return;

    const nodeMap = new Map(allNodes.map(n => [n.id, n]));

    // --- Snap every node to its computed minimum size ---
    for (const n of allNodes) {
        if (n.computeSize) {
            try {
                const [minW, minH] = n.computeSize();
                n.size[0] = Math.max(minW, 60);
                n.size[1] = Math.max(minH, 30);
            } catch (_) {}
        }
    }

    const nodeH = n => (n.size?.[1] ?? 60) + 10;
    const nodeW = n => n.size?.[0] ?? 180;

    // ===================================================================
    //  Core sub-graph layout — positions a set of nodes starting at (0, 0)
    //  Returns { width, height } of the bounding box.
    // ===================================================================
    function layoutNodeSet(nodes, allLinks) {
        if (!nodes.length) return { width: 0, height: 0 };

        const idSet = new Set(nodes.map(n => n.id));

        // --- Build adjacency (only edges whose BOTH endpoints are in the set) ---
        const inDegree     = new Map(nodes.map(n => [n.id, 0]));
        const successors   = new Map(nodes.map(n => [n.id, []]));
        const predecessors = new Map(nodes.map(n => [n.id, []]));
        const minPredSlot  = new Map(nodes.map(n => [n.id, Infinity]));
        const incomingSlot = new Map();

        for (const link of allLinks) {
            const from = link.origin_id, to = link.target_id;
            if (!idSet.has(from) || !idSet.has(to)) continue;
            successors.get(from).push(to);
            predecessors.get(to).push(from);
            inDegree.set(to, inDegree.get(to) + 1);
            minPredSlot.set(to, Math.min(minPredSlot.get(to), link.origin_slot ?? Infinity));
            if (!incomingSlot.has(to)) incomingSlot.set(to, new Map());
            const inner = incomingSlot.get(to);
            inner.set(from, Math.min(inner.get(from) ?? Infinity, link.origin_slot ?? 0));
        }

        // --- Phase 1: Kahn's BFS leveling ---
        const level = new Map();
        const tempDeg = new Map(inDegree);
        const roots = nodes.filter(n => inDegree.get(n.id) === 0).map(n => n.id);
        roots.forEach(id => level.set(id, 0));
        const queue = [...roots];
        let qi = 0;
        while (qi < queue.length) {
            const id = queue[qi++];
            const lv = level.get(id);
            for (const sid of successors.get(id)) {
                const nl = lv + 1;
                if (!level.has(sid) || level.get(sid) < nl) level.set(sid, nl);
                tempDeg.set(sid, tempDeg.get(sid) - 1);
                if (tempDeg.get(sid) === 0) queue.push(sid);
            }
        }

        // --- Phase 2: cyclic nodes ---
        let changed = true;
        while (changed) {
            changed = false;
            for (const n of nodes) {
                if (level.has(n.id)) continue;
                const levelled = (predecessors.get(n.id) ?? []).filter(pid => level.has(pid));
                if (!levelled.length) continue;
                level.set(n.id, Math.max(...levelled.map(pid => level.get(pid))) + 1);
                changed = true;
            }
        }
        for (const n of nodes) { if (!level.has(n.id)) level.set(n.id, 0); }

        // --- Phase 3: place disconnected sources near their successors ---
        // Nodes with no incoming edges (other than START) should sit one level
        // before their earliest successor, not at level 0.
        for (const n of nodes) {
            if (n.type === 'langgraph/start') continue;
            if (inDegree.get(n.id) !== 0) continue;
            const succs = successors.get(n.id) ?? [];
            const succLevels = succs.map(sid => level.get(sid)).filter(l => l != null);
            if (succLevels.length > 0) {
                level.set(n.id, Math.max(0, Math.min(...succLevels) - 1));
            }
        }

        // --- Group into columns ---
        const maxLevel = Math.max(0, ...level.values());
        const cols = Array.from({ length: maxLevel + 1 }, () => []);
        for (const n of nodes) cols[level.get(n.id)].push(n);

        // --- Sort within columns ---
        const idxInCol = new Map();
        cols[0].sort((a, b) => {
            if (a.type === 'langgraph/start') return -1;
            if (b.type === 'langgraph/start') return  1;
            if (a.type === 'langgraph/end')   return  1;
            if (b.type === 'langgraph/end')   return -1;
            return a.id - b.id;
        });
        cols[0].forEach((n, i) => idxInCol.set(n.id, i));

        const avgPredIdx = n => {
            const ps = predecessors.get(n.id);
            if (!ps?.length) return Infinity;
            return ps.reduce((s, pid) => s + (idxInCol.get(pid) ?? 0), 0) / ps.length;
        };
        for (let c = 1; c <= maxLevel; c++) {
            cols[c].sort((a, b) => {
                const diff = avgPredIdx(a) - avgPredIdx(b);
                if (Math.abs(diff) > 0.001) return diff;
                return (minPredSlot.get(a.id) ?? Infinity) - (minPredSlot.get(b.id) ?? Infinity);
            });
            cols[c].forEach((n, i) => idxInCol.set(n.id, i));
        }

        const avgSuccIdx = n => {
            const ss = successors.get(n.id);
            if (!ss?.length) return Infinity;
            const vals = ss.map(sid => idxInCol.get(sid)).filter(v => v != null);
            if (!vals.length) return Infinity;
            return vals.reduce((s, v) => s + v, 0) / vals.length;
        };
        for (let c = maxLevel - 1; c >= 1; c--) {
            cols[c].sort((a, b) => {
                const diff = avgPredIdx(a) - avgPredIdx(b);
                if (Math.abs(diff) > 0.001) return diff;
                const slotDiff = (minPredSlot.get(a.id) ?? Infinity) - (minPredSlot.get(b.id) ?? Infinity);
                if (Math.abs(slotDiff) > 0.001) return slotDiff;
                return avgSuccIdx(a) - avgSuccIdx(b);
            });
            cols[c].forEach((n, i) => idxInCol.set(n.id, i));
        }

        // --- Column geometry & initial placement ---
        const COL_GAP = 80, ROW_GAP = 55;
        const colWidth = cols.map(col => col.reduce((w, n) => Math.max(w, n.size?.[0] ?? 220), 0));
        const colH = col => col.reduce((h, n) => h + nodeH(n), 0) + Math.max(0, col.length - 1) * ROW_GAP;
        const maxColH = Math.max(...cols.map(colH));
        const midY = maxColH / 2;

        const ctrY = new Map();
        let x = 0;
        for (let c = 0; c <= maxLevel; c++) {
            let y = midY - colH(cols[c]) / 2;
            for (const n of cols[c]) {
                n.pos[0] = x;
                n.pos[1] = y;
                ctrY.set(n.id, y + nodeH(n) / 2);
                y += nodeH(n) + ROW_GAP;
            }
            x += colWidth[c] + COL_GAP;
        }

        // --- Barycentric refinement ---
        const resolveOverlaps = (col) => {
            col.sort((a, b) => a.pos[1] - b.pos[1]);
            for (let i = 1; i < col.length; i++) {
                const prev = col[i - 1];
                const minY = prev.pos[1] + nodeH(prev) + ROW_GAP;
                if (col[i].pos[1] < minY) {
                    col[i].pos[1] = minY;
                    ctrY.set(col[i].id, col[i].pos[1] + nodeH(col[i]) / 2);
                }
            }
        };

        const slotAwarePredY = (n) => {
            const preds = predecessors.get(n.id);
            if (!preds?.length) return null;
            const seen = new Set();
            let total = 0, count = 0;
            for (const pid of preds) {
                if (seen.has(pid)) continue;
                seen.add(pid);
                const predNode = nodeMap.get(pid);
                const slot = incomingSlot.get(n.id)?.get(pid) ?? 0;
                const numSlots = predNode?.outputs?.length ?? 1;
                const frac = numSlots > 1 ? (slot / (numSlots - 1)) - 0.5 : 0;
                total += (ctrY.get(pid) ?? 0) + frac * nodeH(predNode) * 0.15;
                count++;
            }
            return count > 0 ? total / count : null;
        };

        for (let pass = 0; pass < 4; pass++) {
            for (let c = 1; c <= maxLevel; c++) {
                for (const n of cols[c]) {
                    const avg = slotAwarePredY(n);
                    if (avg == null) continue;
                    n.pos[1] = avg - nodeH(n) / 2;
                    ctrY.set(n.id, avg);
                }
                resolveOverlaps(cols[c]);
            }
            for (let c = maxLevel - 1; c >= 0; c--) {
                for (const n of cols[c]) {
                    const succs = successors.get(n.id);
                    if (!succs?.length) continue;
                    const avg = succs.reduce((s, sid) => s + (ctrY.get(sid) ?? 0), 0) / succs.length;
                    n.pos[1] = avg - nodeH(n) / 2;
                    ctrY.set(n.id, avg);
                }
                resolveOverlaps(cols[c]);
            }
        }

        // --- Origin-align to (0, 0) ---
        let bx0 = Infinity, by0 = Infinity, bx1 = -Infinity, by1 = -Infinity;
        for (const n of nodes) {
            bx0 = Math.min(bx0, n.pos[0]);
            by0 = Math.min(by0, n.pos[1]);
            bx1 = Math.max(bx1, n.pos[0] + nodeW(n));
            by1 = Math.max(by1, n.pos[1] + nodeH(n));
        }
        for (const n of nodes) {
            n.pos[0] -= bx0;
            n.pos[1] -= by0;
        }
        return { width: bx1 - bx0, height: by1 - by0 };
    }

    // ===================================================================
    //  Collect all links into an array for easy filtering
    // ===================================================================
    const allLinks = [];
    for (const linkId in graph.links) {
        const link = graph.links[linkId];
        if (link && nodeMap.has(link.origin_id) && nodeMap.has(link.target_id)) {
            allLinks.push(link);
        }
    }

    // ===================================================================
    //  Detect groups and build membership
    // ===================================================================
    const groups = graph._groups || [];
    for (const g of groups) g.recomputeInsideNodes();

    // Build nodeId → group (first enclosing group wins)
    const nodeGroup = new Map();
    const activeGroups = [];
    for (const g of groups) {
        if (g._nodes.length === 0) continue;
        activeGroups.push(g);
        for (const n of g._nodes) {
            if (!nodeGroup.has(n.id)) nodeGroup.set(n.id, g);
        }
    }

    // ===================================================================
    //  No groups → plain flat layout (original behaviour)
    // ===================================================================
    if (activeGroups.length === 0) {
        layoutNodeSet(allNodes, allLinks);

        // Add margin
        const LAYOUT_MARGIN = 140;
        for (const n of allNodes) {
            n.pos[0] += LAYOUT_MARGIN;
            n.pos[1] += LAYOUT_MARGIN;
        }

    } else {
        // ==============================================================
        //  Group-aware two-level layout
        // ==============================================================
        const GROUP_PAD = 40;         // padding inside group box
        const GROUP_TITLE_H = 34;    // space for the group title bar
        const GROUP_GAP = 60;        // gap between groups in meta-layout

        // --- Step 1: Layout each group internally ---
        const groupSize = new Map();  // group → { width, height }
        for (const g of activeGroups) {
            const members = g._nodes.filter(n => nodeGroup.get(n.id) === g);
            const memberIds = new Set(members.map(n => n.id));
            const intraLinks = allLinks.filter(l => memberIds.has(l.origin_id) && memberIds.has(l.target_id));
            const sz = layoutNodeSet(members, intraLinks);
            groupSize.set(g, sz);
        }

        // --- Step 2: Build meta-graph items ---
        // Each group becomes a virtual "super-node" with the internal layout size.
        // Ungrouped nodes remain as individual items.
        const ungrouped = allNodes.filter(n => !nodeGroup.has(n.id));
        const metaItems = [];   // { id, isGroup, group?, node?, size: [w,h] }
        const metaIdMap = new Map();  // id → metaItem

        for (const g of activeGroups) {
            const sz = groupSize.get(g);
            const w = sz.width + GROUP_PAD * 2;
            const h = sz.height + GROUP_PAD * 2 + GROUP_TITLE_H;
            const item = {
                id: `__group_${activeGroups.indexOf(g)}`,
                isGroup: true, group: g,
                size: [w, h],
                pos: [0, 0],
                type: null,
                computeSize: null,
            };
            metaItems.push(item);
            metaIdMap.set(item.id, item);
            // Map each member node's id → this meta-item's id for edge mapping
            for (const n of g._nodes) {
                if (nodeGroup.get(n.id) === g) metaIdMap.set(n.id, item);
            }
        }
        for (const n of ungrouped) {
            const item = {
                id: n.id,
                isGroup: false, node: n,
                size: [nodeW(n), nodeH(n) - 10],
                pos: [0, 0],
                type: n.type,
                computeSize: null,
            };
            metaItems.push(item);
            metaIdMap.set(n.id, item);
        }

        // Build meta-links: remap inter-group / ungrouped edges to meta-item ids
        const metaLinkSet = new Set();
        const metaLinks = [];
        for (const link of allLinks) {
            const fromMeta = metaIdMap.get(link.origin_id);
            const toMeta = metaIdMap.get(link.target_id);
            if (!fromMeta || !toMeta || fromMeta === toMeta) continue; // skip intra-group
            const key = `${fromMeta.id}→${toMeta.id}`;
            if (metaLinkSet.has(key)) continue;
            metaLinkSet.add(key);
            metaLinks.push({
                origin_id: fromMeta.id,
                target_id: toMeta.id,
                origin_slot: link.origin_slot ?? 0,
            });
        }

        // --- Step 3: Layout the meta-graph ---
        layoutNodeSet(metaItems, metaLinks);

        // --- Step 4: Translate group-internal nodes to final global positions ---
        const LAYOUT_MARGIN = 140;
        for (const item of metaItems) {
            if (item.isGroup) {
                const g = item.group;
                const gx = item.pos[0] + LAYOUT_MARGIN + GROUP_PAD;
                const gy = item.pos[1] + LAYOUT_MARGIN + GROUP_PAD + GROUP_TITLE_H;
                for (const n of g._nodes) {
                    if (nodeGroup.get(n.id) !== g) continue;
                    n.pos[0] += gx;
                    n.pos[1] += gy;
                }
                // Resize group bounding box
                let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
                for (const n of g._nodes) {
                    if (nodeGroup.get(n.id) !== g) continue;
                    x0 = Math.min(x0, n.pos[0]);
                    y0 = Math.min(y0, n.pos[1]);
                    x1 = Math.max(x1, n.pos[0] + nodeW(n));
                    y1 = Math.max(y1, n.pos[1] + nodeH(n));
                }
                g.pos = [x0 - GROUP_PAD, y0 - GROUP_PAD - GROUP_TITLE_H];
                g.size = [x1 - x0 + GROUP_PAD * 2, y1 - y0 + GROUP_PAD * 2 + GROUP_TITLE_H];
            } else {
                item.node.pos[0] = item.pos[0] + LAYOUT_MARGIN;
                item.node.pos[1] = item.pos[1] + LAYOUT_MARGIN;
            }
        }
    }

    // ===================================================================
    //  Fit the viewport to show all laid-out nodes
    // ===================================================================
    {
        const PAD = 80;
        let bx0 = Infinity, by0 = Infinity, bx1 = -Infinity, by1 = -Infinity;
        for (const n of allNodes) {
            bx0 = Math.min(bx0, n.pos[0]);
            by0 = Math.min(by0, n.pos[1]);
            bx1 = Math.max(bx1, n.pos[0] + nodeW(n));
            by1 = Math.max(by1, n.pos[1] + nodeH(n));
        }
        // Include group boxes in bounding calculation
        for (const g of (graph._groups || [])) {
            if (g._nodes.length === 0) continue;
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

    // ===================================================================
    //  Sort router output / input ports to match target/source Y order
    // ===================================================================
    for (const node of allNodes) {
        if (!node.properties || node.properties.type !== "router") continue;
        if (!node.outputs || node.outputs.length < 2) continue;

        const slotInfo = node.outputs.map((output, i) => {
            let targetY = Infinity;
            if (output.links && output.links.length > 0) {
                const link = graph.links[output.links[0]];
                if (link) {
                    const tgt = nodeMap.get(link.target_id);
                    if (tgt) targetY = tgt.pos[1];
                }
            }
            return { output, targetY, oldSlot: i };
        });

        slotInfo.sort((a, b) => {
            if (a.output.name === "OTHER" && b.output.name !== "OTHER") return 1;
            if (b.output.name === "OTHER" && a.output.name !== "OTHER") return -1;
            return a.targetY - b.targetY;
        });

        if (slotInfo.every((info, i) => info.oldSlot === i)) continue;

        node.outputs = slotInfo.map(info => info.output);
        slotInfo.forEach((info, newSlot) => {
            (info.output.links || []).forEach(linkId => {
                const link = graph.links[linkId];
                if (link) link.origin_slot = newSlot;
            });
        });
    }

    for (const node of allNodes) {
        if (!node.inputs || node.inputs.length < 2) continue;

        const slotInfo = node.inputs.map((input, i) => {
            let sourceY = Infinity;
            if (input.link != null) {
                const link = graph.links[input.link];
                if (link) {
                    const src = nodeMap.get(link.origin_id);
                    if (src) sourceY = src.pos[1];
                }
            }
            return { input, sourceY, oldSlot: i };
        });

        slotInfo.sort((a, b) => a.sourceY - b.sourceY);

        if (slotInfo.every((info, i) => info.oldSlot === i)) continue;

        node.inputs = slotInfo.map(info => info.input);
        slotInfo.forEach((info, newSlot) => {
            if (info.input.link != null) {
                const link = graph.links[info.input.link];
                if (link) link.target_slot = newSlot;
            }
        });
    }

    canvas.setDirty(true, true);
}

/**
 * Initializes all toolbar button event listeners.
 */
export function initToolbar(graph, canvas, isDirty, {
    saveGraphFn,
    fetchGraphListFn,
    updateBreadcrumbsFn,
    loadGraphFn,
    stateInput
}) {
    // --- Add Node helper ---
    const addNode = (type) => {
        if (type === "langgraph/start" || type === "langgraph/end") {
            if (graph.findNodesByType(type).length > 0) {
                alert(`Only one ${type.split('/')[1].toUpperCase()} node is allowed.`);
                return;
            }
        }
        const node = LiteGraph.createNode(type);
        if (!node) return;
        node.pos = [canvas.canvas.width / 2, canvas.canvas.height / 2];
        graph.add(node);
    };

    // --- Button Bindings ---
    const startBtn = document.getElementById("add-start");
    if (startBtn) startBtn.addEventListener("click", () => addNode("langgraph/start"));

    const autoLayoutBtn = document.getElementById("auto-layout");
    if (autoLayoutBtn) autoLayoutBtn.addEventListener("click", () => autoLayout(graph, canvas));

    const runBtn = document.getElementById("run-graph");
    if (runBtn) runBtn.addEventListener("click", () => executeGraph(graph, canvas, isDirty));

    const stopBtn = document.getElementById("stop-graph");
    if (stopBtn) stopBtn.addEventListener("click", stopGraph);

    const debugBtn = document.getElementById("debug-mode");
    if (debugBtn) {
        const initialDebug = getDebugMode();
        debugBtn.classList.toggle("active", initialDebug);
        debugBtn.textContent = initialDebug ? "🐛 Debug Mode: ON" : "🐛 Debug Mode: OFF";
        debugBtn.title = initialDebug ? "Debug mode ON — pauses before every node" : "Debug mode OFF";

        debugBtn.addEventListener("click", () => {
            const enabled = !getDebugMode();
            setDebugMode(enabled);
            debugBtn.classList.toggle("active", enabled);
            debugBtn.textContent = enabled ? "🐛 Debug Mode: ON" : "🐛 Debug Mode: OFF";
            debugBtn.title = enabled ? "Debug mode ON — pauses before every node" : "Debug mode OFF";
        });
    }

    const rerunBtn = document.getElementById("rerun-graph");
    if (rerunBtn) {
        rerunBtn.addEventListener("click", async () => {
            const thread_id = getThreadId();
            if (thread_id) {
                pushLog(thread_id, "🔄 RERUN: Resetting and restarting graph...", "info");
                try {
                    const statusRes = await fetch(`${API_BASE}/graph_status/${thread_id}`);
                    if (statusRes.ok) {
                        const statusData = await statusRes.json();
                        if (statusData.status === "running") {
                            await fetch(`${API_BASE}/stop/${thread_id}`, { method: "POST" });
                            await new Promise(r => setTimeout(r, 600));
                        }
                    }
                } catch (e) {
                    console.warn("Failed to check/stop status during rerun:", e);
                }
                await clearGraphState(graph);
                await new Promise(r => setTimeout(r, 400));
            }
            await executeGraph(graph, canvas, isDirty);
        });
    }

    // --- Clear State Button ---
    const clearBtn = document.getElementById("clear-graph");
    if (clearBtn) {
        clearBtn.addEventListener("click", async () => {
            if (confirm("Are you sure you want to CLEAR STATE and logs?")) {
                const thread_id = getThreadId();
                try {
                    const statusRes = await fetch(`${API_BASE}/graph_status/${thread_id}`);
                    if (statusRes.ok) {
                        const statusData = await statusRes.json();
                        if (statusData.status === "running") {
                            console.info("[Clear State] Stopping active execution before reset...");
                            await fetch(`${API_BASE}/stop/${thread_id}`, { method: "POST" });
                            await new Promise(r => setTimeout(r, 500));
                        }
                    }
                } catch (e) {
                    console.error("Failed to check/stop status before clearing:", e);
                }
                clearGraphState(graph);
            }
        });
    }

    // --- Step Back Button ---
    const stepBackBtn = document.getElementById("step-back-btn");
    if (stepBackBtn) {
        stepBackBtn.addEventListener("click", async () => {
            const thread_id = getThreadId();

            try {
                const statusRes = await fetch(`${API_BASE}/graph_status/${thread_id}`);
                if (statusRes.ok) {
                    const statusData = await statusRes.json();
                    if (statusData.status === "running") {
                        alert("Cannot step back while execution is running. Please stop the execution first.");
                        return;
                    }
                }
            } catch (e) {
                console.error("Failed to check status before stepping back:", e);
            }

            if (confirm("Step back to the previous checkpoint in the execution history?")) {
                try {
                    addLog("Stepping back to previous checkpoint...", "info");
                    const res = await fetch(`${API_BASE}/step_back/${thread_id}`, {
                        method: "POST"
                    });

                    if (res.ok) {
                        const data = await res.json();
                        if (data.status === "success") {
                            addLog(`Successfully stepped back to checkpoint: ${data.previous_checkpoint}`, "completion");

                            // 1. Clear old glows first
                            if (graph._nodes) {
                                graph._nodes.forEach(node => {
                                    node.boxcolor = null;
                                    node._last_exec_time = null;
                                    node.setDirtyCanvas(true, true);
                                });
                            }

                            // 2. Fetch fresh status and apply glows + state widget
                            const subgraphNode = new URLSearchParams(window.location.search).get('subgraph_node');
                            const cleanPath = getCleanPath(subgraphNode);
                            let statusUrl = `${API_BASE}/graph_status/${thread_id}`;
                            if (cleanPath) statusUrl += `?subgraph_node=${encodeURIComponent(cleanPath)}`;
                            const statusRes = await fetch(statusUrl);
                            if (statusRes.ok) {
                                const statusData = await statusRes.json();
                                applyNodeGlows(graph, statusData);
                                updateStateWidget(statusData, graph);
                            }
                        } else {
                            addLog(`Step back failed: ${data.message}`, "error");
                            alert(`Failed to step back: ${data.message}`);
                        }
                    } else {
                        const errorData = await res.json();
                        addLog(`Step back error: ${errorData.message || "Unknown error"}`, "error");
                        alert(`Failed to step back: ${errorData.message || "Unknown error"}`);
                    }
                } catch (err) {
                    console.error("Step back request failed:", err);
                    addLog(`Step back failed: ${err.message}`, "error");
                    alert(`Failed to step back: ${err.message}`);
                }
            }
        });
    }

    // --- Restart Server Button ---
    const restartServerBtn = document.getElementById("restart-server");
    if (restartServerBtn) {
        restartServerBtn.addEventListener("click", async () => {
            if (isDirty.value) {
                const saveFirst = confirm("You have unsaved changes. Would you like to save before restarting?");
                if (saveFirst) {
                    await saveGraphFn();
                }
            }

            if (!confirm("Are you sure you want to restart the API server? This will abort any running sessions.")) return;

            try {
                addLog("System: Requesting server restart...", "warning");
                const res = await fetch(`${API_BASE}/restart`, { method: "POST" });
                if (res.ok) {
                    addLog("System: Server is restarting. Reconnecting...", "completion");
                    setTimeout(() => {
                        location.reload();
                    }, 2000);
                } else {
                    addLog(`System: Server restart failed (Status: ${res.status}).`, "error");
                }
            } catch (e) {
                addLog("System: Connection lost. The server is likely restarting. Reconnecting in 5s...", "info");
                setTimeout(() => {
                    location.reload();
                }, 5000);
            }
        });
    }

    // --- New Graph Button ---
    const newGraphBtn = document.getElementById("new-graph");
    if (newGraphBtn) {
        newGraphBtn.addEventListener("click", () => {
            if (isDirty.value && !confirm("You have unsaved changes. Are you sure you want to create a new graph?")) {
                return;
            }

            graph.clear();
            graph.extra = {};
            graph.extra.initial_state = JSON.stringify({ input: "" }, null, 2);
            if (stateInput) stateInput.value = graph.extra.initial_state;

            const logContent = document.getElementById("log-content");
            if (logContent) logContent.innerHTML = "";
            updateStateWidget({}, graph);

            const selector = document.getElementById("graph-selector");
            if (selector) selector.value = "unsaved_graph.json";

            isDirty.value = false;
            addLog("System: New graph initialized. Awaiting creation...", "info");
        });
    }

    // --- Save Button ---
    const saveBackendBtn = document.getElementById("save-graph-backend");
    if (saveBackendBtn) {
        saveBackendBtn.addEventListener("click", saveGraphFn);
    }

    // --- Global Keyboard Shortcuts ---
    window.addEventListener("keydown", (e) => {
        const isMac = /Mac|iPhone|iPod|iPad/.test(navigator.platform) || /Mac|iPhone|iPod|iPad/.test(navigator.userAgent);
        const key = e.key.toLowerCase();

        const isSaveKey = isMac ? (e.metaKey && key === 's') : (e.ctrlKey && key === 's');
        const isUndo = isMac ? (e.metaKey && key === 'z' && !e.shiftKey) : (e.ctrlKey && key === 'z' && !e.shiftKey);
        const isRedo = isMac ? (e.metaKey && key === 'z' && e.shiftKey) : (e.ctrlKey && (key === 'y' || (key === 'z' && e.shiftKey)));

        // Allow native undo/redo in text inputs, textareas, and contenteditable elements
        const el = document.activeElement;
        const isEditable = el && (
            el.tagName === "INPUT" ||
            el.tagName === "TEXTAREA" ||
            el.isContentEditable
        );

        if (isSaveKey) {
            e.preventDefault();
            saveGraphFn();
        } else if (isUndo && !isEditable && canUndo()) {
            e.preventDefault();
            undo();
        } else if (isRedo && !isEditable && canRedo()) {
            e.preventDefault();
            redo();
        }
    }, true);
}
