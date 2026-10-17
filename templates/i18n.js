/* i18n.js — idioma da INTERFACE do site (botões, avisos, rótulos). Não tem relação com os idiomas de tradução dos mods.
   - A pessoa ESCOLHE o idioma num seletor (index, ao lado dos temas). Não existe detecção automática.
   - A escolha fica num cookie "lang" (vale 1 ano). O servidor lê o cookie e coloca o idioma em window.__I18N__
     antes deste código, então a página já nasce no idioma certo, sem piscar em português.
   - Chave que falta num idioma cai no português do Brasil (pt-BR), que é o padrão e o fallback.
   Marcação no HTML (o texto em português dentro da tag é só o valor inicial; o dicionário sempre vence):
     data-i18n="chave"               troca o texto da tag
     data-i18n-html="chave"          troca o HTML da tag (só pra textos nossos, com <b>, <a>, <span>)
     data-i18n-placeholder="chave"   placeholder
     data-i18n-aria-label="chave"    aria-label
     data-i18n-title="chave"         title
     data-i18n-alt="chave"           alt
   No JS:  t('chave', { n: 3 })   ·   I18n.plural('chave', n)   ·   I18n.get('lista')  (pra listas, ex.: frases) */
(function () {
  var D = window.__I18N__ || {};
  var DEFAULT = 'pt-BR';
  var code = D.code || DEFAULT;
  var dict = D.dict || {};
  var fb = D.fallback || {};
  var langs = D.langs || [];

  function raw(key) {
    if (Object.prototype.hasOwnProperty.call(dict, key)) return dict[key];
    if (Object.prototype.hasOwnProperty.call(fb, key)) return fb[key];
    return null;
  }
  function fmt(s, vars) {
    if (!vars) return s;
    return s.replace(/\{(\w+)\}/g, function (m, k) { return vars[k] != null ? vars[k] : m; });
  }
  function t(key, vars) {
    var s = raw(key);
    if (typeof s !== 'string') return key;      // chave sem texto: mostra a própria chave (fica fácil de achar o erro)
    return fmt(s, vars);
  }
  /* Plural: procura chave.one / chave.few / chave.many / chave.other conforme a regra do idioma. */
  function plural(key, n, vars) {
    var cat = 'other';
    try { cat = new Intl.PluralRules(code).select(n); } catch (e) {}
    if (n === 0 && raw(key + '.zero') !== null) cat = 'zero';   // opcional: idiomas em que o 0 não cai no plural (ex.: pt)
    var v = { n: n };
    if (vars) for (var k in vars) v[k] = vars[k];
    var s = raw(key + '.' + cat);
    if (typeof s !== 'string') s = raw(key + '.other');
    return typeof s === 'string' ? fmt(s, v) : key;
  }
  function get(key) { return raw(key); }

  var ATTRS = [['data-i18n-placeholder', 'placeholder'], ['data-i18n-aria-label', 'aria-label'],
               ['data-i18n-title', 'title'], ['data-i18n-alt', 'alt']];
  function apply(root) {
    root = root || document;
    var els = root.querySelectorAll('[data-i18n]'), i, s;
    for (i = 0; i < els.length; i++) {
      s = raw(els[i].getAttribute('data-i18n'));
      if (typeof s === 'string') els[i].textContent = s;
    }
    els = root.querySelectorAll('[data-i18n-html]');
    for (i = 0; i < els.length; i++) {
      s = raw(els[i].getAttribute('data-i18n-html'));
      if (typeof s === 'string') els[i].innerHTML = s;
    }
    ATTRS.forEach(function (a) {
      var list = root.querySelectorAll('[' + a[0] + ']');
      for (var j = 0; j < list.length; j++) {
        var v = raw(list[j].getAttribute(a[0]));
        if (typeof v === 'string') list[j].setAttribute(a[1], v);
      }
    });
  }

  function writeCookie(v) {
    try {
      document.cookie = 'lang=' + v + '; path=/; max-age=31536000; samesite=lax' + (location.protocol === 'https:' ? '; secure' : '');
    } catch (e) {}
  }
  /* Troca o idioma: grava a escolha e recarrega, pra tudo (inclusive textos montados por JS) nascer no idioma novo. */
  function set(newCode) {
    for (var i = 0; i < langs.length; i++) {
      if (langs[i].code === newCode) { writeCookie(newCode); location.reload(); return true; }
    }
    return false;
  }
  function date(iso, opts) {
    try { return new Date(iso).toLocaleDateString(code, opts || { day: '2-digit', month: 'short', year: 'numeric' }); }
    catch (e) { return ''; }
  }

  document.documentElement.setAttribute('lang', code);
  window.I18n = { code: code, langs: langs, t: t, plural: plural, get: get, apply: apply, set: set, date: date };
  window.t = t;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { apply(document); });
  else apply(document);
})();
