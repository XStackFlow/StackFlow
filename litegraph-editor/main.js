/**
 * StackFlow LiteGraph Editor — Main Entry Point
 *
 * This file orchestrates all component modules. Each concern is split into
 * its own file under ./components/ for readability and maintainability:
 *
 *   utils.js         — Shared helpers (escapeHTML, getNodeLogicalId, getCleanPath)
 *   logging.js       — Log panel UI (addLog, pushLog)
 *   sidebar.js       — State panel, right sidebar, resize system
 *   session.js       — Session ID management & switching
 *   nodes.js         — Built-in node definitions (Portal, Start, End, Subgraph, WithNamespace)
 *   execution.js     — Execution engine, status polling, state widget
 *   toolbar.js       — Toolbar button/keyboard bindings
 *   graph_manager.js — Graph loading, switching, breadcrumbs, dynamic node registration
 *   context_menu.js  — Right-click context menu patches (Mark Completed, Interrupt)
 *   history.js       — Undo/Redo history (pre-existing)
 *   litegraph_patch.js — LiteGraph monkey-patches (pre-existing)
 *   json_editor.js   — JSON editor modal (pre-existing)
 */

import { LGraph, LGraphCanvas, LiteGraph } from 'litegraph.js';
import { applyLiteGraphPatch } from './components/litegraph_patch.js';
import { initHistory, saveHistory, undo, redo, clearHistory, setHistoryBlock, initHistoryHooks } from './components/history.js';
import { addLog } from './components/logging.js';
import { initZoomSlider } from './components/zoom.js';
import { initViewStateButton } from './components/sidebar.js';
import {
    getSessionId, setSessionId, getSessions, setSessions,
    syncSessionLogs, updateSessionUI, initSessionInput
} from './components/session.js';
import { registerBuiltinNodes } from './components/nodes.js';
import {
    getLastStateData, getThreadId,
    executeGraph, clearGraphState, stopGraph,
    reloadLogs, updateStateWidget, updateActiveSessions,
    startStatusPolling, silentSaveGraph, markNodeCompleted,
    registerSwitchGraph
} from './components/execution.js';
import { saveGraphToServer, initToolbar } from './components/toolbar.js';
import {
    fetchNodes, fetchGraphList, loadGraph,
    switchGraph, updateBreadcrumbs, initGraphSelector, openPackageManager,
} from './components/graph_manager.js';
import { initContextMenu } from './components/context_menu.js';
import { initVariablesPanel } from './components/variables.js';
import { initGraphExplorer } from './components/graph_explorer.js';


// ==========================================================================
// 1. Apply LiteGraph Patches (before any graph/canvas creation)
// ==========================================================================
applyLiteGraphPatch();


// ==========================================================================
// 2. Initialize Core LiteGraph Instances
// ==========================================================================
const API_BASE = "http://localhost:8000";
const graph = new LGraph();
const canvas = new LGraphCanvas("#main-canvas", graph);
const isDirty = { value: false };

// Helper to allow custom widgets easily
LiteGraph.LGraphNode.prototype.addCustomWidget = function (widget) {
    if (!this.widgets) this.widgets = [];
    this.widgets.push(widget);
    return widget;
};

// Warn before leaving with unsaved changes
window.addEventListener('beforeunload', (e) => {
    if (isDirty.value) {
        e.preventDefault();
        e.returnValue = '';
    }
});


// ==========================================================================
// 3. Canvas Visual Settings
// ==========================================================================
canvas.background_color = "#1a1a1a";
canvas.grid_color = "#333";
canvas.render_shadows = true;
canvas.show_fps = false;
if (canvas.ds) canvas.ds.show_fps = false;
canvas.show_stats = false;

// Close LiteGraph dialogs/prompts when clicking outside
window.addEventListener("mousedown", (e) => {
    const isDialog = e.target.closest(".graphdialog, .litegraph.prompt, .litegraph.context-menu");
    if (!isDialog) {
        const dialogs = document.querySelectorAll(".graphdialog, .litegraph.prompt");
        if (dialogs.length > 0) {
            dialogs.forEach(d => d.remove());
        }
    }
}, true);

canvas.onBackgroundMouseDown = function (e) {
    LiteGraph.closeAllContextMenus();
};

// Persistence & Workflow settings
graph.allow_cycles = true;
graph.allow_multi_link_to_inputs = true;
canvas.allow_multi_link = true;
LiteGraph.allow_multi_link_for_all_nodes = true;


// ==========================================================================
// 4. Initialize History System
// ==========================================================================
initHistory(graph, canvas, isDirty, addLog);

const undoBtn = document.getElementById("undo-btn");
if (undoBtn) undoBtn.addEventListener("click", () => undo());
const redoBtn = document.getElementById("redo-btn");
if (redoBtn) redoBtn.addEventListener("click", () => redo());
const clearHisBtn = document.getElementById("clear-history-btn");
if (clearHisBtn) clearHisBtn.addEventListener("click", () => {
    if (confirm("Are you sure you want to clear the entire history?")) {
        clearHistory();
    }
});

// Hook into LiteGraph events for auto-saving history
initHistoryHooks(LiteGraph);

// ==========================================================================
// 5. Initialize Sidebar (State Panel, View Full State, Resize)
// ==========================================================================
initViewStateButton(getLastStateData, canvas);


// ==========================================================================
// 6. Initialize Session Management
// ==========================================================================
initSessionInput(() => reloadLogs(graph, canvas));


// ==========================================================================
// 7. Register Built-in Node Types
// ==========================================================================
const boundSwitchGraph = (graphName, pushHistory, subgraphNode, fullPathOverride, isInline) => {
    switchGraph(graph, canvas, isDirty, graphName, pushHistory, subgraphNode, fullPathOverride, isInline);
};
registerBuiltinNodes(graph, isDirty, boundSwitchGraph);


// ==========================================================================
// 8. Global Graph Properties (Initial State Editor)
// ==========================================================================
if (!graph.extra) graph.extra = {};
if (!graph.extra.initial_state) {
    graph.extra.initial_state = JSON.stringify({ input: "" }, null, 2);
}

const stateInput = document.getElementById("state-json-input");
if (stateInput) {
    stateInput.value = graph.extra.initial_state;
    stateInput.addEventListener("input", (e) => {
        graph.extra.initial_state = e.target.value;
        isDirty.value = true;
    });
}

const formatBtn = document.getElementById("format-json");
if (formatBtn && stateInput) {
    formatBtn.addEventListener("click", () => {
        try {
            const obj = JSON.parse(stateInput.value);
            const formatted = JSON.stringify(obj, null, 2);
            stateInput.value = formatted;
            graph.extra.initial_state = formatted;
            isDirty.value = true;
        } catch (e) {
            alert("Invalid JSON: " + e.message);
        }
    });
}

// When a graph is loaded, update the state editor
const originalConfigure = graph.configure;
graph.configure = function (data) {
    originalConfigure.apply(this, arguments);
    if (this.extra && this.extra.initial_state && stateInput) {
        stateInput.value = this.extra.initial_state;
    }
    isDirty.value = false;
};


// ==========================================================================
// 9. Canvas Resizing
// ==========================================================================
const UI_ZOOM = 0.9; // Must match body { zoom } in index.html

function resize() {
    // Expand canvas to fill the zoomed body (body CSS size = viewport / zoom)
    canvas.resize(window.innerWidth / UI_ZOOM, window.innerHeight / UI_ZOOM);
}
window.addEventListener("resize", resize);
resize();


// ==========================================================================
// 9b. Graph Zoom Slider
// ==========================================================================
initZoomSlider(canvas);


// ==========================================================================
// 10. Initialize Toolbar
// ==========================================================================
const boundSaveGraph = () => saveGraphToServer(graph, isDirty,
    (retries, reload) => fetchGraphList(graph, canvas, isDirty, (name) => loadGraph(graph, isDirty, name, canvas), retries, reload),
    () => updateBreadcrumbs(graph, canvas, isDirty),
    canvas
);

const boundLoadGraph = (name) => loadGraph(graph, isDirty, name, canvas);

initToolbar(graph, canvas, isDirty, {
    saveGraphFn: boundSaveGraph,
    fetchGraphListFn: (retries, reload) => fetchGraphList(graph, canvas, isDirty, boundLoadGraph, retries, reload),
    updateBreadcrumbsFn: () => updateBreadcrumbs(graph, canvas, isDirty),
    loadGraphFn: boundLoadGraph,
    stateInput
});

document.getElementById("open-package-manager")
    ?.addEventListener("click", () => openPackageManager(() => fetchNodes(canvas, isDirty)));


// ==========================================================================
// 11. Initialize Context Menu Patches
// ==========================================================================
initContextMenu(
    isDirty,
    (node) => markNodeCompleted(node, graph, canvas, isDirty, boundSaveGraph),
    () => updateStateWidget(null, graph)
);


// ==========================================================================
// 12. Initialize Graph Selector & Popstate
// ==========================================================================
initGraphSelector(graph, canvas, isDirty, boundLoadGraph);

// ==========================================================================
// 12b. Initialize Graph Explorer (folder tree + GitHub install)
// ==========================================================================
initGraphExplorer(graph, canvas, isDirty, {
    switchGraphFn: (name) => switchGraph(graph, canvas, isDirty, name),
    fetchGraphListFn: (retries, reload) => fetchGraphList(graph, canvas, isDirty, boundLoadGraph, retries, reload),
});


// ==========================================================================
// 13. Active Sessions Polling
// ==========================================================================
// Give session click handlers access to switchGraph without a circular import.
registerSwitchGraph((graphName) => switchGraph(graph, canvas, isDirty, graphName));
setInterval(() => updateActiveSessions(graph, canvas), 3000);
updateActiveSessions(graph, canvas);


// ==========================================================================
// 14. Application Init Sequence
// ==========================================================================
async function init() {
    addLog("System: Initializing editor...", "info");

    // 0. Start the graph animation loop
    graph.start();

    // 1. Fetch and register nodes first
    await fetchNodes(canvas, isDirty);

    // 2. Fetch graph list and load the first one
    await fetchGraphList(graph, canvas, isDirty, boundLoadGraph);

    // 4. Load logs and sync sessions
    updateBreadcrumbs(graph, canvas, isDirty);
    reloadLogs(graph, canvas);
    syncSessionLogs();

    // 5. Initialize variables panel
    await initVariablesPanel();

    addLog("System: Editor ready.", "completion");
}

// Start initialization
init();
