"""Regras para SENHAS NOVAS (cadastro e redefinição). O login NÃO usa isso, então
quem já tem senha antiga (ex.: de 6 caracteres) continua entrando normalmente.

Regras obrigatórias:
  - de 6 a 64 caracteres (espaços valem, então dá pra usar uma frase)
  - não pode ser igual ao nome de usuário (nem conter o nome inteiro, se tiver 4+ letras)
  - não pode estar na lista de senhas comuns (com truques tipo p@ssw0rd e "senha2024!")
  - não pode ser uma sequência ou repetição óbvia (123456, abcdef, aaaaaa, 121212)

Maiúscula, símbolo, número e 10+ caracteres são só RECOMENDAÇÃO (a tela mostra a dica).
"""
import unicodedata

PASSWORD_MIN = 6
PASSWORD_RECOMMENDED = 10
PASSWORD_MAX = 64

_COMMON_RAW = """
123456 1234567 12345678 123456789 1234567890 12345 1234 123123 111111 000000 654321 121212 112233
123321 666666 777777 888888 999999 555555 222222 333333 444444 987654321 987654 123654 147258
147258369 159753 789456 789456123 741852963 321654 232323 101010 696969 1q2w3e 1q2w3e4r 1q2w3e4r5t
1qaz2wsx 1qazxsw2 q1w2e3 q1w2e3r4 q1w2e3r4t5 qwerty qwertyuiop qwerty123 qwertz qwe123 qweasd
qweasdzxc asdfgh asdfghjkl asdf1234 asd123 zxcvbn zxcvbnm zxc123 qazwsx qazwsxedc password passw0rd
password1 password12 password123 pass123 pass1234 passwd admin admin123 admin1234 administrator root
toor login welcome welcome1 letmein iloveyou monkey dragon master sunshine princess football baseball
superman batman trustno1 shadow michael jennifer jessica ashley charlie abc123 abc12345 abcd1234
abcdef abcdefg abcdefgh test test123 testing guest hello hello123 secret changeme default google
facebook instagram whatsapp tiktok youtube whatever freedom starwars pokemon minecraft roblox fortnite
naruto computer internet killer hunter ranger soccer hockey summer winter cheese pepper cookie banana
chocolate
senha senha123 senha1234 senha12345 senhaforte minhasenha minhasenha123 mudar mudar123 mudar1234
brasil brasil123 brazil amor amor123 amorzinho teamo teamo123 tequiero meuamor eutamo deus deus123 jesus
jesus123 familia amigos amizade princesa gatinho cachorro felicidade saudade liberdade bemvindo
flamengo corinthians palmeiras saopaulo santos vasco gremio cruzeiro botafogo fluminense internacional
atletico neymar ronaldo messi gabriel lucas mateus felipe rafael pedro joao maria juliana beatriz bruno
carlos fernanda amanda camila usuario usuario123 teste teste123 teste1234 qwe12345 senhasenha
sims sims3 sims4 sims123 sims1234 thesims thesims4 sims4translator translator tradutor tradutor123
sims4android plumbob simoleons rosebud motherlode kaching
"""
COMMON_PASSWORDS = frozenset(_COMMON_RAW.split())

_KEYBOARD_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm", "1234567890", "qazwsxedc")

_LEET_BASE = {"0": "o", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "@": "a", "$": "s", "!": "i", "+": "t"}


def _leet_variants(text):
    """'p@ssw0rd' -> 'password'. O '1' pode ser 'i' ou 'l', então gera as duas."""
    out = set()
    for one in ("i", "l"):
        table = dict(_LEET_BASE, **{"1": one})
        out.add("".join(table.get(ch, ch) for ch in text))
    return out


def _is_predictable(pw):
    """Tudo igual (aaaaaa), sequência seguida (123456, abcdef, 987654) ou padrão curto repetido (121212, abcabc)."""
    low = pw.lower()
    if len(low) < 4:
        return False
    if len(set(low)) == 1:
        return True
    steps = {ord(b) - ord(a) for a, b in zip(low, low[1:])}
    if steps in ({1}, {-1}):
        return True
    for size in (2, 3):
        if len(low) % size == 0 and len(low) // size >= 2 and low == low[:size] * (len(low) // size):
            return True
    if len(low) >= 6:
        for row in _KEYBOARD_ROWS:
            if low in row or low in row[::-1]:
                return True
    return False


def _in_common_list(pw):
    low = pw.lower()
    stem = low.rstrip("".join(ch for ch in set(low) if not ch.isalpha()))   # tira números/símbolos do FIM: senha2024! -> senha
    bases = {low}
    if len(stem) >= 4:
        bases.add(stem)
    for base in bases:
        candidates = {base} | _leet_variants(base)
        if any(c in COMMON_PASSWORDS for c in candidates):
            return True
    return False


def _squash(text):
    return unicodedata.normalize("NFKC", str(text or "")).casefold().replace(" ", "")


def validate_new_password(password, username=""):
    """Retorna None se a senha serve, ou a mensagem (pt-br) do que está errado."""
    pw = str(password or "")
    if len(pw) < PASSWORD_MIN:
        return f"A senha precisa ter pelo menos {PASSWORD_MIN} caracteres."
    if len(pw) > PASSWORD_MAX:
        return f"A senha pode ter no máximo {PASSWORD_MAX} caracteres."
    user = _squash(username)
    if user:
        flat = _squash(pw)
        if flat == user or (len(user) >= 4 and user in flat):
            return "A senha não pode ser igual ao seu nome de usuário (nem conter ele)."
    if _is_predictable(pw):
        return "Essa senha é previsível demais (sequência ou repetição). Misture mais."
    if _in_common_list(pw):
        return "Essa senha é muito comum e fácil de adivinhar. Escolha outra."
    return None
