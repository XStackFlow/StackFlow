/**
 * Built-in node definitions — Portal, Start, End, Subgraph, WithNamespace nodes.
 * Also handles LiteGraph type registration.
 */

import { LiteGraph } from 'litegraph.js';
import { getNodeLogicalId } from './utils.js';

// These are set during init and used by Subgraph's combo widget
let availableGraphs = [];

/**
 * Sets the available graphs list (used by Subgraph combo widget).
 */
export function setAvailableGraphs(graphs) {
    availableGraphs = graphs;
}

/**
 * Re-validates all subgraph nodes in the graph after the graph list changes.
 */
export function revalidateSubgraphNodes(graph) {
    if (!graph?._nodes) return;
    for (const node of graph._nodes) {
        if (node.type === 'langgraph/subgraph') node._validate?.();
    }
}

/**
 * Gets the current available graphs list.
 */
export function getAvailableGraphs() {
    return availableGraphs;
}

/**
 * Registers all built-in node types with LiteGraph.
 * @param {LGraph} graph - the graph instance (for node count checks)
 * @param {object} isDirty - the dirty flag { value: boolean }
 * @param {Function} switchGraphFn - callback to switch to a subgraph view
 */
export function registerBuiltinNodes(graph, isDirty, switchGraphFn) {

    // --- Portal Entrance ---
    function LangGraphPortalEntrance() {
        this.addInput("In", null);
        this.addProperty("tag", "A");
        this.title = "Portal Entrance";
        this.tagWidget = this.addWidget("text", "Portal Tag", this.properties.tag, (v) => {
            this.properties.tag = v;
            this.title = "Portal Entrance [" + v + "]";
            isDirty.value = true;
        });
        this.color = "#1e40af";
        this.size = [180, 48];
    }

    LangGraphPortalEntrance.prototype.onPropertyChanged = function (name, value) {
        if (name === "tag") {
            this.title = "Portal Entrance [" + (value || "") + "]";
            if (this.tagWidget) this.tagWidget.value = value;
            isDirty.value = true;
        }
    };

    LangGraphPortalEntrance.prototype.onConfigure = function (o) {
        if (this.properties.tag !== undefined) {
            this.title = "Portal Entrance [" + this.properties.tag + "]";
            if (this.tagWidget) this.tagWidget.value = this.properties.tag;
        }
    };

    // --- Portal Exit ---
    function LangGraphPortalExit() {
        this.addOutput("Out", null);
        this.addProperty("tag", "A");
        this.title = "Portal Exit";
        this.tagWidget = this.addWidget("text", "Portal Tag", this.properties.tag, (v) => {
            this.properties.tag = v;
            this.title = "Portal Exit [" + v + "]";
            isDirty.value = true;
        });
        this.color = "#92400e";
        this.size = [180, 48];
    }

    LangGraphPortalExit.prototype.onPropertyChanged = function (name, value) {
        if (name === "tag") {
            this.title = "Portal Exit [" + (value || "") + "]";
            if (this.tagWidget) this.tagWidget.value = value;
            isDirty.value = true;
        }
    };

    LangGraphPortalExit.prototype.onConfigure = function (o) {
        if (this.properties.tag !== undefined) {
            this.title = "Portal Exit [" + this.properties.tag + "]";
            if (this.tagWidget) this.tagWidget.value = this.properties.tag;
        }
    };

    // --- END Node ---
    function LangGraphEnd() {
        if (graph.findNodesByType("langgraph/end").length > 0) {
            alert("Only one END node is allowed in a LangGraph.");
            setTimeout(() => graph.remove(this), 0);
        }
        this.addInput("In", "state");
        this.title = "END";
        this.color = "#4c1d95";
        this.size = [100, 40];
    }

    // --- START Node ---
    function LangGraphStart() {
        if (graph.findNodesByType("langgraph/start").length > 0) {
            alert("Only one START node is allowed in a LangGraph.");
            setTimeout(() => graph.remove(this), 0);
        }
        this.addOutput("Out", "state");
        this.title = "START";
        this.color = "#065f46";
        this.size = [100, 40];
    }

    // --- Subgraph Node ---
    function LangGraphSubgraph() {
        this.addInput("In", "state");
        this.addOutput("Out", "state");
        this.addProperty("subgraph", "");
        this.addProperty("inline", false);
        this.title = "SUBGRAPH";
        this.color = "#3b82f6";
        this.size = [220, 110];

        this.graphWidget = this.addWidget("combo", "Graph", this.properties.subgraph, (v) => {
            this.properties.subgraph = v;
            this.title = "SUBGRAPH: " + (v || "None");
            isDirty.value = true;
        }, { property: "subgraph", values: () => availableGraphs });

        this.addWidget("toggle", "Inline", this.properties.inline, (v) => {
            this.properties.inline = v;
            isDirty.value = true;
        }, { property: "inline" });

        if (this.properties.subgraph) {
            this.title = "SUBGRAPH: " + this.properties.subgraph;
        }

        this.addWidget("button", "Open", "", () => {
            if (this.properties.subgraph) {
                const nodeName = getNodeLogicalId(this);
                switchGraphFn(this.properties.subgraph, true, nodeName, null, this.properties.inline);
            }
        });
    }

    LangGraphSubgraph.prototype._validate = function () {
        const val = this.properties.subgraph;
        const missing = val && !availableGraphs.includes(val);
        this.color    = missing ? "#7f1d1d" : "#3b82f6";
        this.boxcolor = missing ? "#dc2626" : null;
        this.setDirtyCanvas?.(true, true);
    };

    LangGraphSubgraph.prototype.onPropertyChanged = function (name, value) {
        if (name === "subgraph") {
            this.title = "SUBGRAPH: " + (value || "None");
            if (this.graphWidget) this.graphWidget.value = value;
            isDirty.value = true;
            this._validate();
        }
    };

    LangGraphSubgraph.prototype.onConfigure = function (o) {
        if (this.properties.subgraph) {
            this.title = "SUBGRAPH: " + this.properties.subgraph;
            if (this.graphWidget) this.graphWidget.value = this.properties.subgraph;
        }
        this._validate();
    };

    // --- WithNamespace Node ---
    function LangGraphWithNamespace() {
        this.addInput("In", "state");
        this.addOutput("Out", "state");
        this.addProperty("namespace", "");

        this.updateTitle = function () {
            this.title = "NS: " + (this.properties.namespace || "Global");
        };

        this.namespaceWidget = this.addWidget("text", "Namespace", this.properties.namespace, (v) => {
            this.properties.namespace = v;
            this.updateTitle();
            isDirty.value = true;
        });

        this.updateTitle();
        this.color = "#ec4899";
        this.size = [210, 60];
    }

    LangGraphWithNamespace.prototype.onPropertyChanged = function (name, value) {
        if (name === "namespace") {
            if (this.namespaceWidget) this.namespaceWidget.value = value;
            this.updateTitle();
            isDirty.value = true;
        }
    };

    LangGraphWithNamespace.prototype.onConfigure = function (o) {
        if (this.properties.namespace !== undefined) {
            if (this.namespaceWidget) this.namespaceWidget.value = this.properties.namespace;
            this.updateTitle();
        }
    };

    // --- Clear defaults and register ---
    LiteGraph.clearRegisteredTypes();

    LiteGraph.registerNodeType("langgraph/port_in", LangGraphPortalEntrance);
    LiteGraph.registerNodeType("langgraph/port_out", LangGraphPortalExit);
    LiteGraph.registerNodeType("langgraph/start", LangGraphStart);
    LiteGraph.registerNodeType("langgraph/end", LangGraphEnd);
    LiteGraph.registerNodeType("langgraph/subgraph", LangGraphSubgraph);
    LiteGraph.registerNodeType("langgraph/WithNamespace", LangGraphWithNamespace);
}
