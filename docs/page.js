/* page.js — VULNERATOR-AI post-boot ambient features
 * Unholy Pentest Division · 2026
 *
 * Called by boot.js via window.initPostBoot(audioElement) from dismiss().
 * Initialises:
 *   1. SSH-style connection/OODA event toasts (bottom-left)
 *   2. Audio frequency spectrum visualiser (below sys-bar)
 */

window.initPostBoot = function (audioEl) {
  initToasts();
  initVisualizer(audioEl);
  initKaraoke(audioEl);
};

/* ═══════════════════════════════════════════════════════════════
   TOAST SYSTEM — live system event notifications
   Appear every 6–18s, stack up to 4, auto-dismiss after 4s.
   Click a toast to dismiss early.
═══════════════════════════════════════════════════════════════ */
function initToasts() {
  let oodaIter = Math.floor(Math.random() * 60) + 1;

  // Message pool — cat: category label, msg: string or fn → string
  const POOL = [
    /* network */
    { cat: 'NET',    msg: 'SYN 0.0.0.0:31337 — BLOCKED'                    },
    { cat: 'NET',    msg: 'PORT SCAN: 172.16.0.5 — DETECTED'                },
    { cat: 'NET',    msg: 'OUTBOUND 443/TLS 1.3 — ALLOWED'                  },
    { cat: 'NET',    msg: 'PROBE: 10.0.0.1:22 — DENIED'                     },
    { cat: 'NET',    msg: 'REVERSE SHELL ATTEMPT — DROPPED'                  },
    { cat: 'NET',    msg: 'EXFIL ATTEMPT: 8.8.8.8:53 — BLOCKED'             },
    /* OODA loop */
    { cat: 'OODA',   msg: 'observe: 3 new attack vectors identified'         },
    { cat: 'OODA',   msg: 'orient: updating adversary threat model'          },
    { cat: 'OODA',   msg: 'decide: pivot to web application surface'         },
    { cat: 'OODA',   msg: 'act: deploying nuclei scanner'                    },
    { cat: 'OODA',   msg: () => `loop iteration #${++oodaIter}`              },
    { cat: 'OODA',   msg: 'lateral movement: 2 new pivots evaluated'         },
    /* Hermes LLM */
    { cat: 'HERMES', msg: 'new engagement task assigned'                     },
    { cat: 'HERMES', msg: 'tool: kali_run(nmap -sV -sC -T4)'                 },
    { cat: 'HERMES', msg: 'tool: kali_run(nuclei -t cves/)'                  },
    { cat: 'HERMES', msg: 'tool: mcp_playwright_goto'                        },
    { cat: 'HERMES', msg: 'tool: kali_run(sqlmap --batch)'                   },
    { cat: 'HERMES', msg: () => `confidence: ${(0.85+Math.random()*0.14).toFixed(2)}` },
    { cat: 'HERMES', msg: 'reasoning chain: 7 steps completed'               },
    { cat: 'HERMES', msg: 'handoff: findings queued for report'               },
    /* Kali tool output */
    { cat: 'KALI',   msg: 'nmap: 3 open ports on target'                     },
    { cat: 'KALI',   msg: 'nuclei: CVE-2024-xxxx — confirmed'                },
    { cat: 'KALI',   msg: 'gobuster: /admin — HTTP 403'                      },
    { cat: 'KALI',   msg: 'sqlmap: injection point found'                    },
    { cat: 'KALI',   msg: 'ffuf: 14 paths enumerated'                        },
    { cat: 'KALI',   msg: 'prowler: 2 misconfigurations'                     },
    /* System */
    { cat: 'SYS',    msg: 'kali-mcp: heartbeat OK'                           },
    { cat: 'SYS',    msg: () => `mem: ${(1.8+Math.random()*1.4).toFixed(1)} GB / 8 GB` },
    { cat: 'SYS',    msg: 'playwright: new browser session opened'            },
    { cat: 'SYS',    msg: 'tunnel: keepalive packet sent'                    },
    { cat: 'SYS',    msg: 'AES-256-GCM session key rotated'                  },
  ];

  // Build container
  const container = document.createElement('div');
  container.id = 'toast-container';
  document.body.appendChild(container);

  function showToast() {
    const entry = POOL[Math.floor(Math.random() * POOL.length)];
    const msg   = typeof entry.msg === 'function' ? entry.msg() : entry.msg;

    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.innerHTML =
      `<span class="toast__cat">[${entry.cat}]</span>` +
      `<span class="toast__msg">${msg}</span>` +
      `<div class="toast__bar"></div>`;

    container.appendChild(toast);

    // Cap at 4 visible toasts
    const visible = [...container.querySelectorAll('.toast:not(.toast--exit)')];
    if (visible.length > 4) removeToast(visible[0]);

    const timer = setTimeout(() => removeToast(toast), 4200);
    toast.addEventListener('click', () => { clearTimeout(timer); removeToast(toast); });
  }

  function removeToast(toast) {
    if (toast.classList.contains('toast--exit')) return;
    toast.classList.add('toast--exit');
    toast.addEventListener('animationend', () => toast.remove(), { once: true });
  }

  // First toast after 2.5s, then random interval
  setTimeout(showToast, 2500);
  (function sched() {
    setTimeout(() => { showToast(); sched(); }, 6000 + Math.random() * 12000);
  })();
}

/* ═══════════════════════════════════════════════════════════════
   AUDIO VISUALISER — time-domain waveform inserted below the hero
   in the normal document flow. Click to pause / resume.
═══════════════════════════════════════════════════════════════ */
function initVisualizer(audioEl) {
  if (!audioEl) return;

  const heroEl = document.querySelector('.hero');
  if (!heroEl) return;

  const canvas = document.createElement('canvas');
  canvas.id = 'visualizer';
  canvas.setAttribute('aria-label', 'Audio waveform — click to pause or resume');
  canvas.setAttribute('role', 'button');
  canvas.setAttribute('tabindex', '0');

  // Insert after the hero, in normal document flow
  heroEl.insertAdjacentElement('afterend', canvas);

  const ctx = canvas.getContext('2d');

  function resize() {
    canvas.width  = canvas.offsetWidth;
    canvas.height = 80;
  }
  // Wait for layout before reading offsetWidth
  requestAnimationFrame(() => {
    resize();
    window.addEventListener('resize', resize, { passive: true });
  });

  /* Play / pause toggle */
  function togglePlayback() {
    if (audioEl.paused) audioEl.play().catch(() => {});
    else                audioEl.pause();
  }
  canvas.addEventListener('click',   togglePlayback);
  canvas.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') togglePlayback(); });

  try {
    const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const source   = audioCtx.createMediaElementSource(audioEl);
    const analyser = audioCtx.createAnalyser();

    // Smaller fftSize + lower smoothing = snappier, more responsive waveform
    analyser.fftSize               = 1024;
    analyser.smoothingTimeConstant = 0.55;

    source.connect(analyser);
    analyser.connect(audioCtx.destination);

    const bufLen  = analyser.fftSize; // 1024 samples
    const dataArr = new Uint8Array(bufLen);

    // Amplification factor — MP3 tracks rarely fill the full 0-255 range,
    // so we boost the deviation from centre to get visible oscillation
    const AMPLIFY = 4.2;

    (function draw() {
      requestAnimationFrame(draw);

      const W = canvas.width  || canvas.offsetWidth;
      const H = canvas.height || 80;
      if (!W) return;

      ctx.clearRect(0, 0, W, H);

      /* ── Paused state ── */
      if (audioEl.paused) {
        ctx.shadowBlur  = 4;
        ctx.shadowColor = 'rgba(140, 30, 30, 0.5)';
        ctx.strokeStyle = 'rgba(110, 24, 24, 0.5)';
        ctx.lineWidth   = 1.5;
        ctx.beginPath();
        ctx.moveTo(0, H / 2);
        ctx.lineTo(W, H / 2);
        ctx.stroke();

        ctx.shadowBlur    = 0;
        ctx.fillStyle     = 'rgba(199, 90, 90, 0.7)';
        ctx.font          = '11px ui-monospace, "JetBrains Mono", monospace';
        ctx.textAlign     = 'center';
        ctx.letterSpacing = '0.2em';
        ctx.fillText('‖  PAUSED  —  CLICK TO RESUME', W / 2, H / 2 + 4);
        return;
      }

      analyser.getByteTimeDomainData(dataArr);

      const sliceW  = W / bufLen;
      const center  = H / 2;

      /* ── Filled waveform area ── */
      ctx.beginPath();
      ctx.moveTo(0, center);
      for (let i = 0; i < bufLen; i++) {
        // Normalise to -1…1, then amplify
        const norm = ((dataArr[i] / 128.0) - 1) * AMPLIFY;
        const y    = Math.max(0, Math.min(H, center + norm * center));
        ctx.lineTo(i * sliceW, y);
      }
      ctx.lineTo(W, center);
      ctx.closePath();
      const grd = ctx.createLinearGradient(0, 0, 0, H);
      grd.addColorStop(0,   'rgba(210, 50, 50, 0.50)');
      grd.addColorStop(0.5, 'rgba(160, 30, 30, 0.20)');
      grd.addColorStop(1,   'rgba(100, 15, 15, 0.05)');
      ctx.fillStyle = grd;
      ctx.fill();

      /* ── Glowing waveform line ── */
      ctx.shadowBlur  = 20;
      ctx.shadowColor = 'rgba(255, 50, 50, 0.80)';
      ctx.strokeStyle = '#ff3838';
      ctx.lineWidth   = 2;
      ctx.lineJoin    = 'round';
      ctx.beginPath();
      for (let i = 0; i < bufLen; i++) {
        const norm = ((dataArr[i] / 128.0) - 1) * AMPLIFY;
        const y    = Math.max(0, Math.min(H, center + norm * center));
        i === 0 ? ctx.moveTo(0, y) : ctx.lineTo(i * sliceW, y);
      }
      ctx.stroke();

      /* ── Faint mirrored reflection ── */
      ctx.shadowBlur  = 6;
      ctx.shadowColor = 'rgba(200, 35, 35, 0.25)';
      ctx.strokeStyle = 'rgba(180, 32, 32, 0.22)';
      ctx.lineWidth   = 1;
      ctx.beginPath();
      for (let i = 0; i < bufLen; i++) {
        const norm = ((dataArr[i] / 128.0) - 1) * AMPLIFY;
        const yr   = Math.max(0, Math.min(H, center - norm * center)); // mirrored
        i === 0 ? ctx.moveTo(0, yr) : ctx.lineTo(i * sliceW, yr);
      }
      ctx.stroke();

      /* ── "click to pause" hint ── */
      ctx.shadowBlur    = 0;
      ctx.fillStyle     = 'rgba(90, 20, 20, 0.50)';
      ctx.font          = '10px ui-monospace, monospace';
      ctx.textAlign     = 'right';
      ctx.letterSpacing = '0.1em';
      ctx.fillText('▶ click to pause', W - 14, 14);

    })();

  } catch (e) {
    canvas.style.display = 'none';
  }
}

/* ═══════════════════════════════════════════════════════════════
   KARAOKE — synchronized lyrics above the waveform
   One line at a time, fade in → hold → fade out.
   Adjust OFFSET (seconds) to shift all lyrics forward/backward.
═══════════════════════════════════════════════════════════════ */
function initKaraoke(audioEl) {
  if (!audioEl) return;

  // ── Global offset: positive = lyrics appear later, negative = earlier ──
  const OFFSET = 0;

  // ── Auto-synced via Whisper anchors (107.7 BPM, 249s) ──────────
  const LYRICS = [
    { t:  63.92, text: 'Scan the dark, the signal\'s clear', s: 'verse' },
    { t:  66.15, text: 'Hermes rises, death is near',         s: 'verse' },
    { t:  68.38, text: 'Kali loaded, blades are drawn',       s: 'verse' },
    { t:  70.61, text: 'Playwright crawls before the dawn',   s: 'verse' },
    { t:  72.83, text: 'ATT&CK maps the kill-chain line',     s: 'verse' },
    { t:  75.06, text: 'OODA loop begins the crime',          s: 'verse' },
    { t:  77.68, text: 'No UI, no fucking gate',              s: 'prechorus' },
    { t:  79.91, text: 'Four containers seal your fate',      s: 'prechorus' },
    { t:  82.14, text: 'Direct attack, pure intent',          s: 'prechorus' },
    { t:  84.37, text: 'Authorized target\u2026 no relent',  s: 'prechorus' },
    { t:  85.22, text: 'VULNERATOR!',                         s: 'chorus' },
    { t:  87.54, text: 'Break the wall!',                     s: 'chorus' },
    { t:  89.86, text: 'VULNERATOR!',                         s: 'chorus' },
    { t:  92.18, text: 'Hear the call!',                      s: 'chorus' },
    { t:  94.50, text: 'Hermes brain, Kali blade',            s: 'chorus' },
    { t:  96.81, text: 'We infiltrate, we never fade',        s: 'chorus' },
    { t:  99.13, text: 'VULNERATOR!',                         s: 'chorus' },
    { t: 101.45, text: 'Rise and strike!',                    s: 'chorus' },
    { t: 103.77, text: 'VULNERATOR!',                         s: 'chorus' },
    { t: 106.08, text: 'Own the night!',                      s: 'chorus' },
    { t: 122.08, text: 'Nmap whispers, nuclei screams',       s: 'verse' },
    { t: 124.31, text: 'Ffuf digs through broken dreams',     s: 'verse' },
    { t: 126.54, text: 'Sqlmap cuts, gobuster crawls',        s: 'verse' },
    { t: 128.77, text: 'Bloodhound maps the domain walls',    s: 'verse' },
    { t: 130.99, text: 'Scope is set, the hunt is real',      s: 'verse' },
    { t: 133.22, text: 'Every finding is made of steel',      s: 'verse' },
    { t: 134.54, text: 'Minimal code, maximum speed',         s: 'prechorus' },
    { t: 136.77, text: 'No middleman, pure need',             s: 'prechorus' },
    { t: 139.00, text: 'Docker fortress, silent war',         s: 'prechorus' },
    { t: 141.23, text: 'The agent walks the darkest floor',   s: 'prechorus' },
    { t: 143.95, text: 'VULNERATOR!',                         s: 'chorus' },
    { t: 146.27, text: 'Break the wall!',                     s: 'chorus' },
    { t: 148.59, text: 'VULNERATOR!',                         s: 'chorus' },
    { t: 150.91, text: 'Hear the call!',                      s: 'chorus' },
    { t: 153.22, text: 'Hermes brain, Kali blade',            s: 'chorus' },
    { t: 155.54, text: 'We infiltrate, we never fade',        s: 'chorus' },
    { t: 157.86, text: 'VULNERATOR!',                         s: 'chorus' },
    { t: 160.18, text: 'Rise and strike!',                    s: 'chorus' },
    { t: 162.49, text: 'VULNERATOR!',                         s: 'chorus' },
    { t: 164.81, text: 'Own the night!',                      s: 'chorus' },
    { t: 170.08, text: 'Observe!',                            s: 'bridge' },
    { t: 171.47, text: 'Orient!',                             s: 'bridge' },
    { t: 172.87, text: 'Decide!',                             s: 'bridge' },
    { t: 174.26, text: 'ACT!',                                s: 'bridge' },
    { t: 175.65, text: 'Observe!',                            s: 'bridge' },
    { t: 177.04, text: 'Orient!',                             s: 'bridge' },
    { t: 178.44, text: 'Decide!',                             s: 'bridge' },
    { t: 179.83, text: 'ACT!',                                s: 'bridge' },
  ];

  // Apply offset
  const lines = LYRICS.map(l => ({ ...l, t: l.t + OFFSET }));

  // ── Wait for waveform canvas to exist then insert zone after it ──
  function insertZone() {
    const canvas = document.getElementById('visualizer');
    if (!canvas) { requestAnimationFrame(insertZone); return; }

    const zone = document.createElement('div');
    zone.id = 'karaoke-zone';

    const lineEl = document.createElement('div');
    lineEl.id = 'karaoke-line';
    zone.appendChild(lineEl);

    canvas.insertAdjacentElement('afterend', zone);
    startTracking(lineEl);
  }
  requestAnimationFrame(insertZone);

  function startTracking(lineEl) {
    let lastIdx = -1;
    let fadeOutTimer = null;

    function showLine(idx) {
      const entry = lines[idx];
      clearTimeout(fadeOutTimer);

      lineEl.className = 'kl kl--' + entry.s;
      lineEl.textContent = entry.text;
      lineEl.classList.add('kl--in');
      lineEl.classList.remove('kl--out');

      // Compute hold time until next line (min 0.8s, max 5s)
      const nextT = idx + 1 < lines.length ? lines[idx + 1].t : entry.t + 4;
      const hold  = Math.max(800, Math.min(5000, (nextT - entry.t - 0.6) * 1000));

      fadeOutTimer = setTimeout(() => {
        lineEl.classList.add('kl--out');
        lineEl.classList.remove('kl--in');
      }, hold);
    }

    audioEl.addEventListener('timeupdate', () => {
      const t = audioEl.currentTime;
      let idx = -1;
      for (let i = 0; i < lines.length; i++) {
        if (lines[i].t <= t) idx = i;
        else break;
      }
      if (idx !== lastIdx) {
        lastIdx = idx;
        if (idx >= 0) showLine(idx);
        else {
          clearTimeout(fadeOutTimer);
          lineEl.className = 'kl';
          lineEl.textContent = '';
        }
      }
    });

    audioEl.addEventListener('seeked', () => { lastIdx = -1; });
  }
}
