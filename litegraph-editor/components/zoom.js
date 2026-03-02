/**
 * Graph Zoom Slider — manages the vertical zoom bar for canvas zoom control.
 */

/**
 * Initialize the zoom slider and wire it to the LGraphCanvas.
 * @param {LGraphCanvas} canvas - The LiteGraph canvas instance.
 */
export function initZoomSlider(canvas) {
    const zoomSlider = document.getElementById("graph-zoom-slider");
    const zoomLabel = document.getElementById("zoom-label");
    const zoomInBtn = document.getElementById("zoom-in-btn");
    const zoomOutBtn = document.getElementById("zoom-out-btn");

    if (!zoomSlider || !canvas || !canvas.ds) return;

    let isSliderDragging = false;

    function updateZoomLabel(scale) {
        if (zoomLabel) zoomLabel.textContent = `${Math.round(scale * 100)}%`;
    }

    function setCanvasZoom(scale) {
        const cx = canvas.canvas.width / 2;
        const cy = canvas.canvas.height / 2;
        canvas.setZoom(scale, [cx, cy]);
        canvas.setDirty(true, true);
        updateZoomLabel(scale);
    }

    // Slider → canvas zoom
    zoomSlider.addEventListener("input", () => {
        isSliderDragging = true;
        const scale = parseInt(zoomSlider.value) / 100;
        setCanvasZoom(scale);
    });

    zoomSlider.addEventListener("change", () => {
        isSliderDragging = false;
    });

    // +/- buttons
    if (zoomInBtn) {
        zoomInBtn.addEventListener("click", () => {
            const newScale = Math.min(3, (canvas.ds.scale || 1) + 0.2);
            zoomSlider.value = Math.round(newScale * 100);
            setCanvasZoom(newScale);
        });
    }
    if (zoomOutBtn) {
        zoomOutBtn.addEventListener("click", () => {
            const newScale = Math.max(0.1, (canvas.ds.scale || 1) - 0.2);
            zoomSlider.value = Math.round(newScale * 100);
            setCanvasZoom(newScale);
        });
    }

    // Double-click label to reset to 100%
    if (zoomLabel) {
        zoomLabel.addEventListener("dblclick", () => {
            zoomSlider.value = 100;
            setCanvasZoom(1);
        });
    }

    // Sync slider when user zooms with scroll wheel
    setInterval(() => {
        if (!isSliderDragging && canvas.ds) {
            const currentScale = canvas.ds.scale || 1;
            const sliderVal = Math.round(currentScale * 100);
            if (parseInt(zoomSlider.value) !== sliderVal) {
                zoomSlider.value = Math.max(10, Math.min(300, sliderVal));
                updateZoomLabel(currentScale);
            }
        }
    }, 200);

    // Initialize label
    updateZoomLabel(canvas.ds.scale || 1);
}
