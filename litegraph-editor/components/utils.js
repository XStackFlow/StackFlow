/**
 * Shared utility functions for the LiteGraph editor.
 */

/**
 * Escapes HTML special characters to prevent XSS and broken UI layouts
 * when rendering text via innerHTML.
 */
export function escapeHTML(str) {
    if (!str && str !== 0) return "";
    if (typeof str !== 'string') str = String(str);
    return str.replace(/[&<>"']/g, function (m) {
        return {
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            '"': '&quot;',
            "'": '&#039;'
        }[m];
    });
}

/**
 * Standard helper for calculating logical Node IDs (matches backend graph_factory.py)
 */
export function getNodeLogicalId(node) {
    if (!node) return null;
    let baseName = node.properties?.name || node.title;
    if (node.type === "langgraph/start") baseName = "START";
    if (node.type === "langgraph/end") baseName = "END";

    // Proactive: Match backend subgraph naming logic before sanitization
    if (node.type === "langgraph/subgraph" && baseName && baseName.startsWith("SUBGRAPH: ")) {
        baseName = baseName.replace("SUBGRAPH: ", "SUBGRAPH@");
    }

    const sanitizedBaseName = baseName.replace(/[^a-zA-Z0-9_@]/g, '_');
    const id = node.id;

    // Handle inlined nodes (e.g. "Sub_1@@4")
    if (typeof id === 'string' && id.includes("@@")) {
        const parts = id.split("@@");
        const rawId = parts.pop();
        const pfx = parts.join("@@") + "@@";
        return `${pfx}${sanitizedBaseName}_${rawId}`;
    }

    return `${sanitizedBaseName}_${id}`;
}

/**
 * Cleans a namespace path by removing parenthesized graph-file annotations.
 */
export function getCleanPath(path) {
    if (!path) return "";
    return path.split('@@').map(seg => seg.split('(')[0]).join('@@');
}
