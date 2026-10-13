(() => {
  const MAX_PREVIEW_FACES = 36_000;
  const MAX_PREVIEW_POINTS = 24_000;
  const scalarTypes = {
    char: { size: 1, read: (view, offset) => view.getInt8(offset) },
    int8: { size: 1, read: (view, offset) => view.getInt8(offset) },
    uchar: { size: 1, read: (view, offset) => view.getUint8(offset) },
    uint8: { size: 1, read: (view, offset) => view.getUint8(offset) },
    short: { size: 2, read: (view, offset) => view.getInt16(offset, true) },
    int16: { size: 2, read: (view, offset) => view.getInt16(offset, true) },
    ushort: { size: 2, read: (view, offset) => view.getUint16(offset, true) },
    uint16: { size: 2, read: (view, offset) => view.getUint16(offset, true) },
    int: { size: 4, read: (view, offset) => view.getInt32(offset, true) },
    int32: { size: 4, read: (view, offset) => view.getInt32(offset, true) },
    uint: { size: 4, read: (view, offset) => view.getUint32(offset, true) },
    uint32: { size: 4, read: (view, offset) => view.getUint32(offset, true) },
    float: { size: 4, read: (view, offset) => view.getFloat32(offset, true) },
    float32: { size: 4, read: (view, offset) => view.getFloat32(offset, true) },
    double: { size: 8, read: (view, offset) => view.getFloat64(offset, true) },
    float64: { size: 8, read: (view, offset) => view.getFloat64(offset, true) },
  };

  function parseHeader(bytes) {
    const headerBytes = bytes.subarray(0, Math.min(bytes.length, 65_536));
    const headerText = new TextDecoder("ascii").decode(headerBytes);
    const marker = headerText.indexOf("end_header");
    if (marker < 0) throw new Error("Заголовок PLY не найден или слишком длинный.");
    const lineEnd = headerText.indexOf("\n", marker);
    if (lineEnd < 0) throw new Error("В заголовке PLY отсутствует конец строки.");
    const header = headerText.slice(0, marker).split(/\r?\n/);
    let format = null;
    let current = null;
    const elements = [];

    for (const line of header) {
      const fields = line.trim().split(/\s+/);
      if (fields[0] === "format") format = fields[1];
      else if (fields[0] === "element") {
        const count = Number(fields[2]);
        if (!Number.isSafeInteger(count) || count < 0) throw new Error("Некорректное число элементов PLY.");
        current = { name: fields[1], count, properties: [] };
        elements.push(current);
      } else if (fields[0] === "property" && current) {
        if (fields[1] === "list" && fields.length >= 5) {
          current.properties.push({ list: true, countType: fields[2], itemType: fields[3], name: fields[4] });
        } else if (fields.length >= 3) {
          current.properties.push({ list: false, type: fields[1], name: fields[2] });
        }
      }
    }

    if (format !== "binary_little_endian") throw new Error("Предпросмотр поддерживает бинарный PLY little-endian.");
    const vertex = elements[0];
    const face = elements[1]?.name === "face" ? elements[1] : null;
    if (!vertex || vertex.name !== "vertex") throw new Error("В PLY не найдены вершины.");
    if (elements.length > (face ? 2 : 1)) throw new Error("PLY содержит неподдерживаемые типы элементов.");
    return { dataOffset: lineEnd + 1, vertex, face };
  }

  function parsePly(buffer) {
    const bytes = new Uint8Array(buffer);
    const data = new DataView(buffer);
    const { dataOffset, vertex, face } = parseHeader(bytes);
    if (vertex.count === 0) throw new Error("В PLY нет вершин для предпросмотра.");
    const positions = new Float32Array(vertex.count * 3);
    const bounds = { min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] };
    const offsets = { x: null, y: null, z: null };
    let vertexStride = 0;

    for (const property of vertex.properties) {
      if (property.list) throw new Error("Списки свойств вершин PLY не поддерживаются.");
      const scalar = scalarTypes[property.type];
      if (!scalar) throw new Error(`Неподдерживаемый тип PLY: ${property.type}.`);
      if (Object.hasOwn(offsets, property.name)) offsets[property.name] = { offset: vertexStride, type: property.type };
      vertexStride += scalar.size;
    }
    if (Object.values(offsets).some((item) => item === null)) throw new Error("У вершин PLY должны быть координаты x, y, z.");

    let cursor = dataOffset;
    for (let index = 0; index < vertex.count; index += 1) {
      for (let axis = 0; axis < 3; axis += 1) {
        const key = ["x", "y", "z"][axis];
        const property = offsets[key];
        const value = scalarTypes[property.type].read(data, cursor + property.offset);
        if (!Number.isFinite(value)) throw new Error("В PLY найдены некорректные координаты.");
        positions[index * 3 + axis] = value;
        bounds.min[axis] = Math.min(bounds.min[axis], value);
        bounds.max[axis] = Math.max(bounds.max[axis], value);
      }
      cursor += vertexStride;
    }

    const triangles = [];
    let faceCount = 0;
    if (face) {
      const indexList = face.properties.find((property) => property.list && property.name === "vertex_indices");
      if (!indexList) throw new Error("В PLY не найдены индексы вершин граней.");
      const countScalar = scalarTypes[indexList.countType];
      const itemScalar = scalarTypes[indexList.itemType];
      if (!countScalar || !itemScalar) throw new Error("Неподдерживаемый тип индексов граней PLY.");
      const faceStep = Math.max(1, Math.ceil(face.count / MAX_PREVIEW_FACES));
      for (let faceIndex = 0; faceIndex < face.count; faceIndex += 1) {
        for (const property of face.properties) {
          if (property.list) {
            const countReader = scalarTypes[property.countType];
            const itemReader = scalarTypes[property.itemType];
            if (!countReader || !itemReader) throw new Error("Неподдерживаемое свойство граней PLY.");
            const itemCount = countReader.read(data, cursor);
            cursor += countReader.size;
            if (!Number.isSafeInteger(itemCount) || itemCount < 0 || itemCount > vertex.count + 1) {
              throw new Error("Некорректная грань в файле PLY.");
            }
            const samplePolygon = property.name === "vertex_indices" && faceIndex % faceStep === 0;
            let first = null;
            let previous = null;
            for (let item = 0; item < itemCount; item += 1) {
              const index = itemReader.read(data, cursor);
              if (samplePolygon) {
                if (index < 0 || index >= vertex.count) throw new Error("Грань PLY ссылается на отсутствующую вершину.");
                if (item === 0) first = previous = index;
                else if (item === 1) previous = index;
                else {
                  triangles.push(first, previous, index);
                  previous = index;
                }
              }
              cursor += itemReader.size;
            }
          } else {
            const scalar = scalarTypes[property.type];
            if (!scalar) throw new Error(`Неподдерживаемый тип PLY: ${property.type}.`);
            cursor += scalar.size;
          }
        }
        faceCount += 1;
      }
    }

    return {
      positions,
      triangles: new Uint32Array(triangles),
      bounds,
      vertexCount: vertex.count,
      faceCount,
      previewFaceCount: triangles.length / 3,
    };
  }

  function createViewer(canvas) {
    const context = canvas.getContext("2d", { alpha: true });
    let geometry = null;
    let yaw = 0.68;
    let pitch = -0.38;
    let zoom = 1;
    let drag = null;
    let camera = new Float32Array(0);
    let screenX = new Float32Array(0);
    let screenY = new Float32Array(0);
    let faceRecords = new Float32Array(0);
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
      const center = bounds.min.map((value, axis) => (value + bounds.max[axis]) / 2);
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
        const rotatedX = x * cosYaw - z * sinYaw;
        const rotatedZ = x * sinYaw + z * cosYaw;
        const cameraY = y * cosPitch - rotatedZ * sinPitch;
        const cameraZ = y * sinPitch + rotatedZ * cosPitch;
        camera[index] = rotatedX;
        camera[index + 1] = cameraY;
        camera[index + 2] = cameraZ;
        const vertex = index / 3;
        screenX[vertex] = width / 2 + rotatedX * scale;
        screenY[vertex] = height / 2 - cameraY * scale;
      }

      if (triangles.length) renderMesh(triangles, camera, screenX, screenY);
      else renderPoints(positions.length / 3, camera, screenX, screenY, scale);
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
      pitch = Math.max(-1.45, Math.min(1.45, pitch - (event.clientY - drag.y) * sensitivity));
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
      pitch = -0.38;
      zoom = 1;
      render();
    });
    canvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      zoom = Math.max(0.25, Math.min(5, zoom * Math.exp(-event.deltaY * 0.001)));
      render();
    }, { passive: false });
    new ResizeObserver(render).observe(canvas);

    return {
      load(value) {
        geometry = value;
        camera = new Float32Array(value.positions.length);
        screenX = new Float32Array(value.positions.length / 3);
        screenY = new Float32Array(value.positions.length / 3);
        faceRecords = new Float32Array((value.triangles.length / 3) * 8);
        faceOrder = new Uint32Array(value.triangles.length / 3);
        yaw = 0.68;
        pitch = -0.38;
        zoom = 1;
        render();
      },
      render,
    };
  }

  window.CadyxPlyPreview = { parse: parsePly, createViewer };
})();
