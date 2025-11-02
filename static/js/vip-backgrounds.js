// static/js/vip-backgrounds.js
// VIP Backgrounds Canvas Engine (Gold / Star / Galaxy)
// - Один canvas на каждый .profile-background
// - Нулевые зависимости, бережный rAF, DPR<=2, pause on hidden, reduce-motion aware

(function () {
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduce) return;

  const containers = document.querySelectorAll('.profile-background');
  if (!containers.length) return;

  // Общие утилиты
  const TAU = Math.PI * 2;
  const rnd = (a, b) => a + Math.random() * (b - a);
  const clampDPR = () => Math.max(1, Math.min(2, window.devicePixelRatio || 1));

  // Один обработчик видимости на документ — ставим флаг паузы
  let paused = document.hidden;
  document.addEventListener('visibilitychange', () => {
    paused = document.hidden;
  });

  // Фабрики сцен — возвращают объект { init(), draw(dt) }
  function makeGoldScene(ctx, cvs, getDpr) {
    const scene = {
      count: 80,
      parts: [],
      init() {
        this.parts.length = 0;
        const dpr = getDpr();
        for (let i = 0; i < this.count; i++) {
          this.parts.push({
            x: rnd(0, cvs.width),
            y: rnd(0, cvs.height),
            r: rnd(0.7 * dpr, 1.6 * dpr),
            vx: rnd(0.03, 0.08) * dpr,
            vy: rnd(0.02, 0.06) * dpr,
            a: rnd(0.4, 0.95),
            t: rnd(0, 1000),
          });
        }
      },
      draw(dt) {
        ctx.globalCompositeOperation = 'lighter';
        for (const p of this.parts) {
          p.x += p.vx * dt;
          p.y += p.vy * dt;
          if (p.x > cvs.width + 10) p.x = -10;
          if (p.y > cvs.height + 10) p.y = -10;
          p.t += dt;

          const flicker = 0.5 + 0.5 * Math.sin(p.t * 0.002 + p.x * 0.001);
          const alpha = Math.min(1, p.a * flicker);

          const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.r * 2.2);
          g.addColorStop(0, `rgba(255,240,200,${0.85 * alpha})`);
          g.addColorStop(0.5, `rgba(240,200,110,${0.55 * alpha})`);
          g.addColorStop(1, `rgba(0,0,0,0)`);
          ctx.fillStyle = g;
          ctx.beginPath();
          ctx.arc(p.x, p.y, p.r * 2.2, 0, TAU);
          ctx.fill();
        }
        ctx.globalCompositeOperation = 'source-over';
      },
    };
    return scene;
  }

  function makeStarScene(ctx, cvs, getDpr) {
    const scene = {
      smallCount: 140,
      meteorCount: 6,
      small: [],
      meteors: [],
      _spawnMeteor: null,
      init() {
        const dpr = getDpr();
        this.small.length = 0;
        this.meteors.length = 0;

        for (let i = 0; i < this.smallCount; i++) {
          this.small.push({
            x: Math.random() * cvs.width,
            y: Math.random() * cvs.height,
            r: (0.8 + Math.random() * 0.6) * dpr,
            tw: 0.3 + Math.random() * 0.9,
            ph: Math.random() * TAU,
          });
        }

        const spawnMeteor = () => {
          const fromTop = Math.random() < 0.6;
          const startX = fromTop ? Math.random() * cvs.width : -40 * dpr;
          const startY = fromTop ? -30 * dpr : Math.random() * cvs.height * 0.35;

          const speed = (0.35 + Math.random() * 0.35) * dpr;
          const angle = Math.PI / 2 + Math.PI / 6 + Math.random() * 0.4; // ~100–140°
          const vx = Math.cos(angle) * speed;
          const vy = Math.sin(angle) * speed;

          return {
            x: startX,
            y: startY,
            vx,
            vy,
            life: 1200 + Math.random() * 1000,
            maxLife: 1200 + Math.random() * 1000,
            len: 50 * dpr + Math.random() * 40 * dpr,
            w: 1.2 * dpr + Math.random() * 0.8 * dpr,
          };
        };

        for (let i = 0; i < this.meteorCount; i++) {
          const m = spawnMeteor();
          m.life *= Math.random(); // рассинхрон
          this.meteors.push(m);
        }

        this._spawnMeteor = spawnMeteor;
      },
      draw(dt) {
        const now = performance.now();
        const dpr = getDpr();
        const drift = Math.sin(now * 0.00005) * 0.05 * dpr;

        // мелкие звезды
        for (const s of this.small) {
          const a = 0.45 + 0.45 * Math.sin(s.ph + now * 0.001 * s.tw);
          ctx.fillStyle = `rgba(255,255,255,${a})`;
          ctx.beginPath();
          ctx.arc(s.x + drift, s.y, s.r, 0, TAU);
          ctx.fill();
        }

        // метеоры
        ctx.save();
        ctx.globalCompositeOperation = 'lighter';
        for (let i = 0; i < this.meteors.length; i++) {
          const m = this.meteors[i];
          m.x += m.vx * dt;
          m.y += m.vy * dt;
          m.life -= dt;

          const headAlpha = Math.max(0, m.life / m.maxLife) * 0.9 + 0.1;
          const headGrad = ctx.createRadialGradient(m.x, m.y, 0, m.x, m.y, m.w * 3.5);
          headGrad.addColorStop(0, `rgba(255,255,255,${headAlpha})`);
          headGrad.addColorStop(1, `rgba(0,0,0,0)`);
          ctx.fillStyle = headGrad;
          ctx.beginPath();
          ctx.arc(m.x, m.y, m.w * 3.5, 0, TAU);
          ctx.fill();

          const tx = m.x - m.vx * m.len;
          const ty = m.y - m.vy * m.len;
          const tailGrad = ctx.createLinearGradient(m.x, m.y, tx, ty);
          tailGrad.addColorStop(0.0, `rgba(255,255,255,${0.55 * headAlpha})`);
          tailGrad.addColorStop(0.2, `rgba(210,220,255,${0.35 * headAlpha})`);
          tailGrad.addColorStop(1.0, `rgba(0,0,0,0)`);
          ctx.strokeStyle = tailGrad;
          ctx.lineWidth = m.w;
          ctx.beginPath();
          ctx.moveTo(m.x, m.y);
          ctx.lineTo(tx, ty);
          ctx.stroke();

          const off =
            m.x > cvs.width + 60 * dpr || m.y > cvs.height + 60 * dpr || m.life <= 0;
          if (off) this.meteors[i] = this._spawnMeteor();
        }
        ctx.restore();
      },
    };
    return scene;
  }

  function makeGalaxyScene(ctx, cvs, getDpr) {
    const scene = {
      starsCount: 130,
      stars: [],
      blobs: [],
      init() {
        const dpr = getDpr();
        this.stars.length = 0;
        this.blobs.length = 0;

        for (let i = 0; i < this.starsCount; i++) {
          this.stars.push({
            x: Math.random() * cvs.width,
            y: Math.random() * cvs.height,
            r: (0.8 + Math.random() * 0.8) * dpr,
            tw: 0.25 + Math.random() * 0.7,
            ph: Math.random() * TAU,
          });
        }

        const palette = [
          { c1: [210, 170, 255], c2: [120, 30, 180] }, // violets
          { c1: [255, 140, 210], c2: [180, 70, 200] }, // magenta-violet
          { c1: [160, 120, 255], c2: [110, 80, 200] }, // cool violet
        ];
        const blobCount = 5;
        for (let i = 0; i < blobCount; i++) {
          const p = palette[i % palette.length];
          this.blobs.push({
            x: Math.random() * cvs.width,
            y: Math.random() * cvs.height,
            r: (260 + Math.random() * 220) * dpr,
            c1: p.c1,
            c2: p.c2,
            tw: 0.8 + Math.random() * 0.6,
            ph: Math.random() * TAU,
            dx: (Math.random() * 0.06 - 0.03) * dpr,
            dy: (Math.random() * 0.06 - 0.03) * dpr,
          });
        }
      },
      draw(dt) {
        const now = performance.now();

        // туманности
        ctx.save();
        ctx.globalCompositeOperation = 'lighter';
        for (const b of this.blobs) {
          const pulse = 1 + 0.02 * Math.sin(b.ph + now * 0.001 * b.tw);
          const R = b.r * pulse;

          b.x += b.dx * dt;
          b.y += b.dy * dt;
          if (b.x < -R) b.x = cvs.width + R;
          if (b.x > cvs.width + R) b.x = -R;
          if (b.y < -R) b.y = cvs.height + R;
          if (b.y > cvs.height + R) b.y = -R;

          const g = ctx.createRadialGradient(b.x, b.y, 0, b.x, b.y, R);
          const c1 = `rgba(${b.c1[0]},${b.c1[1]},${b.c1[2]},0.22)`;
          const c2 = `rgba(${b.c2[0]},${b.c2[1]},${b.c2[2]},0.12)`;
          g.addColorStop(0.0, c1);
          g.addColorStop(0.55, c2);
          g.addColorStop(1.0, 'rgba(0,0,0,0)');

          ctx.fillStyle = g;
          ctx.beginPath();
          ctx.arc(b.x, b.y, R, 0, TAU);
          ctx.fill();
        }
        ctx.restore();

        // звёзды
        for (const s of this.stars) {
          const a = 0.45 + 0.45 * Math.sin(s.ph + now * 0.001 * s.tw);
          ctx.fillStyle = `rgba(255,255,255,${a})`;
          ctx.beginPath();
          ctx.arc(s.x, s.y, s.r, 0, TAU);
          ctx.fill();
        }
      },
    };
    return scene;
  }

  // Инициализация для каждого контейнера
  containers.forEach((container) => {
    // Определяем режим
    let mode = 'none';
    if (container.classList.contains('bg--vip-gold')) mode = 'gold';
    else if (container.classList.contains('bg--vip-star')) mode = 'star';
    else if (container.classList.contains('bg--vip-galaxy')) mode = 'galaxy';
    if (mode === 'none') return;

    // Создаём canvas
    const cvs = document.createElement('canvas');
    cvs.className = 'vip-canvas';
    const ctx = cvs.getContext('2d', { alpha: true });
    container.appendChild(cvs);

    // DPR и размеры
    let dpr = clampDPR();
    function fit() {
      const rect = container.getBoundingClientRect();
      cvs.style.width = rect.width + 'px';
      cvs.style.height = rect.height + 'px';
      dpr = clampDPR();
      cvs.width = Math.max(1, Math.floor(rect.width * dpr));
      cvs.height = Math.max(1, Math.floor(rect.height * dpr));
    }
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(container);

    // Геттер DPR для сцен
    const getDpr = () => dpr;

    // Сцена
    const scene =
      mode === 'gold'
        ? makeGoldScene(ctx, cvs, getDpr)
        : mode === 'star'
        ? makeStarScene(ctx, cvs, getDpr)
        : makeGalaxyScene(ctx, cvs, getDpr);

    scene.init();

    // Луп
    let lastT = performance.now();
    let raf = null;

    function tick(now) {
      if (paused) {
        raf = requestAnimationFrame(tick);
        return;
      }
      const dt = Math.min(33, now - lastT); // ограничим шаг
      lastT = now;

      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, cvs.width, cvs.height);

      scene.draw(dt);

      raf = requestAnimationFrame(tick);
    }
    raf = requestAnimationFrame(tick);

    // Пересоздание при серьёзном изменении размеров/DPR
    let lastW = cvs.width,
      lastH = cvs.height,
      lastD = dpr;
    function maybeReinit() {
      if (lastW !== cvs.width || lastH !== cvs.height || lastD !== dpr) {
        scene.init();
        lastW = cvs.width;
        lastH = cvs.height;
        lastD = dpr;
      }
    }
    window.addEventListener(
      'resize',
      () => {
        fit();
        maybeReinit();
      },
      { passive: true }
    );
  });
})();
