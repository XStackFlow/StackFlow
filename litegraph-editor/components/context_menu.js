/**
 * Context menu patches — adds "Mark as Completed" and "Interrupt Here" to
 * the node right-click menu, plus visual glow effects for interrupted nodes.
 */

import { LiteGraph } from 'litegraph.js';

/**
 * Applies context menu patches and visual interrupt effects to LiteGraph nodes.
 * @param {object} isDirty - dirty flag { value: boolean }
 * @param {Function} markNodeCompletedFn - callback to mark a node as completed
 * @param {Function} updateStateWidgetFn - callback to update state widget
 */
export function initContextMenu(isDirty, markNodeCompletedFn, updateStateWidgetFn) {

    // --- Context Menu: Mark as Completed & Interrupt ---
    const originalGetExtraMenuOptions = LiteGraph.LGraphNode.prototype.getExtraMenuOptions;
    LiteGraph.LGraphNode.prototype.getExtraMenuOptions = function (canvas, options) {
        const isInterrupted = this.properties.interrupt_before === true;

        // Add Mark as Completed at the top
        options.unshift({
            content: "✅ Mark as Completed",
            callback: () => {
                markNodeCompletedFn(this);
            }
        });

        // Add Interrupt at the top
        options.unshift({
            content: isInterrupted ? "🟢 Remove Interrupt" : "🛑 Interrupt Here",
            callback: () => {
                this.properties.interrupt_before = !isInterrupted;
                this.onInterruptChanged();
                isDirty.value = true;
            }
        });

        // Add a separator before the original options
        options.splice(2, 0, null);

        if (originalGetExtraMenuOptions) {
            originalGetExtraMenuOptions.apply(this, arguments);
        }
    };

    // --- Interrupt Changed Visual Feedback (Purple Glow) ---
    LiteGraph.LGraphNode.prototype.onInterruptChanged = function () {
        if (this.properties.interrupt_before) {
            this.boxcolor = "#d946ef";
            if (!this.title.startsWith("🛑 ")) {
                this.title = "🛑 " + this.title;
            }
        } else {
            this.boxcolor = null;
            if (this.title.startsWith("🛑 ")) {
                this.title = this.title.substring(3);
            }
        }
        this.setDirtyCanvas(true, true);

        // Immediate update of the state window to show/hide the breakpoint
        updateStateWidgetFn();
    };

    // --- Custom Draw for the 'Glow' effect ---
    const originalOnDrawForeground = LiteGraph.LGraphNode.prototype.onDrawForeground;
    LiteGraph.LGraphNode.prototype.onDrawForeground = function (ctx) {
        if (originalOnDrawForeground) {
            originalOnDrawForeground.apply(this, arguments);
        }

        if (this.properties.interrupt_before && !this.flags.collapsed) {
            ctx.save();
            ctx.shadowColor = "#a855f7";
            ctx.shadowBlur = 20;
            ctx.strokeStyle = "#d946ef";
            ctx.lineWidth = 2;
            ctx.strokeRect(-2, -2, this.size[0] + 4, this.size[1] + 4);
            ctx.restore();
        }
    };

    // --- Ensure visual state is restored on load ---
    const originalOnConfigure = LiteGraph.LGraphNode.prototype.onConfigure;
    LiteGraph.LGraphNode.prototype.onConfigure = function (o) {
        if (originalOnConfigure) originalOnConfigure.apply(this, arguments);
        if (this.properties.interrupt_before) {
            this.onInterruptChanged();
        }
    };
}
