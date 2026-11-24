const EARTH_RADIUS = 6371008.8;
const TO_DEGREES = 180 / Math.PI;
const GLOBE_SCRIPT = "https://cdn.jsdelivr.net/npm/globe.gl@2.35.1/dist/globe.gl.min.js";
const EARTH_TEXTURE = "https://cdn.jsdelivr.net/npm/three-globe/example/img/earth-blue-marble.jpg";

export function globePosition(x, y, z) {
  return {
    lat: Math.atan2(z, Math.hypot(x, y)) * TO_DEGREES,
    lng: Math.atan2(y, x) * TO_DEGREES,
    altitude: Math.hypot(x, y, z) / EARTH_RADIUS - 1,
  };
}

export function readTracks(packets) {
  if (!Array.isArray(packets)) throw new Error("Expected a CZML array.");
  const tracks = [];
  let start = Infinity;
  let end = -Infinity;
  for (const packet of packets) {
    const position = packet?.position;
    const values = position?.cartesian;
    const epoch = Date.parse(position?.epoch);
    if ((position?.referenceFrame ?? "FIXED") !== "FIXED" || !Number.isFinite(epoch) ||
        !Array.isArray(values) || values.length < 8 || values.length % 4) continue;
    const samples = [];
    for (let i = 0; i < values.length; i += 4) {
      const offset = values[i];
      const x = values[i + 1], y = values[i + 2], z = values[i + 3];
      const time = epoch + offset * 1000;
      if (![offset, x, y, z, time].every(Number.isFinite) ||
          Math.abs(time) > 8.64e15 || (i && time <= samples.at(-1).time)) break;
      samples.push({ time, x, y, z });
    }
    if (samples.length * 4 !== values.length) continue;
    start = Math.min(start, samples[0].time);
    end = Math.max(end, samples.at(-1).time);
    tracks.push({
      name: packet.name || packet.id || "Satellite",
      samples,
      path: samples.map(({ x, y, z }) => globePosition(x, y, z)),
    });
  }
  if (!tracks.length) throw new Error("No supported Earth-fixed Cartesian tracks were found.");
  return { tracks, start, end };
}

export function interpolate(samples, time) {
  if (time < samples[0].time || time > samples.at(-1).time) return null;
  let low = 0;
  let high = samples.length - 1;
  while (high - low > 1) {
    const middle = Math.floor((low + high) / 2);
    if (samples[middle].time <= time) low = middle;
    else high = middle;
  }
  const a = samples[low];
  const b = samples[high];
  const fraction = (time - a.time) / (b.time - a.time);
  return globePosition(
    a.x + (b.x - a.x) * fraction,
    a.y + (b.y - a.y) * fraction,
    a.z + (b.z - a.z) * fraction,
  );
}

let globeLibrary;
function loadGlobe() {
  globeLibrary ??= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = GLOBE_SCRIPT;
    script.onload = () => resolve(window.Globe);
    script.onerror = () => {
      script.remove();
      globeLibrary = undefined;
      reject(new Error("Could not load Globe.GL. Check your internet connection."));
    };
    document.head.append(script);
  });
  return globeLibrary;
}

const get = (id) => document.getElementById(id);
let globe;
let version = 0;
let animation = 0;
let active;
let lastFrame;

function resizeGlobe() {
  const viewport = get("globe");
  if (globe && !viewport.hidden) globe.width(viewport.clientWidth).height(viewport.clientHeight);
}

function stopPlayback() {
  cancelAnimationFrame(animation);
  animation = 0;
  lastFrame = undefined;
  get("orbit-play").textContent = "Play";
}

function draw(time) {
  const markers = active.tracks.flatMap(({ samples, marker }) => {
    const position = interpolate(samples, time);
    if (!position) return [];
    return [Object.assign(marker, position)];
  });
  globe.htmlElementsData(markers);
  get("orbit-time").value = (time - active.start) / 1000;
  get("orbit-clock").textContent = new Date(time).toISOString().slice(0, 19).replace("T", " ") + " UTC";
}

function tick(now) {
  const time = Math.min(active.end, active.start + get("orbit-time").valueAsNumber * 1000 +
    (lastFrame === undefined ? 0 : (now - lastFrame) * 60));
  lastFrame = now;
  draw(time);
  if (time < active.end) animation = requestAnimationFrame(tick);
  else stopPlayback();
}

export async function showOrbit(loadPackets) {
  const dialog = get("orbit-dialog");
  const viewport = get("globe");
  const status = get("orbit-status");
  const controls = get("orbit-controls");
  const slider = get("orbit-time");
  const current = ++version;
  stopPlayback();
  viewport.hidden = true;
  controls.hidden = true;
  get("orbit-title").textContent = "Orbit viewer";
  status.textContent = "Loading orbit…";
  if (!dialog.open) dialog.showModal();
  try {
    const packets = await loadPackets();
    if (current !== version || !dialog.open) return;
    const data = readTracks(packets);
    const Globe = await loadGlobe();
    if (current !== version || !dialog.open) return;

    viewport.hidden = false;
    if (!globe) {
      globe = new Globe(viewport, { animateIn: false })
        .backgroundColor("#081522")
        .globeImageUrl(EARTH_TEXTURE)
        .atmosphereColor("#6fb8dc")
        .pathPoints("path")
        .pathPointLat("lat")
        .pathPointLng("lng")
        .pathPointAlt("altitude")
        .pathColor(() => "#69c5e4")
        .pathTransitionDuration(0)
        .htmlAltitude("altitude")
        .htmlElement(({ element }) => element)
        .htmlTransitionDuration(0);
      new ResizeObserver(resizeGlobe).observe(viewport);
    }
    globe.resumeAnimation();
    resizeGlobe();
    active = data;
    for (const track of active.tracks) {
      const element = document.createElement("span");
      element.className = "orbit-marker";
      element.title = track.name;
      track.marker = { element };
    }
    globe.pathsData(active.tracks);
    const { lat, lng, altitude } = active.tracks[0].path[0];
    globe.pointOfView({ lat, lng, altitude: Math.max(2.5, altitude * 1.25 + 0.4) });
    slider.max = (active.end - active.start) / 1000;
    draw(active.start);
    controls.hidden = false;
    get("orbit-title").textContent = packets.find((packet) => packet?.id === "document")?.name || "Orbit viewer";
    status.textContent = `${active.tracks.length.toLocaleString()} ${active.tracks.length === 1 ? "track" : "tracks"} · Playback at 60×`;
  } catch (error) {
    if (current === version && dialog.open) {
      viewport.hidden = true;
      globe?.pauseAnimation();
      status.textContent = error.message;
    }
  }
}

get("orbit-play").addEventListener("click", () => {
  if (animation) {
    stopPlayback();
    return;
  }
  const slider = get("orbit-time");
  if (slider.valueAsNumber >= Number(slider.max)) draw(active.start);
  get("orbit-play").textContent = "Pause";
  animation = requestAnimationFrame(tick);
});
get("orbit-time").addEventListener("input", (event) => {
  lastFrame = undefined;
  draw(active.start + event.target.valueAsNumber * 1000);
});
get("orbit-dialog").addEventListener("close", () => {
  version += 1;
  stopPlayback();
  globe?.pauseAnimation();
});
