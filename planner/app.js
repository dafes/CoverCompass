"use strict";

const PLAN_FORMAT = "cover-compass-plan";
const PLAN_VERSION = 1;
const DRAFT_KEY = "cover-compass-planner-draft-v1";
const EARTH_RADIUS = 6371008.8;

const elements = Object.fromEntries(
  [...document.querySelectorAll("[id]")].map((element) => [element.id, element]),
);

const state = {
  map: null,
  geocoder: null,
  outline: [],
  shutters: [],
  outlineOverlay: null,
  draftOverlay: null,
  shutterOverlays: new Map(),
  drawing: false,
  placing: false,
  location: null,
};

let toastTimer;
let saveTimer;

function normalizeAzimuth(value) {
  return ((value % 360) + 360) % 360;
}

function angularDifference(first, second) {
  return Math.abs(((first - second + 540) % 360) - 180);
}

function round(value, digits = 7) {
  const scale = 10 ** digits;
  return Math.round(value * scale) / scale;
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.add("visible");
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => elements.toast.classList.remove("visible"), 2400);
}

function setStatus(message, action = false) {
  elements["map-status"].textContent = message;
  elements["map-status"].classList.toggle("action", action);
}

function setLocation(position) {
  state.location = { lat: position.lat(), lng: position.lng() };
  elements.coordinates.textContent = `${state.location.lat.toFixed(6)}, ${state.location.lng.toFixed(6)}`;
  scheduleSave();
}

function polygonCenter(points) {
  const total = points.reduce(
    (result, point) => ({ lat: result.lat + point.lat(), lng: result.lng + point.lng() }),
    { lat: 0, lng: 0 },
  );
  return { lat: total.lat / points.length, lng: total.lng / points.length };
}

function localPoint(latLng, referenceLatitude) {
  const latitude = (latLng.lat() * Math.PI) / 180;
  const longitude = (latLng.lng() * Math.PI) / 180;
  return {
    x: EARTH_RADIUS * longitude * Math.cos((referenceLatitude * Math.PI) / 180),
    y: EARTH_RADIUS * latitude,
  };
}

function nearestSegment(position) {
  const referenceLatitude = position.lat();
  const target = localPoint(position, referenceLatitude);
  let nearest = null;
  state.outline.forEach((start, index) => {
    const end = state.outline[(index + 1) % state.outline.length];
    const a = localPoint(start, referenceLatitude);
    const b = localPoint(end, referenceLatitude);
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const lengthSquared = dx * dx + dy * dy;
    const fraction = lengthSquared === 0
      ? 0
      : Math.max(0, Math.min(1, ((target.x - a.x) * dx + (target.y - a.y) * dy) / lengthSquared));
    const x = a.x + fraction * dx;
    const y = a.y + fraction * dy;
    const distance = Math.hypot(target.x - x, target.y - y);
    if (nearest === null || distance < nearest.distance) {
      nearest = { index, fraction, distance };
    }
  });
  return nearest;
}

function pointOnSegment(segmentIndex, fraction) {
  const start = state.outline[segmentIndex];
  const end = state.outline[(segmentIndex + 1) % state.outline.length];
  return google.maps.geometry.spherical.interpolate(start, end, fraction);
}

function outwardAzimuth(segmentIndex) {
  const start = state.outline[segmentIndex];
  const end = state.outline[(segmentIndex + 1) % state.outline.length];
  const middle = google.maps.geometry.spherical.interpolate(start, end, 0.5);
  const heading = google.maps.geometry.spherical.computeHeading(start, end);
  const first = normalizeAzimuth(heading + 90);
  const second = normalizeAzimuth(heading - 90);
  const center = new google.maps.LatLng(polygonCenter(state.outline));
  const firstPoint = google.maps.geometry.spherical.computeOffset(middle, 2, first);
  const secondPoint = google.maps.geometry.spherical.computeOffset(middle, 2, second);
  return google.maps.geometry.spherical.computeDistanceBetween(firstPoint, center)
    > google.maps.geometry.spherical.computeDistanceBetween(secondPoint, center)
    ? first
    : second;
}

function shutterAzimuth(shutter) {
  const outward = outwardAzimuth(shutter.segmentIndex);
  return normalizeAzimuth(outward + (shutter.flipped ? 180 : 0));
}

function compassName(azimuth) {
  const names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  return names[Math.round(azimuth / 45) % 8];
}

function disposeOverlay(overlay) {
  if (overlay) overlay.setMap(null);
}

function drawDraft() {
  disposeOverlay(state.draftOverlay);
  if (!state.drawing || state.outline.length === 0) return;
  state.draftOverlay = new google.maps.Polyline({
    map: state.map,
    path: state.outline,
    strokeColor: "#ffc561",
    strokeOpacity: 1,
    strokeWeight: 3,
    icons: [{ icon: { path: google.maps.SymbolPath.CIRCLE, scale: 4, fillColor: "#ffc561", fillOpacity: 1, strokeWeight: 0 }, offset: "0", repeat: "100%" }],
  });
}

function bindOutlineEditing() {
  const path = state.outlineOverlay.getPath();
  ["insert_at", "remove_at", "set_at"].forEach((eventName) => {
    path.addListener(eventName, () => {
      state.outline = path.getArray();
      state.shutters = state.shutters.filter((shutter) => shutter.segmentIndex < state.outline.length);
      if (state.outline.length >= 3) setLocation(new google.maps.LatLng(polygonCenter(state.outline)));
      renderShutters();
      updateControls();
      scheduleSave();
    });
  });
}

function renderOutline() {
  disposeOverlay(state.outlineOverlay);
  state.outlineOverlay = null;
  if (state.outline.length < 3 || state.drawing) return;
  state.outlineOverlay = new google.maps.Polygon({
    map: state.map,
    paths: state.outline,
    editable: true,
    clickable: false,
    fillColor: "#f2a93b",
    fillOpacity: 0.16,
    strokeColor: "#ffc561",
    strokeOpacity: 1,
    strokeWeight: 3,
    zIndex: 2,
  });
  bindOutlineEditing();
}

function renderShutterOverlay(shutter) {
  const center = pointOnSegment(shutter.segmentIndex, shutter.segmentPosition);
  const start = state.outline[shutter.segmentIndex];
  const end = state.outline[(shutter.segmentIndex + 1) % state.outline.length];
  const wallHeading = google.maps.geometry.spherical.computeHeading(start, end);
  const halfWidth = Math.min(2.2, Math.max(0.8, google.maps.geometry.spherical.computeDistanceBetween(start, end) * 0.1));
  const facadeLine = new google.maps.Polyline({
    map: state.map,
    path: [
      google.maps.geometry.spherical.computeOffset(center, halfWidth, wallHeading + 180),
      google.maps.geometry.spherical.computeOffset(center, halfWidth, wallHeading),
    ],
    strokeColor: "#ffb84e",
    strokeOpacity: 1,
    strokeWeight: 7,
    zIndex: 6,
  });
  const azimuth = shutterAzimuth(shutter);
  shutter.facadeAzimuth = azimuth;
  const direction = new google.maps.Polyline({
    map: state.map,
    path: [center, google.maps.geometry.spherical.computeOffset(center, 5, azimuth)],
    strokeColor: "#ffffff",
    strokeOpacity: 0.92,
    strokeWeight: 2,
    zIndex: 7,
    icons: [{ icon: { path: google.maps.SymbolPath.FORWARD_CLOSED_ARROW, scale: 3, fillColor: "#ffffff", fillOpacity: 1, strokeColor: "#ffffff" }, offset: "100%" }],
  });
  const focusCard = () => document.querySelector(`[data-shutter-id="${CSS.escape(shutter.id)}"] input`)?.focus();
  facadeLine.addListener("click", focusCard);
  direction.addListener("click", focusCard);
  state.shutterOverlays.set(shutter.id, [facadeLine, direction]);
}

function clearShutterOverlays() {
  state.shutterOverlays.forEach((overlays) => overlays.forEach(disposeOverlay));
  state.shutterOverlays.clear();
}

function renderShutters() {
  clearShutterOverlays();
  elements["shutter-list"].replaceChildren();
  if (state.shutters.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "Your shutters will appear here.";
    elements["shutter-list"].append(empty);
  }
  state.shutters.forEach((shutter) => {
    if (state.outline.length >= 3) renderShutterOverlay(shutter);
    const card = document.createElement("article");
    card.className = "shutter-card";
    card.dataset.shutterId = shutter.id;
    const input = document.createElement("input");
    input.value = shutter.name;
    input.maxLength = 100;
    input.setAttribute("aria-label", "Shutter name");
    input.addEventListener("input", () => {
      shutter.name = input.value;
      updateControls();
      scheduleSave();
    });
    const azimuth = document.createElement("span");
    azimuth.className = "azimuth";
    azimuth.textContent = `${compassName(shutter.facadeAzimuth)} · ${shutter.facadeAzimuth.toFixed(1)}°`;
    const actions = document.createElement("div");
    actions.className = "card-actions";
    const flip = document.createElement("button");
    flip.type = "button";
    flip.textContent = "Flip direction";
    flip.addEventListener("click", () => {
      shutter.flipped = !shutter.flipped;
      renderShutters();
      scheduleSave();
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "delete";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => {
      state.shutters = state.shutters.filter((item) => item.id !== shutter.id);
      renderShutters();
      updateControls();
      scheduleSave();
    });
    actions.append(flip, remove);
    card.append(input, azimuth, actions);
    elements["shutter-list"].append(card);
  });
  updateControls();
}

function startDrawing() {
  if ((state.outline.length || state.shutters.length) && !window.confirm("Replace the current outline and its shutters?")) return;
  disposeOverlay(state.outlineOverlay);
  clearShutterOverlays();
  state.outlineOverlay = null;
  state.outline = [];
  state.shutters = [];
  state.drawing = true;
  state.placing = false;
  document.querySelector(".map-stage").classList.add("drawing");
  document.querySelector(".map-stage").classList.remove("placing");
  setStatus("Click each roof corner, then close the outline", true);
  renderShutters();
  drawDraft();
  updateControls();
  scheduleSave();
}

function finishOutline() {
  if (state.outline.length < 3) return;
  state.drawing = false;
  disposeOverlay(state.draftOverlay);
  state.draftOverlay = null;
  document.querySelector(".map-stage").classList.remove("drawing");
  renderOutline();
  setLocation(new google.maps.LatLng(polygonCenter(state.outline)));
  setStatus("Outline ready · drag corners to adjust");
  updateControls();
  scheduleSave();
}

function togglePlacement() {
  if (state.outline.length < 3 || state.drawing) return;
  state.placing = !state.placing;
  elements["place-shutter"].classList.toggle("active", state.placing);
  elements["place-shutter"].textContent = state.placing ? "Click a facade on the map…" : "Place a shutter";
  document.querySelector(".map-stage").classList.toggle("placing", state.placing);
  setStatus(state.placing ? "Click near the wall where the shutter sits" : "Outline ready · drag corners to adjust", state.placing);
}

function addShutter(position) {
  const nearest = nearestSegment(position);
  const resolution = (156543.03392 * Math.cos((position.lat() * Math.PI) / 180)) / 2 ** state.map.getZoom();
  if (!nearest || nearest.distance > Math.max(8, resolution * 36)) {
    showToast("Click closer to an outline edge");
    return;
  }
  const shutter = {
    id: globalThis.crypto?.randomUUID?.() ?? `shutter-${Date.now()}-${state.shutters.length}`,
    name: `Shutter ${state.shutters.length + 1}`,
    segmentIndex: nearest.index,
    segmentPosition: round(nearest.fraction, 6),
    flipped: false,
    facadeAzimuth: 0,
  };
  state.shutters.push(shutter);
  renderShutters();
  scheduleSave();
  showToast("Shutter added · name it or flip its arrow");
}

function updateControls() {
  elements["undo-point"].disabled = !state.drawing || state.outline.length === 0;
  elements["finish-outline"].disabled = !state.drawing || state.outline.length < 3;
  elements["place-shutter"].disabled = state.drawing || state.outline.length < 3;
  const ready = Boolean(
    state.location
      && elements["house-name"].value.trim()
      && elements["time-zone"].value.trim()
      && state.outline.length >= 3
      && state.shutters.length > 0
      && state.shutters.every((shutter) => shutter.name.trim()),
  );
  elements["export-button"].disabled = !ready;
  elements["copy-button"].disabled = !ready;
  elements["download-button"].disabled = !ready;
  elements["export-summary"].textContent = ready
    ? `${state.shutters.length} shutter${state.shutters.length === 1 ? "" : "s"} ready to assign in CoverCompass.`
    : "Add an outline and at least one named shutter.";
}

function planObject() {
  const location = state.location
    ?? (state.outline.length ? polygonCenter(state.outline) : state.map.getCenter().toJSON());
  return {
    format: PLAN_FORMAT,
    version: PLAN_VERSION,
    house: {
      name: elements["house-name"].value.trim(),
      latitude: round(location.lat),
      longitude: round(location.lng),
      time_zone: elements["time-zone"].value.trim(),
      rotation: 0,
    },
    outline: state.outline.map((point) => ({
      latitude: round(point.lat()),
      longitude: round(point.lng()),
    })),
    shutters: state.shutters.map((shutter) => ({
      id: shutter.id,
      name: shutter.name.trim(),
      facade_azimuth: round(shutterAzimuth(shutter), 1),
      segment_index: shutter.segmentIndex,
      segment_position: round(shutter.segmentPosition, 6),
    })),
  };
}

function planJson() {
  return `${JSON.stringify(planObject(), null, 2)}\n`;
}

function safeFilename() {
  const base = elements["house-name"].value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "home";
  return `${base}-cover-compass-plan.json`;
}

function downloadPlan() {
  const url = URL.createObjectURL(new Blob([planJson()], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = safeFilename();
  link.click();
  URL.revokeObjectURL(url);
  showToast("Plan downloaded");
}

async function copyPlan() {
  try {
    await navigator.clipboard.writeText(planJson());
    showToast("Plan JSON copied");
  } catch {
    showToast("Clipboard access was blocked; use Download instead");
  }
}

function scheduleSave() {
  window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(() => {
    if (!state.map) return;
    localStorage.setItem(DRAFT_KEY, planJson());
  }, 250);
}

function validateLoadedPlan(value, complete = true) {
  if (value?.format !== PLAN_FORMAT || value?.version !== PLAN_VERSION) throw new Error("Not a CoverCompass plan version 1");
  if (!value.house || !Array.isArray(value.outline) || !Array.isArray(value.shutters)) throw new Error("The plan is missing required fields");
  if (complete && (value.outline.length < 3 || value.shutters.length < 1)) throw new Error("The plan needs an outline and a shutter");
  if (!Number.isFinite(value.house.latitude) || !Number.isFinite(value.house.longitude)) throw new Error("The house location is invalid");
  if (value.outline.some((point) => !Number.isFinite(point.latitude) || !Number.isFinite(point.longitude))) throw new Error("The outline contains invalid coordinates");
  if (value.shutters.some((shutter) => !Number.isInteger(shutter.segment_index)
    || shutter.segment_index < 0
    || shutter.segment_index >= value.outline.length
    || !Number.isFinite(shutter.segment_position)
    || shutter.segment_position < 0
    || shutter.segment_position > 1
    || !Number.isFinite(shutter.facade_azimuth))) {
    throw new Error("A shutter contains invalid geometry");
  }
}

function loadPlan(value, { fit = true } = {}) {
  validateLoadedPlan(value, false);
  disposeOverlay(state.outlineOverlay);
  disposeOverlay(state.draftOverlay);
  clearShutterOverlays();
  state.drawing = value.outline.length > 0 && value.outline.length < 3;
  state.placing = false;
  state.outline = value.outline.map((point) => new google.maps.LatLng(point.latitude, point.longitude));
  state.location = { lat: Number(value.house.latitude), lng: Number(value.house.longitude) };
  elements["house-name"].value = String(value.house.name || "Home");
  elements["time-zone"].value = String(value.house.time_zone || Intl.DateTimeFormat().resolvedOptions().timeZone);
  elements.coordinates.textContent = `${state.location.lat.toFixed(6)}, ${state.location.lng.toFixed(6)}`;
  state.shutters = value.shutters
    .filter((shutter) => Number.isInteger(shutter.segment_index) && shutter.segment_index < state.outline.length)
    .map((shutter) => {
      const loaded = {
        id: String(shutter.id),
        name: String(shutter.name),
        segmentIndex: shutter.segment_index,
        segmentPosition: Number(shutter.segment_position),
        facadeAzimuth: Number(shutter.facade_azimuth),
        flipped: false,
      };
      if (state.outline.length >= 3) {
        loaded.flipped = angularDifference(loaded.facadeAzimuth, outwardAzimuth(loaded.segmentIndex)) > 90;
      }
      return loaded;
    });
  renderOutline();
  drawDraft();
  renderShutters();
  if (fit && state.outline.length) {
    const bounds = new google.maps.LatLngBounds();
    state.outline.forEach((point) => bounds.extend(point));
    state.map.fitBounds(bounds, 80);
  } else if (state.location) {
    state.map.setCenter(state.location);
  }
  document.querySelector(".map-stage").classList.toggle("drawing", state.drawing);
  setStatus(
    state.drawing
      ? "Draft restored · continue clicking roof corners"
      : state.outline.length >= 3
        ? "Plan loaded · drag corners to adjust"
        : "Find the house and draw its outline",
    state.drawing,
  );
  updateControls();
  scheduleSave();
}

async function importFile(file) {
  try {
    const value = JSON.parse(await file.text());
    validateLoadedPlan(value, true);
    loadPlan(value);
    showToast("Plan opened");
  } catch (error) {
    showToast(error instanceof Error ? error.message : "Could not open that plan");
  } finally {
    elements["import-file"].value = "";
  }
}

async function searchAddress(event) {
  event.preventDefault();
  const address = elements.address.value.trim();
  if (!address || !state.geocoder) return;
  try {
    const response = await state.geocoder.geocode({ address });
    const result = response.results[0];
    if (!result) throw new Error("No matching location found");
    if (result.geometry.viewport) state.map.fitBounds(result.geometry.viewport);
    else state.map.setCenter(result.geometry.location);
    state.map.setZoom(Math.max(state.map.getZoom() ?? 20, 19));
    setLocation(result.geometry.location);
    setStatus("Location selected · draw the roof outline");
  } catch (error) {
    showToast(error instanceof Error ? error.message : "Address search failed");
  }
}

function handleMapClick(event) {
  if (state.drawing) {
    state.outline.push(event.latLng);
    drawDraft();
    updateControls();
    scheduleSave();
    return;
  }
  if (state.placing) addShutter(event.latLng);
}

function clearPlan() {
  if ((state.outline.length || state.shutters.length) && !window.confirm("Clear this plan? This cannot be undone.")) return;
  disposeOverlay(state.outlineOverlay);
  disposeOverlay(state.draftOverlay);
  clearShutterOverlays();
  state.outlineOverlay = null;
  state.draftOverlay = null;
  state.outline = [];
  state.shutters = [];
  state.drawing = false;
  state.placing = false;
  localStorage.removeItem(DRAFT_KEY);
  renderShutters();
  updateControls();
  setStatus("Find the house and draw its outline");
}

async function initializeMap() {
  state.map = new google.maps.Map(elements.map, {
    center: { lat: 51.1657, lng: 10.4515 },
    zoom: 6,
    mapTypeId: "satellite",
    tilt: 0,
    heading: 0,
    rotateControl: false,
    streetViewControl: false,
    fullscreenControl: false,
    mapTypeControlOptions: { mapTypeIds: ["satellite", "roadmap"] },
    gestureHandling: "greedy",
  });
  state.geocoder = new google.maps.Geocoder();
  state.map.addListener("click", handleMapClick);
  elements["api-gate"].classList.add("hidden");
  setStatus("Find the house and draw its outline");
  const saved = localStorage.getItem(DRAFT_KEY);
  if (saved) {
    try {
      loadPlan(JSON.parse(saved));
      showToast("Restored your last draft");
    } catch {
      localStorage.removeItem(DRAFT_KEY);
    }
  }
}

function loadMaps(apiKey) {
  elements["api-error"].textContent = "";
  window.gm_authFailure = () => {
    elements["api-error"].textContent = "Google rejected this key. Check the API and HTTP-referrer restrictions.";
    elements["api-gate"].classList.remove("hidden");
  };
  window.__coverCompassMapReady = initializeMap;
  const parameters = new URLSearchParams({
    key: apiKey,
    loading: "async",
    v: "weekly",
    libraries: "geometry,geocoding",
    callback: "__coverCompassMapReady",
  });
  const script = document.createElement("script");
  script.src = `https://maps.googleapis.com/maps/api/js?${parameters}`;
  script.async = true;
  script.onerror = () => {
    elements["api-error"].textContent = "Google Maps could not be loaded. Check the key and network connection.";
  };
  document.head.append(script);
}

elements["api-form"].addEventListener("submit", (event) => {
  event.preventDefault();
  const key = elements["api-key"].value.trim();
  if (key) loadMaps(key);
});
elements["search-form"].addEventListener("submit", searchAddress);
elements["use-center"].addEventListener("click", () => {
  if (!state.map) return;
  setLocation(state.map.getCenter());
  setStatus("Map center selected · draw the roof outline");
});
elements["draw-outline"].addEventListener("click", startDrawing);
elements["undo-point"].addEventListener("click", () => {
  state.outline.pop();
  drawDraft();
  updateControls();
  scheduleSave();
});
elements["finish-outline"].addEventListener("click", finishOutline);
elements["place-shutter"].addEventListener("click", togglePlacement);
elements["clear-plan"].addEventListener("click", clearPlan);
elements["import-button"].addEventListener("click", () => elements["import-file"].click());
elements["import-file"].addEventListener("change", () => {
  if (elements["import-file"].files[0]) importFile(elements["import-file"].files[0]);
});
elements["export-button"].addEventListener("click", downloadPlan);
elements["download-button"].addEventListener("click", downloadPlan);
elements["copy-button"].addEventListener("click", copyPlan);
[elements["house-name"], elements["time-zone"]].forEach((input) => {
  input.addEventListener("input", () => {
    updateControls();
    scheduleSave();
  });
});

elements["time-zone"].value = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
updateControls();
