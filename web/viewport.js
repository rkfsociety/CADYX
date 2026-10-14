(() => {
  const MAX_PREVIEW_POINTS = 24000;
  function createViewer(canvas) {
    const context = canvas.getContext("2d", { alpha: true });
    let geometry = null;
    let yaw = 0.68;
    let pitch = 0.38;
    let zoom = 1;
    let drag = null;
    let camera = new Float64Array(0);
    let screenX = new Float64Array(0);
    let screenY = new Float64Array(0);
    let faceRecords = new Float64Array(0);
    let faceOrder = new Uint32Array(0);

    function render() {
      const rect = canvas.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const width = Math.round(rect.width * dpr);
      const height = Math.round(rect.height * dpr);
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
      }
      context.clearRect(0, 0, width, height);
      if (!geometry) return;

      const { positions, triangles, bounds } = geometry;
      const center = bounds.min.map((value, axis) => value / 2 + bounds.max[axis] / 2);
      const extent = bounds.min.map((value, axis) => bounds.max[axis] - value);
      const radius = Math.max(Math.hypot(...extent) / 2, 0.001);
      const scale = (Math.min(width, height) * 0.43 * zoom) / radius;
      const cosYaw = Math.cos(yaw);
      const sinYaw = Math.sin(yaw);
      const cosPitch = Math.cos(pitch);
      const sinPitch = Math.sin(pitch);
      for (let index = 0; index < positions.length; index += 3) {
        const x = positions[index] - center[0];
        const y = positions[index + 1] - center[1];
        const z = positions[index + 2] - center[2];
        // CAD model coordinates: XY is horizontal, Z is up.
        const rotatedX = x * cosYaw - y * sinYaw;
        const rotatedDepth = x * sinYaw + y * cosYaw;
        const cameraY = z * cosPitch - rotatedDepth * sinPitch;
        const cameraZ = z * sinPitch + rotatedDepth * cosPitch;
        camera[index] = rotatedX;
        camera[index + 1] = cameraY;
        camera[index + 2] = cameraZ;
        const vertex = index / 3;
        screenX[vertex] = width / 2 + rotatedX * scale;
        screenY[vertex] = height / 2 - cameraY * scale;
      }

      if (triangles.length) renderMesh(triangles, camera, screenX, screenY);
      else if (!geometry.lines) renderPoints(positions.length / 3, camera, screenX, screenY, scale);
      context.strokeStyle = "#8ed4ed";
      context.lineWidth = 2;
      for (let index = 0; index < (geometry.lines || []).length; index += 2) {
        const a = geometry.lines[index], b = geometry.lines[index + 1];
        context.beginPath();
        context.moveTo(screenX[a], screenY[a]);
        context.lineTo(screenX[b], screenY[b]);
        context.stroke();
      }
      context.fillStyle = "#c3ecff";
      for (const point of geometry.points || []) {
        context.fillRect(screenX[point] - 2, screenY[point] - 2, 4, 4);
      }
    }

    function renderMesh(triangles, camera, screenX, screenY) {
      const faceTotal = triangles.length / 3;
      const light = [0.34, 0.68, 0.65];

      for (let face = 0; face < faceTotal; face += 1) {
        const first = triangles[face * 3];
        const second = triangles[face * 3 + 1];
        const third = triangles[face * 3 + 2];
        const a = first * 3;
        const b = second * 3;
        const c = third * 3;
        const ax = camera[a], ay = camera[a + 1], az = camera[a + 2];
        const bx = camera[b], by = camera[b + 1], bz = camera[b + 2];
        const cx = camera[c], cy = camera[c + 1], cz = camera[c + 2];
        const ux = bx - ax, uy = by - ay, uz = bz - az;
        const vx = cx - ax, vy = cy - ay, vz = cz - az;
        const nx = uy * vz - uz * vy;
        const ny = uz * vx - ux * vz;
        const nz = ux * vy - uy * vx;
        const normalLength = Math.hypot(nx, ny, nz) || 1;
        const brightness = 0.34 + 0.66 * Math.abs((nx * light[0] + ny * light[1] + nz * light[2]) / normalLength);
        const offset = face * 8;
        faceRecords[offset] = screenX[first];
        faceRecords[offset + 1] = screenY[first];
        faceRecords[offset + 2] = screenX[second];
        faceRecords[offset + 3] = screenY[second];
        faceRecords[offset + 4] = screenX[third];
        faceRecords[offset + 5] = screenY[third];
        faceRecords[offset + 6] = (az + bz + cz) / 3;
        faceRecords[offset + 7] = brightness;
        faceOrder[face] = face;
      }

      faceOrder.sort((left, right) => faceRecords[left * 8 + 6] - faceRecords[right * 8 + 6]);
      for (const face of faceOrder) {
        const offset = face * 8;
        const brightness = faceRecords[offset + 7];
        const green = Math.round(105 + brightness * 87);
        context.fillStyle = `rgb(${Math.round(green * 0.69)}, ${green}, ${Math.round(green * 0.53)})`;
        context.beginPath();
        context.moveTo(faceRecords[offset], faceRecords[offset + 1]);
        context.lineTo(faceRecords[offset + 2], faceRecords[offset + 3]);
        context.lineTo(faceRecords[offset + 4], faceRecords[offset + 5]);
        context.closePath();
        context.fill();
      }
    }

    function renderPoints(pointCount, camera, screenX, screenY, scale) {
      const step = Math.max(1, Math.ceil(pointCount / MAX_PREVIEW_POINTS));
      const pointSize = Math.max(1.2, Math.min(2.4, scale * 0.003));
      for (let point = 0; point < pointCount; point += step) {
        const depth = camera[point * 3 + 2];
        const shade = Math.max(0, Math.min(1, 0.5 + depth / (scale * 0.8)));
        const green = Math.round(133 + shade * 86);
        context.fillStyle = `rgba(${Math.round(green * 0.65)}, ${green}, ${Math.round(green * 0.7)}, 0.82)`;
        context.fillRect(screenX[point], screenY[point], pointSize, pointSize);
      }
    }

    canvas.addEventListener("pointerdown", (event) => {
      drag = { x: event.clientX, y: event.clientY };
      canvas.setPointerCapture(event.pointerId);
      canvas.classList.add("dragging");
    });
    canvas.addEventListener("pointermove", (event) => {
      if (!drag) return;
      const sensitivity = (Math.PI * 2) / Math.max(canvas.clientHeight, 1);
      yaw -= (event.clientX - drag.x) * sensitivity;
      pitch = Math.max(-1.45, Math.min(1.45, pitch + (event.clientY - drag.y) * sensitivity));
      drag = { x: event.clientX, y: event.clientY };
      render();
    });
    const stopDrag = () => {
      drag = null;
      canvas.classList.remove("dragging");
    };
    canvas.addEventListener("pointerup", stopDrag);
    canvas.addEventListener("pointercancel", stopDrag);
    canvas.addEventListener("dblclick", () => {
      yaw = 0.68;
      pitch = 0.38;
      zoom = 1;
      render();
    });
    canvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      zoom = Math.max(0.25, Math.min(5, zoom * Math.exp(-event.deltaY * 0.001)));
      render();
    }, { passive: false });
    new ResizeObserver(render).observe(canvas);

    function resetView() {
      yaw = 0.68;
      pitch = 0.38;
      zoom = 1;
    }

    return {
      fit() { resetView(); render(); },
      getCamera() { return { yaw, pitch, zoom, projection: "orthographic" }; },
      setCamera(value) {
        if (!value || ![value.yaw, value.pitch, value.zoom].every(Number.isFinite)
            || Math.abs(value.pitch) > 1.45 || value.zoom < 0.25 || value.zoom > 5
            || value.projection !== "orthographic") {
          throw new Error("Invalid orthographic camera.");
        }
        ({ yaw, pitch, zoom } = value);
        render();
      },
      load(value, { reset = true } = {}) {
        geometry = value;
        camera = new Float64Array(value.positions.length);
        screenX = new Float64Array(value.positions.length / 3);
        screenY = new Float64Array(value.positions.length / 3);
        faceRecords = new Float64Array((value.triangles.length / 3) * 8);
        faceOrder = new Uint32Array(value.triangles.length / 3);
        if (reset) resetView();
        render();
      },
      render,
    };
  }

  window.CadyxViewport = { createViewer };
})();
