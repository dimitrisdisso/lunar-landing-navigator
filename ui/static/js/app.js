/**
 * Lunar Landing Navigator — UI controller
 */
(() => {

  // ── state ──────────────────────────────────────────────────────────────────
  let currentData    = null;
  let currentSiteIdx = 0;
  let pollTimer      = null;
  let progressPct    = 0;
  let _knownLogLen   = 0;

  const LOG_STEPS = ['Fetching Sun', 'Loading NASA', 'Screening', 'Analysing', 'Building'];

  const $ = id => document.getElementById(id);

  const screens = { landing: $('landing-screen'), loading: $('loading-screen'), app: $('app-screen') };

  // ── screen transitions ─────────────────────────────────────────────────────
  function showScreen(name) {
    Object.values(screens).forEach(s => s.classList.remove('active'));
    screens[name].classList.add('active');
    if (name === 'app') setTimeout(() => Globe.resize(), 60);
  }

  // ── landing form ───────────────────────────────────────────────────────────
  function bindLandingEvents() {
    const analyzeBtn = $('analyze-btn');
    const backBtn = $('back-btn');

    if (!analyzeBtn || !backBtn) {
      console.error('Landing UI elements not found; page may still be loading.');
      return;
    }

    analyzeBtn.addEventListener('click', () => {
      const date = $('date-input').value;
      const time = $('time-input').value || '00:00';
      const days = parseFloat($('days-input').value) || 6.5;
      if (!date) { alert('Please select a date.'); return; }
      startAnalysis(date, time, days);
    });

    backBtn.addEventListener('click', () => {
      if (pollTimer) clearInterval(pollTimer);
      Globe.pauseAutoSpin && Globe.pauseAutoSpin();
      showScreen('landing');
    });
  }

  document.addEventListener('DOMContentLoaded', bindLandingEvents);

  // ── analysis request ───────────────────────────────────────────────────────
  function startAnalysis(date, time, days) {
    $('loading-status').textContent = 'Connecting to NASA servers…';
    $('loading-status').style.color = '';
    $('log-box').innerHTML = '';
    $('progress-bar').style.width = '0%';
    progressPct  = 0;
    _knownLogLen = 0;
    showScreen('loading');

    fetch('/analyze', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ date, time, days })
    })
    .then(r => r.json())
    .then(({ job_id, error }) => {
      if (error) { _showError(error); return; }
      pollTimer = setInterval(() => _pollStatus(job_id), 1200);
    })
    .catch(err => _showError(err.toString()));
  }

  function _pollStatus(jobId) {
    fetch(`/status/${jobId}`)
      .then(r => r.json())
      .then(({ status, log, result }) => {
        const logBox = $('log-box');
        for (let i = _knownLogLen; i < log.length; i++) {
          const line = document.createElement('div');
          line.className = 'log-line';
          line.textContent = log[i];
          logBox.appendChild(line);
          logBox.scrollTop = logBox.scrollHeight;
          if (log[i] && !log[i].startsWith('  '))
            $('loading-status').textContent = log[i].replace(/[…\.]+$/, '');
        }
        _knownLogLen = log.length;

        const latestLog = log.join('\n');
        let step = 0;
        LOG_STEPS.forEach((s, i) => { if (latestLog.includes(s)) step = i + 1; });
        progressPct = Math.max(progressPct, step / LOG_STEPS.length * 90);
        $('progress-bar').style.width = progressPct + '%';

        if (status === 'done' && result) {
          clearInterval(pollTimer);
          $('progress-bar').style.width = '100%';
          setTimeout(() => _loadResults(result), 400);
        } else if (status === 'error') {
          clearInterval(pollTimer);
          _showError(log[log.length - 1] || 'Unknown error');
        }
      })
      .catch(() => {});
  }

  function _showError(msg) {
    $('loading-status').textContent = 'Error: ' + msg;
    $('loading-status').style.color = '#f87171';
    if (pollTimer) clearInterval(pollTimer);
  }

  // ── load results ───────────────────────────────────────────────────────────
  function _loadResults(data) {
    currentData    = data;
    currentSiteIdx = 0;
    _knownLogLen   = 0;

    $('topbar-date').textContent   = data.arrival_datetime + ' UTC  ·  ' + data.days + '-day stay';
    $('topbar-source').textContent = data.terrain_source + '  ·  ' + data.ephemeris_source;

    // Site header (single site, no tabs)
    const tabsEl = $('site-tabs');
    tabsEl.innerHTML = '';
    const site = data.sites[0];
    const header = document.createElement('div');
    header.className = 'site-header';
    header.innerHTML = `Best Site <span class="site-score">${site.score.toFixed(0)}/100</span>`;
    header.style.cssText = 'font-size: 1rem; color: #d4a23c; font-weight: 500; padding: 0.5rem 1rem;';
    tabsEl.appendChild(header);

    showScreen('app');

    // Init globe
    Globe.init($('moon-canvas'));
    Globe.updateMarkers(data.sites);

    // Tag the first eph entry with the base rotation so scrubbing knows where to start
    if (data.eph_series && data.eph_series.length) {
      data.eph_series[0]._baseRot = -THREE.MathUtils.degToRad(data.arrival_sun_lon);
    }
    Globe.setEphData(data.eph_series || []);
    Globe.setArrivalOrientation(data.arrival_sun_lon);

    // Legend (single site)
    const legendEl = $('globe-legend');
    legendEl.innerHTML = '';
    const siteLegend = data.sites[0];
    legendEl.innerHTML += `
      <div class="legend-item">
        <div class="legend-dot" style="background:#d4a23c"></div>
        <span>Landing: ${_ns(siteLegend.lat)}, ${_ew(siteLegend.lon)}</span>
      </div>`;
    legendEl.innerHTML += `
      <div class="legend-item"><div class="legend-dot" style="background:#e8c25a"></div><span>☀ Sun</span></div>
      <div class="legend-item"><div class="legend-dot" style="background:#6f8fa8;opacity:0.85"></div><span>🌍 Earth</span></div>`;

    // Time slider
    _initSlider(data);

    _selectSite(0);
    window.addEventListener('resize', () => Globe.resize());
    WacViewer.init();
  }

  // ── time slider ────────────────────────────────────────────────────────────
  function _initSlider(data) {
    const slider    = $('time-slider');
    const display   = $('slider-time-display');
    const labelCtr  = $('slider-label-center');
    const labelR    = $('slider-label-right');

    slider.value = 0;
    labelR.textContent = `+${data.days}d`;
    labelCtr.textContent = `+${(data.days / 2).toFixed(1)}d`;

    function _update(val) {
      const frac = val / 1000;
      const hrs  = frac * data.days * 24;
      const d    = Math.floor(hrs / 24);
      const h    = Math.floor(hrs % 24);
      const m    = Math.floor((hrs * 60) % 60);
      display.textContent = `T+${d}d ${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')} UTC`;

      // Scrub the globe and get back the eph index
      const idx = Globe.scrubToFraction(frac);
      // Update Sun/Earth arrows for selected site at this time
      if (data.eph_series && idx !== undefined) {
        const ep = data.eph_series[Math.min(idx, data.eph_series.length - 1)];
        if (ep) Globe.updateSunEarth(ep.sunLon, ep.sunLat, ep.earthLon, ep.earthLat);
      }
    }

    slider.addEventListener('mousedown',  () => Globe.pauseAutoSpin());
    slider.addEventListener('touchstart', () => Globe.pauseAutoSpin(), { passive: true });
    slider.addEventListener('mouseup',    () => Globe.resumeAutoSpin());
    slider.addEventListener('touchend',   () => Globe.resumeAutoSpin());
    slider.addEventListener('input', e   => _update(parseInt(e.target.value)));
    _update(0);
  }

  // ── site selection ─────────────────────────────────────────────────────────
  function _selectSite(idx) {
    if (!currentData) return;
    currentSiteIdx = idx;
    const site = currentData.sites[idx];

    document.querySelectorAll('.site-tab').forEach((t, i) => t.classList.toggle('active', i === idx));

    Globe.updateSunEarth(site.sun_lon, site.sun_lat, site.earth_lon, site.earth_lat);
    WacViewer.update(site.lat, site.lon);
    _renderLeft(site);
    _renderRight(site);
  }

  // ── left panel ─────────────────────────────────────────────────────────────
  function _renderLeft(site) {
    $('coord-lat').textContent = _ns(site.lat);
    $('coord-lon').textContent = _ew(site.lon);
    $('coord-time').textContent = currentData.arrival_datetime + ' UTC';
    $('score-badge').textContent = site.score.toFixed(1);

    const cb = $('constraints-badge');
    cb.textContent  = site.constraints_ok ? 'ALL CONSTRAINTS MET' : 'CONSTRAINT FAILED';
    cb.className    = 'constraints-badge ' + (site.constraints_ok ? 'pass' : 'fail');

    const k = site.key;
    const stats = [
      { name: 'Sunlit During Stay',    value: _pct(k.sunlit_pct),
        bar: k.sunlit_pct,  barClass: k.sunlit_pct >= 80 ? 'good' : k.sunlit_pct >= 20 ? 'medium' : 'bad' },
      { name: 'Earth Visibility',      value: _pct(k.earth_vis_pct),
        bar: k.earth_vis_pct, barClass: k.earth_vis_pct >= 80 ? 'good' : k.earth_vis_pct >= 25 ? 'medium' : 'bad',
        note: k.longest_outage_h > 0 ? `Longest outage: ${k.longest_outage_h.toFixed(0)} h` : 'No outages' },
      { name: 'Surface Temperature',
        value: k.t_min_c !== null ? `${k.t_min_c.toFixed(0)}°C to ${k.t_max_c.toFixed(0)}°C` : 'n/a',
        note: k.t_mean_c !== null ? `Mean: ${k.t_mean_c.toFixed(0)}°C` : '' },
      { name: 'Landing Slope',         value: `${k.slope_mean.toFixed(1)}° mean`,
        bar: 100 - k.slope_mean / 10 * 100,
        barClass: k.slope_mean <= 5 ? 'good' : k.slope_mean <= 8 ? 'medium' : 'bad',
        note: `Max in zone: ${k.slope_max.toFixed(1)}°` },
      { name: 'Obstacle-Free Zone',    value: `${k.r_inscribed_m.toFixed(0)} m radius`,
        bar: Math.min(k.r_inscribed_m / 500 * 100, 100),
        barClass: k.r_inscribed_m >= 200 ? 'good' : k.r_inscribed_m >= 100 ? 'medium' : 'bad',
        note: `Safe area: ${k.safe_area_km2.toFixed(2)} km²` },
      { name: 'Sun Elevation',         value: `${k.sun_el_min.toFixed(1)}° – ${k.sun_el_max.toFixed(1)}°`,
        note: `Solar flux: ${k.sun_flux.toFixed(0)} W/m²` },
      { name: 'Elevation',             value: (k.elevation_m >= 0 ? '+' : '') + k.elevation_m.toFixed(0) + ' m',
        note: 'vs 1737.4 km reference sphere' },
      { name: 'GCR Radiation Dose',    value: `~${k.gcr_msv.toFixed(1)} mSv`,
        note: `over ${currentData.days}-day stay (Chang'e-4 LND)` },
    ];

    const ksEl = $('key-stats');
    ksEl.innerHTML = '';
    stats.forEach((s, i) => {
      const card = document.createElement('div');
      card.className = 'stat-card';
      card.style.animationDelay = (i * 0.04) + 's';
      card.innerHTML = `
        <div class="stat-name">${s.name}</div>
        <div class="stat-value">${s.value}</div>
        ${s.note ? `<div class="stat-note">${s.note}</div>` : ''}
        ${s.bar != null ? `<div class="stat-bar-wrap"><div class="stat-bar ${s.barClass}" style="width:0%" data-target="${Math.max(0, Math.min(100, s.bar))}"></div></div>` : ''}`;
      ksEl.appendChild(card);
    });
    setTimeout(() => ksEl.querySelectorAll('.stat-bar').forEach(b => b.style.width = b.dataset.target + '%'), 80);

    // Score breakdown
    const sbEl = $('score-breakdown');
    sbEl.innerHTML = '<div class="sb-title">Score Breakdown</div>';
    Object.entries(site.score_parts).forEach(([label, val]) => {
      const match = label.match(/\((\d+)\)/);
      const max   = match ? parseInt(match[1]) : 30;
      const pct   = Math.round(val / max * 100);
      sbEl.innerHTML += `
        <div class="sb-row">
          <div class="sb-name">${label.replace(/\s*\(\d+\)/, '')}</div>
          <div class="sb-val">${val.toFixed(1)}</div>
          <div class="sb-bar-wrap"><div class="sb-bar" style="width:${pct}%"></div></div>
        </div>`;
    });

    // Constraints
    const clEl = $('constraints-list');
    clEl.innerHTML = '<div class="cl-title">NASA Artemis III Constraints</div>';
    site.constraints.forEach(c => {
      clEl.innerHTML += `
        <div class="cl-item">
          <span class="cl-icon ${c.pass ? 'pass' : 'fail'}">${c.pass ? '✓' : '✗'}</span>
          <span>${c.name}</span>
        </div>`;
    });
  }

  // ── right panel ────────────────────────────────────────────────────────────
  function _renderRight(site) {
    const listEl = $('sections-list');
    listEl.innerHTML = '';
    let first = true;
    Object.values(site.sections).forEach(sec => {
      const item = document.createElement('div');
      item.className = 'section-item';

      const head = document.createElement('div');
      head.className = 'section-head' + (first ? ' open' : '');

      // Quality-aware status badge — not just "does it work" but "is it actually good".
      // Each section has specific quality signals derived from the actual data values.
      const _secKey = sec.title ? sec.title.split('.')[0].trim() : '';
      const _allText = (sec.items || []).map(it => (it[1] || '') + (it[0] || '')).join(' ');
      const _site = currentData && currentData.sites ? currentData.sites[currentSiteIdx] : null;
      const _key  = _site ? _site.key : null;

      function _sectionStatus(key, allText, site, keyStats) {
        // Hard block always wins regardless of section
        if (allText.includes('BLOCKING') || allText.includes('⚠ Requires operational')) return 'block';

        switch (key) {
          case '1': { // Terrain — OK if max slope ≤10°, warn if 8–10°, block if >10°
            if (!keyStats) return 'warn';
            const mx = keyStats.slope_max;
            if (mx == null) return 'warn';
            return mx <= 7 ? 'ok' : mx <= 10 ? 'warn' : 'block';
          }
          case '2': { // Roughness — OK <0.5 m, warn 0.5–1 m, block >1 m (high hazard)
            if (!keyStats) return 'warn';
            // extract rough_max from allText since it's not in keyStats
            const m = allText.match(/(\d+\.\d+)\s*m\s*(mean|max)/i);
            if (!m) return allText.includes('UNVERIFIED') ? 'warn' : 'ok';
            const v = parseFloat(m[1]);
            return v < 0.5 ? 'ok' : v < 1.0 ? 'warn' : 'block';
          }
          case '3': { // Craters/boulders — always warn if UNVERIFIED sub-pixel hazards present
            if (allText.includes('UNVERIFIED')) return 'warn';
            if (allText.includes('none resolved')) return 'ok';
            return 'warn';
          }
          case '4': { // Landing zone — OK if r≥200m and area ok; warn if provisional
            if (!keyStats) return 'warn';
            const r = keyStats.r_inscribed_m;
            if (r == null) return 'warn';
            if (allText.includes('UNVERIFIED')) return 'warn';
            return r >= 200 ? 'ok' : r >= 100 ? 'warn' : 'block';
          }
          case '5': { // Illumination — OK if 100% sunlit; warn if <80%; block if <5%
            if (!keyStats) return 'warn';
            const pct = keyStats.sunlit_pct;
            if (pct == null) return 'warn';
            return pct >= 80 ? 'ok' : pct >= 5 ? 'warn' : 'block';
          }
          case '6': { // Shadowing — OK if 0h blocked; warn otherwise
            const shadH = allText.match(/(\d+)\s*h\s*(blocked|shadow)/i);
            if (shadH && parseInt(shadH[1]) > 0) return 'warn';
            return 'ok';
          }
          case '7': { // Thermal — OK if no transitions and swing <30K; warn otherwise
            if (allText.includes('not computed')) return 'warn';
            const swingM = allText.match(/swing.*?(\d+)\s*K/i);
            const swing = swingM ? parseInt(swingM[1]) : 0;
            const transM = allText.match(/transitions.*?(\d+)/i);
            const trans = transM ? parseInt(transM[1]) : 0;
            return (swing < 30 && trans === 0) ? 'ok' : 'warn';
          }
          case '8': { // Comms — OK if earth vis ≥80%; warn if ≥25%; block if <25%
            if (!keyStats) return 'warn';
            const ep = keyStats.earth_vis_pct;
            if (ep == null) return 'warn';
            return ep >= 80 ? 'ok' : ep >= 25 ? 'warn' : 'block';
          }
          case '9': { // Soil — always reference data, mark warn (indicative, not measured)
            return 'warn';
          }
          case '10': { // Geology — OK if no >30° slopes; warn if any
            if (allText.includes('LOW') && !allText.includes('ELEVATED')) return 'ok';
            if (allText.includes('ELEVATED')) return 'warn';
            if (allText.includes('No measurement')) return 'warn';
            return 'ok';
          }
          case '11': { // Radiation — block if SEP blocking; warn for reference GCR
            if (allText.includes('BLOCKING') || allText.includes('operational forecast')) return 'block';
            return 'warn'; // GCR is reference-only, never fully "ok" without site measurement
          }
          default:
            if (allText.includes('BLOCKING')) return 'block';
            if (allText.includes('UNVERIFIED') || allText.includes('PROVISIONAL')) return 'warn';
            return 'ok';
        }
      }

      head.innerHTML = `<span class="section-title">${sec.title}</span><span class="section-arrow">▼</span>`;

      const body = document.createElement('div');
      body.className = 'section-body' + (first ? ' open' : '');

      if (sec.source) {
        body.innerHTML += `<div class="detail-row" style="border-bottom:1px dashed #2a2f37;margin-bottom:6px;padding-bottom:6px"><div class="detail-key">Data source</div><div class="detail-val" style="color:#d4a23c;font-size:11px;line-height:1.3">${sec.source}</div></div>`;
      }

      (sec.items || []).forEach(([key, val]) => {
        if (!key && !val) return;
        body.innerHTML += `
          <div class="detail-row">
            <div class="detail-key">${key}</div>
            <div class="detail-val">${val}</div>
          </div>`;
      });

      head.addEventListener('click', () => {
        const open = head.classList.contains('open');
        head.classList.toggle('open', !open);
        body.classList.toggle('open', !open);
      });

      item.appendChild(head);
      item.appendChild(body);
      listEl.appendChild(item);
      first = false;
    });
  }

  // ── helpers ────────────────────────────────────────────────────────────────
  function _ns(lat) { return `${Math.abs(lat).toFixed(4)}° ${lat < 0 ? 'S' : 'N'}`; }
  function _ew(lon) {
    lon = ((lon + 180) % 360) - 180;
    return `${Math.abs(lon).toFixed(4)}° ${lon >= 0 ? 'E' : 'W'}`;
  }
  function _pct(v) { return v != null ? `${v.toFixed(0)}%` : 'n/a'; }

})();

// ── WAC Imagery Viewer ────────────────────────────────────────────────────────
// Fetches NASA LROC WAC global mosaic tiles (Trek WMTS) and draws the site
// crosshair at the exact sub-tile pixel position of the landing coordinates.
// Source: LRO_WAC_Mosaic_Global_303ppd_v02 (~100 m/px native resolution).
const WacViewer = (() => {
  const WAC_BASE = 'https://trek.nasa.gov/tiles/Moon/EQ/LRO_WAC_Mosaic_Global_303ppd_v02/1.0.0/default/default028mm';
  const TILE_PX  = 256;
  const GRID     = 3;
  let _zoom = 7;
  let _lat  = null;
  let _lon  = null;

  // Exact tile-space position (floating-point col/row) for a coord at zoom z.
  // The integer part is the tile index; the fraction is the pixel offset within it.
  function _ll2tileF(lat, lon, z) {
    const n   = Math.pow(2, z);
    const col = (lon + 180) / 360 * n;
    const row = (90 - lat) / 180 * (n / 2);
    return { col, row };
  }

  // Integer tile index (clamped)
  function _ll2tile(lat, lon, z) {
    const { col, row } = _ll2tileF(lat, lon, z);
    const n = Math.pow(2, z);
    return { col: Math.max(0, Math.min(Math.floor(col), n - 1)),
             row: Math.max(0, Math.min(Math.floor(row), n / 2 - 1)) };
  }

  function _gsd(lat, z) {
    const R = 1737400;
    return 2 * Math.PI * R * Math.cos(lat * Math.PI / 180) / (Math.pow(2, z) * TILE_PX);
  }

  // Compute the canvas pixel (px, py) where the site sits in the GRID×GRID canvas.
  // Returns null if the site falls outside the drawn tile grid.
  function _sitePixel(lat, lon, zoom) {
    const { col: cCol, row: cRow } = _ll2tile(lat, lon, zoom);
    const { col: fCol, row: fRow } = _ll2tileF(lat, lon, zoom);
    const half = Math.floor(GRID / 2);
    // pixel offset of the site within the centre tile
    const fracX = fCol - Math.floor(fCol);   // 0–1 within the tile
    const fracY = fRow - Math.floor(fRow);
    // canvas pixel: left edge of centre tile + fractional offset
    const px = half * TILE_PX + fracX * TILE_PX;
    const py = half * TILE_PX + fracY * TILE_PX;
    return { px: Math.round(px), py: Math.round(py) };
  }

  function _drawCrosshair(ctx, px, py, canvasSize) {
    const ARM = 12, GAP = 4;
    ctx.save();
    ctx.strokeStyle = 'rgba(212,162,60,0.9)';
    ctx.lineWidth   = 1.5;
    ctx.beginPath();
    // vertical
    ctx.moveTo(px, py - ARM - GAP);  ctx.lineTo(px, py - GAP);
    ctx.moveTo(px, py + GAP);        ctx.lineTo(px, py + ARM + GAP);
    // horizontal
    ctx.moveTo(px - ARM - GAP, py);  ctx.lineTo(px - GAP, py);
    ctx.moveTo(px + GAP, py);        ctx.lineTo(px + ARM + GAP, py);
    ctx.stroke();
    // small centre dot
    ctx.fillStyle = 'rgba(212,162,60,0.9)';
    ctx.beginPath();
    ctx.arc(px, py, 2, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  function _load(lat, lon, zoom) {
    _lat = lat; _lon = lon; _zoom = zoom;
    const canvas  = document.getElementById('wac-canvas');
    const caption = document.getElementById('wac-caption');
    const zLabel  = document.getElementById('wac-zoom-label');
    if (!canvas) return;

    zLabel.textContent = `z${zoom}`;
    const ctx = canvas.getContext('2d');
    const size = GRID * TILE_PX;
    canvas.width  = size;
    canvas.height = size;
    ctx.fillStyle = '#05080d';
    ctx.fillRect(0, 0, size, size);
    caption.textContent = 'Loading…';

    const { col: cCol, row: cRow } = _ll2tile(lat, lon, zoom);
    const half  = Math.floor(GRID / 2);
    const site  = _sitePixel(lat, lon, zoom);
    let loaded  = 0;
    const total = GRID * GRID;

    function _finish() {
      // Draw crosshair at exact site pixel after all tiles are in
      _drawCrosshair(ctx, site.px, site.py, size);
      const gsd   = _gsd(lat, zoom);
      const cover = (GRID * TILE_PX * gsd / 1000).toFixed(0);
      caption.textContent =
        `${_ns(lat)}, ${_ew(lon)}  ·  ~${gsd.toFixed(0)} m/px  ·  ${cover}×${cover} km view`;
    }

    for (let dr = -half; dr <= half; dr++) {
      for (let dc = -half; dc <= half; dc++) {
        const tCol = cCol + dc;
        const tRow = cRow + dr;
        const px   = (dc + half) * TILE_PX;
        const py   = (dr + half) * TILE_PX;
        const url  = `${WAC_BASE}/${zoom}/${tRow}/${tCol}.jpg`;
        const img  = new Image();
        img.crossOrigin = 'anonymous';
        img.onload = () => {
          ctx.drawImage(img, px, py, TILE_PX, TILE_PX);
          if (++loaded === total) _finish();
        };
        img.onerror = () => {
          ctx.fillStyle = '#0a0d12';
          ctx.fillRect(px, py, TILE_PX, TILE_PX);
          if (++loaded === total) _finish();
        };
        img.src = url;
      }
    }
  }

  function update(lat, lon) { _load(lat, lon, _zoom); }

  function init() {
    const zIn  = document.getElementById('wac-zoom-in');
    const zOut = document.getElementById('wac-zoom-out');
    if (zIn)  zIn.addEventListener('click',  () => { if (_zoom < 9 && _lat != null) _load(_lat, _lon, _zoom + 1); });
    if (zOut) zOut.addEventListener('click', () => { if (_zoom > 4 && _lat != null) _load(_lat, _lon, _zoom - 1); });
  }

  // helpers (duplicated here so this module is self-contained)
  function _ns(lat) { return `${Math.abs(lat).toFixed(3)}°${lat < 0 ? 'S' : 'N'}`; }
  function _ew(lon) { lon = ((lon+180)%360)-180; return `${Math.abs(lon).toFixed(3)}°${lon>=0?'E':'W'}`; }

  return { init, update };
})();
