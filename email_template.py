"""E-mail bonito (HTML) com o código de 6 dígitos: ícone, nome do site e botão.

E-mail não executa JavaScript, então NÃO existe botão "copiar" de verdade em nenhum e-mail.
O código aparece grande, em uma única "palavra" (um toque duplo ou segurar seleciona tudo)
e o botão roxo abre o site, onde a pessoa digita o código.

Só tabelas + CSS inline (é o que todos os apps de e-mail entendem). Tem um bloco de modo escuro
que os apps que suportam usam; os outros mostram o claro.
"""
import base64
import html as _html

PURPLE = "#b89cd9"          # cor do ícone do site
PURPLE_SOFT = "#f1eafa"     # fundo claro da caixa do código
INK = "#2b1a45"             # texto escuro (também usado no botão, que precisa de contraste)

_ICON_B64 = """
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
"""
ICON_PNG = base64.b64decode("".join(_ICON_B64.split()))

_COPY = {
    "verify": ("Confirme seu e-mail",
               "Use o código abaixo para confirmar o e-mail de recuperação da sua conta."),
    "reset": ("Redefina sua senha",
              "Use o código abaixo para criar uma nova senha para a sua conta."),
}


def render_code_email(code, purpose, ttl_min, site_name, site_url=None, logo_src=None):
    """Retorna (texto_simples, html). logo_src: 'cid:logo' (anexo embutido), uma URL/data-URI, ou None (sem imagem)."""
    title, intro = _COPY.get(purpose, _COPY["verify"])
    e = _html.escape
    name, code_h = e(site_name), e(str(code))
    url = e(site_url) if site_url else ""

    text = (f"{site_name}\n\n{title}\n{intro}\n\n    {code}\n\n"
            f"O código vale por {ttl_min} minutos.\n"
            f"Se você não pediu este código, ignore este e-mail: nada será alterado. "
            f"Nunca compartilhe o código com ninguém.\n")
    if site_url:
        text += f"\n{site_url}\n"

    logo = (f'<tr><td align="center" style="padding:30px 24px 6px">'
            f'<img src="{logo_src}" width="72" height="72" alt="" '
            f'style="display:block;width:72px;height:72px;border-radius:36px;border:0"></td></tr>') if logo_src else \
           '<tr><td style="padding-top:26px;font-size:0;line-height:0">&nbsp;</td></tr>'

    font = "font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
    page = f"""<!DOCTYPE html>
<html lang="pt-BR"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark">
<title>{e(title)}</title>
<style>
@media (prefers-color-scheme: dark) {{
  .page {{ background:#17111f !important; }}
  .card {{ background:#231a31 !important; }}
  .ink {{ color:#f1eafa !important; }}
  .muted {{ color:#b9aacb !important; }}
  .codebox {{ background:#33264a !important; border-color:{PURPLE} !important; }}
}}
</style></head>
<body style="margin:0;padding:0;background:#f4f0fa" class="page">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;font-size:1px;line-height:1px">Seu código: {code_h} &middot; vale por {ttl_min} minutos&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="page" style="background:#f4f0fa"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" class="card" style="max-width:460px;background:#ffffff;border-radius:20px">
{logo}
<tr><td align="center" class="ink" style="padding:6px 24px 0;{font}font-size:17px;font-weight:700;color:{INK}">{name}</td></tr>
<tr><td align="center" style="padding:18px 28px 0"><div style="height:3px;width:44px;background:{PURPLE};border-radius:3px;font-size:0;line-height:0">&nbsp;</div></td></tr>
<tr><td align="center" class="ink" style="padding:18px 28px 0;{font}font-size:22px;font-weight:700;color:{INK}">{e(title)}</td></tr>
<tr><td align="center" class="muted" style="padding:8px 32px 0;{font}font-size:15px;line-height:1.5;color:#5b4b73">{e(intro)}</td></tr>
<tr><td align="center" style="padding:22px 28px 0">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
  <td align="center" class="codebox ink" style="background:{PURPLE_SOFT};border:2px dashed {PURPLE};border-radius:14px;padding:16px 18px 16px 26px;font-family:'Courier New',Courier,monospace;font-size:36px;font-weight:700;letter-spacing:8px;color:{INK}"><span style="-webkit-user-select:all;user-select:all">{code_h}</span></td>
  </tr></table></td></tr>
<tr><td align="center" class="muted" style="padding:22px 32px 0;{font}font-size:13px;line-height:1.5;color:#5b4b73">O código vale por <b>{ttl_min} minutos</b>.</td></tr>
<tr><td align="center" class="muted" style="padding:10px 32px 30px;{font}font-size:12px;line-height:1.5;color:#8a7ba0">Se você não pediu este código, ignore este e-mail: nada será alterado.<br>Nunca compartilhe o código com ninguém.</td></tr>
</table>
</td></tr></table>
</body></html>"""
    return text, page
