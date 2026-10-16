(() => {
  const MAX_PREVIEW_FACES = 36_000;
  const MAX_PREVIEW_POINTS = 24_000;
  const asciiDecoder = new TextDecoder("ascii");
  const scalarTypes = {
    char: { size: 1, read: (view, offset) => view.getInt8(offset) },
    int8: { size: 1, read: (view, offset) => view.getInt8(offset) },
    uchar: { size: 1, read: (view, offset) => view.getUint8(offset) },
    uint8: { size: 1, read: (view, offset) => view.getUint8(offset) },
    short: { size: 2, read: (view, offset, littleEndian) => view.getInt16(offset, littleEndian) },
    int16: { size: 2, read: (view, offset, littleEndian) => view.getInt16(offset, littleEndian) },
    ushort: { size: 2, read: (view, offset, littleEndian) => view.getUint16(offset, littleEndian) },
    uint16: { size: 2, read: (view, offset, littleEndian) => view.getUint16(offset, littleEndian) },
    int: { size: 4, read: (view, offset, littleEndian) => view.getInt32(offset, littleEndian) },
    int32: { size: 4, read: (view, offset, littleEndian) => view.getInt32(offset, littleEndian) },
    uint: { size: 4, read: (view, offset, littleEndian) => view.getUint32(offset, littleEndian) },
    uint32: { size: 4, read: (view, offset, littleEndian) => view.getUint32(offset, littleEndian) },
    float: { size: 4, read: (view, offset, littleEndian) => view.getFloat32(offset, littleEndian) },
    float32: { size: 4, read: (view, offset, littleEndian) => view.getFloat32(offset, littleEndian) },
    double: { size: 8, read: (view, offset, littleEndian) => view.getFloat64(offset, littleEndian) },
    float64: { size: 8, read: (view, offset, littleEndian) => view.getFloat64(offset, littleEndian) },
  };

  function parseHeader(bytes) {
    const headerBytes = bytes.subarray(0, Math.min(bytes.length, 65_536));
    const headerText = asciiDecoder.decode(headerBytes);
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

    if (format !== "ascii" && format !== "binary_little_endian" && format !== "binary_big_endian") {
      throw new Error("Формат PLY для предпросмотра не поддерживается.");
    }
    const littleEndian = format === "binary_little_endian";
    const vertex = elements[0];
    const face = elements[1]?.name === "face" ? elements[1] : null;
    if (!vertex || vertex.name !== "vertex") throw new Error("В PLY не найдены вершины.");
    if (elements.length > (face ? 2 : 1)) throw new Error("PLY содержит неподдерживаемые типы элементов.");
    return { dataOffset: lineEnd + 1, vertex, face, littleEndian, format };
  }

  function triangulatePolygon(indices, positions, triangles) {
    const polygon = [];
    for (const index of indices) {
      if (polygon[polygon.length - 1] !== index) polygon.push(index);
    }
    if (polygon.length > 1 && polygon[0] === polygon[polygon.length - 1]) polygon.pop();
    if (polygon.length < 3) return;
    if (polygon.length === 3) {
      triangles.push(...polygon);
      return;
    }

    let normalX = 0, normalY = 0, normalZ = 0;
    for (let index = 0; index < polygon.length; index += 1) {
      const current = polygon[index] * 3;
      const next = polygon[(index + 1) % polygon.length] * 3;
      const x = positions[current], y = positions[current + 1], z = positions[current + 2];
      const nextX = positions[next], nextY = positions[next + 1], nextZ = positions[next + 2];
      normalX += (y - nextY) * (z + nextZ);
      normalY += (z - nextZ) * (x + nextX);
      normalZ += (x - nextX) * (y + nextY);
    }
    const droppedAxis = Math.abs(normalX) > Math.abs(normalY)
      ? (Math.abs(normalX) > Math.abs(normalZ) ? 0 : 2)
      : (Math.abs(normalY) > Math.abs(normalZ) ? 1 : 2);
    const projected = polygon.map((index) => {
      const offset = index * 3;
      if (droppedAxis === 0) return [positions[offset + 1], positions[offset + 2]];
      if (droppedAxis === 1) return [positions[offset], positions[offset + 2]];
      return [positions[offset], positions[offset + 1]];
    });
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const point of projected) {
      minX = Math.min(minX, point[0]);
      maxX = Math.max(maxX, point[0]);
      minY = Math.min(minY, point[1]);
      maxY = Math.max(maxY, point[1]);
    }
    const extent = Math.max(maxX - minX, maxY - minY);
    const epsilon = Math.max(Number.MIN_VALUE, extent * extent * 1e-12);
    const cross = (a, b, c) => (b[0] - a[0]) * (c[1] - a[1])
      - (b[1] - a[1]) * (c[0] - a[0]);

    let winding = 0, convex = true;
    for (let index = 0; index < projected.length; index += 1) {
      const turn = cross(projected[(index + projected.length - 1) % projected.length],
        projected[index], projected[(index + 1) % projected.length]);
      if (Math.abs(turn) <= epsilon) continue;
      const sign = Math.sign(turn);
      if (winding && winding !== sign) convex = false;
      winding = sign;
    }
    if (!winding) return;
    if (convex) {
      for (let index = 1; index + 1 < polygon.length; index += 1) {
        triangles.push(polygon[0], polygon[index], polygon[index + 1]);
      }
      return;
    }

    const orientation = Math.sign(projected.reduce((area, point, index) => {
      const next = projected[(index + 1) % projected.length];
      return area + point[0] * next[1] - next[0] * point[1];
    }, 0));
    if (!orientation) return;
    const remaining = polygon.map((_, index) => index);
    while (remaining.length > 3) {
      let clipped = false;
      for (let position = 0; position < remaining.length; position += 1) {
        const previous = remaining[(position + remaining.length - 1) % remaining.length];
        const current = remaining[position];
        const next = remaining[(position + 1) % remaining.length];
        const a = projected[previous], b = projected[current], c = projected[next];
        if (orientation * cross(a, b, c) <= epsilon) continue;
        let containsVertex = false;
        for (const candidate of remaining) {
          if (candidate === previous || candidate === current || candidate === next) continue;
          const point = projected[candidate];
          if (orientation * cross(a, b, point) >= -epsilon
              && orientation * cross(b, c, point) >= -epsilon
              && orientation * cross(c, a, point) >= -epsilon) {
            containsVertex = true;
            break;
          }
        }
        if (containsVertex) continue;
        triangles.push(polygon[previous], polygon[current], polygon[next]);
        remaining.splice(position, 1);
        clipped = true;
        break;
      }
      if (!clipped) throw new Error("Не удалось триангулировать грань PLY.");
    }
    triangles.push(polygon[remaining[0]], polygon[remaining[1]], polygon[remaining[2]]);
  }

  function nextAsciiRecord(bytes, cursor) {
    while (cursor < bytes.length) {
      const start = cursor;
      while (cursor < bytes.length && bytes[cursor] !== 10) cursor += 1;
      let end = cursor;
      if (end > start && bytes[end - 1] === 13) end -= 1;
      const line = asciiDecoder.decode(bytes.subarray(start, end)).trim();
      cursor += Number(cursor < bytes.length);
      if (line) return { values: line.split(/\s+/), cursor };
    }
    throw new Error("Данные PLY обрезаны.");
  }

  function parseAsciiPly(bytes, dataOffset, vertex, face) {
    const bounds = { min: [Infinity, Infinity, Infinity], max: [-Infinity, -Infinity, -Infinity] };
    const coordinateReaders = ["x", "y", "z"].map((name) => {
      const index = vertex.properties.findIndex((property) => property.name === name);
      if (index < 0 || vertex.properties[index].list || !scalarTypes[vertex.properties[index].type]) {
        throw new Error("У вершин PLY должны быть координаты x, y, z.");
      }
      return { index, type: vertex.properties[index].type };
    });
    if (vertex.properties.some((property) => property.list || !scalarTypes[property.type])) {
      throw new Error("Списки свойств вершин PLY не поддерживаются.");
    }

    const pointCloud = !face || face.count === 0;
    const previewVertexCount = pointCloud ? Math.min(vertex.count, MAX_PREVIEW_POINTS) : vertex.count;
    const positions = new Float32Array(previewVertexCount * 3);
    let cursor = dataOffset;
    for (let index = 0; index < vertex.count; index += 1) {
      const record = nextAsciiRecord(bytes, cursor);
      cursor = record.cursor;
      if (record.values.length !== vertex.properties.length) throw new Error("Некорректная строка вершин PLY.");
      const sampleIndex = Math.floor(index * previewVertexCount / vertex.count);
      const previousSampleIndex = index > 0
        ? Math.floor((index - 1) * previewVertexCount / vertex.count) : -1;
      const sampledOffset = sampleIndex !== previousSampleIndex ? sampleIndex * 3 : -1;
      for (let axis = 0; axis < 3; axis += 1) {
        const reader = coordinateReaders[axis];
        const value = Number(record.values[reader.index]);
        if (!Number.isFinite(value)) throw new Error("В PLY найдены некорректные координаты.");
        const previewValue = Math.fround(value);
        if (!Number.isFinite(previewValue)) {
          throw new Error("Координаты PLY слишком велики для предпросмотра.");
        }
        if (sampledOffset >= 0) positions[sampledOffset + axis] = previewValue;
        bounds.min[axis] = Math.min(bounds.min[axis], value);
        bounds.max[axis] = Math.max(bounds.max[axis], value);
      }
    }

    const triangles = [];
    let faceCount = 0;
    if (face) {
      if (!face.properties.some((property) => property.list && property.name === "vertex_indices")) {
        throw new Error("В PLY не найдены индексы вершин граней.");
      }
      const faceStep = Math.max(1, Math.ceil(face.count / MAX_PREVIEW_FACES));
      for (let faceIndex = 0; faceIndex < face.count; faceIndex += 1) {
        const record = nextAsciiRecord(bytes, cursor);
        cursor = record.cursor;
        let valueIndex = 0;
        for (const property of face.properties) {
          if (property.list) {
            const countType = scalarTypes[property.countType];
            const itemType = scalarTypes[property.itemType];
            if (!countType || !itemType || valueIndex >= record.values.length) {
              throw new Error("Неподдерживаемое свойство граней PLY.");
            }
            const itemCount = Number(record.values[valueIndex++]);
            if (!Number.isSafeInteger(itemCount) || itemCount < 0 || itemCount > vertex.count + 1
                || itemCount > record.values.length - valueIndex) {
              throw new Error("Некорректная грань в файле PLY.");
            }
            const samplePolygon = property.name === "vertex_indices" && faceIndex % faceStep === 0;
            if (samplePolygon) {
              const polygon = [];
              for (let item = 0; item < itemCount; item += 1) {
                const pointIndex = Number(record.values[valueIndex + item]);
                if (!Number.isSafeInteger(pointIndex) || pointIndex < 0 || pointIndex >= vertex.count) {
                  throw new Error("Грань PLY ссылается на отсутствующую вершину.");
                }
                polygon.push(pointIndex);
              }
              triangulatePolygon(polygon, positions, triangles);
            }
            valueIndex += itemCount;
          } else {
            if (!scalarTypes[property.type] || valueIndex >= record.values.length) {
              throw new Error("Неподдерживаемое свойство граней PLY.");
            }
            valueIndex += 1;
          }
        }
        if (valueIndex !== record.values.length) throw new Error("Некорректная строка граней PLY.");
        faceCount += 1;
      }
    }

    return {
      positions,
      triangles: new Uint32Array(triangles),
      bounds,
      vertexCount: vertex.count,
      previewVertexCount,
      faceCount,
      previewFaceCount: triangles.length / 3,
    };
  }

  function parsePly(buffer) {
    const bytes = new Uint8Array(buffer);
    const data = new DataView(buffer);
    const { dataOffset, vertex, face, littleEndian, format } = parseHeader(bytes);
    if (vertex.count === 0) throw new Error("В PLY нет вершин для предпросмотра.");
    if (format === "ascii") return parseAsciiPly(bytes, dataOffset, vertex, face);
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

    if (vertex.count > Math.floor((buffer.byteLength - dataOffset) / vertexStride)) {
      throw new Error("Данные вершин PLY обрезаны.");
    }
    const pointCloud = !face || face.count === 0;
    const previewVertexCount = pointCloud ? Math.min(vertex.count, MAX_PREVIEW_POINTS) : vertex.count;
    const positions = new Float32Array(previewVertexCount * 3);
    const coordinateReaders = ["x", "y", "z"].map((key) => ({
      offset: offsets[key].offset, read: scalarTypes[offsets[key].type].read,
    }));
    let cursor = dataOffset;
    for (let index = 0; index < vertex.count; index += 1) {
      const sampleIndex = Math.floor(index * previewVertexCount / vertex.count);
      const previousSampleIndex = index > 0
        ? Math.floor((index - 1) * previewVertexCount / vertex.count) : -1;
      const sampledOffset = sampleIndex !== previousSampleIndex ? sampleIndex * 3 : -1;
      for (let axis = 0; axis < 3; axis += 1) {
        const property = coordinateReaders[axis];
        const value = property.read(data, cursor + property.offset, littleEndian);
        if (!Number.isFinite(value)) throw new Error("В PLY найдены некорректные координаты.");
        const previewValue = Math.fround(value);
        if (!Number.isFinite(previewValue)) {
          throw new Error("Координаты PLY слишком велики для предпросмотра.");
        }
        if (sampledOffset >= 0) positions[sampledOffset + axis] = previewValue;
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
            if (countReader.size > buffer.byteLength - cursor) throw new Error("Данные граней PLY обрезаны.");
            const itemCount = countReader.read(data, cursor, littleEndian);
            cursor += countReader.size;
            if (!Number.isSafeInteger(itemCount) || itemCount < 0 || itemCount > vertex.count + 1) {
              throw new Error("Некорректная грань в файле PLY.");
            }
            const listBytes = itemCount * itemReader.size;
            if (listBytes > buffer.byteLength - cursor) throw new Error("Данные граней PLY обрезаны.");
            const samplePolygon = property.name === "vertex_indices" && faceIndex % faceStep === 0;
            if (!samplePolygon) {
              cursor += listBytes;
              continue;
            }
            const polygon = [];
            for (let item = 0; item < itemCount; item += 1) {
              const index = itemReader.read(data, cursor, littleEndian);
              if (!Number.isSafeInteger(index) || index < 0 || index >= vertex.count) {
                throw new Error("Грань PLY ссылается на отсутствующую вершину.");
              }
              polygon.push(index);
              cursor += itemReader.size;
            }
            triangulatePolygon(polygon, positions, triangles);
          } else {
            const scalar = scalarTypes[property.type];
            if (!scalar) throw new Error(`Неподдерживаемый тип PLY: ${property.type}.`);
            if (scalar.size > buffer.byteLength - cursor) throw new Error("Данные граней PLY обрезаны.");
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
      previewVertexCount,
      faceCount,
      previewFaceCount: triangles.length / 3,
    };
  }

  window.CadyxPlyPreview = { parse: parsePly };
})();
