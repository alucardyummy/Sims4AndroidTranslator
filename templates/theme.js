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
  doc.setAttribute('data-page', Object.prototype.hasOwnProperty.call(pages, seg) ? pages[seg] : (seg || 'index'));

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
  function readTheme() { try { return localStorage.getItem(TKEY) || ''; } catch (e) { return ''; } }   // '', 'default' ou id
  function writeTheme(v) { try { if (v) localStorage.setItem(TKEY, v); else localStorage.removeItem(TKEY); } catch (e) {} }
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
    return function () {
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
      return fetch('/api/my_css', { credentials: 'same-origin', cache: 'no-store' })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (!d.logged_in) { api.clear(); return; }          // sessão expirou
          var css = d.css || '';
          remember(css);
          if (!safe && box().textContent !== css) apply(css);
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
