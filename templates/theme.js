/* theme.js — aplica o CSS personalizado de quem está logado, em todas as páginas.
   - Marca <html data-page="index|info|editor|merge|create"> pra pessoa mirar uma tela só.
   - Aplica o CSS guardado no aparelho na hora (sem piscar) e confere com o servidor.
   - Abrir qualquer página com ?safe=1 no link desliga o CSS pessoal naquela visita.
   O CSS entra por textContent numa tag <style>: não existe como "escapar" dela. */
(function () {
  var KEY = 'user_css', doc = document.documentElement, el = null;
  var pages = { '': 'index', info: 'info', editor: 'editor', merge: 'merge', create: 'create' };
  var seg = location.pathname.replace(/^\/+|\/+$/g, '').split('/')[0];
  doc.setAttribute('data-page', Object.prototype.hasOwnProperty.call(pages, seg) ? pages[seg] : (seg || 'index'));

  var safe = /[?&]safe=1(?:&|$)/.test(location.search);

  function box() {
    if (!el) { el = document.createElement('style'); el.id = 'user-css'; (document.head || doc).appendChild(el); }
    return el;
  }
  function read() { try { return localStorage.getItem(KEY) || ''; } catch (e) { return ''; } }
  function remember(css) { try { if (css) localStorage.setItem(KEY, css); else localStorage.removeItem(KEY); } catch (e) {} }
  function apply(css) { box().textContent = css || ''; }

  var api = window.UserCss = {
    safe: safe,
    booted: false,
    apply: apply,
    remember: remember,
    clear: function () { apply(''); remember(''); },
    revert: function () { apply(safe ? '' : read()); },   // volta ao que está salvo (descarta a prévia)
    sync: function () {
      return fetch('/api/my_css', { credentials: 'same-origin', cache: 'no-store' })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (!d.logged_in) { api.clear(); return; }      // sessão expirou
          var css = d.css || '';
          remember(css);
          if (!safe && box().textContent !== css) apply(css);
        })
        .catch(function () {});
    }
  };

  var logged = false;
  try { logged = localStorage.getItem('is_logged_in') === '1'; } catch (e) {}
  if (!logged || safe) return;

  api.booted = true;
  var cached = read();
  if (cached) apply(cached);
  api.sync();
})();
