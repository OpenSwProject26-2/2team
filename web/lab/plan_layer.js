// 파이프라인 export/<plan id>.json(운행안)을 MapLibre 지도에 그린다. 패널을 모른다.
//
//   import {showPlan} from './plan_layer.js';
//   const drawn = showPlan(map, exportJson, {fit: true});   // 다시 부르면 이전 층을 지운다
//   drawn.bounds; drawn.remove();
//
// - 노선별 색 선(shape), 빈 차 구간(shape_return, 있을 때)은 옅은 점선, 정류장 점, 역(kind=station)은 큰 고리,
//   진행 방향은 선 위 작은 꺾쇠, 소외 우선 삽입 정류장(fill_inserted, 있을 때)은 마름모.
// - 노선 색 = order(있으면) 또는 route_id 숫자 순번 → 같은 노선 번호는 어느 안에서든 같은 색.
// - 글자 층(symbol)을 쓰지 않는다: 글꼴·아이콘 파일 없이(인터넷 없이) 그려진다.

export const ROUTE_COLORS = ['#1d4ed8', '#d9480f', '#0f9d58', '#9c36b5', '#c2255c', '#0b7285', '#e67700', '#5c940d', '#364fc7', '#a61e4d', '#087f5b', '#862e9c'];
const drawn = new WeakMap();   // map → {prefix → handle}

export function routeIndex(route, i) {
  if (Number.isFinite(route?.order)) return route.order;
  const m = /(\d+)/.exec(String(route?.route_id ?? ''));
  return m ? Number(m[1]) - 1 : i;
}
export function routeColor(route, i) { const k = routeIndex(route, i); return ROUTE_COLORS[((k % ROUTE_COLORS.length) + ROUTE_COLORS.length) % ROUTE_COLORS.length]; }

const ll = (p) => [p[1], p[0]];   // export 좌표는 [lat, lon]
const R = 6371008.8, rad = Math.PI / 180;
function meters(a, b) {   // [lon, lat]
  const x = (b[0] - a[0]) * rad * Math.cos((a[1] + b[1]) / 2 * rad), y = (b[1] - a[1]) * rad;
  return Math.hypot(x, y) * R;
}
function offset(p, dx, dy) { return [p[0] + dx / (R * Math.cos(p[1] * rad)) / rad, p[1] + dy / R / rad]; }

/** 선을 따라 every m마다 진행 방향 꺾쇠(두 짧은 선분). */
export function chevrons(line, every = 350, size = 16) {
  const out = []; let acc = every / 2;
  for (let i = 1; i < line.length; i++) {
    const a = line[i - 1], b = line[i], d = meters(a, b);
    if (d <= 0) continue;
    let t = acc;
    while (t <= d) {
      const f = t / d, p = [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
      const ux = (b[0] - a[0]) * Math.cos(a[1] * rad), uy = b[1] - a[1], n = Math.hypot(ux, uy) || 1;
      const dx = ux / n, dy = uy / n;   // 진행 방향 단위 벡터(동·북, m 기준)
      const back = (s) => offset(p, -dx * size + s * -dy * size * 0.7, -dy * size + s * dx * size * 0.7);
      out.push([back(1), p, back(-1)]);
      t += every;
    }
    acc = t - d;
  }
  return out;
}

function diamond(p, r = 22) { return [[offset(p, 0, r), offset(p, r, 0), offset(p, 0, -r), offset(p, -r, 0), offset(p, 0, r)]]; }

/** exportJson → GeoJSON 묶음(테스트·다른 지도 라이브러리에서도 쓰게 분리). */
export function planFeatures(exportJson) {
  const lines = [], returns = [], arrows = [], stops = [], inserted = [];
  (exportJson?.routes || []).forEach((r, i) => {
    const color = routeColor(r, i), props = {route_id: r.route_id, name: r.name || r.route_id, color, vehicles: r.vehicles, headway_min: r.headway_min, cycle_min: r.cycle_min};
    const shape = (r.shape || []).map(ll);
    if (shape.length >= 2) {
      lines.push({type: 'Feature', geometry: {type: 'LineString', coordinates: shape}, properties: props});
      for (const c of chevrons(shape)) arrows.push({type: 'Feature', geometry: {type: 'LineString', coordinates: c}, properties: {color}});
    }
    const ret = (r.shape_return || []).map(ll);
    if (ret.length >= 2) returns.push({type: 'Feature', geometry: {type: 'LineString', coordinates: ret}, properties: props});
    for (const s of r.stops || []) {
      if (!Number.isFinite(s.lat) || !Number.isFinite(s.lon)) continue;
      const p = [s.lon, s.lat], sp = {...props, stop_id: s.stop_id, stop_name: s.name, seq: s.seq, kind: s.kind || 'stop'};
      if (s.fill_inserted) inserted.push({type: 'Feature', geometry: {type: 'Polygon', coordinates: diamond(p)}, properties: sp});
      else stops.push({type: 'Feature', geometry: {type: 'Point', coordinates: p}, properties: sp});
    }
  });
  const fc = (features) => ({type: 'FeatureCollection', features});
  return {lines: fc(lines), returns: fc(returns), arrows: fc(arrows), stops: fc(stops), inserted: fc(inserted)};
}

export function planBounds(exportJson) {
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
  for (const r of exportJson?.routes || []) for (const [lat, lon] of [...(r.shape || []), ...(r.stops || []).map((x) => [x.lat, x.lon])]) {
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) continue;
    w = Math.min(w, lon); e = Math.max(e, lon); s = Math.min(s, lat); n = Math.max(n, lat);
  }
  return Number.isFinite(w) ? [[w, s], [e, n]] : null;
}

/**
 * @param map MapLibre 지도
 * @param exportJson export/<plan id>.json
 * @param options {prefix='plan', before: 아래에 둘 층 id(없으면 맨 위), fit: true면 노선에 맞춰 화면 이동, padding, width=2.4: 노선 선 두께}
 * @returns {layers, sources, bounds, remove()}
 */
export function showPlan(map, exportJson, options = {}) {
  const prefix = options.prefix || 'plan';
  const reg = drawn.get(map) || {}; drawn.set(map, reg);
  reg[prefix]?.remove();
  const f = planFeatures(exportJson), bounds = planBounds(exportJson);
  const ids = {lines: `${prefix}-lines`, casing: `${prefix}-casing`, returns: `${prefix}-returns`, arrows: `${prefix}-arrows`, stops: `${prefix}-stops`, stations: `${prefix}-stations`, inserted: `${prefix}-inserted`};
  const src = (k) => `${prefix}-${k}`;
  const handle = {layers: [], sources: [], bounds, removed: false,
    remove() {
      if (handle.removed) return; handle.removed = true;
      if (!map.getStyle?.()) return;
      for (const id of handle.layers) if (map.getLayer(id)) map.removeLayer(id);
      for (const id of handle.sources) if (map.getSource(id)) map.removeSource(id);
      if (reg[prefix] === handle) delete reg[prefix];
    }};
  reg[prefix] = handle;
  const draw = () => {
    if (handle.removed) return;
    const before = options.before && map.getLayer(options.before) ? options.before : undefined;
    const add = (layer) => { map.addLayer(layer, before); handle.layers.push(layer.id); };
    for (const k of ['lines', 'returns', 'arrows', 'stops', 'inserted']) { map.addSource(src(k), {type: 'geojson', data: f[k]}); handle.sources.push(src(k)); }
    const round = {'line-cap': 'round', 'line-join': 'round'};
    add({id: ids.returns, type: 'line', source: src('returns'), layout: {'line-join': 'round'}, paint: {'line-color': ['get', 'color'], 'line-width': 1.6, 'line-opacity': 0.45, 'line-dasharray': [1.5, 1.5]}});
    const w = options.width ?? 2.4;
    add({id: ids.casing, type: 'line', source: src('lines'), layout: round, paint: {'line-color': '#ffffff', 'line-width': w + 1.8, 'line-opacity': 0.85}});
    add({id: ids.lines, type: 'line', source: src('lines'), layout: round, paint: {'line-color': ['get', 'color'], 'line-width': w, 'line-opacity': 0.95}});
    add({id: ids.arrows, type: 'line', source: src('arrows'), minzoom: 13, layout: round, paint: {'line-color': ['get', 'color'], 'line-width': 1.6}});
    add({id: ids.stops, type: 'circle', source: src('stops'), filter: ['!=', ['get', 'kind'], 'station'], paint: {'circle-radius': ['interpolate', ['linear'], ['zoom'], 11, 2.2, 15, 4.2], 'circle-color': '#ffffff', 'circle-stroke-color': ['get', 'color'], 'circle-stroke-width': 1.6}});
    add({id: ids.stations, type: 'circle', source: src('stops'), filter: ['==', ['get', 'kind'], 'station'], paint: {'circle-radius': ['interpolate', ['linear'], ['zoom'], 11, 4, 15, 7], 'circle-color': '#ffffff', 'circle-stroke-color': '#111827', 'circle-stroke-width': 2.4}});
    add({id: ids.inserted, type: 'fill', source: src('inserted'), paint: {'fill-color': ['get', 'color'], 'fill-opacity': 0.9, 'fill-outline-color': '#ffffff'}});
    if (options.fit && bounds) map.fitBounds(bounds, {padding: options.padding ?? 50, duration: 0});
  };
  // addLayer는 스타일 JSON만 읽혔으면 된다(타일이 안 받아져도 그린다 → 인터넷 없이도 선·점)
  const styleReady = () => (map.style ? map.style._loaded : map.isStyleLoaded?.());
  const tryDraw = () => { if (handle.removed) return; if (styleReady()) draw(); else map.once('styledata', tryDraw); };
  tryDraw();
  handle.ids = ids;
  return handle;
}
