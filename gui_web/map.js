/*
 * map.js – Leaflet-Kartenansicht für die Qt-WebEngine.
 * Kommuniziert über QWebChannel mit dem Python-Backend ("bridge"):
 *   Python -> JS:  stateUpdated(json), settingsUpdated(json)
 *                  (State enthält aircraft, trail, trails, route, own,
 *                   selected_rel, alerts, center, zoom_cmd, labels, …)
 *   JS -> Python:  uiReady(), aircraftClicked(call), mapContextMenu(lat, lon),
 *                  mapMoved(lat, lon, zoom, north, south, east, west)
 */
"use strict";

window.ltuMapStartupStage = "map-script-started";
window.ltuMapBridgeError = null;
window.ltuMapVisualReady = false;
window.ltuLeafletCreated = false;

// Top-View-Silhouetten (100x100-Grid, Nase oben) – identisch zur Doku (docs/gui_preview.svg)
const PLANE_PATH =
  "M50 3 L54 26 L55 34 L95 57 L95 63 L55 49 L53 74 L74 87 L74 92 " +
  "L52 84 L50 97 L48 84 L26 92 L26 87 L47 74 L45 49 L5 63 L5 57 " +
  "L45 34 L46 26 Z";

function heliSvgInner(color) {
  return (
    `<rect x="2" y="44" width="96" height="6" rx="3" fill="${color}" stroke="#0c0e16" stroke-width="3"/>` +
    `<polygon points="46,62 54,62 52,92 48,92" fill="${color}" stroke="#0c0e16" stroke-width="3"/>` +
    `<rect x="36" y="88" width="28" height="6" rx="2" fill="${color}" stroke="#0c0e16" stroke-width="3"/>` +
    `<ellipse cx="50" cy="41" rx="14" ry="29" fill="${color}" stroke="#0c0e16" stroke-width="3"/>` +
    `<circle cx="50" cy="47" r="6" fill="#0c0e16"/>`
  );
}

function iconHtml(ac, size, showLabel) {
  const inner = ac.kind === "heli"
    ? heliSvgInner(ac.color)
    : `<path d="${PLANE_PATH}" fill="${ac.color}" stroke="#0c0e16" stroke-width="4" stroke-linejoin="round"/>`;
  const ring = ac.emerg
    ? `<circle cx="50" cy="50" r="47" fill="none" stroke="${ac.color}" stroke-width="7"/>`
    : "";
  const lbl = showLabel && ac.label
    ? `<div class="lbl${ac.emerg ? " emerg" : ""}">${escapeHtml(ac.label)}</div>`
    : "";
  return (
    `<div class="ac${ac.selected ? " sel" : ""}" style="width:${size}px;height:${size}px">` +
    lbl +
    `<svg viewBox="0 0 100 100" style="transform:rotate(${(ac.hdg || 0).toFixed(0)}deg)">` +
    ring + inner +
    `</svg></div>`
  );
}

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ------------------------------------------------------------------
const settings = {
  tile_url: "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
  tile_attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  max_zoom: 19,
  center: [50.033, 8.570],
  zoom: 7,
  icon_size: 26,
  selected_size: 34,
  label_color: "#f2f4fa",
  accent: "#4da3ff",
};

const ADSB_ATTRIBUTION =
  'ADS-B sources: <a href="https://github.com/adsblol/api">adsb.lol</a> · ' +
  '<a href="https://github.com/adsbfi/opendata">adsb.fi</a> · ' +
  '<a href="https://opensky-network.org/">OpenSky</a>';

function mapAttribution() {
  return [settings.tile_attribution, ADSB_ATTRIBUTION].filter(Boolean).join(" · ");
}

const map = L.map("map", {
  center: settings.center,
  zoom: settings.zoom,
  zoomControl: true,
  attributionControl: true,
  worldCopyJump: true,
});
map.attributionControl.setPrefix(false);
window.ltuLeafletCreated = true;
window.ltuMapStartupStage = "leaflet-map-created";

const FALLBACK_TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
const FALLBACK_TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
settings.tile_attribution = settings.tile_attribution || FALLBACK_TILE_ATTRIBUTION;
const TILE_LAYER_WAIT_MS = 7000;
const tileStats = {
  loaded: 0,
  failed: 0,
  active_url: settings.tile_url,
  fallback_used: false,
  fallback_reason: null,
  last_error: null,
};
let tileLayer = null;
let tileLayerGeneration = 0;
let tileFallbackTimer = null;

function setMapNotice(key, detail) {
  if (typeof window.ltuSetMapNotice === "function") window.ltuSetMapNotice(key, detail);
}

function updateNoticeAfterMapReady() {
  if (window.ltuMapAssetError) {
    setMapNotice("asset_failed");
  } else if (window.ltuMapBridgeError && !window.ltuBridgeReady) {
    setMapNotice("bridge_failed");
  } else {
    setMapNotice(null);
  }
}

function waitForMapViewport(generation, isFallback) {
  tileFallbackTimer = window.setTimeout(() => {
    if (generation !== tileLayerGeneration || tileStats.loaded > 0) return;
    map.invalidateSize({ animate: false });
    const size = map.getSize();
    if (!size || size.x <= 0 || size.y <= 0) {
      waitForMapViewport(generation, isFallback);
      return;
    }
    window.ltuMapStartupStage = "map-viewport-restored";
    setMapNotice("loading");
    installTileLayer(tileStats.active_url, settings.max_zoom, mapAttribution(), isFallback);
  }, 3000);
}

function switchToFallback(reason) {
  if (tileStats.fallback_used) {
    setMapNotice("tiles_failed");
    return;
  }
  tileStats.fallback_used = true;
  tileStats.fallback_reason = reason;
  settings.tile_url = FALLBACK_TILE_URL;
  settings.tile_attribution = FALLBACK_TILE_ATTRIBUTION;
  settings.max_zoom = 19;
  window.ltuMapStartupStage = "tile-fallback-loading";
  setMapNotice("fallback");
  console.warn(`[LTU] Primary map tiles unavailable (${reason}); retrying with OpenStreetMap subdomains.`);
  installTileLayer(FALLBACK_TILE_URL, 19, mapAttribution(), true);
}

function installTileLayer(url, maxZoom, attribution, isFallback = false) {
  if (tileFallbackTimer !== null) {
    window.clearTimeout(tileFallbackTimer);
    tileFallbackTimer = null;
  }
  if (tileLayer) map.removeLayer(tileLayer);

  const generation = ++tileLayerGeneration;
  tileStats.loaded = 0;
  tileStats.failed = 0;
  tileStats.active_url = url;
  window.ltuMapVisualReady = false;
  const options = { maxZoom, attribution };
  if (url.indexOf("{s}") !== -1) options.subdomains = ["a", "b", "c"];
  tileLayer = L.tileLayer(url, options);
  tileLayer.on("tileload", () => {
    if (generation !== tileLayerGeneration) return;
    if (tileFallbackTimer !== null) {
      window.clearTimeout(tileFallbackTimer);
      tileFallbackTimer = null;
    }
    tileStats.loaded += 1;
    window.ltuMapVisualReady = true;
    window.ltuMapStartupStage = "tiles-loaded";
    updateNoticeAfterMapReady();
  });
  tileLayer.on("tileerror", (event) => {
    if (generation !== tileLayerGeneration) return;
    tileStats.failed += 1;
    const tile = event && event.tile;
    tileStats.last_error = {
      at: new Date().toISOString(),
      url: String((tile && (tile.currentSrc || tile.src)) || "").slice(0, 320),
    };
  });
  tileLayer.addTo(map);

  tileFallbackTimer = window.setTimeout(() => {
    if (generation !== tileLayerGeneration || tileStats.loaded > 0) return;
    tileFallbackTimer = null;
    map.invalidateSize({ animate: false });
    const size = map.getSize();
    if (!size || size.x <= 0 || size.y <= 0) {
      window.ltuMapStartupStage = "map-zero-size";
      setMapNotice("viewport_failed");
      console.warn("[LTU] Map viewport has zero size; waiting for the window layout before retrying tiles.");
      waitForMapViewport(generation, isFallback);
      return;
    }
    if (!isFallback) {
      switchToFallback("no tiles loaded within the startup window");
      return;
    }
    window.ltuMapStartupStage = "tiles-unavailable";
    setMapNotice("tiles_failed");
    console.error("[LTU] No map tiles loaded from the primary or fallback provider.");
  }, TILE_LAYER_WAIT_MS);
}

window.ltuRefreshMapLayout = () => map.invalidateSize({ animate: false });
window.ltuGetMapDiagnostics = () => {
  const container = document.getElementById("map");
  const rect = container ? container.getBoundingClientRect() : null;
  const size = map.getSize();
  return {
    leaflet: typeof L !== "undefined",
    document_ready: document.readyState,
    bridge_ready: !!window.ltuBridgeReady,
    map_size: [size.x, size.y],
    container_size: rect ? [rect.width, rect.height] : null,
    tile_url: tileStats.active_url,
    tile_loaded: tileStats.loaded,
    tile_failed: tileStats.failed,
    tile_fallback_used: tileStats.fallback_used,
    tile_fallback_reason: tileStats.fallback_reason,
    tile_last_error: tileStats.last_error,
  };
};
window.ltuBridgeReady = false;

installTileLayer(settings.tile_url, settings.max_zoom, mapAttribution());

const markers = new Map();      // call -> {marker, html}
const trailGroup = L.layerGroup().addTo(map);
let trailPoints = [];
const routeGroup = L.layerGroup().addTo(map);
let routeKey = "";
const trailsGroup = L.layerGroup().addTo(map);   // Spuren aller Flugzeuge
const trailLines = new Map();                    // call -> Polyline
let ownMarker = null;                            // eigenes Flugzeug (MSFS/TCAS)
let ownKey = "";

function ownHtml(o) {
  const s = settings.selected_size;
  return (
    `<div class="ac own" style="width:${s}px;height:${s}px">` +
    `<svg viewBox="0 0 100 100" style="transform:rotate(${(o.hdg || 0).toFixed(0)}deg)">` +
    `<circle cx="50" cy="50" r="46" fill="none" stroke="${settings.accent}" stroke-width="6" opacity="0.9"/>` +
    `<path d="${PLANE_PATH}" fill="#ffffff" stroke="${settings.accent}" stroke-width="4" stroke-linejoin="round"/>` +
    `</svg></div>`
  );
}

function ownIcon(o) {
  const s = settings.selected_size;
  return L.divIcon({
    html: ownHtml(o), className: "acwrap",
    iconSize: [s, s], iconAnchor: [s / 2, s / 2],
  });
}

let bridge = null;

function applySettings(s) {
  Object.assign(settings, s || {});
  document.documentElement.style.setProperty("--label-color", settings.label_color);
  document.documentElement.style.setProperty("--accent", settings.accent);
  if (s && s.tile_url) {
    tileStats.fallback_used = false;
    tileStats.fallback_reason = null;
    tileStats.last_error = null;
    window.ltuMapStartupStage = "map-style-loading";
    setMapNotice("loading");
    installTileLayer(
      s.tile_url,
      s.max_zoom || settings.max_zoom,
      mapAttribution(),
    );
  }
  if (s && s.center) map.setView(s.center, s.zoom || map.getZoom(), { animate: false });
}

function renderState(st) {
  if (!st) return;

  // --- Marker ---
  const seen = new Set();
  for (const ac of st.aircraft) {
    seen.add(ac.call);
    const size = ac.selected ? settings.selected_size : settings.icon_size;
    const html = iconHtml(ac, size, st.labels);
    const entry = markers.get(ac.call);
    if (!entry) {
      const icon = L.divIcon({
        html: html,
        className: "acwrap",
        iconSize: [size, size],
        iconAnchor: [size / 2, size / 2],
      });
      const marker = L.marker([ac.lat, ac.lon], { icon, interactive: true }).addTo(map);
      marker.on("click", () => bridge && bridge.aircraftClicked(ac.call));
      markers.set(ac.call, { marker, html, size });
    } else {
      entry.marker.setLatLng([ac.lat, ac.lon]);
      if (entry.html !== html || entry.size !== size) {
        entry.marker.setIcon(L.divIcon({
          html: html,
          className: "acwrap",
          iconSize: [size, size],
          iconAnchor: [size / 2, size / 2],
        }));
        entry.html = html;
        entry.size = size;
      }
    }
  }
  for (const [call, entry] of markers) {
    if (!seen.has(call)) {
      map.removeLayer(entry.marker);
      markers.delete(call);
    }
  }

  // --- Spuren aller Flugzeuge (Toggle "Spuren") ---
  const trails = st.trails || {};
  const colorBy = {};
  for (const ac of st.aircraft) colorBy[ac.call] = ac.color;
  for (const call of Array.from(trailLines.keys())) {
    if (!(call in trails)) {
      trailsGroup.removeLayer(trailLines.get(call));
      trailLines.delete(call);
    }
  }
  for (const call in trails) {
    const pts = trails[call];
    if (!pts || pts.length < 2) continue;
    const color = colorBy[call] || settings.accent;
    const line = trailLines.get(call);
    if (!line) {
      trailLines.set(call, L.polyline(pts, {
        color, weight: 2, opacity: 0.6, interactive: false,
      }).addTo(trailsGroup));
    } else {
      line.setLatLngs(pts);
      line.setStyle({ color });
    }
  }

  // --- Eigenes Flugzeug (MSFS/TCAS) ---
  if (st.own && st.own.valid) {
    const key = [st.own.lat, st.own.lon, st.own.hdg].join(",");
    if (!ownMarker) {
      ownMarker = L.marker([st.own.lat, st.own.lon], {
        icon: ownIcon(st.own), interactive: false, zIndexOffset: 1000,
      }).addTo(map);
      ownKey = key;
    } else {
      ownMarker.setLatLng([st.own.lat, st.own.lon]);
      if (key !== ownKey) {
        ownMarker.setIcon(ownIcon(st.own));
        ownKey = key;
      }
    }
  } else if (ownMarker) {
    map.removeLayer(ownMarker);
    ownMarker = null;
    ownKey = "";
  }

  // --- Trail (inkrementell) ---
  const incoming = st.trail || [];
  const sameHead = trailPoints.length && incoming.length &&
    trailPoints[0][0] === incoming[0][0] && trailPoints[0][1] === incoming[0][1];
  if (incoming.length < trailPoints.length || !sameHead) {
    trailGroup.clearLayers();
    trailPoints = [];
  }
  for (let i = Math.max(1, trailPoints.length); i < incoming.length; i++) {
    const a = incoming[i - 1], b = incoming[i];
    L.polyline([[a[0], a[1]], [b[0], b[1]]], {
      color: b[2], weight: 3, opacity: 0.9, interactive: false,
    }).addTo(trailGroup);
  }
  trailPoints = incoming;

  // --- Route ---
  const key = st.route ? JSON.stringify([st.route.origin_icao, st.route.dest_icao]) : "";
  if (key !== routeKey) {
    routeKey = key;
    routeGroup.clearLayers();
    if (st.route) {
      if (st.route.path && st.route.path.length) {
        L.polyline(st.route.path, {
          color: settings.accent, weight: 2, dashArray: "6 6", opacity: 0.9, interactive: false,
        }).addTo(routeGroup);
      }
      const ends = [
        [st.route.origin_icao, st.route.origin_name, st.route.path && st.route.path[0]],
        [st.route.dest_icao, st.route.dest_name, st.route.path && st.route.path[st.route.path.length - 1]],
      ];
      for (const [icao, name, pos] of ends) {
        if (!icao || !pos) continue;
        L.circleMarker(pos, {
          radius: 5, color: settings.accent, weight: 2,
          fillColor: settings.accent, fillOpacity: 0.6,
        }).bindTooltip(`${icao}${name ? " – " + name : ""}`, { direction: "top" })
          .addTo(routeGroup);
      }
    }
  }

  // --- Status-Overlay: welcher Teil funktioniert / ist down ---
  const net = document.getElementById("netstatus");
  if (net) {
    const s = st.status || {};
    const order = ["proxy", "adsb.lol", "adsb.fi", "opensky", "ersatz", "msfs"];
    const short = { "adsb.lol": "lol", "adsb.fi": "fi", "opensky": "OSKY", "ersatz": "Ersatz" };
    const parts = [];
    for (const k of order) {
      const e = s[k];
      if (!e || e.ok == null) continue;
      const cls = e.ok ? "ok" : (e.code === 429 ? "warn" : "bad");
      const tip = `${k}: ${e.ok ? "ok" : (e.code === 429 ? "429 rate-limited" : (e.detail || "down"))}`;
      parts.push(`<span class="nd ${cls}" title="${escapeHtml(tip)}">${escapeHtml(short[k] || k)}</span>`);
    }
    const n = st.net;
    if (n && n.enabled) {
      let role = n.role === "ersatz" ? "Ersatz-Proxy (du!)" : n.role;
      if (n.ersatz && n.role !== "ersatz") role += ` → ${n.ersatz.name || "?"}`;
      parts.push(`<span class="nrole">${escapeHtml(role)} · ${n.peers || 0} Clients</span>`);
    } else if (n) {
      parts.push(`<span class="nrole">Mesh aus</span>`);
    }
    net.innerHTML = parts.join("");
  }

  // --- Kamera ---
  if (st.center) map.panTo(st.center, { animate: false });
  if (st.zoom_cmd) map.setZoom(Math.max(3, Math.min(21, map.getZoom() + st.zoom_cmd)));
}

map.on("moveend", () => {
  if (bridge) {
    const c = map.getCenter();
    const b = map.getBounds();
    // Zentrum + Zoom + sichtbarer Bereich (bbox) -> Python fragt genau diesen Bereich
    bridge.mapMoved(c.lat, c.lng, map.getZoom(),
                    b.getNorth(), b.getSouth(), b.getEast(), b.getWest());
  }
});
map.on("contextmenu", (e) => {
  if (bridge) bridge.mapContextMenu(e.latlng.lat, e.latlng.lng);
});

// ------------------------------------------------------------------
function retryOrReportQtBridge(attempt, reason) {
  if (attempt < 3) {
    window.setTimeout(() => connectQtBridge(attempt + 1), 250);
    return;
  }
  window.ltuMapStartupStage = "webchannel-unavailable";
  window.ltuMapBridgeError = reason;
  setMapNotice("bridge_failed", reason);
  console.error("[LTU] Qt WebChannel bridge could not be initialized:", reason);
}

function connectQtBridge(attempt) {
  if (window.ltuBridgeReady) return;
  const hasConstructor = typeof QWebChannel === "function";
  const hasTransport = typeof qt !== "undefined" && !!qt.webChannelTransport;
  if (!hasConstructor || !hasTransport) {
    const reason = !hasConstructor
      ? `QWebChannel constructor unavailable${window.ltuQWebChannelLoadError ? `: ${window.ltuQWebChannelLoadError}` : ""}`
      : "qt.webChannelTransport unavailable";
    window.ltuMapStartupStage = "waiting-for-webchannel";
    window.ltuMapBridgeError = reason;
    if (attempt < 40) {
      window.setTimeout(() => connectQtBridge(attempt + 1), 125);
    } else {
      window.ltuMapStartupStage = "webchannel-unavailable";
      window.ltuMapBridgeError = reason;
      setMapNotice("bridge_failed", reason);
      console.error(`[LTU] ${reason} after ${(attempt + 1) * 125} ms`);
    }
    return;
  }

  window.ltuMapStartupStage = "connecting-webchannel";
  window.ltuMapBridgeError = null;
  let callbackFinished = false;
  const callbackTimeout = window.setTimeout(() => {
    if (callbackFinished || window.ltuBridgeReady) return;
    callbackFinished = true;
    window.ltuMapStartupStage = "webchannel-callback-timeout";
    window.ltuMapBridgeError = "QWebChannel did not return its bridge callback";
    retryOrReportQtBridge(attempt, "QWebChannel did not return its bridge callback");
  }, 1500);

  try {
    new QWebChannel(qt.webChannelTransport, (channel) => {
      if (callbackFinished) return;
      callbackFinished = true;
      window.clearTimeout(callbackTimeout);
      if (window.ltuBridgeReady) return;
      try {
        const candidate = channel && channel.objects && channel.objects.bridge;
        if (!candidate) throw new Error("QWebChannel object 'bridge' is missing");
        bridge = candidate;
        bridge.stateUpdated.connect((json) => {
          try { renderState(JSON.parse(json)); } catch (error) { console.error(error); }
        });
        bridge.settingsUpdated.connect((json) => {
          try { applySettings(JSON.parse(json)); } catch (error) { console.error(error); }
        });
        window.ltuBridgeReady = true;
        window.ltuMapStartupStage = "webchannel-ready";
        window.ltuMapBridgeError = null;
        if (window.ltuMapVisualReady) updateNoticeAfterMapReady();
        bridge.uiReady();
      } catch (error) {
        window.ltuBridgeReady = false;
        window.ltuMapStartupStage = "webchannel-bridge-error";
        window.ltuMapBridgeError = String(error && (error.stack || error.message) || error);
        console.error("[LTU] Qt WebChannel bridge initialization failed:", error);
        retryOrReportQtBridge(attempt, window.ltuMapBridgeError);
      }
    });
  } catch (error) {
    callbackFinished = true;
    window.clearTimeout(callbackTimeout);
    window.ltuBridgeReady = false;
    window.ltuMapStartupStage = "webchannel-connect-error";
    window.ltuMapBridgeError = String(error && (error.stack || error.message) || error);
    console.error("[LTU] Qt WebChannel connection failed:", error);
    retryOrReportQtBridge(attempt, window.ltuMapBridgeError);
  }
}

window.ltuBridgeReady = false;
window.ltuMapStartupStage = "map-script-ready";
connectQtBridge(0);
