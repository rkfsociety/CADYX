const fs = require("node:fs");
const inspector = require("node:inspector");
const path = require("node:path");
const { performance } = require("node:perf_hooks");
const vm = require("node:vm");

const options = new Map(process.argv.slice(2).map((argument) => {
  const [key, value] = argument.replace(/^--/, "").split("=", 2);
  return [key, value];
}));
const numberOption = (name, fallback) => Number(options.get(name) || fallback);
const gridSizes = (options.get("grid") || "12,48,96,144").split(",").map(Number);
const frameCount = numberOption("frames", 10);
const warmupFrames = numberOption("warmup", 2);
const width = numberOption("width", 1280);
const height = numberOption("height", 800);
const devicePixelRatio = Math.min(2, Math.max(1, numberOption("dpr", 2)));
const cameraZoom = numberOption("zoom", 3.5);

function createStressScene(cells) {
  const positions = [];
  const triangles = [];
  const span = 80;
  for (let row = 0; row <= cells; row += 1) {
    const y = (row / cells - 0.5) * span;
    for (let column = 0; column <= cells; column += 1) {
      const x = (column / cells - 0.5) * span;
      const z = 2 * Math.sin(x * 0.12) * Math.cos(y * 0.13);
      positions.push(x, y, z);
    }
  }
  for (let row = 0; row < cells; row += 1) {
    for (let column = 0; column < cells; column += 1) {
      const lowerLeft = row * (cells + 1) + column;
      const lowerRight = lowerLeft + 1;
      const upperLeft = lowerLeft + cells + 1;
      const upperRight = upperLeft + 1;
      triangles.push(lowerLeft, lowerRight, upperRight, lowerLeft, upperRight, upperLeft);
    }
  }
  return {
    positions,
    triangles: Uint32Array.from(triangles),
    lines: [],
    points: [],
    bounds: { min: [-span / 2, -span / 2, -2], max: [span / 2, span / 2, 2] },
  };
}

function createViewer(scene, { shaded, wireframe, interactiveRenderScale }) {
  const eventHandlers = {};
  const animationFrames = [];
  const context = {
    clearRect() {}, fillRect() {}, beginPath() {}, moveTo() {}, lineTo() {}, closePath() {},
    fill() {}, stroke() {}, strokeRect() {}, save() {}, restore() {}, setLineDash() {}, arc() {},
    fillText() {}, measureText(value) { return { width: String(value).length * 6 }; },
    createImageData(imageWidth, imageHeight) {
      return { width: imageWidth, height: imageHeight, data: new Uint8ClampedArray(imageWidth * imageHeight * 4) };
    },
    putImageData() {}, drawImage() {},
  };
  const canvas = {
    width: 0,
    height: 0,
    style: { removeProperty() {} },
    classList: { add() {}, remove() {} },
    getContext() { return context; },
    getBoundingClientRect() { return { left: 0, top: 0, width, height }; },
    addEventListener(name, callback) { eventHandlers[name] = callback; },
    setPointerCapture() {},
  };
  const sandbox = {
    window: { devicePixelRatio },
    document: { createElement() { return { width: 0, height: 0, getContext() { return context; } }; } },
    ResizeObserver: class { observe() {} },
    performance,
    requestAnimationFrame(callback) {
      animationFrames.push(callback);
      return animationFrames.length;
    },
  };
  vm.runInNewContext(
    `${fs.readFileSync(path.join(__dirname, "../web/viewport.js"), "utf8")}\n//# sourceURL=web/viewport.js`,
    sandbox,
  );
  const viewer = sandbox.window.CadyxViewport.createViewer(canvas, {
    showCoordinateGizmo: false,
    showRotationAxes: false,
    showWorldAxes: false,
    shaded,
    wireframe,
  });
  const loadStarted = performance.now();
  viewer.load({ ...scene, interactiveRenderScale });
  viewer.setCamera({ yaw: 0.68, pitch: 0.38, zoom: cameraZoom, projection: "orthographic" });
  const setupMs = performance.now() - loadStarted;

  return {
    canvas,
    eventHandlers,
    animationFrames,
    viewer,
    setupMs,
  };
}

function percentile(values, fraction) {
  const ordered = values.slice().sort((left, right) => left - right);
  return ordered[Math.min(ordered.length - 1, Math.ceil(ordered.length * fraction) - 1)];
}

function summarize(name, times, canvas, backingPixels = canvas.width * canvas.height) {
  const medianMs = percentile(times, 0.5);
  return {
    mode: name,
    frameMsP50: Number(medianMs.toFixed(2)),
    frameMsP95: Number(percentile(times, 0.95).toFixed(2)),
    cpuOnlyFpsEstimate: Number((1000 / medianMs).toFixed(1)),
    backingPixels,
  };
}

function measureFullResolution(viewer, canvas) {
  for (let frame = 0; frame < warmupFrames; frame += 1) viewer.render();
  const times = [];
  for (let frame = 0; frame < frameCount; frame += 1) {
    const started = performance.now();
    viewer.render();
    times.push(performance.now() - started);
  }
  return times;
}

function beginOrbit(bench) {
  bench.eventHandlers.pointerdown({ button: 0, clientX: width / 2, clientY: height / 2, pointerId: 1 });
}

function orbitFrame(bench, index) {
  const angle = index * 0.06;
  bench.eventHandlers.pointermove({
    clientX: width / 2 + Math.sin(angle) * height * 0.18,
    clientY: height / 2 + Math.cos(angle) * height * 0.18,
  });
  const callback = bench.animationFrames.shift();
  if (!callback) throw new Error("Orbit did not schedule a viewport frame.");
  const started = performance.now();
  callback(performance.now());
  return performance.now() - started;
}

function measureOrbit(bench) {
  beginOrbit(bench);
  for (let frame = 0; frame < warmupFrames; frame += 1) orbitFrame(bench, frame);
  const times = [];
  for (let frame = 0; frame < frameCount; frame += 1) times.push(orbitFrame(bench, frame + warmupFrames));
  const backingPixels = bench.canvas.width * bench.canvas.height;
  bench.eventHandlers.pointerup();
  return { times, backingPixels };
}

function post(session, method) {
  return new Promise((resolve, reject) => {
    session.post(method, (error, result) => error ? reject(error) : resolve(result));
  });
}

async function profileOrbit(bench, frames) {
  const session = new inspector.Session();
  session.connect();
  await post(session, "Profiler.enable");
  await post(session, "Profiler.start");
  beginOrbit(bench);
  for (let frame = 0; frame < frames; frame += 1) orbitFrame(bench, frame + 500);
  const { profile } = await post(session, "Profiler.stop");
  bench.eventHandlers.pointerup();
  await post(session, "Profiler.disable");
  session.disconnect();

  const framesByNode = new Map(profile.nodes.map((node) => [node.id, node.callFrame]));
  const selfTimeByFunction = new Map();
  for (let index = 0; index < (profile.samples || []).length; index += 1) {
    const callFrame = framesByNode.get(profile.samples[index]);
    if (!callFrame) continue;
    const isViewportFunction = /render|raster|drawEdge|flushVisibleRun|trackballPoint|scheduleCameraRender/i
      .test(callFrame.functionName || "");
    if (!callFrame.url.includes("viewport.js") && !isViewportFunction) continue;
    const key = `${callFrame.functionName || "(anonymous)"}:${callFrame.lineNumber + 1}`;
    selfTimeByFunction.set(key, (selfTimeByFunction.get(key) || 0) + (profile.timeDeltas?.[index] || 0));
  }
  return Array.from(selfTimeByFunction, ([functionName, microseconds]) => ({
    function: functionName,
    selfMs: Number((microseconds / 1000).toFixed(1)),
  })).sort((left, right) => right.selfMs - left.selfMs).slice(0, 10);
}

async function main() {
  const results = [];
  let largestCombinedBench = null;
  let largestScene = null;
  for (const cells of gridSizes) {
    if (!Number.isInteger(cells) || cells < 2) throw new Error(`Invalid grid size: ${cells}`);
    const scene = createStressScene(cells);
    const geometry = { cells, vertices: scene.positions.length / 3, triangles: scene.triangles.length / 3 };
    const modes = [
      { name: "shaded-full", shaded: true, wireframe: false },
      { name: "edges-full", shaded: false, wireframe: true },
      { name: "shaded+edges-full", shaded: true, wireframe: true },
      { name: "shaded+edges-orbit", shaded: true, wireframe: true, orbit: true },
      { name: "shaded+edges-orbit-depth-0.35", shaded: true, wireframe: true,
        orbit: true, interactiveRenderScale: 0.35 },
      { name: "shaded+edges-orbit-depth-0.25", shaded: true, wireframe: true,
        orbit: true, interactiveRenderScale: 0.25 },
    ];
    for (const mode of modes) {
      const bench = createViewer(scene, mode);
      const orbit = mode.orbit ? measureOrbit(bench) : null;
      const times = orbit ? orbit.times : measureFullResolution(bench.viewer, bench.canvas);
      results.push({
        ...geometry,
        setupMs: Number(bench.setupMs.toFixed(1)),
        ...summarize(mode.name, times, bench.canvas, orbit?.backingPixels),
      });
      if (mode.orbit && cells === Math.max(...gridSizes)) {
        largestCombinedBench = bench;
        largestScene = geometry;
      }
    }
  }

  const hotspotFrames = Math.max(8, Math.min(24, frameCount));
  const hotspots = await profileOrbit(largestCombinedBench, hotspotFrames);
  process.stdout.write(`${JSON.stringify({
    benchmark: "CADYX CPU viewport stress test (mock Canvas API, production viewport.js)",
    viewport: { width, height, devicePixelRatio, cameraZoom },
    warmupFrames,
    measuredFrames: frameCount,
    scenarios: results,
    profiledScene: largestScene,
    profileFrames: hotspotFrames,
    hottestViewportFunctions: hotspots,
    note: "Frame times include production JavaScript geometry, depth-buffer and edge loops; browser/GPU Canvas API compositing is not measured.",
  }, null, 2)}\n`);
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
