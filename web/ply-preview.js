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

    if (format !== "binary_little_endian") throw new Error("Предпросмотр поддерживает только двоичные PLY-файлы с порядком байтов от младшего к старшему.");
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
    const positions = new Float32Array(vertex.count * 3);
    const coordinateReaders = ["x", "y", "z"].map((key) => ({
      offset: offsets[key].offset, read: scalarTypes[offsets[key].type].read,
    }));
    let cursor = dataOffset;
    for (let index = 0; index < vertex.count; index += 1) {
      for (let axis = 0; axis < 3; axis += 1) {
        const property = coordinateReaders[axis];
        const value = property.read(data, cursor + property.offset);
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
            if (countReader.size > buffer.byteLength - cursor) throw new Error("Данные граней PLY обрезаны.");
            const itemCount = countReader.read(data, cursor);
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
            let first = null;
            let previous = null;
            for (let item = 0; item < itemCount; item += 1) {
              const index = itemReader.read(data, cursor);
              if (!Number.isSafeInteger(index) || index < 0 || index >= vertex.count) {
                throw new Error("Грань PLY ссылается на отсутствующую вершину.");
              }
              if (item === 0) first = previous = index;
              else if (item === 1) previous = index;
              else {
                triangles.push(first, previous, index);
                previous = index;
              }
              cursor += itemReader.size;
            }
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
      faceCount,
      previewFaceCount: triangles.length / 3,
    };
  }

  window.CadyxPlyPreview = { parse: parsePly };
})();

