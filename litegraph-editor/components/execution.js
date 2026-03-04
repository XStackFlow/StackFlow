/**
 * Execution engine — graph execution, status polling, state widget updates,
 * active session monitoring, and the "Mark as Completed" workflow.
 */

import { LiteGraph } from 'litegraph.js';
import { escapeHTML, getNodeLogicalId, getCleanPath } from './utils.js';
import { addLog, pushLog, getLogContent } from './logging.js';
import { getSessionId, setSessionId, getSessions, setSessions, updateSessionUI } from './session.js';
import { openJSONEditor } from './json_editor.js';
import { nodeTypeMetadata } from './graph_manager.js';

const API_BASE = "http://localhost:8000";

// --- Switch Graph callback (registered from main.js to avoid circular imports) ---
let _switchGraph = null;
export function registerSwitchGraph(fn) { _switchGraph = fn; }

// --- Execution State ---
let executionThreadId = null;
let statusInterval = null;
let lastLogIndex = 0;
let lastStateData = { status: 'idle', active_nodes: [], next_nodes: [] };
let debugMode = localStorage.getItem("debugMode") === "true";
let pollGeneration = 0;  // Incremented on context switch to discard stale poll responses

export function getDebugMode() { return debugMode; }
export function setDebugMode(val) { debugMode = val; localStorage.setItem("debugMode", val); }

/**
 * Returns the most recent state data.
 */
export function getLastStateData() {
    return lastStateData;
}

/**
 * Computes the current thread ID from URL params / session / graph selector.
 */
export function getThreadId() {
    const urlParams = new URLSearchParams(window.location.search);
    const urlThreadId = urlParams.get('thread_id');
    const sessionId = getSessionId();

    // If URL has a thread_id, we check if it matches the current global sessionId.
    if (urlThreadId) {
        const parts = urlThreadId.split('_');
        const urlSid = parts[parts.length - 1];
        if (urlSid === sessionId) {
            return urlThreadId;
        }
        // Reconstruct: keep everything before the last underscore as the root
        const root = parts.slice(0, -1).join('_') || urlThreadId;
        return `${root}_${sessionId}`;
    }

    const selector = document.getElementById("graph-selector");
    const graphName = (selector ? selector.value : "unsaved_graph").replace(".json", "");
    return `${graphName}_${sessionId}`;
}

/**
 * Renders a value as an inline primitive span.
 */
function sfPrimitive(v) {
    if (v === null)             return `<span class="sf-jv-null">null</span>`;
    if (typeof v === 'boolean') return `<span class="sf-jv-bool">${v}</span>`;
    if (typeof v === 'number')  return `<span class="sf-jv-num">${v}</span>`;
    return `<span class="sf-jv-str">"${escapeHTML(String(v))}"</span>`;
}

/**
 * Recursively builds a collapsible JSON tree (Chrome DevTools style).
 * Each collapsible row gets a `data-path` attribute so open/closed state
 * can be saved and restored across re-renders.
 * @param {*}      value       — the value to render
 * @param {string|number|null} key — property key (null = root)
 * @param {boolean} isArrParent — whether the parent is an array
 * @param {string}  path        — dot-separated path string for this node
 */
function sfBuildTree(value, key = null, isArrParent = false, path = '__root__') {
    const isComplex = value !== null && typeof value === 'object';

    if (!isComplex) {
        // Leaf row — no collapsible wrapper needed
        const keyHtml = key !== null
            ? (isArrParent
                ? `<span class="sf-idx">${key}</span>`
                : `<span class="sf-state-key">${escapeHTML(String(key))}</span>`)
            : '';
        const sep = key !== null ? `<span class="sf-state-sep">:&nbsp;</span>` : '';
        return `<div class="sf-state-leaf">${keyHtml}${sep}${sfPrimitive(value)}</div>`;
    }

    // Collapsible row for object / array
    const isArr   = Array.isArray(value);
    const entries = isArr ? value.map((v, i) => [i, v]) : Object.entries(value);
    const ob      = isArr ? '[' : '{';
    const cb      = isArr ? ']' : '}';
    const count   = entries.length;
    const hint    = count > 0 ? `${count} ${isArr ? 'items' : 'keys'}` : '';

    const keyHtml = key !== null
        ? (isArrParent
            ? `<span class="sf-idx">${key}</span>`
            : `<span class="sf-state-key">${escapeHTML(String(key))}</span>`)
        : '';
    const sep = key !== null ? `<span class="sf-state-sep">:&nbsp;</span>` : '';

    const children = count === 0
        ? ''
        : entries.map(([k, v]) => sfBuildTree(v, k, isArr, `${path}.${k}`)).join('');

    return `<div class="sf-state-row" data-path="${escapeHTML(path)}">` +
        `<div class="sf-state-header" onclick="this.closest('.sf-state-row').classList.toggle('open')">` +
        `<span class="sf-state-arrow"></span>` +
        `${keyHtml}${sep}` +
        `<span class="sf-state-brace">${ob}</span>` +
        `<span class="sf-state-hint">${escapeHTML(hint)}</span>` +
        `<span class="sf-state-brace sf-close">${cb}</span>` +
        `</div>` +
        `<div class="sf-state-children">${children}</div>` +
        `<div class="sf-state-closing">${cb}</div>` +
        `</div>`;
}

function buildCollapsibleState(obj) {
    const rootHtml = sfBuildTree(obj, null, false, '__root__');
    return `<div class="sf-state-tree">${rootHtml}</div>`;
}

/**
 * Updates the Current State widget in the sidebar.
 */
export function updateStateWidget(data, graph) {
    if (data) {
        lastStateData = data;
    } else {
        data = lastStateData;
    }

    const stateContent = document.getElementById("current-state-content");
    if (!stateContent) return;

    const displayState = data.last_state || data.result;
    let html = "";

    // Helper to format node names with visible IDs
    const formatNode = (nodeName) => {
        if (!nodeName) return "Unknown";
        return nodeName.replace(/@@/g, ">");
    };

    // 1. Status Section (Ultra Compact)
    const status = (data.status || 'idle').toLowerCase();
    const statusColor = status === "running" ? "#3b82f6" : (status === "completed" ? "#10b981" : "#777");
    const activeNodes = data.active_nodes || [];
    const activeLabel = activeNodes.length > 0 ? activeNodes.map(n => escapeHTML(formatNode(n))).join(", ") : "None";

    const nextList = data.next_nodes_global || data.next_nodes || [];
    const nextNodesFormatted = nextList.length
        ? `[${nextList.map(n => escapeHTML(n)).join(", ")}]`
        : (status === "completed" ? "Finished" : "None");

    // 2. Breakpoints Section
    const allNodes = graph._nodes || [];
    const interruptedNodes = allNodes.filter(n => n.properties.interrupt_before === true);
    let breakpointsHtml = "";
    if (interruptedNodes.length > 0) {
        const bpNames = interruptedNodes.map(n => {
            const base = n.properties.name || n.title.replace("🛑 ", "");
            const sanitized = base.replace(/[^a-zA-Z0-9_]/g, '_');
            return escapeHTML(`${sanitized}_${n.id}`);
        });
        const bpFormatted = `[${bpNames.join(", ")}]`;

        breakpointsHtml = `<div style="display: flex; gap: 4px; align-items: baseline; color: #a855f7; font-size: 10px; margin-top: 2px;">` +
            `<span style="font-weight: bold; opacity: 0.7; flex-shrink: 0;">STOP:</span>` +
            `<span style="color: #eee; font-family: monospace; word-break: break-all;">${bpFormatted}</span>` +
            `</div>`;
    }

    const namespaceLabel = displayState && displayState.__subgraph_node__
        ? `<div style="color: #f59e0b; font-size: 9px; margin-bottom: 4px; font-weight: bold; text-transform: uppercase; border-bottom: 1px solid rgba(245, 158, 11, 0.3); padding-bottom: 2px;">SCOPE: Subgraph [${escapeHTML(displayState.__subgraph_node__)}]</div>`
        : '';

    html += `<div style="margin-bottom: 4px; padding: 6px 8px; background: rgba(30, 30, 30, 0.7); border-radius: 4px; border: 1px solid #333; font-size: 11px; line-height: 1.2;">` +
        namespaceLabel +
        `<div style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 2px;">` +
        `<span style="font-weight: bold; color: #666; font-size: 8px; text-transform: uppercase;">STATUS: <span style="color: ${statusColor};">${escapeHTML(status)}</span></span>` +
        `${data.status === "running" ? `<span style="color: #3b82f6; font-size: 10px;">RUNNING: <b>${activeLabel}</b></span>` : ''}` +
        `</div>` +
        `<div style="display: flex; gap: 4px; align-items: baseline; color: #3b82f6; font-size: 10px; margin-top: 2px;">` +
        `<span style="font-weight: bold; opacity: 0.7; flex-shrink: 0;">NEXT:</span>` +
        `<span style="color: #eee; font-family: monospace; word-break: break-all;">${nextNodesFormatted}</span>` +
        `</div>` +
        breakpointsHtml +
        `</div>`;

    // 1b. Module Review Button — shown when an active node has a `review_ui` property
    // ReviewGate nodes block while waiting for user input (graph stays running).
    // The button appears when the node is currently active (blue glow).
    if (status === 'running' && graph._nodes && activeNodes.length > 0) {
        const activeSet = new Set(activeNodes.map(n => n.toLowerCase().replace(/[^a-z0-9@_]/g, '')));
        for (const node of graph._nodes) {
            if (!node.properties.review_ui) continue;
            const nodeLogical = getNodeLogicalId(node).toLowerCase().replace(/[^a-z0-9@_]/g, '');
            if (!activeSet.has(nodeLogical)) continue;
            // This node has a review_ui and is currently active (waiting for review)
            const moduleId = (node.type.split('/')[0]) || 'unknown';
            const reviewUiPath = node.properties.review_ui;
            const qParams = new URLSearchParams({ thread_id: executionThreadId || '' });
            for (const [k, v] of Object.entries(node.properties)) {
                if (['name', 'type', 'review_ui'].includes(k)) continue;
                if (v !== undefined && v !== null && v !== '') qParams.set(k, v);
            }
            const reviewPageUrl = `${API_BASE}/modules/${moduleId}/ui/${reviewUiPath}?${qParams}`;
            const label = node.properties.stage || node.title || 'Review';
            html += `<div style="margin-bottom: 6px; padding: 8px; background: rgba(236, 72, 153, 0.1); border: 1px solid rgba(236, 72, 153, 0.3); border-radius: 6px; text-align: center;">` +
                `<a href="${reviewPageUrl}" target="_blank" style="color: #EC4899; font-size: 12px; font-weight: 600; text-decoration: none; display: inline-flex; align-items: center; gap: 6px;">` +
                `<span style="font-size: 16px;">&#9998;</span> Open Review (${escapeHTML(label)})` +
                `</a>` +
                `</div>`;
            break;
        }
    }

    // 2. Global State Section
    if (displayState && Object.keys(displayState).length > 0) {
        // Remove internal flag before displaying
        const cleanState = { ...displayState };
        delete cleanState.__subgraph_node__;

        html += `<div style="font-weight: bold; color: #10b981; margin-bottom: 2px; font-size: 9px; text-transform: uppercase; padding-left: 6px;">Current State</div>`;
        html += buildCollapsibleState(cleanState);
    } else {
        html += `<div class="no-state" style="padding: 10px; text-align: center; color: #555; font-size: 10px;">No active state.</div>`;
    }

    // Save which paths are currently open so we can restore them after the re-render.
    // New paths default to open only if they are the root node.
    const openPaths = new Set(
        [...stateContent.querySelectorAll('.sf-state-row.open')].map(el => el.dataset.path)
    );
    // If nothing was open yet (first render), open the root by default.
    const firstRender = openPaths.size === 0;

    stateContent.innerHTML = html;

    // Restore open state: re-add 'open' class to any row whose path was previously open.
    stateContent.querySelectorAll('.sf-state-row').forEach(el => {
        const p = el.dataset.path;
        if (firstRender ? p === '__root__' : openPaths.has(p)) {
            el.classList.add('open');
        }
    });
}

/**
 * Applies visual node glowing based on status data.
 */
export function applyNodeGlows(graph, data) {
    if (!graph._nodes) return;

    const activeNodes = data.active_nodes || [];

    // Detect if any active node is a subgraph — while a subgraph is executing,
    // suppress green completion glows on sibling parent-graph nodes to avoid
    // the misleading impression that the parent graph is still running.
    const activeNodeIds = new Set(activeNodes);
    const subgraphIsActive = activeNodes.length > 0 && graph._nodes.some(n =>
        n.type === "langgraph/subgraph" && activeNodeIds.has(getNodeLogicalId(n))
    );

    graph._nodes.forEach(node => {
        const baseName = node.properties.name || node.title;
        const nodeName = getNodeLogicalId(node);

        // Current Active Status (Blue)
        let isActive = activeNodeIds.has(nodeName);
        let isCompletedEnd = false;

        // Special case: If graph is COMPLETED, mark END nodes for white glow
        if (data.status === "completed" && baseName && baseName.startsWith("END")) {
            isCompletedEnd = true;
        }

        // Check if this node is in the next_nodes list (for failed/interrupted status)
        const isNextNode = (data.next_nodes || []).some(nextName => {
            const cleanNext = nextName.toLowerCase().replace(/[^a-z0-9@]/g, '');
            const cleanNode = nodeName.toLowerCase().replace(/[^a-z0-9@]/g, '');

            // 1. Strict match
            if (nextName === nodeName || cleanNext === cleanNode) return true;

            // 2. Namespaced path match
            if (nextName.startsWith(nodeName + "@@")) return true;

            return false;
        });

        if (isCompletedEnd) {
            node.boxcolor = "#a1a1aa";
        } else if (isActive) {
            node.boxcolor = (data.status === "failed") ? "#ef4444" : "#3b82f6";
        } else if (data.status === "failed" && isNextNode) {
            node.boxcolor = "#f59e0b"; // Orange
        } else if (data.status === "interrupted" && isNextNode) {
            node.boxcolor = "#a1a1aa";
        } else {
            node.boxcolor = null;
        }

        // Green completion pulse: skip non-active nodes while a subgraph is running
        // to prevent cyclic parent nodes from continuously re-glowing.
        if (data.node_elapsed && data.node_elapsed[nodeName] !== undefined && (!subgraphIsActive || isActive)) {
            const finishTime = data.node_elapsed[nodeName] * 1000;
            if (!node._last_exec_time || Math.abs(node._last_exec_time - finishTime) > 100) {
                node._last_exec_time = finishTime;
            }
        } else if (subgraphIsActive && !isActive) {
            // Clear any stale glow so it doesn't linger while the subgraph runs
            node._last_exec_time = null;
        }

        node.setDirtyCanvas(true, true);
    });
}

/**
 * Reloads logs and status for the current or specified thread.
 */
export async function reloadLogs(graph, canvas, targetThreadId = null) {
    const logContent = getLogContent();
    if (!logContent) return;

    // Stop any existing polling before context switch
    pollGeneration++;
    if (statusInterval) {
        clearInterval(statusInterval);
        statusInterval = null;
    }

    // Clear any existing node glows/status colors before reloading
    if (graph && graph._nodes) {
        graph._nodes.forEach(node => {
            node.boxcolor = null;
            node._last_exec_time = null;
            node.setDirtyCanvas(true, true);
        });
    }

    logContent.innerHTML = "";
    lastLogIndex = 0;

    const urlParams = new URLSearchParams(window.location.search);
    let threadId = targetThreadId || getThreadId();
    const subgraphNode = urlParams.get('subgraph_node');
    const cleanPath = getCleanPath(subgraphNode);
    const sessionId = getSessionId();

    console.log(`[Status] reloadLogs: threadId=${threadId}, cleanPath=${cleanPath}`);

    // If no specific threadId was provided and not in URL, follow active sessions
    if (!targetThreadId && !urlParams.get('thread_id')) {
        try {
            const activeRes = await fetch(`${API_BASE}/active_sessions`);
            if (activeRes.ok) {
                const activeData = await activeRes.json();
                const defaultThreadId = getThreadId();

                const exactMatch = activeData.active_sessions.find(s => s.thread_id === defaultThreadId);
                const sessionMatch = activeData.active_sessions.find(s => s.thread_id.endsWith("_" + sessionId));

                if (exactMatch) {
                    threadId = exactMatch.thread_id;
                } else if (sessionMatch) {
                    threadId = sessionMatch.thread_id;
                    console.log(`Following active execution for current session: ${threadId}`);
                }
            }
        } catch (e) {
            console.warn("Could not check active sessions, using default threadId", e);
        }
    }

    executionThreadId = threadId;

    // Sync URL
    const url = new URL(window.location);
    if (threadId) {
        url.searchParams.set('thread_id', threadId);

        // Sync local sessionId if thread follows graphName_sessionId pattern
        const parts = threadId.split("_");
        if (parts.length > 1) {
            const potentialSessionId = parts[parts.length - 1];
            if (potentialSessionId !== sessionId) {
                console.log(`Syncing session ID from thread: ${potentialSessionId}`);
                setSessionId(potentialSessionId);
                updateSessionUI();
            }
        }
    } else {
        url.searchParams.delete('thread_id');
    }

    if (subgraphNode) {
        url.searchParams.set('subgraph_node', subgraphNode);
    } else {
        url.searchParams.delete('subgraph_node');
    }

    window.history.replaceState(window.history.state, "", url);

    addLog(`Loading history for session: ${sessionId}...`, "info");

    try {
        // 1. Fetch historical logs
        const logRes = await fetch(`${API_BASE}/logs/${threadId}`);
        if (logRes.ok) {
            const logData = await logRes.json();
            if (logData.logs && logData.logs.length > 0) {
                if (logData.logs.length >= 100) {
                    addLog("--- Showing only the last 100 lines of history ---", "warning");
                }
                logData.logs.forEach(log => {
                    const logText = log.trim();
                    let type = "info";
                    const logLower = logText.toLowerCase();
                    if (logLower.includes("error:") || logLower.includes("failed:") || logLower.includes("exception:")) {
                        type = "error";
                    } else if (logLower.includes("warning:")) {
                        type = "warning";
                    } else if (logLower.includes("success") || logLower.includes("completed successfully")) {
                        type = "completion";
                    }
                    addLog(logText, type);
                });
                lastLogIndex = logData.logs.length;
            } else {
                addLog("No logs found for this session.", "info");
            }
        }

        // 2. Check current status once
        let statusUrl = `${API_BASE}/graph_status/${threadId}`;
        if (cleanPath) statusUrl += `?subgraph_node=${encodeURIComponent(cleanPath)}`;

        const statusRes = await fetch(statusUrl);
        if (statusRes.ok) {
            const statusData = await statusRes.json();

            // Apply current execution state visually
            applyNodeGlows(graph, statusData);

            // Update Current State Widget on load
            updateStateWidget(statusData, graph);

            // Resume polling ONLY if it's still running
            if (statusData.status === "running") {
                addLog("Detecting active execution. Resuming live monitoring...", "info");
                if (statusData.logs) {
                    lastLogIndex = statusData.logs.length;
                }
                startStatusPolling(graph, canvas);
            } else {
                executionThreadId = null;
            }
        }
    } catch (e) {
        console.error("Failed to load session logs:", e);
    }
}

/**
 * Silently saves the current graph to the backend (used before execution).
 */
export async function silentSaveGraph(graph, isDirty, graphName) {
    if (!graphName) return;
    const cleanName = graphName.replace(".json", "");
    try {
        const data = JSON.parse(JSON.stringify(graph.serialize()));
        // Clean transient visual properties
        if (data.nodes) {
            data.nodes.forEach(node => {
                if (node.boxcolor) delete node.boxcolor;
                if (node.color) delete node.color;
            });
        }

        // Persist module dependency manifest
        if (data.nodes) {
            const deps = {};
            data.nodes.forEach(node => {
                if (!node.type || node.type.startsWith("langgraph/")) return;
                const meta = nodeTypeMetadata[node.type];
                if (!meta || !meta.module_id) return;
                if (deps[meta.module_id]) return;
                deps[meta.module_id] = {
                    origin: meta.origin || "builtin",
                    source_url: meta.source_url || null,
                };
            });
            if (Object.keys(deps).length > 0) {
                data._module_deps = deps;
            }
        }

        const response = await fetch(`${API_BASE}/save_graph/${cleanName}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data)
        });
        if (response.ok) {
            isDirty.value = false;
        }
    } catch (e) {
        console.warn("Silent save failed:", e);
    }
}

/**
 * Executes the current graph.
 */
export async function executeGraph(graph, canvas, isDirty) {
    lastLogIndex = 0;
    const stateContent = document.getElementById("current-state-content");
    if (stateContent) stateContent.innerHTML = `<div class="no-state">Starting execution...</div>`;

    const stateInput = document.getElementById("state-json-input");
    const selector = document.getElementById("graph-selector");
    const graphName = (selector ? selector.value : "unsaved_graph").replace(".json", "");
    const sessionId = getSessionId();

    let params = {};
    try {
        params = JSON.parse(stateInput.value);
    } catch (e) {
        alert("Invalid Initial State JSON. Please fix it before running.");
        return;
    }

    try {
        let threadId = `${graphName}_${sessionId}`;

        // AUTO-SAVE: Always save the graph before running
        await silentSaveGraph(graph, isDirty, graphName);

        let payload = {
            root_graph_id: graphName,
            params: params,
            thread_id: threadId,
            debug_mode: debugMode,
        };

        const urlParams = new URLSearchParams(window.location.search);
        const subgraphNode = urlParams.get('subgraph_node');

        // If inside a subgraph, resume the PARENT graph execution
        if (subgraphNode) {
            const parentThreadId = urlParams.get('thread_id');
            if (parentThreadId) {
                threadId = parentThreadId;

                const rootGraphId = threadId.endsWith('_' + sessionId)
                    ? threadId.slice(0, -(sessionId.length + 1))
                    : threadId;

                payload = {
                    root_graph_id: rootGraphId,
                    params: params,
                    thread_id: threadId,
                    debug_mode: debugMode,
                };
                addLog(`System: Resuming execution for parent graph ${rootGraphId}...`, "info");
            }
        }

        pushLog(threadId, `Initiating execution for ${threadId}...`, "info");

        const response = await fetch(`${API_BASE}/execute`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });

        const data = await response.json();

        // Check for validation errors
        if (data.status === "error") {
            addLog(`Graph validation failed: ${data.message}`, "error");
            alert(`Failed to start execution:\n\n${data.message}`);
            console.error("Graph validation error:", data);
            if (data.traceback) {
                console.error("Traceback:", data.traceback);
            }
            return;
        }

        if (data.thread_id) {
            executionThreadId = data.thread_id;

            // Sync URL immediately
            const url = new URL(window.location);
            url.searchParams.set('thread_id', executionThreadId);
            window.history.replaceState(window.history.state, "", url);

            pushLog(executionThreadId, `Execution started. Thread: ${executionThreadId}`, "info");
            startStatusPolling(graph, canvas);
        } else {
            pushLog(threadId, `Failed to start: ${data.detail || 'Unknown error'}`, "error");
            console.error("Execution failed to start:", data);
        }
    } catch (err) {
        console.error("Execution request failed:", err);
    }
}

/**
 * Clears the current graph execution state & logs.
 */
export async function clearGraphState(graph) {
    const thread_id = getThreadId();
    if (!thread_id) return;

    try {
        await fetch(`${API_BASE}/reset/${thread_id}`, { method: "DELETE" });

        // Stop any active status polling
        if (statusInterval) {
            clearInterval(statusInterval);
            statusInterval = null;
        }
        executionThreadId = null;

        // Clear local UI logs and highlights
        const logContent = getLogContent();
        if (logContent) logContent.innerHTML = "";
        lastLogIndex = 0;

        // Update the state widget to show idle/empty
        updateStateWidget({}, graph);

        // Clear all active highlights on the nodes
        if (graph._nodes) {
            graph._nodes.forEach(node => {
                node.boxcolor = null;
                node._last_exec_time = null;
                node.setDirtyCanvas(true, true);
            });
        }

        addLog(`Persistent logs deleted for thread ${thread_id}`, "info");

    } catch (e) {
        console.error("Failed to clear state:", e);
        addLog(`Error: Fail to clear state: ${e.message}`, "error");
    }
}

/**
 * Starts polling the backend for execution status updates.
 */
export function startStatusPolling(graph, canvas) {
    if (statusInterval) clearInterval(statusInterval);
    const myGeneration = pollGeneration;
    statusInterval = setInterval(async () => {
        if (!executionThreadId) return;
        // Discard if context has switched (e.g. user navigated into/out of a subgraph)
        if (pollGeneration !== myGeneration) {
            clearInterval(statusInterval);
            statusInterval = null;
            return;
        }
        try {
            const urlParams = new URLSearchParams(window.location.search);
            let subgraphNode = urlParams.get('subgraph_node');

            // If a node is selected in the UI and it's a subgraph, prioritize showing its state
            if (canvas.selected_nodes) {
                const selectedList = Object.values(canvas.selected_nodes);
                if (selectedList.length === 1) {
                    const node = selectedList[0];
                    if (node.type === "langgraph/subgraph") {
                        const nodeName = getNodeLogicalId(node);
                        subgraphNode = nodeName;
                    }
                }
            }

            const cleanPath = getCleanPath(subgraphNode);
            let statusUrl = `${API_BASE}/graph_status/${executionThreadId}`;
            if (cleanPath) statusUrl += `?subgraph_node=${encodeURIComponent(cleanPath)}`;

            console.debug(`[Status] Polling: ${statusUrl}`);

            const res = await fetch(statusUrl);
            if (!res.ok) return;
            // Double-check generation after await — context may have switched during fetch
            if (pollGeneration !== myGeneration) return;
            const data = await res.json();
            console.log(`[Status] Received for ${executionThreadId}:`, data);

            // Check if we are viewing a subgraph and if the state is correctly scoped
            if (urlParams.get('subgraph_node') && data.last_state && !data.last_state.__subgraph_node__) {
                console.warn("[Status] Subgraph node is active in URL but response has no __subgraph_node__ flag. State might be from root.");
            }

            // Process new backend logs
            const incomingLogs = data.new_logs || [];
            if (data.logs && data.logs.length > lastLogIndex) {
                for (let i = lastLogIndex; i < data.logs.length; i++) {
                    incomingLogs.push(data.logs[i]);
                }
                lastLogIndex = data.logs.length;
            }

            if (incomingLogs.length > 0) {
                incomingLogs.forEach(log => {
                    let type = "info";
                    const logLower = log.toLowerCase();
                    if (logLower.includes("error:") || logLower.includes("failed:") || logLower.includes("exception:")) {
                        type = "error";
                    } else if (logLower.includes("warning:")) {
                        type = "warning";
                    } else if (logLower.includes("success") || logLower.includes("completed successfully")) {
                        type = "completion";
                    }
                    addLog(log, type);
                });
            }

            // Visual node glowing & timing
            applyNodeGlows(graph, data);

            // Update Current State Widget
            updateStateWidget(data, graph);

            // Stop polling when terminal state is reached.
            // Interrupted counts as terminal — the graph is paused, not running.
            // Polling restarts when the user resumes execution.
            const terminalStates = ["completed", "failed", "interrupted"];
            if (terminalStates.includes(data.status)) {
                console.log(`Execution reached terminal state: ${data.status}. Stopping polling.`);
                if (statusInterval) {
                    clearInterval(statusInterval);
                    statusInterval = null;
                }
            }

            // Trigger a faster refresh of the active sessions list
            updateActiveSessions(graph, canvas);
        } catch (e) {
            console.error("Polling error:", e);
        }
    }, 1000);
}

// --- Recently Stopped Sessions Tracking ---
let previousActiveThreadIds = new Set();
let recentlyStoppedSessions = [];
const STOPPED_SESSION_MAX = 10;
const STOPPED_SESSION_TTL_MS = 5 * 60 * 1000; // 5 minutes

/**
 * Renders the recently stopped sessions list.
 */
function renderStoppedSessions(graph, canvas) {
    const stoppedContainer = document.getElementById("stopped-sessions-list");
    if (!stoppedContainer) return;

    // Prune expired entries
    const now = Date.now();
    recentlyStoppedSessions = recentlyStoppedSessions.filter(s => (now - s.stoppedAt) < STOPPED_SESSION_TTL_MS);

    // Deduplicate by thread_id (keep first/most-recent occurrence)
    const seenIds = new Set();
    recentlyStoppedSessions = recentlyStoppedSessions.filter(s => {
        if (seenIds.has(s.thread_id)) return false;
        seenIds.add(s.thread_id);
        return true;
    });

    if (recentlyStoppedSessions.length === 0) {
        stoppedContainer.innerHTML = '<div class="no-sessions">No recently stopped sessions</div>';
        return;
    }

    stoppedContainer.innerHTML = "";
    recentlyStoppedSessions.forEach(session => {
        const item = document.createElement("div");
        const statusClass = session.status ? `status-${session.status}` : "";
        item.className = `stopped-session-item ${statusClass}`;

        const parts = session.thread_id.split("_");
        const sId = parts.length > 1 ? parts[parts.length - 1] : session.thread_id;
        const gName = parts.length > 1 ? parts.slice(0, -1).join("_") : "unknown";

        const stoppedTime = new Date(session.stoppedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        const statusLabel = session.status || "stopped";

        item.innerHTML = `
            <div class="active-session-info">
                <span class="active-session-id">${sId} (${gName})</span>
                <span class="active-session-node">
                    <span class="stopped-session-status">${statusLabel}</span> · ${stoppedTime}
                </span>
            </div>
            <div class="active-session-indicator"></div>
        `;

        item.onclick = async () => {
            const selector = document.getElementById("graph-selector");
            if (gName !== "unknown") {
                const filename = gName.endsWith(".json") ? gName : `${gName}.json`;
                if (selector && selector.value !== filename && _switchGraph) {
                    await _switchGraph(filename);
                }
            }

            const sessions = getSessions();
            setSessionId(sId);

            if (!sessions.includes(sId)) {
                sessions.unshift(sId);
                if (sessions.length > 10) sessions.pop();
                setSessions(sessions);
            }

            updateSessionUI();
            reloadLogs(graph, canvas, session.thread_id);
        };

        stoppedContainer.appendChild(item);
    });
}

/**
 * Updates the active sessions panel in the sidebar.
 */
export async function updateActiveSessions(graph, canvas, reloadLogsFn) {
    const listContainer = document.getElementById("active-sessions-list");
    if (!listContainer) return;

    try {
        const res = await fetch(`${API_BASE}/active_sessions`);
        if (!res.ok) return;
        const data = await res.json();

        const currentActiveIds = new Set();

        if (data.active_sessions && data.active_sessions.length > 0) {
            listContainer.innerHTML = "";
            data.active_sessions.forEach(session => {
                currentActiveIds.add(session.thread_id);

                const item = document.createElement("div");
                item.className = "active-session-item";

                const parts = session.thread_id.split("_");
                const sId = parts.length > 1 ? parts[parts.length - 1] : session.thread_id;
                const gName = parts.length > 1 ? parts.slice(0, -1).join("_") : "unknown";

                item.innerHTML = `
                    <div class="active-session-info">
                        <span class="active-session-id">${sId} (${gName})</span>
                        <span class="active-session-node">Node: ${session.active_nodes?.[0] || 'Idle'}</span>
                    </div>
                    <div class="active-session-indicator"></div>
                `;

                item.onclick = async () => {
                    const selector = document.getElementById("graph-selector");
                    if (gName !== "unknown") {
                        const filename = gName.endsWith(".json") ? gName : `${gName}.json`;
                        if (selector && selector.value !== filename && _switchGraph) {
                            await _switchGraph(filename);
                        }
                    }

                    const sessions = getSessions();
                    setSessionId(sId);

                    if (!sessions.includes(sId)) {
                        sessions.unshift(sId);
                        if (sessions.length > 10) sessions.pop();
                        setSessions(sessions);
                    }

                    updateSessionUI();
                    reloadLogs(graph, canvas, session.thread_id);
                };

                listContainer.appendChild(item);
            });
        } else {
            listContainer.innerHTML = '<div class="no-sessions">No sessions currently running</div>';
        }

        // Detect sessions that just stopped (were active last poll but not now)
        if (previousActiveThreadIds.size > 0) {
            for (const prevId of previousActiveThreadIds) {
                if (!currentActiveIds.has(prevId)) {
                    // This session just stopped — add to recently stopped
                    // Avoid duplicates
                    if (!recentlyStoppedSessions.some(s => s.thread_id === prevId)) {
                        // Try to determine final status via a quick status check
                        let finalStatus = "stopped";
                        try {
                            const statusRes = await fetch(`${API_BASE}/graph_status/${prevId}`);
                            if (statusRes.ok) {
                                const statusData = await statusRes.json();
                                finalStatus = statusData.status || "stopped";
                            }
                        } catch (e) {
                            // Ignore — default to "stopped"
                        }

                        recentlyStoppedSessions.unshift({
                            thread_id: prevId,
                            stoppedAt: Date.now(),
                            status: finalStatus
                        });

                        // Cap the list
                        if (recentlyStoppedSessions.length > STOPPED_SESSION_MAX) {
                            recentlyStoppedSessions.pop();
                        }
                    }
                }
            }
        }

        previousActiveThreadIds = currentActiveIds;

        // Render stopped sessions
        renderStoppedSessions(graph, canvas);

    } catch (e) {
        console.error("Failed to update active sessions:", e);
    }
}

/**
 * Stops the currently running graph execution.
 */
export async function stopGraph() {
    const urlParams = new URLSearchParams(window.location.search);
    const threadId = executionThreadId || urlParams.get('thread_id');

    if (threadId) {
        pushLog(threadId, "========================================", "info");
        pushLog(threadId, "🛑 STOPPING: Requesting termination", "info");
        try {
            await fetch(`${API_BASE}/stop/${threadId}`, { method: "POST" });
        } catch (e) {
            console.error("Failed to send stop request:", e);
        }
    } else {
        addLog("No active execution found to stop.", "warning");
    }
}

/**
 * Marks a node as completed with a user-edited state payload.
 */
export async function markNodeCompleted(node, graph, canvas, isDirty, saveGraphToServerFn) {
    const urlParams = new URLSearchParams(window.location.search);
    const thread_id = urlParams.get('thread_id');
    const graph_id = urlParams.get('graph');
    const checkpoint_path = urlParams.get('subgraph_node') || "";
    const cleanPath = getCleanPath(checkpoint_path);
    const sessionId = getSessionId();

    if (!thread_id) {
        alert("Please select or create a Session first.");
        return;
    }

    const nodeName = getNodeLogicalId(node);

    addLog(`System: Marking ${nodeName} as completed...`, "info");

    // 1. Fetch current status to get current values
    let currentValues = {};
    try {
        const res = await fetch(`${API_BASE}/graph_status/${thread_id}?g=${graph_id}&subgraph_node=${cleanPath}`);
        if (res.ok) {
            const status = await res.json();
            currentValues = status.result || status.last_state || {};
            delete currentValues["__subgraph_node__"];
        }
    } catch (e) {
        console.warn("Failed to fetch current state, starting with empty:", e);
    }

    // 2. Open JSON Editor — user edits state
    openJSONEditor(
        { properties: { _seed: JSON.stringify(currentValues) } },
        { value: "" },
        "_seed",
        canvas,
        isDirty,
        async (newVal) => {
            try {
                addLog(`System: Marking ${nodeName} as completed with edited state...`, "warning");

                // Save graph first
                await saveGraphToServerFn();

                const rootGraphId = thread_id.endsWith('_' + sessionId)
                    ? thread_id.slice(0, -(sessionId.length + 1))
                    : graph_id.replace('.json', '');

                const seedRes = await fetch(`${API_BASE}/seed_state`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        thread_id,
                        root_graph_id: rootGraphId,
                        checkpoint_ns: cleanPath,
                        values: JSON.parse(newVal),
                        as_node: nodeName
                    })
                });

                const result = await seedRes.json();
                if (seedRes.ok && result.status === "success") {
                    addLog(`System: ${result.message}`, "completion");
                    setTimeout(() => reloadLogs(graph, canvas), 500);
                } else {
                    const errorMsg = result.message || "Unknown error";
                    addLog(`System: Mark completed failed: ${errorMsg}`, "error");
                    if (result.traceback) console.error(result.traceback);
                }
            } catch (e) {
                addLog(`System: Error marking completed: ${e.message}`, "error");
            }
        }
    );
}
