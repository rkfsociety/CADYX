(() => {
  function createCameraPresetController({ root, canvas, viewer }) {
    const presets = window.CadyxViewportHelpers.CAMERA_PRESETS;
    const buttons = [...root.querySelectorAll("[data-camera-preset]")];
    function setSelected(selectedPreset) {
      buttons.forEach((button) => {
        button.setAttribute("aria-pressed", String(button.dataset.cameraPreset === selectedPreset));
      });
    }
    function apply(name) {
      if (!Object.prototype.hasOwnProperty.call(presets, name)) return false;
      const preset = presets[name];
      viewer.setCamera({ ...preset, zoom: viewer.getCamera().zoom, projection: "orthographic" });
      setSelected(name);
      return true;
    }
    root.addEventListener("click", (event) => {
      const button = event.target?.closest?.("[data-camera-preset]")
        || event.target?.parentElement?.closest?.("[data-camera-preset]");
      if (button && buttons.includes(button) && !button.disabled) apply(button.dataset.cameraPreset);
    });
    canvas.addEventListener("pointerdown", () => setSelected(null));
    canvas.addEventListener("wheel", () => setSelected(null), { passive: true });
    return Object.freeze({ apply, setSelected });
  }

  async function loadPly(url, { viewer, parser, fetchImpl } = {}) {
    const buffer = await window.CadyxBrowserApi.requestArrayBuffer(
      url,
      {},
      fetchImpl || window.fetch.bind(window),
    );
    const geometry = parser(buffer);
    viewer.load(geometry, { smoothShading: Boolean(geometry.vertexNormals) });
    return geometry;
  }

  window.CadyxBrowserViewers = Object.freeze({
    createCameraPresetController,
    loadPly,
  });
})();
