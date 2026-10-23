/* theme.js — personalização visual do site, em todas as páginas.
   1) TEMA PRONTO: o servidor coloca os temas (de templates/themes/) em window.__THEMES__ antes deste código.
      O tema escolhido fica no aparelho (vale até pra quem não tem conta) e, se a pessoa estiver logada,
      também na conta.
   2) CSS PESSOAL (só logados): vem depois do tema e ganha dele.
   - <html data-page="index|info|editor|merge|create" data-theme="coquette"> pra pessoa mirar telas/temas.
   - Abrir qualquer página com ?safe=1 no link desliga tema e CSS pessoal naquela visita.
   Tudo entra por textContent numa tag <style>: não existe como "escapar" dela. */
(function () {
  var KEY = 'user_css', TKEY = 'theme', doc = document.documentElement;
  var REG = window.__THEMES__ || {};
  var userEl = null, themeEl = null, fontsDone = {};
  var pages = { '': 'index', info: 'info', editor: 'editor', merge: 'merge', create: 'create' };
  var seg = location.pathname.replace(/^\/+|\/+$/g, '').split('/')[0];
  doc.setAttribute('data-page', window.__PAGE__ || (Object.prototype.hasOwnProperty.call(pages, seg) ? pages[seg] : (seg || 'index')));   // __PAGE__: prévias do Montador de tema (a URL delas não é a da tela)

  var safe = /[?&]safe=1(?:&|$)/.test(location.search);
  var head = function () { return document.head || doc; };

  /* ── CSS pessoal ── */
  function box() {
    if (!userEl) { userEl = document.createElement('style'); userEl.id = 'user-css'; head().appendChild(userEl); }
    return userEl;
  }
  function read() { try { return localStorage.getItem(KEY) || ''; } catch (e) { return ''; } }
  function remember(css) { try { if (css) localStorage.setItem(KEY, css); else localStorage.removeItem(KEY); } catch (e) {} }
  function apply(css) { box().textContent = css || ''; }

  /* ── Tema pronto ── */
  /* Cookie "theme": o servidor o coloca no 1º acesso de um aparelho novo (quem já tem conta), pra o tema certo
     aparecer ANTES da primeira pintura. Valores: id do tema, 'default' ou 'none' (conta sem tema escolhido = ignora o cookie). */
  function readCookie() { try { var m = document.cookie.match(/(?:^|;\s*)theme=([a-z0-9_-]{1,32})(?:;|$)/); return m ? m[1] : ''; } catch (e) { return ''; } }
  function writeCookie(v) {
    try { document.cookie = 'theme=' + (v || '') + '; path=/; max-age=' + (v ? 31536000 : 0) + '; samesite=lax' + (location.protocol === 'https:' ? '; secure' : ''); } catch (e) {}
  }
  function readTheme() {                                                                               // '', 'default' ou id
    var c = readCookie();
    if (c && c !== 'none') return c;
    try { return localStorage.getItem(TKEY) || ''; } catch (e) { return ''; }
  }
  function writeTheme(v) { try { if (v) localStorage.setItem(TKEY, v); else localStorage.removeItem(TKEY); } catch (e) {} writeCookie(v); }
  function themeStyle() {
    if (!themeEl) {
      themeEl = document.createElement('style'); themeEl.id = 'theme-css';
      var u = document.getElementById('user-css');            // o tema vem SEMPRE antes do CSS pessoal
      if (u && u.parentNode) u.parentNode.insertBefore(themeEl, u); else head().appendChild(themeEl);
    }
    return themeEl;
  }
  function loadFonts(id) {
    var t = REG[id];
    if (!t || !t.fonts || fontsDone[id]) return;
    fontsDone[id] = 1;
    var l = document.createElement('link'); l.rel = 'stylesheet'; l.href = t.fonts; head().appendChild(l);
  }
  /* ── Extras de tema (peças interativas) ──
     O CSS do tema só desenha. Quando um tema precisa de botão ou de script (ex.: o Cult apaga e derrete as velas),
     a peça mora aqui: nasce no rodapé da home quando o tema é ativado e é desmontada quando a pessoa troca de tema.
     Pra criar a de outro tema: EXTRAS.idDoTema = function (host) { ...monta o que quiser dentro de host...;
                                                                  return function () { ...desfaz tudo... }; };
     (o visual dela fica no CSS do próprio tema, e a classe do bloco é "theme-extras"). */
  var EXTRAS = {}, EARLY = {};
  var extraHost = null, extraOff = null;

  function syncExtras(id) {
    if (EARLY[id]) EARLY[id]();                       // estado que precisa existir antes da 1ª pintura (sem piscar)
    function run() {
      if (extraOff) { try { extraOff(); } catch (e) {} extraOff = null; }
      if (extraHost && extraHost.parentNode) extraHost.parentNode.removeChild(extraHost);
      extraHost = null;
      var make = id && EXTRAS[id];
      var anchor = document.querySelector('.github-badge');          // só a home tem rodapé
      if (!make || !anchor || doc.getAttribute('data-page') !== 'index') return;
      extraHost = document.createElement('div');
      extraHost.className = 'theme-extras';
      anchor.parentNode.insertBefore(extraHost, anchor);
      extraOff = make(extraHost);
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run); else run();
  }

  /* Cult: luas em fases + "apagar as velas" (lembrado no aparelho) + "derreter as velas".
     Derreter NÃO fica salvo: recarregar a página traz as velas de volta (e depois de derreter, os botões somem). */
  var CULT_KEY = 'cult_candles_out';
  EARLY.cult = function () {
    try { if (localStorage.getItem(CULT_KEY) === '1') doc.classList.add('cu-out'); } catch (e) {}
  };
  EXTRAS.cult = function (host) {
    var out = false;
    try { out = localStorage.getItem(CULT_KEY) === '1'; } catch (e) {}
    host.classList.add('cult-extras');
    host.innerHTML =
      '<div class="cult-moons" aria-hidden="true"></div>' +
      '<div class="cult-btns">' +
        '<button type="button" class="cult-btn" data-act="blow"></button>' +
        '<button type="button" class="cult-btn" data-act="melt"></button>' +
      '</div>';
    var blow = host.querySelector('[data-act="blow"]'), melt = host.querySelector('[data-act="melt"]');

    function paint() {
      var melted = doc.classList.contains('cu-melt');
      doc.classList.toggle('cu-out', out || melted);                 // derretida = apagada
      blow.textContent = out ? 'acender as velas' : 'apagar as velas';
      melt.textContent = 'derreter as velas';
    }
    host.addEventListener('click', function (e) {
      var b = e.target.closest && e.target.closest('[data-act]');
      if (!b) return;
      if (b.getAttribute('data-act') === 'blow') {
        out = !out;
        try { if (out) localStorage.setItem(CULT_KEY, '1'); else localStorage.removeItem(CULT_KEY); } catch (err) {}
        paint();
      } else {
        doc.classList.add('cu-melt'); paint();
      }
    });
    paint();

    /* Parallax do pentagrama: acompanha a inclinação do celular (o círculo vai pro lado contrário, como se estivesse mais longe).
       Android/Chrome lê direto; no iPhone o navegador exige um toque antes (pedimos no 1º toque da página).
       Respeita "reduzir movimento" e pausa com a aba escondida. */
    var tiltOff = (function () {
      var DEBUG = /[?&]tilt=debug(?:&|$)/.test(location.search), dbg = null, nEv = 0, nMo = 0;
      var reduced = !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
      if (DEBUG) {                                                // ?tilt=debug → caixinha no canto com o que o aparelho está mandando
        dbg = document.createElement('pre');
        dbg.style.cssText = 'position:fixed;left:6px;top:6px;z-index:99999;margin:0;padding:6px 8px;font:11px/1.35 monospace;color:#fff;background:rgba(0,0,0,.78);border:1px solid #ffa24a;pointer-events:none;white-space:pre';
        document.body.appendChild(dbg);
      }
      if (reduced && !DEBUG) return function () {};
      var MAX = 45, RANGE = 15, DZ = 2.5, tx = 0, ty = 0, cx = 0, cy = 0, base = null, raf = 0, on = false, got = false, probe = 0, lastG = null, lastB = null, sign = 1, centerT = 0;   // MAX = px de movimento · RANGE = graus de inclinação pra chegar no máximo
      function clamp(v) { return Math.max(-1, Math.min(1, v)); }
      function dz(d) { return d > DZ ? d - DZ : d < -DZ ? d + DZ : 0; }   // zona morta: tremidinha da mão (até ~DZ graus) não mexe o círculo
      function onTilt(e) {
        nEv++;
        if (e.gamma == null || e.beta == null) return;
        got = true;
        lastG = e.gamma; lastB = e.beta; sign = 1;
        update();
      }
      function update() {                                         // converte (leitura − centro) em deslocamento do círculo
        if (lastG == null) return;
        if (!base) base = { b: lastB, g: lastG };                 // 1ª leitura = centro provisório (o recentro abaixo corrige)
        tx = sign * clamp(dz(lastG - base.g) / RANGE) * MAX;      // inclinar pra direita → círculo vai pra direita (mesmo lado)
        ty = clamp(dz(lastB - base.b) / RANGE) * MAX;
        if (!raf) raf = requestAnimationFrame(step);
      }
      function step() {
        cx += (tx - cx) * 0.08; cy += (ty - cy) * 0.08;           // suaviza: nada de tremer
        doc.style.setProperty('--cu-tx', cx.toFixed(1) + 'px');
        doc.style.setProperty('--cu-ty', cy.toFixed(1) + 'px');
        raf = (Math.abs(tx - cx) > 0.1 || Math.abs(ty - cy) > 0.1) && !document.hidden ? requestAnimationFrame(step) : 0;
      }
      function onMotion(e) {                                      // plano B: acelerômetro (alguns aparelhos/WebViews não mandam deviceorientation)
        var a = e.accelerationIncludingGravity; nMo++;
        if (got || !a || a.x == null || a.y == null) return;
        var g = Math.asin(clamp(a.x / 9.81)) * 57.2958, b = Math.asin(clamp(a.y / 9.81)) * 57.2958;   // graus, como gamma/beta
        lastG = g; lastB = b; sign = -1;                          // no acelerômetro o sinal de x é invertido em relação ao gamma
        update();
      }
      function onMouse(e) {                                       // computador (sem sensor): o mouse faz o papel da inclinação
        tx = -((e.clientX / window.innerWidth) - 0.5) * 2 * MAX;
        ty = -((e.clientY / window.innerHeight) - 0.5) * 2 * MAX;
        if (!raf) raf = requestAnimationFrame(step);
      }
      function start() {
        if (on) return; on = true;
        window.addEventListener('deviceorientation', onTilt);
        window.addEventListener('devicemotion', onMotion);
        centerT = setInterval(function () {                       // recentra por tempo: ~3s pra o círculo voltar ao meio com o celular parado, em qualquer posição
          if (!base || lastG == null) return;
          base.g += (lastG - base.g) * 0.05; base.b += (lastB - base.b) * 0.05;
          update();
        }, 100);
        probe = setTimeout(function () {                          // 1,5s sem nenhum dado de sensor? usa o mouse (se houver)
          if (!got && window.matchMedia && matchMedia('(pointer: fine)').matches) window.addEventListener('mousemove', onMouse);
        }, 1500);
      }
      function ask() {
        document.removeEventListener('click', ask, true);
        DeviceOrientationEvent.requestPermission().then(function (r) { if (r === 'granted') { base = null; lastG = null; start(); } }).catch(function () {});   // base=null: recalibra no 1º dado depois da permissão
      }
      var dbgT = 0;
      if (DEBUG) dbgT = setInterval(function () {
        dbg.textContent = 'tilt debug v5 (mesmo lado)\n' +
          'desvio (graus): ' + (base && lastG != null ? (lastG - base.g).toFixed(1) + ' / ' + (lastB - base.b).toFixed(1) : '-') + '\n' +
          'https: ' + window.isSecureContext + '\n' +
          'reduzir movimento: ' + reduced + '\n' +
          'translate no css: ' + (window.CSS && CSS.supports && CSS.supports('translate', '1px')) + '\n' +
          'orientation eventos: ' + nEv + '\n' +
          'motion eventos: ' + nMo + '\n' +
          'tx/ty: ' + cx.toFixed(0) + ' / ' + cy.toFixed(0);
      }, 300);
      start();                                                                                                       // sempre já escuta: se o aparelho libera sozinho, funciona sem toque
      if (typeof DeviceOrientationEvent !== 'undefined' && typeof DeviceOrientationEvent.requestPermission === 'function')
        document.addEventListener('click', ask, true);                                                               // iOS (e navegadores que pedem permissão): o 1º toque libera o sensor
      return function () {
        document.removeEventListener('click', ask, true);
        window.removeEventListener('deviceorientation', onTilt);
        window.removeEventListener('mousemove', onMouse);
        window.removeEventListener('devicemotion', onMotion);
        clearTimeout(probe); clearInterval(centerT);
        if (dbg) { clearInterval(dbgT); if (dbg.parentNode) dbg.parentNode.removeChild(dbg); }
        if (raf) cancelAnimationFrame(raf);
        doc.style.removeProperty('--cu-tx'); doc.style.removeProperty('--cu-ty');
      };
    })();

    return function () {
      tiltOff();
      doc.classList.remove('cu-out', 'cu-melt');
    };
  };

  function applyTheme(id) {
    var t = id && REG[id] ? REG[id] : null;
    if (t) { loadFonts(id); doc.setAttribute('data-theme', id); } else doc.removeAttribute('data-theme');
    themeStyle().textContent = t ? t.css : '';
    syncExtras(t ? id : '');
  }
  function saveThemeOnServer(v) {
    return fetch('/api/save_theme', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ theme: v }) })
      .catch(function () {});
  }
  function isLogged() { try { return localStorage.getItem('is_logged_in') === '1'; } catch (e) { return false; } }

  var themeChanges = 0;   // quantas vezes a pessoa trocou de tema nesta página (o sync usa pra não desfazer a escolha dela)

  window.UserTheme = {
    list: function () {
      return Object.keys(REG).map(function (id) {
        var t = REG[id];
        return { id: id, name: t.name || id, blurb: t.blurb || '', swatch: t.swatch || [], nameFont: t.nameFont || '' };
      });
    },
    current: function () { var c = readTheme(); return c && c !== 'default' && REG[c] ? c : ''; },
    preload: function () { Object.keys(REG).forEach(loadFonts); },     // pra mostrar o nome na fonte do tema
    set: function (id) {
      if (id && !REG[id]) return;
      themeChanges++;
      writeTheme(id || 'default');
      applyTheme(id);
      if (isLogged()) saveThemeOnServer(id || 'default');
    }
  };

  var api = window.UserCss = {
    safe: safe,
    booted: false,
    apply: apply,
    remember: remember,
    clear: function () { apply(''); remember(''); },
    revert: function () { apply(safe ? '' : read()); },       // volta ao que está salvo (descarta a prévia)
    sync: function () {
      var seenChanges = themeChanges;
      return fetch('/api/my_css', { credentials: 'same-origin', cache: 'no-store' })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (!d.logged_in) { api.clear(); return; }          // sessão expirou
          var css = d.css || '';
          remember(css);
          if (!safe && box().textContent !== css) apply(css);
          // Se a pessoa trocou de tema enquanto a conta ainda respondia, vale a escolha dela (já foi pro servidor):
          // a resposta chegou com o tema ANTIGO e desfazia o clique (ex.: voltar pro Padrão não pegava).
          if (seenChanges !== themeChanges) return;
          // tema: a conta manda; se a conta ainda não escolheu, o do aparelho sobe pra ela
          var local = readTheme();
          if (d.theme) {
            if (d.theme !== local) { writeTheme(d.theme); if (!safe) applyTheme(d.theme === 'default' ? '' : d.theme); }
          } else if (local) { saveThemeOnServer(local); }
        })
        .catch(function () {});
    }
  };

  /* ── Boot (síncrono, antes de a página aparecer: sem piscar) ── */
  var cur = readTheme();
  if (cur && cur !== 'default' && !safe) applyTheme(cur);

  if (!isLogged() || safe) return;
  api.booted = true;
  var cached = read();
  if (cached) apply(cached);
  api.sync();
})(); 
