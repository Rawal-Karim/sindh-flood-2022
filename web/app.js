import * as THREE from 'three';
import { OrbitControls } from './vendor/OrbitControls.js';

const $ = id => document.getElementById(id);

// Every asset URL carries the build stamp. The pipeline rewrites web/assets/ wholesale
// whenever the AOI or the composite changes, and browsers happily serve the previous
// scene.json against the same path — which shows up as correct-looking but stale
// figures. Bump BUILD (and ?v= on the script tag in index.html) on every rebuild.
const BUILD = '8';
const A = path => `./assets/${path}?b=${BUILD}`;

// ── load ────────────────────────────────────────────────────────────────────
const S = await (await fetch(A('scene.json'))).json();
const heights = new Float32Array(await (await fetch(A('terrain.bin'))).arrayBuffer());

const MW = S.mesh.width, MH = S.mesh.height;
const { west, south, east, north } = S.bounds;

// World units are kilometres, so the vertical exaggeration slider reads as a true
// multiplier against real horizontal distance.
const LAT_MID = (south + north) / 2;
const WIDTH_KM = (east - west) * 111.32 * Math.cos(LAT_MID * Math.PI / 180);
const DEPTH_KM = (north - south) * 110.54;

// X = east, Z = south (north is -Z), Y = up.
const lonToX = lon => (lon - west) / (east - west) * WIDTH_KM - WIDTH_KM / 2;
const latToZ = lat => (north - lat) / (north - south) * DEPTH_KM - DEPTH_KM / 2;

function heightAt(col, row) {          // bilinear, in metres
  const c = Math.min(Math.max(col, 0), MW - 1.001);
  const r = Math.min(Math.max(row, 0), MH - 1.001);
  const c0 = Math.floor(c), r0 = Math.floor(r), fc = c - c0, fr = r - r0;
  const i = (rr, cc) => heights[rr * MW + cc];
  return i(r0, c0) * (1 - fc) * (1 - fr) + i(r0, c0 + 1) * fc * (1 - fr) +
         i(r0 + 1, c0) * (1 - fc) * fr + i(r0 + 1, c0 + 1) * fc * fr;
}
const heightAtLonLat = (lon, lat) => heightAt(
  (lon - west) / (east - west) * (MW - 1),
  (north - lat) / (north - south) * (MH - 1));

// ── scene ───────────────────────────────────────────────────────────────────
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.setSize(innerWidth, innerHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;
$('view').appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0xcfe4f3);

const camera = new THREE.PerspectiveCamera(38, innerWidth / innerHeight, 1, 8000);
// Oblique from the SSW, matching the reference figure: mountains on the left,
// the plain receding north. DEPTH_KM / (2 tan(fov/2)) frames the full block.
camera.position.set(-300, 470, 780);

const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, 30, 0);
controls.enableDamping = true;
controls.dampingFactor = 0.08;
controls.maxPolarAngle = Math.PI * 0.495;
controls.minDistance = 60;
controls.maxDistance = 2200;

scene.add(new THREE.AmbientLight(0xffffff, 1.55));
const sun = new THREE.DirectionalLight(0xffffff, 0.85);
sun.position.set(-1, 1.4, -1);
scene.add(sun);

// ── terrain ─────────────────────────────────────────────────────────────────
let EXAG = 14;
const yOf = m => m / 1000 * EXAG;

const geo = new THREE.PlaneGeometry(WIDTH_KM, DEPTH_KM, MW - 1, MH - 1);
geo.rotateX(-Math.PI / 2);           // plane row 0 -> north edge (-Z)
const pos = geo.attributes.position;
function applyExag() {
  for (let r = 0, k = 0; r < MH; r++)
    for (let c = 0; c < MW; c++, k++)
      pos.setY(k, yOf(heights[k]));
  pos.needsUpdate = true;
  geo.computeVertexNormals();
  rebuildSkirt();
  redrapeOverlays();
  water.position.y = 0.35 * (EXAG / 14);
  envMesh.position.y = 0.30 * (EXAG / 14);
}

const terrainTex = new THREE.TextureLoader().load(A('terrain.jpg'));
terrainTex.colorSpace = THREE.SRGBColorSpace;
terrainTex.anisotropy = renderer.capabilities.getMaxAnisotropy();
const terrain = new THREE.Mesh(geo, new THREE.MeshLambertMaterial({ map: terrainTex }));
scene.add(terrain);

// Side walls + base, so the block reads as a solid slab like the reference figure.
const skirtMat = new THREE.MeshLambertMaterial({ color: 0xa08a72, side: THREE.DoubleSide });
let skirt = null;
function rebuildSkirt() {
  if (skirt) { scene.remove(skirt); skirt.geometry.dispose(); }
  const BASE = -12 * (EXAG / 14);
  const verts = [];
  const push = (x1, y1, z1, x2, y2, z2) => {
    verts.push(x1, y1, z1, x1, BASE, z1, x2, y2, z2,
               x2, y2, z2, x1, BASE, z1, x2, BASE, z2);
  };
  const X = c => c / (MW - 1) * WIDTH_KM - WIDTH_KM / 2;
  const Z = r => r / (MH - 1) * DEPTH_KM - DEPTH_KM / 2;
  for (let c = 0; c < MW - 1; c++) {
    push(X(c), yOf(heights[c]), Z(0), X(c + 1), yOf(heights[c + 1]), Z(0));
    const b = (MH - 1) * MW;
    push(X(c + 1), yOf(heights[b + c + 1]), Z(MH - 1), X(c), yOf(heights[b + c]), Z(MH - 1));
  }
  for (let r = 0; r < MH - 1; r++) {
    push(X(0), yOf(heights[(r + 1) * MW]), Z(r + 1), X(0), yOf(heights[r * MW]), Z(r));
    push(X(MW - 1), yOf(heights[r * MW + MW - 1]), Z(r),
         X(MW - 1), yOf(heights[(r + 1) * MW + MW - 1]), Z(r + 1));
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(verts, 3));
  g.computeVertexNormals();
  skirt = new THREE.Mesh(g, skirtMat);
  scene.add(skirt);
}

// ── water ───────────────────────────────────────────────────────────────────
const texLoader = new THREE.TextureLoader();
function dataTex(url) {
  const t = texLoader.load(url);
  t.colorSpace = THREE.NoColorSpace;   // R/G carry data, not colour
  t.generateMipmaps = false;           // mipmaps would blur the observed mask
  t.minFilter = THREE.LinearFilter;
  t.magFilter = THREE.LinearFilter;
  t.wrapS = t.wrapT = THREE.ClampToEdgeWrapping;
  return t;
}

// Water surface. Two things beyond a flat blue wash:
//   age   — ch2 of the frame carries days-since-observation. A carried-forward
//           reading is drawn progressively paler and greyer so stale ground reads as
//           "last seen a while ago" rather than as a confident measurement.
//   flow  — a tileable noise texture advected along the DEM's downhill direction,
//           sampled twice at different scales and speeds so the surface drifts and
//           shimmers instead of sitting still. Amplitude scales with coverage, so
//           deep water moves and shallow fringes stay calm.
const WATER_FRAG = `
uniform sampler2D mapA, mapB, flowMap, noiseMap;
uniform float mixT, showUnobs, opacity, uTime, flowOn, ageFade;
varying vec2 vUv;

vec3 shallowCol(){ return vec3(0.34,0.63,0.88); }
vec3 deepCol(){ return vec3(0.02,0.26,0.60); }

void main(){
  vec4 a = texture2D(mapA, vUv);
  vec4 b = texture2D(mapB, vUv);
  float known = mix(a.g, b.g, mixT);
  if (known < 0.5) {
    if (showUnobs < 0.5) discard;
    gl_FragColor = vec4(0.55, 0.60, 0.65, 0.52);
    return;
  }
  float cov = mix(a.r, b.r, mixT);
  if (cov < 0.02) discard;

  float age = mix(a.b, b.b, mixT);            // 0 = just observed, 1 = fully stale
  vec3 col = mix(shallowCol(), deepCol(), clamp(cov*1.4, 0.0, 1.0));
  float alpha = clamp(cov*1.9, 0.0, 0.93);

  if (flowOn > 0.5) {
    vec2 flow = texture2D(flowMap, vUv).rg * 2.0 - 1.0;
    float t = uTime * 0.012;
    // Tiling is deliberately coarse and mipmaps are off on noiseMap: at vUv*42
    // with mipmaps the tile minifies into the mip chain and half the motion is
    // averaged away (measured 13.7 vs 27.8 mean RGB change over 40 s of flow).
    float n1 = texture2D(noiseMap, vUv*13.0 + flow*t*0.9).r;
    float n2 = texture2D(noiseMap, vUv*22.0 - flow*t*1.5 + 0.37).r;
    float ripple = (n1*0.62 + n2*0.38) - 0.5;
    float amp = smoothstep(0.05, 0.55, cov);  // calm at the fringes, alive in the deep
    col += ripple * 0.30 * amp;
    // a narrow bright band travelling with the flow reads as glancing sunlight
    float glint = smoothstep(0.66, 0.94, n1*0.5 + n2*0.5);
    col += glint * 0.26 * amp;
    alpha += ripple * 0.10 * amp;
  }

  // Stale readings desaturate toward the unobserved grey and lose opacity.
  float f = age * ageFade;
  col = mix(col, vec3(0.55,0.60,0.65), f*0.75);
  alpha *= mix(1.0, 0.30, f);

  gl_FragColor = vec4(col, clamp(alpha, 0.0, 0.96) * opacity);
}`;
const WATER_VERT = `
varying vec2 vUv;
void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`;

const flowMap = dataTex(A('flow.png'));
const noiseMap = texLoader.load(A('noise.png'));
noiseMap.colorSpace = THREE.NoColorSpace;
noiseMap.wrapS = noiseMap.wrapT = THREE.RepeatWrapping;
// The noise is already smooth, so skip the mip chain — mipmaps were averaging the
// ripple away to a flat tone at these tiling factors.
noiseMap.generateMipmaps = false;
noiseMap.minFilter = THREE.LinearFilter;

const waterMat = new THREE.ShaderMaterial({
  uniforms: { mapA: { value: null }, mapB: { value: null },
              flowMap: { value: flowMap }, noiseMap: { value: noiseMap },
              mixT: { value: 0 }, showUnobs: { value: 1 }, opacity: { value: 1 },
              uTime: { value: 0 }, flowOn: { value: 1 }, ageFade: { value: 1 } },
  vertexShader: WATER_VERT, fragmentShader: WATER_FRAG,
  transparent: true, depthWrite: false, side: THREE.FrontSide,
});
const water = new THREE.Mesh(geo, waterMat);
water.renderOrder = 2;
scene.add(water);

// Same shader, purple, with flow and age fade off — the envelope is a maximum over a
// date range, so it has neither an instantaneous surface nor a meaningful age.
const envMat = new THREE.ShaderMaterial({
  uniforms: { mapA: { value: dataTex(A('frames/envelope.png')) },
              mapB: { value: null },
              flowMap: { value: flowMap }, noiseMap: { value: noiseMap },
              mixT: { value: 0 }, showUnobs: { value: 0 }, opacity: { value: 1 },
              uTime: { value: 0 }, flowOn: { value: 0 }, ageFade: { value: 0 } },
  vertexShader: WATER_VERT,
  fragmentShader: WATER_FRAG
    .replace('return vec3(0.34,0.63,0.88);', 'return vec3(0.62,0.45,0.86);')
    .replace('return vec3(0.02,0.26,0.60);', 'return vec3(0.30,0.12,0.58);'),
  transparent: true, depthWrite: false,
});
envMat.uniforms.mapB.value = envMat.uniforms.mapA.value;
const envMesh = new THREE.Mesh(geo, envMat);
envMesh.renderOrder = 1;
envMesh.visible = false;
scene.add(envMesh);

// ── hill torrents ───────────────────────────────────────────────────────────
// Channels traced from the DEM (priority-flood + D8 + kinematic-wave arrival times),
// restricted to terrain with real relief. Revealed by arrival time so the surge runs
// downhill out of the Kirthar and Sulaiman ravines onto the piedmont.
// The channel geometry is measured topography; the timing is a model.
const TORRENT_FRAG = `
uniform sampler2D arrMap, noiseMap;
uniform float uT, uOpacity, uTime;
varying vec2 vUv;
void main(){
  vec3 a = texture2D(arrMap, vUv).rgb;
  float strength = a.g;
  if (strength < 0.03 || uOpacity < 0.01) discard;
  float d = uT - a.r;                       // how long since the front passed here
  if (d < 0.0) discard;
  float head = smoothstep(0.0, 0.05, d);    // 0 right at the front, 1 well behind
  vec3 col = mix(vec3(0.97,0.99,1.0), vec3(0.09,0.40,0.76), head);
  float n = texture2D(noiseMap, vUv*70.0 + vec2(0.0, -uTime*0.22)).r;
  col += (n - 0.5) * 0.22 * head;
  float alpha = strength * mix(1.0, 0.88, head) * smoothstep(0.0, 0.015, d) * uOpacity;
  gl_FragColor = vec4(col, clamp(alpha, 0.0, 0.95));
}`;
const torrentMat = new THREE.ShaderMaterial({
  uniforms: { arrMap: { value: dataTex(A('arrival.png')) },
              noiseMap: { value: noiseMap },
              uT: { value: 0 }, uOpacity: { value: 0 }, uTime: { value: 0 } },
  vertexShader: WATER_VERT, fragmentShader: TORRENT_FRAG,
  transparent: true, depthWrite: false,
});
const torrent = new THREE.Mesh(geo, torrentMat);
torrent.renderOrder = 3;
scene.add(torrent);

// ── weather ─────────────────────────────────────────────────────────────────
// Rain is drawn as a screen-space veil rather than particles: at this scale the camera
// is 800 km from the far edge, and any particle count that reads as rain up close
// vanishes to nothing across the whole block.
const RAIN_FRAG = `
uniform float uTime, uIntensity, uAspect;
varying vec2 vUv;
float hash(vec2 p){ return fract(sin(dot(p, vec2(41.3, 289.1))) * 43758.5453); }
void main(){
  if (uIntensity < 0.004) discard;
  vec2 uv = vec2(vUv.x * uAspect, vUv.y);
  float a = 0.0;
  for (int L = 0; L < 3; L++){
    float fl = float(L);
    float sc = 55.0 + fl * 52.0;             // three depth layers
    float sp = 1.5 + fl * 1.0;
    vec2 p = vec2(uv.x * sc + uv.y * 7.0, uv.y * sc * 0.30 - uTime * sp);
    vec2 id = floor(p), f = fract(p);
    // density range tuned against the CHIRPS scale: a 5 mm/day area-mean is a light
    // shower, the 20 mm peak days (24 Jul, 18 Aug 2022) are a downpour
    if (hash(id) > 0.978 - uIntensity * 0.105){
      float streak = smoothstep(0.5, 0.0, abs(f.x - 0.5) * 2.0)
                   * smoothstep(1.0, 0.2, f.y);
      a += streak * (0.55 - fl * 0.14);
    }
  }
  gl_FragColor = vec4(vec3(0.82, 0.88, 0.96), a * clamp(uIntensity, 0.0, 1.0));
}`;
const rainMat = new THREE.ShaderMaterial({
  uniforms: { uTime: { value: 0 }, uIntensity: { value: 0 },
              uAspect: { value: innerWidth / innerHeight } },
  vertexShader: WATER_VERT, fragmentShader: RAIN_FRAG,
  transparent: true, depthTest: false, depthWrite: false,
});
// Parented to the camera so it always fills the frustum.
// Held at VEIL_D, comfortably inside the frustum. At z = -1 it sat exactly on the
// near plane (camera.near = 1) and was clipped away entirely.
const VEIL_D = 2.5;
const rainVeil = new THREE.Mesh(new THREE.PlaneGeometry(2, 2), rainMat);
rainVeil.position.z = -VEIL_D;
rainVeil.frustumCulled = false;
rainVeil.renderOrder = 999;
camera.add(rainVeil);
scene.add(camera);

function fitVeil() {
  const h = 2 * Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2) * VEIL_D;
  rainVeil.scale.set(h * camera.aspect / 2, h / 2, 1);
  rainMat.uniforms.uAspect.value = camera.aspect;
}
fitVeil();

// Wetness + drifting cloud shadow are folded into the terrain material. A cloud plane
// above the terrain would slab across the oblique view; shadows on the ground read as
// weather without occluding anything.
const wetMap = dataTex(A('wetness.png'));
const terrainMat = new THREE.MeshLambertMaterial({ map: terrainTex });
const wx = { uWet: { value: 0 }, uCloud: { value: 0 }, uTime: { value: 0 },
             wetMap: { value: wetMap }, noiseMap: { value: noiseMap } };
terrainMat.onBeforeCompile = sh => {
  Object.assign(sh.uniforms, wx);
  sh.fragmentShader =
    'uniform float uWet, uCloud, uTime;\nuniform sampler2D wetMap, noiseMap;\n' +
    sh.fragmentShader.replace('#include <map_fragment>', `
      #include <map_fragment>
      float wv = clamp(texture2D(wetMap, vMapUv).r * uWet, 0.0, 1.0);
      diffuseColor.rgb *= mix(1.0, 0.62, wv);
      diffuseColor.rgb = mix(diffuseColor.rgb,
                             diffuseColor.rgb * vec3(0.86, 0.92, 1.02), wv);
      float cl = texture2D(noiseMap, vMapUv * 3.2 + vec2(uTime * 0.004, uTime * 0.002)).r;
      diffuseColor.rgb *= mix(1.0, 0.55 + 0.45 * cl, uCloud);
    `);
};
terrain.material = terrainMat;

const SKY_CLEAR = new THREE.Color(0xcfe4f3);
const SKY_STORM = new THREE.Color(0x6b7683);

// ── overlays ────────────────────────────────────────────────────────────────
const overlayGroup = new THREE.Group();
scene.add(overlayGroup);
const overlaySpecs = [];   // {obj, coords:[[lon,lat]...][], lift}

function addLines(geojson, color, width, lift, name) {
  const paths = [];
  for (const f of geojson.features) {
    const g = f.geometry; if (!g) continue;
    if (g.type === 'LineString') paths.push(g.coordinates);
    else if (g.type === 'MultiLineString') paths.push(...g.coordinates);
    else if (g.type === 'Polygon') paths.push(...g.coordinates);
    else if (g.type === 'MultiPolygon') for (const p of g.coordinates) paths.push(...p);
  }
  const obj = new THREE.LineSegments(
    new THREE.BufferGeometry(),
    new THREE.LineBasicMaterial({ color, linewidth: width, transparent: true, opacity: .95 }));
  obj.name = name;
  obj.renderOrder = 3;
  overlayGroup.add(obj);
  overlaySpecs.push({ obj, paths, lift });
  return obj;
}

function redrapeOverlays() {
  for (const { obj, paths, lift } of overlaySpecs) {
    const v = [];
    for (const path of paths) {
      for (let i = 0; i < path.length - 1; i++) {
        for (const p of [path[i], path[i + 1]]) {
          const [lon, lat] = p;
          v.push(lonToX(lon), yOf(heightAtLonLat(lon, lat)) + lift * (EXAG / 14), latToZ(lat));
        }
      }
    }
    obj.geometry.dispose();
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(v, 3));
    obj.geometry = g;
  }
  for (const m of breachMarkers) {
    m.position.y = yOf(heightAtLonLat(m.userData.lon, m.userData.lat)) + 1.6 * (EXAG / 14);
  }
}

// ── settlement labels ───────────────────────────────────────────────────────
// Projected to screen each frame as HTML rather than drawn as 3-D sprites: text stays
// crisp at any zoom, and it lets larger towns win when labels collide. Positions come
// from the GeoNames gazetteer, not from tracing the reference figure.
let places = [], labelsOn = true;
const labelLayer = document.createElement('div');
labelLayer.id = 'labels';
document.body.appendChild(labelLayer);

async function loadPlaces() {
  const list = await (await fetch(A('places.json'))).json();
  places = list.map(p => {
    const el = document.createElement('div');
    el.className = 'lbl' + (p.pop >= 150000 ? ' big' : p.pop >= 40000 ? ' mid' : '');
    el.innerHTML = `<i></i><span>${p.name}</span>`;
    labelLayer.appendChild(el);
    // Measure once while briefly shown. offsetWidth is 0 on a display:none element,
    // and falling back to a fixed guess made decluttering reserve far too much space
    // — which then hid the label, keeping offsetWidth at 0 for good.
    el.style.display = 'block';
    const w = el.offsetWidth;
    el.style.display = 'none';
    return { ...p, el, w, v: new THREE.Vector3() };
  });
  // Curated flood towns outrank everything else, then population. Otherwise distant
  // Punjab cities win collisions against the Kachho names the map is actually about.
  places.sort((a, b) => (b.curated - a.curated) || (b.pop - a.pop));
}

const _camDir = new THREE.Vector3();
function updateLabels() {
  if (!places.length) return;
  const W = innerWidth, H = innerHeight;
  const boxes = [];
  camera.getWorldDirection(_camDir);
  for (const p of places) {
    const el = p.el;
    if (!labelsOn) { el.style.display = 'none'; continue; }
    p.v.set(lonToX(p.lon), yOf(heightAtLonLat(p.lon, p.lat)) + 1.2 * (EXAG / 14),
            latToZ(p.lat));
    const dist = camera.position.distanceTo(p.v);
    p.v.project(camera);
    const x = (p.v.x * 0.5 + 0.5) * W, y = (-p.v.y * 0.5 + 0.5) * H;
    // behind the camera, off screen, or too far to be legible
    if (p.v.z > 1 || x < -60 || x > W + 60 || y < 40 || y > H - 90) {
      el.style.display = 'none'; continue;
    }
    // small places drop out as the camera pulls back
    const minPop = dist > 900 ? 120000 : dist > 600 ? 45000 : dist > 320 ? 12000 : 0;
    if (p.pop < minPop && !p.curated) { el.style.display = 'none'; continue; }

    const w = p.w, h = 13;
    let clash = false;
    for (const b of boxes) {
      if (x < b.x + b.w + 4 && x + w + 4 > b.x && y < b.y + b.h + 2 && y + h + 2 > b.y) {
        clash = true; break;
      }
    }
    if (clash) { el.style.display = 'none'; continue; }
    boxes.push({ x, y, w, h });
    el.style.display = 'block';
    el.style.transform = `translate(${x}px, ${y}px)`;
    el.style.opacity = String(Math.max(0.35, Math.min(1, 1400 / dist)));
  }
}

const breachMarkers = [];
async function loadOverlays() {
  const O = S.overlays;
  if (O.bunds)
    addLines(await (await fetch(A(O.bunds))).json(), 0xc8452a, 2, 0.55, 'bunds');
  if (O.permanent_water)
    addLines(await (await fetch(A(O.permanent_water))).json(), 0x0b3c78, 2, 0.30, 'perm');
  // Boundaries and areal features. addLines already walks Polygon/MultiPolygon rings,
  // so these need no new machinery — drawn as draped outlines rather than filled, which
  // keeps the flood layer readable underneath them.
  if (O.provinces)
    addLines(await (await fetch(A(O.provinces))).json(), 0x1b2f45, 3, 1.25, 'provinces');
  if (O.districts)
    addLines(await (await fetch(A(O.districts))).json(), 0x6b7c8c, 1, 0.95, 'districts');
  if (O.lakes)
    addLines(await (await fetch(A(O.lakes))).json(), 0x00b8c4, 3, 0.85, 'lakes');
  if (O.cities)
    addLines(await (await fetch(A(O.cities))).json(), 0x8e2b1a, 2, 0.75, 'cities');
  if (O.breach_candidates_2022) {
    const gj = await (await fetch(A(O.breach_candidates_2022))).json();
    // Magenta, not red: settlement dots are red, and these are a different kind of
    // thing entirely — 2022 breaching points.
    const mat = new THREE.MeshBasicMaterial({ color: 0xd400a0 });
    const sph = new THREE.SphereGeometry(2.0, 10, 8);
    const grp = new THREE.Group(); grp.name = 'breach';
    for (const f of gj.features) {
      const [lon, lat] = f.geometry.coordinates;
      const m = new THREE.Mesh(sph, mat);
      m.userData = { lon, lat };
      m.position.set(lonToX(lon), 0, latToZ(lat));
      grp.add(m); breachMarkers.push(m);
    }
    grp.visible = false;
    overlayGroup.add(grp);
  }
}

// ── timeline ────────────────────────────────────────────────────────────────
// One continuous calendar from 1 Jun 2022 to the last satellite frame. The stretch
// before the first observation is reconstructed (CHIRPS rainfall + DEM routing); from
// the first frame onward it is measured. The scrubber gives the reconstructed stretch
// a fixed share of its length so the monsoon build-up is not squeezed to a sliver.
const dayOf = d => new Date(d + 'T00:00:00Z').getTime() / 86400000;

const STORY_START = dayOf('2022-06-01');
const TORRENT_START = dayOf('2022-07-05');   // first Nai response in the reconstruction
const PRE_SHARE = 0.30;                      // scrubber fraction given to the prologue
const TORRENT_FADE_DAYS = 18;                // handover to the observed flood

let track = 'viirs';
let frames = [], texes = [], obsStart = 0, obsEnd = 1;
let playing = true, speed = 1, tNorm = 0, torrentsOn = true, autoCam = true;

const rainDaily = (S.rain && S.rain.daily) || [];
const rainByDay = new Map(rainDaily.map(r => [dayOf(r.date), r]));
const rainDays = [...rainByDay.keys()].sort((a, b) => a - b);
const peakRain = (S.rain && S.rain.peak_daily_mean_mm) || 1;

function rainAt(day) {
  if (!rainDays.length) return { mm: 0, cum: 0 };
  if (day <= rainDays[0]) return { mm: rainByDay.get(rainDays[0]).mean_mm, cum: 0 };
  if (day >= rainDays[rainDays.length - 1]) {
    const r = rainByDay.get(rainDays[rainDays.length - 1]);
    return { mm: 0, cum: r.cum_frac };
  }
  let i = 0;
  while (i < rainDays.length - 2 && rainDays[i + 1] <= day) i++;
  const a = rainByDay.get(rainDays[i]), b = rainByDay.get(rainDays[i + 1]);
  const f = (day - rainDays[i]) / (rainDays[i + 1] - rainDays[i]);
  return { mm: a.mean_mm * (1 - f) + b.mean_mm * f,
           cum: a.cum_frac * (1 - f) + b.cum_frac * f };
}

const scrubToDay = u => u < PRE_SHARE
  ? STORY_START + (u / PRE_SHARE) * (obsStart - STORY_START)
  : obsStart + ((u - PRE_SHARE) / (1 - PRE_SHARE)) * (obsEnd - obsStart);

function setTrack(name) {
  track = name;
  frames = S.tracks[name];
  texes = frames.map(f => dataTex(A(f.file)));
  obsStart = dayOf(frames[0].date);
  obsEnd = dayOf(frames[frames.length - 1].date);
  buildTicks();
  tNorm = 0;
  update();
}

function buildTicks() {
  const el = $('ticks'); el.innerHTML = '';
  const marks = [];
  for (let d = new Date(Date.UTC(2022, 5, 1)); dayOf(d.toISOString().slice(0, 10)) <= obsEnd;
       d.setUTCMonth(d.getUTCMonth() + 1)) {
    marks.push(d.toISOString().slice(0, 10));
  }
  for (const iso of marks) {
    const day = dayOf(iso);
    if (day < STORY_START) continue;
    const u = day <= obsStart
      ? (day - STORY_START) / (obsStart - STORY_START) * PRE_SHARE
      : PRE_SHARE + (day - obsStart) / (obsEnd - obsStart) * (1 - PRE_SHARE);
    const s = document.createElement('span');
    s.textContent = new Date(iso + 'T00:00:00Z')
      .toLocaleString('en', { month: 'short', timeZone: 'UTC' });
    s.style.left = (u * 100) + '%';
    el.appendChild(s);
  }
  // shade the reconstructed stretch of the track
  $('pre-band').style.width = (PRE_SHARE * 100) + '%';
}

const fmtDay = day => new Date(day * 86400000)
  .toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' });

function update() {
  const day = scrubToDay(tNorm);
  const observed = day >= obsStart;
  const r = rainAt(day);
  const intensity = Math.min(r.mm / Math.max(peakRain, 1e-6), 1);

  // weather
  rainMat.uniforms.uIntensity.value = intensity * 1.15;
  wx.uWet.value = r.cum;
  wx.uCloud.value = intensity * 0.75;
  scene.background.copy(SKY_CLEAR).lerp(SKY_STORM, Math.min(intensity * 1.25, 0.85));
  sun.intensity = 0.85 - 0.45 * Math.min(intensity * 1.3, 1);

  // torrents
  const tT = Math.min(Math.max((day - TORRENT_START) / (obsStart - TORRENT_START), 0), 1.4);
  torrentMat.uniforms.uT.value = tT;
  const fadeOut = observed
    ? Math.max(0, 1 - (day - obsStart) / TORRENT_FADE_DAYS) : 1;
  torrentMat.uniforms.uOpacity.value =
    torrentsOn ? Math.min(tT * 3.0, 1) * fadeOut : 0;

  // observed water
  let near = frames[0];
  if (observed) {
    let i = 0;
    while (i < frames.length - 2 && dayOf(frames[i + 1].date) <= day) i++;
    const dA = dayOf(frames[i].date), dB = dayOf(frames[i + 1]?.date ?? frames[i].date);
    const m = dB > dA ? Math.min(Math.max((day - dA) / (dB - dA), 0), 1) : 0;
    waterMat.uniforms.mapA.value = texes[i];
    waterMat.uniforms.mapB.value = texes[Math.min(i + 1, texes.length - 1)];
    waterMat.uniforms.mixT.value = m;
    waterMat.uniforms.opacity.value = 1;
    near = m < 0.5 ? frames[i] : (frames[i + 1] ?? frames[i]);
  } else {
    // Nothing was measured yet; fade the first frame in over the last few days so the
    // handover is not a cut, but never assert extent before it was observed.
    const lead = Math.max(0, 1 - (obsStart - day) / 6);
    waterMat.uniforms.mapA.value = texes[0];
    waterMat.uniforms.mapB.value = texes[0];
    waterMat.uniforms.mixT.value = 0;
    waterMat.uniforms.opacity.value = lead;
  }

  $('date').textContent = fmtDay(day);

  if (observed) {
    $('area').textContent = near.flood_km2.toLocaleString() + ' km² flooded';
    $('srcline').textContent =
      `${near.sensor} · ${near.res_m} m · ${near.region.replace(/_/g, ' ')} · ` +
      `${near.known_pct}% of frame observed`;
  } else {
    $('area').textContent = r.mm >= 0.005
      ? `${r.mm.toFixed(2)} mm/day area-mean rainfall` : 'monsoon building';
    $('srcline').textContent = tT > 0
      ? `CHIRPS rainfall · DEM-routed hill torrents · ${Math.round(tT * 100)}% of network active`
      : 'CHIRPS daily rainfall · pre-flood';
  }

  const w = $('warn');
  const notes = [];
  if (observed && near.known_pct < 60)
    notes.push(`Only ${near.known_pct}% of the frame has ever been observed on this ` +
               `track — grey areas are not dry, they are unmeasured.`);
  if (observed && near.stale_pct >= 5)
    notes.push(`${near.stale_pct}% of observed ground was last seen more than ` +
               `${S.stale_days} days earlier and is drawn faded.`);
  w.style.display = notes.length ? 'block' : 'none';
  w.textContent = notes.join(' ');

  $('scrub').value = tNorm;
}

// ── controls ────────────────────────────────────────────────────────────────
$('scrub').min = 0; $('scrub').max = 1; $('scrub').step = 0.001;
$('scrub').addEventListener('input', e => { tNorm = +e.target.value; playing = false; $('play').textContent = '▶'; update(); });
$('play').addEventListener('click', () => {
  playing = !playing;
  if (playing && tNorm >= 0.999) tNorm = 0;
  $('play').textContent = playing ? '❚❚' : '▶';
});
$('play').textContent = '❚❚';
$('speed').addEventListener('input', e => speed = +e.target.value);
$('exag').addEventListener('input', e => { EXAG = +e.target.value; applyExag(); });
$('l-water').addEventListener('change', e => water.visible = e.target.checked);
$('l-env').addEventListener('change', e => envMesh.visible = e.target.checked);
$('l-unobs').addEventListener('change', e => waterMat.uniforms.showUnobs.value = e.target.checked ? 1 : 0);
$('l-flow').addEventListener('change', e => waterMat.uniforms.flowOn.value = e.target.checked ? 1 : 0);
$('l-age').addEventListener('change', e => waterMat.uniforms.ageFade.value = e.target.checked ? 1 : 0);
$('l-perm').addEventListener('change', e => { const o = overlayGroup.getObjectByName('perm'); if (o) o.visible = e.target.checked; });
$('l-bund').addEventListener('change', e => { const o = overlayGroup.getObjectByName('bunds'); if (o) o.visible = e.target.checked; });
$('l-prov').addEventListener('change', e => { const o = overlayGroup.getObjectByName('provinces'); if (o) o.visible = e.target.checked; });
$('l-dist').addEventListener('change', e => { const o = overlayGroup.getObjectByName('districts'); if (o) o.visible = e.target.checked; });
$('l-lakes').addEventListener('change', e => { const o = overlayGroup.getObjectByName('lakes'); if (o) o.visible = e.target.checked; });
$('l-cities').addEventListener('change', e => { const o = overlayGroup.getObjectByName('cities'); if (o) o.visible = e.target.checked; });
$('l-breach').addEventListener('change', e => { const o = overlayGroup.getObjectByName('breach'); if (o) o.visible = e.target.checked; });
$('l-torrent').addEventListener('change', e => { torrentsOn = e.target.checked; update(); });
$('l-cam').addEventListener('change', e => { autoCam = e.target.checked; });
$('l-names').addEventListener('change', e => { labelsOn = e.target.checked; });
// Any manual orbit hands the camera back to the user for good.
controls.addEventListener('start', () => { autoCam = false; $('l-cam').checked = false; });
for (const r of document.querySelectorAll('input[name=trk]'))
  r.addEventListener('change', e => { if (e.target.checked) setTrack(e.target.value); });

// Cross-validation figures stay in scene.json (S.cross_validation) and in the README;
// they are just not shown in the panel.
$('valid').innerHTML = `Peak envelope <b>${S.envelope_km2.toLocaleString()} km²</b>`;

// ── orientation gizmo ───────────────────────────────────────────────────────
const gz = $('gizmo').getContext('2d');
function drawGizmo() {
  const w = 148, c = w / 2, R = 46;
  gz.clearRect(0, 0, w, w);
  const az = Math.atan2(camera.position.x - controls.target.x,
                        camera.position.z - controls.target.z);
  gz.strokeStyle = 'rgba(20,60,100,.30)'; gz.lineWidth = 2;
  gz.beginPath(); gz.arc(c, c, R, 0, Math.PI * 2); gz.stroke();
  // North is -Z, so it sits at bearing 0 relative to the camera azimuth below.
  const dirs = [['N', 0, '#c8452a'], ['E', Math.PI / 2, '#12639f'],
                ['S', Math.PI, '#4a6c88'], ['W', -Math.PI / 2, '#4a6c88']];
  gz.font = 'bold 13px Helvetica'; gz.textAlign = 'center'; gz.textBaseline = 'middle';
  for (const [lbl, a, col] of dirs) {
    const t = a - az;
    const x = c + Math.sin(t) * R, y = c - Math.cos(t) * R;
    gz.fillStyle = col; gz.beginPath(); gz.arc(x, y, 11, 0, Math.PI * 2); gz.fill();
    gz.fillStyle = '#fff'; gz.fillText(lbl, x, y + 0.5);
  }
}

// ── run ─────────────────────────────────────────────────────────────────────
await loadOverlays();
await loadPlaces();
for (const [n, on] of [['districts', false], ['cities', false]]) {
  const o = overlayGroup.getObjectByName(n);
  if (o) o.visible = on;
}
setTrack('viirs');
applyExag();

addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
  fitVeil();
});

// Scripted camera: in close on the Kirthar front while the rain builds and the
// torrents run, then easing back to frame the whole block as the flood spreads.
// Keyed to story position, not wall clock, so scrubbing moves the camera too.
const CAM_KEYS = [
  { u: 0.00, pos: [-250, 300, 430], tgt: [-95, 25, 40] },
  { u: 0.22, pos: [-215, 235, 350], tgt: [-105, 25, 30] },
  { u: 0.34, pos: [-285, 360, 560], tgt: [-60, 25, 30] },
  { u: 0.52, pos: [-300, 470, 780], tgt: [0, 30, 0] },
  { u: 1.00, pos: [-215, 520, 830], tgt: [0, 30, 0] },
];
const ease = t => t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
const _p = new THREE.Vector3(), _t = new THREE.Vector3();

function cameraFor(u) {
  let i = 0;
  while (i < CAM_KEYS.length - 2 && CAM_KEYS[i + 1].u <= u) i++;
  const a = CAM_KEYS[i], b = CAM_KEYS[i + 1];
  const f = ease(Math.min(Math.max((u - a.u) / (b.u - a.u), 0), 1));
  _p.fromArray(a.pos).lerp(new THREE.Vector3().fromArray(b.pos), f);
  _t.fromArray(a.tgt).lerp(new THREE.Vector3().fromArray(b.tgt), f);
}

let last = performance.now();
renderer.setAnimationLoop(now => {
  const dt = Math.min((now - last) / 1000, 0.1); last = now;
  // Surface motion, rain and torrent shimmer are independent of the timeline: they
  // keep running while the replay is paused.
  waterMat.uniforms.uTime.value += dt;
  torrentMat.uniforms.uTime.value += dt;
  rainMat.uniforms.uTime.value += dt;
  if (playing) {
    // ~2 min for the full 1 Jun 2022 -> 28 Feb 2023 run at speed 1
    tNorm += dt * 0.0085 * speed;
    if (tNorm >= 1) { tNorm = 1; playing = false; $('play').textContent = '▶'; }
    update();
  }
  if (autoCam) {
    cameraFor(tNorm);
    camera.position.lerp(_p, 1 - Math.pow(0.006, dt));
    controls.target.lerp(_t, 1 - Math.pow(0.006, dt));
  }
  controls.update();
  updateLabels();
  drawGizmo();
  renderer.render(scene, camera);
});

$('loading').classList.add('done');
