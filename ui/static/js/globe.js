/**
 * 3-D Moon globe — Three.js r128
 *
 * Moon behaviour
 * ──────────────
 * • The Moon mesh rotates at the real sidereal rate (27.321661 days/rev).
 *   Initial orientation is set from the JPL Horizons sub-solar longitude at
 *   the user's chosen arrival time, so the lit hemisphere is physically correct.
 * • Time slider scrubs through the mission window; the Moon rotates to the
 *   exact orientation at that moment and Sun/Earth arrows update accordingly.
 * • Auto-rotation resumes when the slider is not being dragged.
 *
 * Camera behaviour
 * ────────────────
 * • LEFT mouse drag  → orbit the camera around the Moon (look around the scene,
 *   see Earth arrow, Sun arrow, stars from any angle).
 * • Scroll / pinch   → zoom.
 * • The Moon itself does NOT respond to drag — only the camera moves.
 *
 * Exposes window.Globe = {
 *   init, updateMarkers, updateSunEarth, setArrivalOrientation,
 *   scrubToFraction, resize
 * }
 */
window.Globe = (() => {

  // ── constants ──────────────────────────────────────────────────────────────
  const SIDEREAL_DAYS   = 27.321661;          // days per full rotation
  const RAD_PER_MS      = (2 * Math.PI) / (SIDEREAL_DAYS * 86400 * 1000);
  const SITE_COLORS     = ['#d4a23c', '#7d9ec0', '#b08968'];

  // ── state ──────────────────────────────────────────────────────────────────
  let renderer, scene, camera, moon, markerGroup, dirGroup;
  let canvas;
  let moonRotY = 0;           // current Y-rotation of moon mesh (radians)
  let lastTs   = null;        // last animation timestamp
  let autoSpin = true;        // false while slider is held

  // Camera orbit state
  let camTheta  =  0;         // azimuth around Y (radians)
  let camPhi    =  0.3;       // elevation (radians, clamped)
  let camRadius = 2.6;
  let isDragging = false;
  let dragStart  = { x: 0, y: 0 };
  let tgtTheta   = 0;
  let tgtPhi     = 0.3;

  // Stored body-fixed Sun/Earth directions (updated by updateSunEarth)
  let _bodyDirs = null;       // { sLon, sLat, eLon, eLat }

  // Per-frame Sun/Earth data array for the slider (set from outside)
  let _ephData  = null;       // array of { sunLon, sunLat, earthLon, earthLat }

  // ── init ────────────────────────────────────────────────────────────────────
  function init(canvasEl) {
    canvas = canvasEl;
    const W = canvas.clientWidth  || 600;
    const H = canvas.clientHeight || 400;

    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(W, H);
    renderer.setClearColor(0x0c0e10, 1);

    scene  = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(45, W / H, 0.1, 1000);
    _updateCamera();

    // Starfield
    scene.add(_makeStars(4000));

    // Lighting — balanced: visible dark side, clean terminator, not blinding
    scene.add(new THREE.AmbientLight(0x2a2d35, 0.9));
    const _fillLight = new THREE.DirectionalLight(0x3a4a6a, 0.22);
    _fillLight.position.set(-4, -1, -2);
    scene.add(_fillLight);
    window._sunLight = new THREE.DirectionalLight(0xfff8ee, 1.4);
    window._sunLight.position.set(5, 2, 3);
    scene.add(window._sunLight);

    // Moon sphere
    const geo = new THREE.SphereGeometry(1, 64, 64);
    const mat = new THREE.MeshPhongMaterial({ color: 0xffffff, specular: 0x050505, shininess: 2 });
    new THREE.TextureLoader().load(
      '/static/textures/moon.jpg',
      tex => { mat.map = tex; mat.needsUpdate = true; },
      undefined,
      () => { mat.color.set(0xa6a69e); }
    );
    moon = new THREE.Mesh(geo, mat);
    scene.add(moon);

    // Equator ring
    scene.add(new THREE.Mesh(
      new THREE.TorusGeometry(1.005, 0.001, 8, 128),
      new THREE.MeshBasicMaterial({ color: 0x2a2f37, transparent: true, opacity: 0.4 })
    ));

    markerGroup = new THREE.Group();
    dirGroup    = new THREE.Group();   // world-space: arrows don't rotate with Moon
    scene.add(markerGroup);
    scene.add(dirGroup);

    // Input
    canvas.addEventListener('mousedown',  _onMouseDown);
    window.addEventListener('mousemove',  _onMouseMove);
    window.addEventListener('mouseup',    _onMouseUp);
    canvas.addEventListener('wheel',      _onWheel, { passive: true });
    canvas.addEventListener('touchstart', _onTouchStart, { passive: true });
    canvas.addEventListener('touchmove',  _onTouchMove,  { passive: false });
    canvas.addEventListener('touchend',   _onMouseUp);

    _animate();
  }

  // ── star field ─────────────────────────────────────────────────────────────
  function _makeStars(n) {
    const pos = new Float32Array(n * 3);
    for (let i = 0; i < n * 3; i++) pos[i] = (Math.random() - 0.5) * 400;
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    return new THREE.Points(geo,
      new THREE.PointsMaterial({ color: 0xffffff, size: 0.35, transparent: true, opacity: 0.65 }));
  }

  // ── geometry helpers ────────────────────────────────────────────────────────
  function _ll2vec(lat, lon) {
    const φ = THREE.MathUtils.degToRad(lat);
    const λ = THREE.MathUtils.degToRad(lon);
    return new THREE.Vector3(Math.cos(φ) * Math.sin(λ), Math.sin(φ), Math.cos(φ) * Math.cos(λ));
  }
  function _ll2xyz(lat, lon, r) { return _ll2vec(lat, lon).multiplyScalar(r); }

  // Convert body-fixed lon/lat → world-space direction (accounting for moonRotY)
  function _bodyToWorld(lat, lon) {
    return _ll2vec(lat, lon).applyEuler(new THREE.Euler(0, moonRotY, 0));
  }

  // ── arrival orientation ─────────────────────────────────────────────────────
  // sunLon = sub-solar longitude in body-fixed frame at arrival.
  // We place the sub-solar point facing the Sun (world +Z axis used as Sun ref).
  function setArrivalOrientation(sunLon) {
    moonRotY = -THREE.MathUtils.degToRad(sunLon);
    _applyMoonRot();
    lastTs = null;
  }

  function _applyMoonRot() {
    if (moon)        moon.rotation.y        = moonRotY;
    if (markerGroup) markerGroup.rotation.y = moonRotY;
  }

  // ── markers (rotate WITH Moon) ──────────────────────────────────────────────
  function updateMarkers(sites) {
    while (markerGroup.children.length) markerGroup.remove(markerGroup.children[0]);
    sites.forEach((site, i) => {
      const color = parseInt(SITE_COLORS[i].replace('#',''), 16);

      const disc = new THREE.Mesh(
        new THREE.CircleGeometry(0.034, 24),
        new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide })
      );
      disc.position.copy(_ll2xyz(site.lat, site.lon, 1.02));
      disc.lookAt(disc.position.clone().multiplyScalar(2));
      markerGroup.add(disc);

      const ring = new THREE.Mesh(
        new THREE.TorusGeometry(0.044, 0.006, 8, 24),
        new THREE.MeshBasicMaterial({ color })
      );
      ring.position.copy(_ll2xyz(site.lat, site.lon, 1.02));
      ring.lookAt(ring.position.clone().multiplyScalar(2));
      markerGroup.add(ring);

      const spike = new THREE.Mesh(
        new THREE.ConeGeometry(0.011, 0.075, 6),
        new THREE.MeshBasicMaterial({ color })
      );
      spike.position.copy(_ll2xyz(site.lat, site.lon, 1.055));
      spike.lookAt(new THREE.Vector3(0, 0, 0));
      spike.rotateX(Math.PI);
      markerGroup.add(spike);
    });
  }

  // ── Sun & Earth arrows (world-space) ────────────────────────────────────────
  function updateSunEarth(sunLon, sunLat, earthLon, earthLat) {
    _bodyDirs = { sLon: sunLon, sLat: sunLat, eLon: earthLon, eLat: earthLat };
    _rebuildArrows();
  }

  function _rebuildArrows() {
    while (dirGroup.children.length) dirGroup.remove(dirGroup.children[0]);
    if (!_bodyDirs) return;
    const { sLon, sLat, eLon, eLat } = _bodyDirs;
    const sunWorld   = _bodyToWorld(sLat, sLon);
    const earthWorld = _bodyToWorld(eLat, eLon);
    if (window._sunLight) window._sunLight.position.copy(sunWorld.clone().multiplyScalar(10));
    _addArrow(sunWorld,   0xe8c25a, 1.65);
    _addArrow(earthWorld, 0x6f8fa8, 1.65);
  }

  function _addArrow(dir, colorHex, tipR) {
    const start = dir.clone().multiplyScalar(1.18);
    const end   = dir.clone().multiplyScalar(tipR - 0.055);
    const len   = start.distanceTo(end);
    const mid   = start.clone().lerp(end, 0.5);

    const shaft = new THREE.Mesh(
      new THREE.CylinderGeometry(0.007, 0.007, len, 6),
      new THREE.MeshBasicMaterial({ color: colorHex, transparent: true, opacity: 0.85 })
    );
    shaft.position.copy(mid);
    shaft.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0), dir.clone().normalize());
    dirGroup.add(shaft);

    const head = new THREE.Mesh(
      new THREE.ConeGeometry(0.02, 0.055, 8),
      new THREE.MeshBasicMaterial({ color: colorHex })
    );
    head.position.copy(end);
    head.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0), dir.clone().normalize());
    dirGroup.add(head);

    const dot = new THREE.Mesh(
      new THREE.SphereGeometry(0.038, 12, 12),
      new THREE.MeshBasicMaterial({ color: colorHex, transparent: true, opacity: 0.9 })
    );
    dot.position.copy(dir.clone().multiplyScalar(tipR));
    dirGroup.add(dot);
  }

  // ── ephemeris data for slider ────────────────────────────────────────────────
  // Called from app.js with an array of { sunLon, sunLat, earthLon, earthLat }
  // indexed at eph step_h resolution across the mission window.
  function setEphData(arr) {
    _ephData = arr;
  }

  // Scrub to a fraction [0,1] of the mission window
  function scrubToFraction(frac) {
    if (!_ephData || !_ephData.length) return;
    const idx = Math.min(Math.floor(frac * _ephData.length), _ephData.length - 1);
    const d = _ephData[idx];
    // Rotate Moon: sun_lon at arrival mapped to 0 → current sun_lon offset.
    // The sub-solar longitude in the Moon body frame IS the sidereal rotation phase,
    // so tracking it from Horizons gives the physically correct Moon orientation.
    if (_ephData[0]) {
      const deltaLon = d.sunLon - _ephData[0].sunLon;
      moonRotY = _ephData[0]._baseRot - THREE.MathUtils.degToRad(deltaLon);
      _applyMoonRot();
    }
    // Update arrows with the Horizons Earth position at this exact moment.
    // Both Sun and Earth positions come directly from JPL Horizons body-fixed
    // coordinates, so both arrows track the real geometry.
    updateSunEarth(d.sunLon, d.sunLat, d.earthLon, d.earthLat);
    return idx;
  }

  // Return the mission-window fraction [0,1] that best matches the current moonRotY.
  // Used by auto-spin to keep the Earth arrow in sync with the spinning Moon.
  function _rotToFrac() {
    if (!_ephData || !_ephData.length || !_ephData[0]) return 0;
    const baseRot   = _ephData[0]._baseRot || 0;
    const deltaRad  = baseRot - moonRotY;          // how far Moon has rotated
    const deltaLon  = THREE.MathUtils.radToDeg(deltaRad);
    // Find the eph entry whose sunLon offset best matches deltaLon
    const target = (_ephData[0].sunLon + deltaLon);
    // Walk forward from start; sunLon drifts ~-12 deg/day
    let best = 0, bestDist = Infinity;
    for (let i = 0; i < _ephData.length; i++) {
      // unwrapped difference
      let diff = Math.abs(_ephData[i].sunLon - (_ephData[0].sunLon + (_ephData[i].sunLon - _ephData[0].sunLon)));
      // simpler: just use index proportion via linear sunLon drift
      const dist = Math.abs((_ephData[i].sunLon - _ephData[0].sunLon) - deltaLon);
      if (dist < bestDist) { bestDist = dist; best = i; }
    }
    return best / (_ephData.length - 1);
  }

  // ── animation loop ──────────────────────────────────────────────────────────
  function _animate(ts) {
    requestAnimationFrame(_animate);

    // Smooth camera
    camTheta += (tgtTheta - camTheta) * 0.1;
    camPhi   += (tgtPhi   - camPhi)   * 0.1;
    _updateCamera();

    // Auto-spin Moon
    if (autoSpin && lastTs !== null && ts !== undefined) {
      const dt = ts - lastTs;
      moonRotY += RAD_PER_MS * dt;
      _applyMoonRot();
      // Keep Sun & Earth arrows synced to the Horizons ephemeris position that
      // matches the current Moon rotation, not frozen at the last scrub position.
      if (_ephData && _ephData.length > 1 && _ephData[0]) {
        const frac = _rotToFrac();
        const idx  = Math.min(Math.round(frac * (_ephData.length - 1)), _ephData.length - 1);
        const d    = _ephData[idx];
        _bodyDirs  = { sLon: d.sunLon, sLat: d.sunLat, eLon: d.earthLon, eLat: d.earthLat };
      }
      _rebuildArrows();
    }
    lastTs = ts;

    renderer.render(scene, camera);
  }

  function _updateCamera() {
    if (!camera) return;
    camera.position.set(
      camRadius * Math.sin(camTheta) * Math.cos(camPhi),
      camRadius * Math.sin(camPhi),
      camRadius * Math.cos(camTheta) * Math.cos(camPhi)
    );
    camera.lookAt(0, 0, 0);
  }

  // ── input handlers ──────────────────────────────────────────────────────────
  function _onMouseDown(e) {
    isDragging = true;
    dragStart  = { x: e.clientX, y: e.clientY };
  }
  function _onMouseMove(e) {
    if (!isDragging) return;
    const dx = e.clientX - dragStart.x;
    const dy = e.clientY - dragStart.y;
    tgtTheta -= dx * 0.005;
    tgtPhi   = Math.max(-Math.PI/2 + 0.05, Math.min(Math.PI/2 - 0.05, tgtPhi + dy * 0.005));
    dragStart = { x: e.clientX, y: e.clientY };
  }
  function _onMouseUp() { isDragging = false; }

  function _onWheel(e) {
    camRadius = Math.max(1.3, Math.min(5.5, camRadius + e.deltaY * 0.003));
  }

  let _pinchDist = null;
  function _onTouchStart(e) {
    if (e.touches.length === 1) {
      isDragging = true;
      dragStart  = { x: e.touches[0].clientX, y: e.touches[0].clientY };
    } else if (e.touches.length === 2) {
      _pinchDist = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
    }
  }
  function _onTouchMove(e) {
    e.preventDefault();
    if (e.touches.length === 1 && isDragging) {
      const dx = e.touches[0].clientX - dragStart.x;
      const dy = e.touches[0].clientY - dragStart.y;
      tgtTheta -= dx * 0.005;
      tgtPhi   = Math.max(-Math.PI/2 + 0.05, Math.min(Math.PI/2 - 0.05, tgtPhi + dy * 0.005));
      dragStart = { x: e.touches[0].clientX, y: e.touches[0].clientY };
    } else if (e.touches.length === 2 && _pinchDist) {
      const d = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
      camRadius = Math.max(1.3, Math.min(5.5, camRadius - (d - _pinchDist) * 0.005));
      _pinchDist = d;
    }
  }

  // ── resize ──────────────────────────────────────────────────────────────────
  function resize() {
    if (!renderer || !canvas) return;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }

  // Expose pause/resume for slider
  function pauseAutoSpin()  { autoSpin = false; }
  function resumeAutoSpin() { autoSpin = true;  lastTs = null; }

  return {
    init,
    updateMarkers,
    updateSunEarth,
    setArrivalOrientation,
    setEphData,
    scrubToFraction,
    pauseAutoSpin,
    resumeAutoSpin,
    resize,
  };
})();
