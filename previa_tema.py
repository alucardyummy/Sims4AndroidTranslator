"""Prévias do Montador de tema.

Em vez de desenhar uma telinha falsa, o montador abre as PÁGINAS DE VERDADE (mesmo <head>, mesmo CSS,
mesmas classes) dentro de um iframe. Aqui a gente pega o HTML real de templates/, tira todos os scripts
(nada de login, rede, redirecionamento) e enche a tela com dados de exemplo no lugar do que o JavaScript
da página colocaria. Assim, se o CSS de uma tela mudar, a prévia muda junto, sem ninguém lembrar de
atualizar um desenho paralelo.

A rota fica em app.py:  /montar-tema/previa/<tela>?m=<janela>
"""
import os
import re
from html import escape

TELAS = {
    "index": "index.html",
    "editor": "editor.html",
    "merge": "merge.html",
    "create": "create.html",
    "info": "info.html",
}

# janelas que dá pra abrir por cima de cada tela (?m=...)
JANELAS = {
    "index": {"auth-modal", "profile-modal", "updates-modal", "custom-alert"},
    "info": {"lang"},
}

_SCRIPT = re.compile(r"<script\b[^>]*>.*?</script>", re.S | re.I)
_TRASH = re.compile(r"const TRASH_SVG = '(<svg.*?</svg>)'", re.S)


def _tira_scripts(texto):
    return _SCRIPT.sub("", texto)


def _entre(texto, ini, fim, novo):
    """Troca o miolo entre dois marcadores (mantém os marcadores). Se não achar, devolve o texto como está."""
    a = texto.find(ini)
    if a < 0:
        return texto
    a += len(ini)
    b = texto.find(fim, a)
    if b < 0:
        return texto
    return texto[:a] + novo + texto[b:]


def _classe_ativa(texto, ident):
    """<div class="modal-overlay" id="x"  ->  <div class="modal-overlay active" id="x" """
    return re.sub(r'(<div class="(?:modal|alert)-overlay)("\s+id="%s")' % re.escape(ident), r"\1 active\2", texto, count=1)


# ───────────────────────── dados de exemplo ─────────────────────────

def _home(corpo, trash, modal):
    cards = ""
    for nome, langs in (("DevDev3_Scent", "ENG_US → POR_BR"), ("janu", "POR_BR → POR_BR"), ("meu_mod_novo", "ENG_US → POR_BR")):
        cards += (
            '<div class="save-item visible"><div class="sel-circle" style="display:none"></div>'
            '<div class="save-info"><div class="save-title">%s</div><div class="save-meta">%s</div></div>'
            '<div class="save-actions" style="display:flex"><button class="btn-icon">editar</button>'
            '<button class="btn-icon delete">%s</button></div></div>' % (escape(nome), langs, trash)
        )
    corpo = corpo.replace('<div class="saves-list" id="saves-list"></div>', '<div class="saves-list" id="saves-list">%s</div>' % cards, 1)
    corpo = corpo.replace('id="saves-area" style="display:none;', 'id="saves-area" style="display:block;', 1)
    corpo = corpo.replace('<div id="skeleton-area" style="', '<div id="skeleton-area" style="display:none;', 1)
    corpo = re.sub(r'(id="greeting"[^>]*>)(</button>)', r"\1Sul sul, você!\2", corpo, count=1)
    # Novidades
    itens = ""
    for tit, txt, data in (("Montador de tema novo", "Agora dá pra montar o seu tema com cores, fontes e vidro.", "22/10"),
                           ("Fundo com padrão", "Bolinhas, estrelas, corações e o seu próprio desenho.", "12/10")):
        itens += ('<div class="update-item"><div class="sel-circle"></div><div><div class="update-item-title">%s</div>'
                  '<div class="update-item-body">%s</div><div class="update-item-date">%s</div></div>'
                  '<button class="update-item-delete">%s</button></div>' % (tit, txt, data, trash))
    corpo = corpo.replace('<div class="updates-empty">Carregando...</div>', itens, 1)
    corpo = corpo.replace('<p class="alert-msg" id="custom-alert-msg"></p>', '<p class="alert-msg" id="custom-alert-msg">Salvo com sucesso!</p>', 1)
    if modal in JANELAS["index"]:
        corpo = _classe_ativa(corpo, modal)
    return corpo


def _editor(corpo):
    linhas = (
        ("0xb35c9de7", "For being in rut", "Por estar no cio", False, False),
        ("0x9512c192", "Those flirty pheromones aren’t for them this time… and…", "Esses feromônios flertantes não são para eles desta vez… e…", True, False),
        ("0xd0baad08", "{0.SimFirstName} is jealous", '<span class="ph">{0.SimFirstName}</span> está com ciúmes', False, True),
        ("0x901e0b1c", "Like Floral Scent", "Gosta de Perfume Floral", False, False),
        ("0xe94de7b3", "Like Sweet Scent", "", False, False),
        ("0xfd3d43a3", "They’re flirting… with someone else", "Estão flertando… com outra pessoa", False, False),
    )
    html = ""
    for i, (chave, orig, trad, fav, ph) in enumerate(linhas):
        orig_h = escape(orig).replace("{0.SimFirstName}", '<span class="ph">{0.SimFirstName}</span>')
        trad_txt = re.sub(r"<[^>]+>", "", trad)
        html += (
            '<div class="string-row" id="srow-%d"><span class="row-num">%d</span><div class="selection-check"></div>'
            '<div><div class="key-hex">%s</div><div class="original-text"><button class="copy-btn">❐</button>'
            '<span class="original-text-inner clamped">%s</span></div></div>'
            '<div class="edit-container"><div class="ta-wrap"><button class="fav-btn%s">★</button>'
            '<span class="note-dot" style="display:none"></span><button class="expand-arrow-btn">▾</button>'
            '<textarea class="edit-textarea ta-collapsed" rows="1">%s</textarea>'
            '<div class="ta-backdrop ta-collapsed">%s</div></div>'
            '<div class="kebab-wrap"><div class="kebab-btn">⋮</div></div></div></div>'
            % (i, i + 1, chave, orig_h, " active" if fav else "", escape(trad_txt), trad or "")
        )
    corpo = _entre(corpo, '<div class="strings-list" id="strings-list">', '<div class="selection-toolbar"', html + "</div>\n  ")
    corpo = corpo.replace('id="top-sub">—</div>', 'id="top-sub">ENG_US → POR_BR</div>', 1)
    corpo = corpo.replace('id="count-badge" onclick="toggleUntranslatedFilter()" title="Clique para filtrar não traduzidas">—</div>',
                          'id="count-badge">5/14</div>', 1)
    corpo = corpo.replace('id="timer-badge">0:00</div>', 'id="timer-badge">0:08</div>', 1)
    corpo = corpo.replace('id="filename-input" placeholder="nome"', 'id="filename-input" placeholder="nome" value="meumod"', 1)
    for ident in ("translate-all-btn", "json-mode-btn", "save-btn"):
        corpo = re.sub(r'(id="%s"[^>]*?)\s+disabled' % ident, r"\1", corpo, count=1)
    return corpo


def _info(corpo, modal):
    corpo = corpo.replace('id="file-name">—</div>', 'id="file-name">MEUMOD.package</div>', 1)
    corpo = corpo.replace('id="instance-value">—</div>', 'id="instance-value">0x0002D0A1</div>', 1)
    corpo = corpo.replace('id="group-value">—</div>', 'id="group-value">0x00000000</div>', 1)
    corpo = corpo.replace('<div id="stbl-section" style="display:none">', '<div id="stbl-section" style="display:block">', 1)
    ops = ""
    for i, (hx, grp, lg) in enumerate((("0x220557DA", "00000000", "ENG_US"), ("0x3F2A91C4", "00000000", "ENG_US"))):
        ops += ('<div class="stbl-option%s"><div class="stbl-option-left"><div class="stbl-option-hex">%s</div>'
                '<div class="stbl-option-meta">group: %s <span class="stbl-lang-badge">%s</span></div></div>'
                '<div class="stbl-option-check">%s</div></div>' % (" selected" if i == 0 else "", hx, grp, lg, "✓" if i == 0 else ""))
    corpo = corpo.replace('<div class="stbl-selector" id="stbl-selector"></div>', '<div class="stbl-selector" id="stbl-selector">%s</div>' % ops, 1)
    langs = ("ENG_US", "FRE_FR", "GER_DE", "ITA_IT", "POR_BR", "SPA_ES")
    lista = "".join('<div class="lang-option%s"><span>%s</span><span class="lang-option-check">✓</span></div>'
                    % (" selected" if l == "POR_BR" else "", l) for l in langs)
    corpo = corpo.replace('<div id="lang-list"></div>', '<div id="lang-list">%s</div>' % lista, 1)
    if modal == "lang":
        corpo = corpo.replace('class="lang-dropdown" id="lang-dropdown"', 'class="lang-dropdown open open-active" id="lang-dropdown"', 1)
    return corpo


def _merge(corpo):
    itens = ""
    for i, (nome, tam) in enumerate((("mod_cabelo.package", "2,4 MB"), ("mod_roupas.package", "310 KB"), ("mod_poses.package", "88 KB"))):
        itens += ('<div class="file-item"><span class="file-item-index">%d</span><span class="file-item-name">%s</span>'
                  '<span class="file-item-size">%s</span><button class="file-item-remove">✕</button></div>' % (i + 1, nome, tam))
    corpo = corpo.replace('<div class="file-list" id="file-list"></div>', '<div class="file-list" id="file-list">%s</div>' % itens, 1)
    corpo = corpo.replace('<div class="file-count" id="file-count"></div>', '<div class="file-count" id="file-count">3 arquivos · 2,8 MB</div>', 1)
    corpo = corpo.replace('class="search-wrap" id="search-wrap"', 'class="search-wrap visible" id="search-wrap"', 1)
    corpo = re.sub(r'(id="merge-btn"[^>]*?)\s+disabled', r"\1", corpo, count=1)
    return corpo


def _create(corpo):
    def cartao(i, titulo, aberto):
        return ('<div class="card%s"><div class="card-header"><span class="card-index">%d</span>'
                '<span class="card-title">%s</span><span class="card-chevron">▾</span>'
                '<button class="card-remove">✕</button></div>%s</div>'
                % (" expanded" if aberto else "", i, titulo,
                   '<div class="card-body"><div class="field"><div class="field-label">Nome interno (tuning name)</div>'
                   '<input type="text" placeholder="meu_mod:trait_nome" value="meu_mod:trait_criativo"></div>'
                   '<div class="field"><div class="field-label">Nome de exibição (aparece no jogo)</div>'
                   '<input type="text" placeholder="Ex: Criativo" value="Criativo"></div></div>' if aberto else ""))
    corpo = corpo.replace('<div class="card-list" id="trait-list"></div>',
                          '<div class="card-list" id="trait-list">%s%s</div>' % (cartao(1, "Criativo", True), cartao(2, "Sonhador", False)), 1)
    corpo = corpo.replace('<div class="card-list" id="social-list"></div>',
                          '<div class="card-list" id="social-list">%s</div>' % cartao(1, "Elogiar o cabelo", False), 1)
    corpo = corpo.replace('placeholder="ex: meu_mod"', 'placeholder="ex: meu_mod" value="meu_mod"', 1)
    return corpo


# ───────────────────────── montagem ─────────────────────────

_PREVIA_JS = """<script>
(function(){
  var h = document.documentElement;
  h.classList.add('pre-has-saves');%s
  /* prévia: nada clica, nada navega (os scripts da página nem existem aqui) */
  ['click','submit','dblclick','contextmenu'].forEach(function(t){
    document.addEventListener(t, function(e){ e.preventDefault(); e.stopPropagation(); }, true);
  });
})();
</script>"""


def montar_previa(tela, modal, template_dir):
    """Devolve o HTML da prévia, ou None se a tela não existir."""
    nome = TELAS.get(tela)
    if not nome:
        return None
    with open(os.path.join(template_dir, nome), encoding="utf-8") as f:
        texto = f.read()
    m = _TRASH.search(texto)
    trash = m.group(1) if m else ""

    corte = texto.find("<body")
    if corte < 0:
        return None
    cabeca, corpo = texto[:corte], texto[corte:]
    cabeca = _tira_scripts(cabeca)
    corpo = _tira_scripts(corpo)

    if modal not in JANELAS.get(tela, set()) and modal != "carregando":
        modal = ""

    if tela == "index":
        corpo = _home(corpo, trash, modal)
    elif tela == "editor":
        corpo = _editor(corpo)
    elif tela == "info":
        corpo = _info(corpo, modal)
    elif tela == "merge":
        corpo = _merge(corpo)
    elif tela == "create":
        corpo = _create(corpo)

    extra = "\n  h.classList.add('google-returning');" if (tela == "index" and modal == "carregando") else ""
    topo = '<script>window.__PAGE__=%r;</script>\n<script src="/theme.js"></script>\n' % tela
    cabeca = cabeca.replace("</head>", topo + "</head>", 1) if "</head>" in cabeca else cabeca + topo
    # a rolagem fica; a barra de rolagem some pra não roubar largura da telinha
    cabeca = cabeca.replace("</head>", "<style>html::-webkit-scrollbar{display:none}html{scrollbar-width:none}</style></head>", 1)
    fim = _PREVIA_JS % extra
    if "</body>" in corpo:
        corpo = corpo.replace("</body>", fim + "</body>", 1)
    else:
        corpo += fim
    return cabeca + corpo
