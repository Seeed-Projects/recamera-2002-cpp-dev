/* reCamera Live — fixed host-side viewer.
 *
 * Video path:   GET /api/video  -> [u32 len][u64 ts][Annex-B access unit]...
 * Results path: GET /api/events -> SSE {type:"meta"|"state"|"log"|...}
 *
 * The browser never talks to the camera. JMuxer remuxes H.264 Annex-B into
 * fMP4 for Media Source Extensions; the detection overlay is drawn on a
 * canvas using the model->frame transform declared by the demo.
 */
(() => {
  'use strict';

  const TOKEN = new URLSearchParams(location.search).get('token') || '';
  const STATIC_PREVIEW = new URLSearchParams(location.search).has('static');
  const $ = (id) => document.getElementById(id);
  const els = {
    video: $('video'), overlay: $('overlay'), wrap: $('video-wrap'), badge: $('video-badge'),
    dot: $('status-dot'), demo: $('demo-name'), device: $('m-device'),
    fps: $('m-fps'), bitrate: $('m-bitrate'), latency: $('m-latency'), detFps: $('m-det-fps'), drops: $('m-drops'),
    videoInfo: $('video-info'), sessionInfo: $('session-info'),
    params: $('params'), paramsState: $('params-state'),
    logList: $('log-list'), logFollow: $('log-follow'), logClear: $('log-clear'),
    toast: $('toast'), notice: $('implemented-notice'),
  };

  let state = null;
  let hello = null;
  let latestMeta = null;
  let paramSchema = {};
  let params = {};
  let schemaKey = '';
  const pendingParams = new Set();
  let jmuxer = null;
  let videoAbort = null;
  let reconnectTimer = null;
  let videoRetry = 0;
  let waitingIdr = true;
  let spsPps = [];
  let lastVideoTs = null;
  let frameIntervals = [];
  let lastVideoDataAt = 0;
  let videoWatchdog = null;
  let hasVideoData = false;
  let drawPending = false;
  let eventSource = null;
  const logKeys = new Set();

  const api = (path) => {
    if (!TOKEN) return path;
    return path + (path.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(TOKEN);
  };

  // ---- small helpers -----------------------------------------------------
  function toast(message, kind = '') {
    els.toast.textContent = message;
    els.toast.className = 'toast show ' + kind;
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => { els.toast.className = 'toast'; }, 3500);
  }

  function setConnected(connected) {
    els.dot.className = 'dot ' + (connected ? 'live' : 'wait');
  }

  function setBadge(text, hidden = false) {
    els.badge.textContent = text;
    els.badge.classList.toggle('hidden', hidden);
  }

  function showUnauthorized() {
    setBadge('Unauthorized: open this page with a ?token link');
    els.device.textContent = 'Unauthorized';
    setConnected(false);
    toast('Unauthorized: open this page with a ?token URL', 'bad');
  }

  function concat(a, b) {
    if (!a.length) return b;
    const out = new Uint8Array(a.length + b.length);
    out.set(a, 0);
    out.set(b, a.length);
    return out;
  }

  async function postJson(path, body) {
    const res = await fetch(api(path), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    let data = {};
    try { data = await res.json(); } catch (e) { /* no body */ }
    return { res, data };
  }

  // ---- H.264 helpers -----------------------------------------------------
  function nalTypes(data) {
    const types = [];
    for (let i = 0; i + 3 < data.length;) {
      if (data[i] === 0 && data[i + 1] === 0 && data[i + 2] === 1) {
        const t = i + 3;
        if (t < data.length) types.push(data[t] & 0x1f);
        i = t + 1;
      } else {
        i += 1;
      }
    }
    return types;
  }

  function containsIdr(data) { return nalTypes(data).includes(5); }
  function containsParamSet(data) {
    return nalTypes(data).some((t) => t === 7 || t === 8);
  }

  // ---- video -------------------------------------------------------------
  function ensureJmuxer() {
    if (jmuxer || typeof window.JMuxer !== 'function') return jmuxer;
    jmuxer = new window.JMuxer({
      node: els.video,
      mode: 'video',
      flushingTime: 150,
      fps: 30,
      clearBuffer: true,
      debug: false,
      onError: () => {
        setBadge('Decode error, waiting to reconnect…');
        scheduleVideoReconnect(true);
      },
    });
    return jmuxer;
  }

  function resetJmuxer() {
    if (jmuxer) {
      try { jmuxer.destroy(); } catch (e) { /* noop */ }
      jmuxer = null;
    }
    try {
      els.video.removeAttribute('src');
      els.video.load();
    } catch (e) { /* noop */ }
  }

  function startVideoWatchdog() {
    if (videoWatchdog) clearInterval(videoWatchdog);
    videoWatchdog = setInterval(() => {
      if (!videoAbort) return;
      const idle = Date.now() - lastVideoDataAt;
      // Initial keyframe wait can legitimately take up to one GOP (~6 s).
      const limit = hasVideoData ? 4000 : 9000;
      if (idle > limit) {
        clearInterval(videoWatchdog);
        videoWatchdog = null;
        scheduleVideoReconnect(true);
      }
    }, 1000);
  }

  function scheduleVideoReconnect(immediate = false) {
    if (videoAbort) {
      try { videoAbort.abort(); } catch (e) { /* noop */ }
      videoAbort = null;
    }
    videoRetry = Math.min(videoRetry + 1, 6);
    const delay = immediate ? 200 : Math.min(500 * videoRetry, 3000);
    clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(() => {
      resetJmuxer();
      connectVideo();
    }, delay);
  }

  function connectVideo() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (videoAbort) {
      try { videoAbort.abort(); } catch (e) { /* noop */ }
    }
    const controller = new AbortController();
    videoAbort = controller;
    lastVideoDataAt = Date.now();
    hasVideoData = false;
    startVideoWatchdog();
    waitingIdr = true;
    spsPps = [];
    lastVideoTs = null;
    frameIntervals = [];
    setBadge(videoRetry ? `Reconnecting video… (${videoRetry})` : 'Waiting for video…');
    if (!ensureJmuxer()) {
      setBadge('Player failed to load');
      return;
    }

    fetch(api('/api/video'), { signal: controller.signal, cache: 'no-store' })
      .then(async (res) => {
        if (res.status === 401 || res.status === 403) {
          showUnauthorized();
          return;
        }
        if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
        setBadge('Waiting for keyframe…');
        const reader = res.body.getReader();
        let buf = new Uint8Array(0);
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          lastVideoDataAt = Date.now();
          hasVideoData = true;
          buf = concat(buf, value);
          while (buf.length >= 12) {
            const view = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
            const len = view.getUint32(0, true);
            const ts = Number(view.getBigUint64(4, true));
            if (buf.length < 12 + len) break;
            const accessUnit = buf.subarray(12, 12 + len);
            feedAccessUnit(accessUnit, ts);
            buf = buf.subarray(12 + len);
          }
        }
        throw new Error('stream ended');
      })
      .catch((err) => {
        if (err && err.name === 'AbortError') return;
        setBadge('Video disconnected, reconnecting…');
        scheduleVideoReconnect(false);
      });
  }

  function feedAccessUnit(data, ts) {
    if (!data.length) return;
    if (waitingIdr) {
      if (!containsIdr(data)) {
        if (containsParamSet(data)) {
          spsPps.push(data.slice());
          if (spsPps.length > 2) spsPps.shift();
        }
        return;
      }
      waitingIdr = false;
      videoRetry = 0;
      if (spsPps.length) {
        let total = data.length;
        for (const item of spsPps) total += item.length;
        const joined = new Uint8Array(total);
        let offset = 0;
        for (const item of spsPps) { joined.set(item, offset); offset += item.length; }
        joined.set(data, offset);
        data = joined;
        spsPps = [];
      }
    }

    const nominal = hello && hello.video && hello.video.fps ? 1000 / hello.video.fps : 1000 / 30;
    let duration = nominal;
    if (lastVideoTs !== null) {
      const delta = ts - lastVideoTs;
      if (delta >= 5 && delta <= 250) {
        frameIntervals.push(delta);
        if (frameIntervals.length > 61) frameIntervals.shift();
      }
      if (frameIntervals.length) {
        duration = frameIntervals.reduce((a, b) => a + b, 0) / frameIntervals.length;
      }
    }
    lastVideoTs = ts;
    try {
      jmuxer.feed({ video: data, duration });
      const p = els.video.play();
      if (p && p.catch) p.catch(() => { /* autoplay may need a gesture; muted usually allows */ });
      setBadge('', true);
    } catch (e) {
      setBadge('Decode failed, waiting to reconnect…');
      scheduleVideoReconnect(true);
    }
  }

  // ---- events ------------------------------------------------------------
  function connectEvents() {
    if (eventSource) eventSource.close();
    eventSource = new EventSource(api('/api/events'));
    eventSource.onmessage = (ev) => {
      let payload;
      try { payload = JSON.parse(ev.data); } catch (e) { return; }
      handleEvent(payload);
    };
    eventSource.onerror = () => {
      setConnected(false);
      // EventSource reconnects automatically.
    };
  }

  function handleEvent(payload) {
    switch (payload.type) {
      case 'state':
        applyState(payload.state);
        break;
      case 'hello':
        hello = payload.hello || hello;
        updateVideoInfo();
        // The service merges this schema with demo.json and ships the result
        // as state.param_schema. Applying the raw device copy here would make
        // the panel alternate between two schemas on every hello (full
        // rebuild = visible flash), so only seed the UI when nothing is shown.
        if (!schemaKey && hello && hello.params_schema) {
          applySchema(hello.params_schema);
        }
        break;
      case 'meta':
        latestMeta = payload.meta;
        noteMetaTiming();
        updateLatency();
        requestDraw();
        break;
      case 'params':
        params = Object.assign({}, params, payload.params || {});
        updateParamControls();
        els.paramsState.textContent = payload.ack && payload.ack.ok ? 'Applied' : 'Send failed';
        toast(payload.ack && payload.ack.ok ? 'Parameter applied' : 'Failed to send parameter',
          payload.ack && payload.ack.ok ? 'ok' : 'bad');
        break;
      case 'log':
        appendLog(payload.log);
        break;
      case 'snapshot':
        if (state) state.video.snapshots = (state.video.snapshots || 0) + 1;
        break;
      case 'action':
        if (payload.result) {
          appendLog({
            at: new Date().toISOString(),
            level: payload.result.ok ? 'info' : 'error',
            msg: `[${payload.action}] ${payload.result.ok ? 'PASS' : 'FAIL'} ${payload.result.stdout || payload.result.stderr || ''}`,
          });
          toast(`${payload.action}: ${payload.result.ok ? 'PASS' : 'FAIL'}`,
            payload.result.ok ? 'ok' : 'bad');
        }
        break;
      case 'error':
        toast(payload.error || 'Service error', 'bad');
        break;
      default:
        break;
    }
  }

  function applyState(next) {
    if (!next) return;
    state = next;
    hello = next.hello || hello;
    els.notice.classList.toggle('hidden', next.implemented !== false || next.implemented_observed === true);
    setConnected(!!next.connected);
    els.demo.textContent = next.demo || '—';
    els.device.textContent = next.device_addr
      ? `${next.device_addr}${next.connected ? '' : ' · Offline'}`
      : 'Waiting for device…';
    els.fps.textContent = (next.video && next.video.fps) ? next.video.fps : '—';
    const bitrateKbps = next.stats && next.stats.bitrate_kbps
      ? Number(next.stats.bitrate_kbps)
      : 0;
    els.bitrate.textContent = bitrateKbps > 0 ? (bitrateKbps / 1000).toFixed(1) : '—';
    els.drops.textContent = next.fragment_drops || 0;
    els.sessionInfo.textContent = next.session_dir ? next.session_dir.split(/[\\/]/).pop() : 'Session not started';

    if (next.param_schema && Object.keys(next.param_schema).length) {
      applySchema(next.param_schema);
    }
    const merged = Object.assign({}, params, next.params || {});
    for (const name of pendingParams) {
      if (name in params) merged[name] = params[name];
    }
    params = merged;
    updateParamControls();

    (next.logs || []).forEach(appendLog);
    if (next.connected && !els.video.videoWidth) setBadge('Waiting for keyframe…');
    updateVideoInfo();
    updateLatency();
    requestDraw();
  }

  function updateVideoInfo() {
    const video = (hello && hello.video) || null;
    if (video && video.width && video.height) {
      const codec = (video.codec || 'h264').toUpperCase();
      els.videoInfo.textContent = `${codec} ${video.width}×${video.height} @ ${video.fps || '?'}fps`;
    } else if (state && state.video) {
      els.videoInfo.textContent = `H.264 · ${state.video.frames} frames`;
    }
  }

  let lastMetaWall = 0;
  let metaIntervalEma = 0;

  function noteMetaTiming() {
    const wall = performance.now();
    if (lastMetaWall) {
      const dt = wall - lastMetaWall;
      if (dt > 5 && dt < 5000) {
        metaIntervalEma = metaIntervalEma ? metaIntervalEma * 0.8 + dt * 0.2 : dt;
        if (els.detFps) els.detFps.textContent = (1000 / metaIntervalEma).toFixed(1);
      }
    }
    lastMetaWall = wall;
  }

  function updateLatency() {
    const meta = latestMeta;
    if (!meta) { els.latency.textContent = '—'; return; }
    /* age_ms is device-side: queue/VPSS wait + inference. Host clock minus
     * device clock measures clock skew, not latency, so it is not used. */
    const age = Number(meta.age_ms);
    els.latency.textContent =
      Number.isFinite(age) && age >= 0 ? String(Math.round(age)) : '—';
  }

  // ---- parameter controls ------------------------------------------------
  // Order-insensitive canonical form. The device advertises each spec as
  // {type,min,max,step,default,label} while demo.json spells the same fields
  // alphabetically. A plain JSON.stringify() comparison treats the two as
  // different schemas and rebuilds every control (a visible flash) on each
  // hello event, then again on the next state event.
  function canonicalJson(value) {
    if (Array.isArray(value)) {
      return '[' + value.map(canonicalJson).join(',') + ']';
    }
    if (value && typeof value === 'object') {
      return '{' + Object.keys(value).sort()
        .map((key) => JSON.stringify(key) + ':' + canonicalJson(value[key]))
        .join(',') + '}';
    }
    return JSON.stringify(value);
  }

  function applySchema(schema) {
    const key = canonicalJson(schema);
    if (key === schemaKey) return;
    schemaKey = key;
    paramSchema = schema || {};
    renderParams();
  }

  function renderParams() {
    els.params.innerHTML = '';
    const entries = Object.entries(paramSchema);
    if (!entries.length) {
      const p = document.createElement('p');
      p.className = 'hint';
      p.textContent = 'demo.json does not declare live.params.';
      els.params.appendChild(p);
      return;
    }
    for (const [name, spec] of entries) {
      if (!spec || typeof spec !== 'object') continue;
      const row = document.createElement('div');
      row.className = 'param';
      const label = document.createElement('label');
      label.textContent = spec.label || name;
      label.htmlFor = `p-${name}`;
      const output = document.createElement('output');
      output.id = `o-${name}`;
      row.append(label, output);

      const current = params[name] !== undefined ? params[name] : spec.default;
      const type = spec.type || 'number';
      let control;
      if (type === 'number' && spec.min !== undefined && spec.max !== undefined) {
        control = document.createElement('input');
        control.type = 'range';
        control.min = spec.min;
        control.max = spec.max;
        control.step = spec.step || 0.01;
        control.value = current !== undefined ? current : spec.min;
        output.textContent = Number(control.value).toFixed(stepDecimals(control.step));
        control.addEventListener('input', () => {
          output.textContent = Number(control.value).toFixed(stepDecimals(control.step));
          queueParam(name, Number(control.value));
        });
      } else if (type === 'boolean') {
        control = document.createElement('input');
        control.type = 'checkbox';
        control.checked = !!current;
        control.className = 'checkbox';
        output.textContent = control.checked ? 'On' : 'Off';
        control.addEventListener('change', () => {
          output.textContent = control.checked ? 'On' : 'Off';
          queueParam(name, control.checked);
        });
      } else if (type === 'enum') {
        control = document.createElement('select');
        for (const option of spec.enum || []) {
          const opt = document.createElement('option');
          opt.value = option;
          opt.textContent = option;
          control.appendChild(opt);
        }
        control.value = current;
        output.textContent = String(control.value);
        control.addEventListener('change', () => {
          output.textContent = control.value;
          queueParam(name, control.value);
        });
      } else if (type === 'string') {
        control = document.createElement('input');
        control.type = 'text';
        control.value = current !== undefined ? current : '';
        output.textContent = control.value;
        control.addEventListener('change', () => {
          output.textContent = control.value;
          queueParam(name, control.value);
        });
      } else {
        control = document.createElement('input');
        control.type = 'number';
        control.min = spec.min !== undefined ? spec.min : '';
        control.max = spec.max !== undefined ? spec.max : '';
        control.step = spec.step || (type === 'integer' ? 1 : 'any');
        control.value = current !== undefined ? current : '';
        output.textContent = String(control.value);
        control.addEventListener('change', () => {
          output.textContent = control.value;
          const value = type === 'integer' ? parseInt(control.value, 10) : Number(control.value);
          queueParam(name, value);
        });
      }
      control.id = `p-${name}`;
      row.appendChild(control);
      els.params.appendChild(row);
    }
  }

  function stepDecimals(step) {
    const text = String(step || '');
    const dot = text.indexOf('.');
    return dot >= 0 ? text.length - dot - 1 : 0;
  }

  function updateParamControls() {
    for (const [name, value] of Object.entries(params)) {
      const control = document.getElementById(`p-${name}`);
      const output = document.getElementById(`o-${name}`);
      if (!control || pendingParams.has(name)) continue;
      if (control.type === 'checkbox') {
        control.checked = !!value;
        if (output) output.textContent = control.checked ? 'On' : 'Off';
      } else if (document.activeElement !== control) {
        control.value = value;
        if (output) {
          output.textContent = control.type === 'range'
            ? Number(value).toFixed(stepDecimals(control.step))
            : String(value);
        }
      }
    }
  }

  const paramTimers = new Map();
  function queueParam(name, value) {
    pendingParams.add(name);
    params[name] = value;
    els.paramsState.textContent = 'Sending…';
    clearTimeout(paramTimers.get(name));
    paramTimers.set(name, setTimeout(() => sendParam(name, value), 120));
  }

  async function sendParam(name, value) {
    try {
      const { res, data } = await postJson('/api/params', { params: { [name]: value } });
      if (!res.ok || !data.ok) {
        const detail = (data.ack && data.ack.detail) || data.error ||
          (Array.isArray(data.details) ? data.details.join('; ') : `HTTP ${res.status}`);
        pendingParams.delete(name);
        els.paramsState.textContent = 'Failed';
        toast(`Failed to send parameter ${name}: ${detail}`, 'bad');
        return;
      }
      if (data.params) params = Object.assign({}, params, data.params);
      pendingParams.delete(name);
      els.paramsState.textContent = 'Applied';
      updateParamControls();
    } catch (err) {
      pendingParams.delete(name);
      els.paramsState.textContent = 'Network error';
      toast(`Failed to send parameter: ${err.message}`, 'bad');
    }
  }

  // ---- overlay -----------------------------------------------------------
  function requestDraw() {
    if (drawPending) return;
    drawPending = true;
    requestAnimationFrame(drawOverlay);
  }

  function drawOverlay() {
    drawPending = false;
    const canvas = els.overlay;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const vw = els.video.videoWidth || (hello && hello.video && hello.video.width) || 1920;
    const vh = els.video.videoHeight || (hello && hello.video && hello.video.height) || 1080;
    if (canvas.width !== vw || canvas.height !== vh) {
      canvas.width = vw;
      canvas.height = vh;
    }
    ctx.clearRect(0, 0, vw, vh);
    const meta = latestMeta;
    const dets = meta && Array.isArray(meta.dets) ? meta.dets : [];
    const shapes = meta
      ? (Array.isArray(meta.shapes) ? meta.shapes
        : (Array.isArray(meta.annotations) ? meta.annotations : []))
      : [];
    if (!dets.length && !shapes.length) return;

    const frameW = Number(meta.frame_w || vw) || vw;
    const frameH = Number(meta.frame_h || vh) || vh;
    const sx = vw / frameW;
    const sy = vh / frameH;
    const line = Math.max(2, Math.round(vw / 640));
    ctx.lineWidth = line;
    ctx.font = `${Math.max(12, Math.round(vw / 80))}px ui-monospace, Menlo, Consolas, monospace`;
    ctx.textBaseline = 'bottom';

    // Shapes first (ROI, lane polylines), detections last.
    drawShapes(ctx, shapes, meta, frameW, frameH, vw, sx, sy);

    dets.slice(0, 200).forEach((det, index) => {
      const box = mapBox(det, meta, frameW, frameH);
      if (!box) return;
      const color = palette(det.target !== undefined ? det.target : index);
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      const x = box.x1 * sx;
      const y = box.y1 * sy;
      const w = Math.max(1, (box.x2 - box.x1) * sx);
      const h = Math.max(1, (box.y2 - box.y1) * sy);
      ctx.strokeRect(x, y, w, h);

      const target = det.target !== undefined ? det.target : index;
      const labels = meta.labels || (hello && hello.labels) || [];
      const label = labels[target] || det.label || `class ${target}`;
      const score = typeof det.score === 'number' ? ` ${(det.score * 100).toFixed(0)}%` : '';
      const text = label + score;
      const textW = ctx.measureText(text).width + 10;
      const textH = Math.max(16, Math.round(vw / 55));
      const ty = y - textH - 2 >= 0 ? y - textH - 2 : y + 2;
      ctx.fillStyle = 'rgba(0,0,0,0.68)';
      ctx.fillRect(x, ty, textW, textH);
      ctx.fillStyle = color;
      ctx.fillText(text, x + 5, ty + textH - 3);
    });
  }

  function mapBox(det, meta, frameW, frameH) {
    const modelW = Number(meta.model_w || (hello && hello.model && hello.model.width) || 640);
    const modelH = Number(meta.model_h || (hello && hello.model && hello.model.height) || 640);
    const pre = meta.preprocess || (hello && hello.model && hello.model.preprocess) || 'stretch';
    const scale = Number(meta.scale || (hello && hello.model && hello.model.scale) || 1) || 1;
    const padX = Number(meta.pad_x || (hello && hello.model && hello.model.pad_x) || 0);
    const padY = Number(meta.pad_y || (hello && hello.model && hello.model.pad_y) || 0);
    const cx = Number(det.x || 0);
    const cy = Number(det.y || 0);
    const bw = Number(det.w || 0);
    const bh = Number(det.h || 0);
    let fx;
    let fy;
    let fw;
    let fh;
    if (pre === 'letterbox') {
      fx = (cx * modelW - padX) / scale;
      fy = (cy * modelH - padY) / scale;
      fw = (bw * modelW) / scale;
      fh = (bh * modelH) / scale;
    } else if (pre === 'crop') {
      const cropW = modelW / scale;
      const cropH = modelH / scale;
      const offX = (frameW - cropW) / 2;
      const offY = (frameH - cropH) / 2;
      fx = offX + cx * cropW;
      fy = offY + cy * cropH;
      fw = bw * cropW;
      fh = bh * cropH;
    } else {
      fx = cx * frameW;
      fy = cy * frameH;
      fw = bw * frameW;
      fh = bh * frameH;
    }
    return {
      x1: Math.max(0, fx - fw / 2),
      y1: Math.max(0, fy - fh / 2),
      x2: Math.min(frameW, fx + fw / 2),
      y2: Math.min(frameH, fy + fh / 2),
    };
  }

  const MAX_SHAPES = 16;
  const MAX_SHAPE_POINTS = 80;

  function drawShapes(ctx, shapes, meta, frameW, frameH, vw, sx, sy) {
    const labels = meta.labels || (hello && hello.labels) || [];
    shapes.slice(0, MAX_SHAPES).forEach((shape, index) => {
      if (!shape || typeof shape !== 'object') return;
      const kind = String(shape.kind || shape.type || 'polyline').toLowerCase();
      const space = shape.space === 'model' ? 'model' : 'frame';
      const raw = Array.isArray(shape.pts) ? shape.pts
        : (Array.isArray(shape.points) ? shape.points : []);
      const pts = raw.slice(0, MAX_SHAPE_POINTS)
        .filter((p) => Array.isArray(p) && p.length >= 2 &&
          Number.isFinite(Number(p[0])) && Number.isFinite(Number(p[1])))
        .map((p) => {
          const q = mapPoint(Number(p[0]), Number(p[1]), space, meta, frameW, frameH);
          return { x: q.x * sx, y: q.y * sy };
        })
        .filter((p) => Number.isFinite(p.x) && Number.isFinite(p.y));
      if (!pts.length) return;

      const target = shape.target !== undefined ? shape.target : index;
      const color = safeColor(shape.color || shape.stroke || palette(target));
      const vh = frameH * sy;
      const width = Math.max(1, Math.min(Math.max(8, vw * 0.03),
        (Number(shape.width) || 2) * (vh / 1080)));
      const label = shape.label || shape.text || labels[target] || '';
      const score = typeof shape.score === 'number'
        ? ` ${(shape.score * 100).toFixed(0)}%` : '';
      const text = label + score;
      const closed = shape.closed === true || kind === 'polygon';

      ctx.save();
      ctx.lineJoin = 'round';
      ctx.lineCap = 'round';

      if (kind === 'point' || kind === 'points') {
        const radius = Math.max(3, Number(shape.radius) || Math.round(vw / 380));
        ctx.fillStyle = color;
        pts.forEach((p) => {
          ctx.beginPath();
          ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
          ctx.fill();
        });
      } else if (kind === 'text') {
        const size = Math.max(12, Number(shape.size) || Math.round(vw / 60));
        drawChip(ctx, shape.text || shape.label || text, pts[0].x, pts[0].y, color, size);
      } else if (!closed || pts.length >= 3) {
        // Halo first so lane paint stays readable on bright asphalt.
        ctx.strokeStyle = 'rgba(0,0,0,0.5)';
        ctx.lineWidth = width * 2.2;
        strokePath(ctx, pts, closed);
        if (closed) {
          ctx.fillStyle = color;
          ctx.globalAlpha = 0.16;
          ctx.fill();
          ctx.globalAlpha = 1;
        }
        ctx.strokeStyle = color;
        ctx.lineWidth = width;
        strokePath(ctx, pts, closed);
      }

      if (text && kind !== 'text') {
        const anchor = pts[pts.length - 1];
        drawChip(ctx, text, anchor.x, anchor.y, color,
          Math.max(12, Math.round(vw / 70)));
      }
      ctx.restore();
    });
  }

  function strokePath(ctx, pts, closed) {
    ctx.beginPath();
    pts.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
    if (closed) ctx.closePath();
    ctx.stroke();
  }

  function drawChip(ctx, text, x, y, color, size) {
    const value = String(text || '');
    if (!value) return;
    ctx.save();
    ctx.font = `${size}px ui-monospace, Menlo, Consolas, monospace`;
    ctx.textBaseline = 'bottom';
    const width = ctx.measureText(value).width + 10;
    const chipY = y - size - 4 >= 0 ? y - size - 4 : y + 4;
    ctx.fillStyle = 'rgba(0,0,0,0.68)';
    ctx.fillRect(x, chipY, width, size + 6);
    ctx.fillStyle = color;
    ctx.fillText(value, x + 5, chipY + size);
    ctx.restore();
  }

  function mapPoint(x, y, space, meta, frameW, frameH) {
    if (space !== 'model') {
      return { x: x * frameW, y: y * frameH };
    }
    const modelW = Number(meta.model_w || (hello && hello.model && hello.model.width) || 640);
    const modelH = Number(meta.model_h || (hello && hello.model && hello.model.height) || 640);
    const pre = meta.preprocess || (hello && hello.model && hello.model.preprocess) || 'stretch';
    const scale = Number(meta.scale || (hello && hello.model && hello.model.scale) || 1) || 1;
    const padX = Number(meta.pad_x || (hello && hello.model && hello.model.pad_x) || 0);
    const padY = Number(meta.pad_y || (hello && hello.model && hello.model.pad_y) || 0);
    if (pre === 'letterbox') {
      return { x: (x * modelW - padX) / scale, y: (y * modelH - padY) / scale };
    }
    if (pre === 'crop') {
      const cropW = modelW / scale;
      const cropH = modelH / scale;
      return {
        x: (frameW - cropW) / 2 + x * cropW,
        y: (frameH - cropH) / 2 + y * cropH,
      };
    }
    return { x: x * frameW, y: y * frameH };
  }

  function safeColor(value) {
    if (typeof value === 'string' && value.length <= 64) {
      if (/^#[0-9a-f]{3,8}$/i.test(value)) return value;
      if (/^(rgb|rgba|hsl|hsla)\(/i.test(value)) return value;
    }
    return palette(0);
  }

  function palette(index) {
    const colors = ['#3ddc97', '#4cc2ff', '#ffc857', '#ff6b6b', '#b28dff', '#ff9f43', '#2ee6d6', '#f368e0'];
    return colors[Math.abs(Number(index) || 0) % colors.length];
  }

  // ---- logs --------------------------------------------------------------
  function appendLog(entry) {
    if (!entry || !entry.msg) return;
    const key = `${entry.at || ''}|${entry.level || ''}|${entry.msg}`;
    if (logKeys.has(key)) return;
    logKeys.add(key);
    if (logKeys.size > 1000) logKeys.clear();
    const li = document.createElement('li');
    li.className = entry.level || 'info';
    const time = document.createElement('span');
    time.className = 't';
    time.textContent = formatTime(entry.at);
    const msg = document.createElement('span');
    msg.textContent = entry.msg;
    li.append(time, msg);
    els.logList.appendChild(li);
    while (els.logList.children.length > 500) els.logList.removeChild(els.logList.firstChild);
    if (els.logFollow.checked) els.logList.scrollTop = els.logList.scrollHeight;
  }

  function formatTime(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value).slice(0, 8);
    return date.toLocaleTimeString('en-US', { hour12: false });
  }

  // ---- boot --------------------------------------------------------------
  async function loadState() {
    try {
      const res = await fetch(api('/api/state'), { cache: 'no-store' });
      if (res.status === 401 || res.status === 403) {
        showUnauthorized();
        return false;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      applyState(await res.json());
      return true;
    } catch (err) {
      setConnected(false);
      return true;
    }
  }

  function syncAspect() {
    const vw = els.video.videoWidth || (hello && hello.video && hello.video.width);
    const vh = els.video.videoHeight || (hello && hello.video && hello.video.height);
    if (vw && vh) {
      els.wrap.style.aspectRatio = `${vw} / ${vh}`;
    }
    requestDraw();
  }

  function bindUi() {
    els.logClear.addEventListener('click', () => {
      els.logList.innerHTML = '';
      logKeys.clear();
    });
    els.video.addEventListener('loadedmetadata', syncAspect);
    els.video.addEventListener('resize', syncAspect);
  }

  async function boot() {
    bindUi();
    ensureJmuxer();
    const authorized = await loadState();
    if (!authorized) {
      return;
    }
    if (!STATIC_PREVIEW) {
      connectVideo();
      connectEvents();
      setInterval(loadState, 2000);
    } else {
      setBadge('Static preview mode');
    }
    requestAnimationFrame(drawOverlay);
  }

  boot();
})();