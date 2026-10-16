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
    const firstLineEnd = headerText.indexOf("\n");
    if (firstLineEnd < 0 || headerText.slice(0, firstLineEnd).trim() !== "ply") {
      throw new Error("Файл не содержит корректный заголовок PLY.");
    }
    let headerEnd = -1;
    let dataOffset = -1;
    for (let lineStart = 0; lineStart < headerText.length;) {
      const lineEnd = headerText.indexOf("\n", lineStart);
      if (lineEnd < 0) break;
      const line = headerText.slice(lineStart, lineEnd).replace(/\r$/, "").trim();
      if (line === "end_header") {
        headerEnd = lineStart;
        dataOffset = lineEnd + 1;
        break;
      }
      lineStart = lineEnd + 1;
    }
    if (headerEnd < 0) throw new Error("Заголовок PLY не найден или слишком длинный.");
    const header = headerText.slice(0, headerEnd).split(/\r?\n/);
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
    const vertexElements = elements.filter((element) => element.name === "vertex");
    const faceIndices = elements.flatMap((element, index) => element.name === "face" ? [index] : []);
    if (vertexElements.length !== 1 || elements[0] !== vertexElements[0]) {
      throw new Error("В PLY должен быть ровно один элемент вершин, расположенный первым.");
    }
    if (faceIndices.length > 1 || faceIndices.some((index) => index !== 1)) {
      throw new Error("В PLY грани должны идти сразу после вершин.");
    }
    const vertex = vertexElements[0];
    const face = faceIndices.length ? elements[1] : null;
    return { dataOffset, vertex, face, littleEndian, format };
  }

  function getColorReaders(properties) {
    const names = [["red", "r"], ["green", "g"], ["blue", "b"]];
    const readers = names.map((aliases) => {
      const index = properties.findIndex((property) => !property.list && aliases.includes(property.name));
      if (index < 0) return null;
      return { index, type: properties[index].type };
    });
    return readers.every(Boolean) ? readers : null;
  }

  function getNormalReaders(properties) {
    const names = [["nx", "normal_x"], ["ny", "normal_y"], ["nz", "normal_z"]];
    const readers = names.map((aliases) => {
      const index = properties.findIndex((property) => !property.list && aliases.includes(property.name));
      if (index < 0) return null;
      return { index, type: properties[index].type };
    });
    return readers.every(Boolean) ? readers : null;
  }

  function writeNormalizedVector(target, offset, x, y, z) {
    const length = Math.hypot(x, y, z);
    if (!Number.isFinite(length) || length <= Number.EPSILON) return false;
    target[offset] = x / length;
    target[offset + 1] = y / length;
    target[offset + 2] = z / length;
    return true;
  }

  function normalizeColor(value, type) {
    if (!Number.isFinite(value)) throw new Error("В PLY найдены некорректные цвета вершин.");
    const scaled = (type === "float" || type === "float32" || type === "double" || type === "float64")
      && value >= 0 && value <= 1 ? value * 255 : value;
    return Math.round(Math.max(0, Math.min(255, scaled)));
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
    const colorReaders = getColorReaders(vertex.properties);
    const normalReaders = getNormalReaders(vertex.properties);

    const pointCloud = !face || face.count === 0;
    const previewVertexCount = pointCloud ? Math.min(vertex.count, MAX_PREVIEW_POINTS) : vertex.count;
    const positions = new Float32Array(previewVertexCount * 3);
    const colors = colorReaders ? new Uint8Array(previewVertexCount * 3) : null;
    const vertexNormals = normalReaders && !pointCloud ? new Float32Array(previewVertexCount * 3) : null;
    let normalsValid = Boolean(vertexNormals);
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
      if (colors) {
        for (let channel = 0; channel < 3; channel += 1) {
          const reader = colorReaders[channel];
          const value = Number(record.values[reader.index]);
          const color = normalizeColor(value, reader.type);
          if (sampledOffset >= 0) colors[sampleIndex * 3 + channel] = color;
        }
      }
      if (normalsValid) {
        const x = Number(record.values[normalReaders[0].index]);
        const y = Number(record.values[normalReaders[1].index]);
        const z = Number(record.values[normalReaders[2].index]);
        if (sampledOffset >= 0) {
          normalsValid = writeNormalizedVector(vertexNormals, sampledOffset, x, y, z);
        } else if (![x, y, z].every(Number.isFinite)) {
          normalsValid = false;
        }
      }
    }

    const triangles = [];
    const triangleColors = [];
    let faceCount = 0;
    if (face) {
      if (!face.properties.some((property) => property.list && property.name === "vertex_indices")) {
        throw new Error("В PLY не найдены индексы вершин граней.");
      }
      const faceColorReaders = getColorReaders(face.properties);
      const faceColorChannels = faceColorReaders
        ? new Map(faceColorReaders.map((reader, channel) => [reader.index, channel])) : null;
      const faceStep = Math.max(1, Math.ceil(face.count / MAX_PREVIEW_FACES));
      for (let faceIndex = 0; faceIndex < face.count; faceIndex += 1) {
        const record = nextAsciiRecord(bytes, cursor);
        cursor = record.cursor;
        const sampleFace = faceIndex % faceStep === 0;
        const triangleStart = triangles.length;
        const faceColor = [0, 0, 0];
        let valueIndex = 0;
        for (let propertyIndex = 0; propertyIndex < face.properties.length; propertyIndex += 1) {
          const property = face.properties[propertyIndex];
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
            const samplePolygon = property.name === "vertex_indices" && sampleFace;
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
            const channel = faceColorChannels?.get(propertyIndex);
            if (channel !== undefined) {
              faceColor[channel] = normalizeColor(Number(record.values[valueIndex]), property.type);
            }
            valueIndex += 1;
          }
        }
        if (valueIndex !== record.values.length) throw new Error("Некорректная строка граней PLY.");
        const addedTriangles = (triangles.length - triangleStart) / 3;
        if (faceColorChannels && addedTriangles) {
          for (let triangle = 0; triangle < addedTriangles; triangle += 1) triangleColors.push(...faceColor);
        }
        faceCount += 1;
      }
    }

    return {
      positions,
      colors,
      vertexNormals: normalsValid ? vertexNormals : null,
      triangleColors: triangleColors.length ? new Uint8Array(triangleColors) : null,
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
    const colorOffsets = { red: null, r: null, green: null, g: null, blue: null, b: null };
    const normalOffsets = { nx: null, normal_x: null, ny: null, normal_y: null, nz: null, normal_z: null };
    let vertexStride = 0;

    for (const property of vertex.properties) {
      if (property.list) throw new Error("Списки свойств вершин PLY не поддерживаются.");
      const scalar = scalarTypes[property.type];
      if (!scalar) throw new Error(`Неподдерживаемый тип PLY: ${property.type}.`);
      if (Object.hasOwn(offsets, property.name)) offsets[property.name] = { offset: vertexStride, type: property.type };
      if (Object.hasOwn(colorOffsets, property.name)) {
        colorOffsets[property.name] = { offset: vertexStride, type: property.type };
      }
      if (Object.hasOwn(normalOffsets, property.name)) {
        normalOffsets[property.name] = { offset: vertexStride, type: property.type };
      }
      vertexStride += scalar.size;
    }
    if (Object.values(offsets).some((item) => item === null)) throw new Error("У вершин PLY должны быть координаты x, y, z.");

    if (vertex.count > Math.floor((buffer.byteLength - dataOffset) / vertexStride)) {
      throw new Error("Данные вершин PLY обрезаны.");
    }
    const pointCloud = !face || face.count === 0;
    const previewVertexCount = pointCloud ? Math.min(vertex.count, MAX_PREVIEW_POINTS) : vertex.count;
    const positions = new Float32Array(previewVertexCount * 3);
    const colorProperties = [
      colorOffsets.red || colorOffsets.r,
      colorOffsets.green || colorOffsets.g,
      colorOffsets.blue || colorOffsets.b,
    ];
    const colors = colorProperties.every(Boolean) ? new Uint8Array(previewVertexCount * 3) : null;
    const normalProperties = [
      normalOffsets.nx || normalOffsets.normal_x,
      normalOffsets.ny || normalOffsets.normal_y,
      normalOffsets.nz || normalOffsets.normal_z,
    ];
    const vertexNormals = normalProperties.every(Boolean) && !pointCloud
      ? new Float32Array(previewVertexCount * 3) : null;
    let normalsValid = Boolean(vertexNormals);
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
      if (colors) {
        for (let channel = 0; channel < 3; channel += 1) {
          const property = colorProperties[channel];
          const value = scalarTypes[property.type].read(data, cursor + property.offset, littleEndian);
          const color = normalizeColor(value, property.type);
          if (sampledOffset >= 0) colors[sampleIndex * 3 + channel] = color;
        }
      }
      if (normalsValid) {
        const xProperty = normalProperties[0], yProperty = normalProperties[1], zProperty = normalProperties[2];
        const x = scalarTypes[xProperty.type].read(data, cursor + xProperty.offset, littleEndian);
        const y = scalarTypes[yProperty.type].read(data, cursor + yProperty.offset, littleEndian);
        const z = scalarTypes[zProperty.type].read(data, cursor + zProperty.offset, littleEndian);
        if (sampledOffset >= 0) {
          normalsValid = writeNormalizedVector(vertexNormals, sampledOffset, x, y, z);
        } else if (![x, y, z].every(Number.isFinite)) {
          normalsValid = false;
        }
      }
      cursor += vertexStride;
    }

    const triangles = [];
    const triangleColors = [];
    let faceCount = 0;
    if (face) {
      const indexList = face.properties.find((property) => property.list && property.name === "vertex_indices");
      if (!indexList) throw new Error("В PLY не найдены индексы вершин граней.");
      const faceColorReaders = getColorReaders(face.properties);
      const faceColorChannels = faceColorReaders
        ? new Map(faceColorReaders.map((reader, channel) => [reader.index, channel])) : null;
      const countScalar = scalarTypes[indexList.countType];
      const itemScalar = scalarTypes[indexList.itemType];
      if (!countScalar || !itemScalar) throw new Error("Неподдерживаемый тип индексов граней PLY.");
      const faceStep = Math.max(1, Math.ceil(face.count / MAX_PREVIEW_FACES));
      for (let faceIndex = 0; faceIndex < face.count; faceIndex += 1) {
        const sampleFace = faceIndex % faceStep === 0;
        const triangleStart = triangles.length;
        const faceColor = [0, 0, 0];
        for (let propertyIndex = 0; propertyIndex < face.properties.length; propertyIndex += 1) {
          const property = face.properties[propertyIndex];
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
            const samplePolygon = property.name === "vertex_indices" && sampleFace;
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
            const channel = faceColorChannels?.get(propertyIndex);
            if (channel !== undefined) {
              const value = scalar.read(data, cursor, littleEndian);
              faceColor[channel] = normalizeColor(value, property.type);
            }
            cursor += scalar.size;
          }
        }
        const addedTriangles = (triangles.length - triangleStart) / 3;
        if (faceColorChannels && addedTriangles) {
          for (let triangle = 0; triangle < addedTriangles; triangle += 1) triangleColors.push(...faceColor);
        }
        faceCount += 1;
      }
    }

    return {
      positions,
      colors,
      vertexNormals: normalsValid ? vertexNormals : null,
      triangleColors: triangleColors.length ? new Uint8Array(triangleColors) : null,
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
