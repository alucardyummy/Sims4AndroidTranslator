"""
translator_v2.py
-----------------
Motor de tradução multi-engine do Sims4AndroidTranslator:
fallback entre engines, retry com backoff, proteção de {placeholders},
tom formal/explícito por campo.

Import no app.py:
    from translator_v2 import translate_with_fallback, list_available_engines
"""

import os
import re
import time
import random
import logging
import requests
from openai import OpenAI, RateLimitError, APIError
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("translator_v2")


# =============================================================================
# REGRAS DE TRADUÇÃO (Opção C — híbrida)
# =============================================================================

GIRIAS_EXPLICITAS = [
    "pau", "rola", "buceta", "cu", "boquete", "punheta", "siririca",
    "trepar", "gozar", "foder", "putaria", "safadeza", "tesão", "tarado", "safado",
]

TERMOS_FORMAIS = [
    "pênis", "vagina", "ânus", "genitália", "orgasmo", "excitação",
]


def build_system_prompt(target_lang_name: str, tone: str) -> str:
    """
    tone: 'formal' (config/menu/aviso/descrição técnica) ou
          'explicit' (diálogo/interação/reação de Sim já explícita no original)
    """
    base = (
        f"Você é um tradutor de textos de mods do jogo The Sims 4 para "
        f"{target_lang_name}, natural e coloquial. Traduza só a frase "
        "recebida, sem continuar história, sem explicar, sem comentar — "
        "responda só com o texto traduzido, do mesmo tamanho/estrutura do "
        "original. Texto entre chaves (ex.: {0.SimFirstName}) e tags/códigos "
        "(<br>, %1, \\n) devem ser copiados exatamente como estão."
    )

    if tone == "explicit":
        style = (
            "Diálogo/interação de Sims adultos. Quando o original já for "
            "explícito, use gírias BR equivalentes (ex.: " +
            ", ".join(GIRIAS_EXPLICITAS) + "). Não adicione tom sexual a um "
            "texto que não tinha."
        )
    else:
        style = (
            "Texto de config/menu/aviso. Use linguagem formal (ex.: " +
            ", ".join(TERMOS_FORMAIS) + ") pra termos anatômicos/sexuais."
        )

    return base + "\n" + style


# =============================================================================
# PROTEÇÃO DE PLACEHOLDERS — extrai {…} antes de mandar pro modelo, recoloca
# depois. Mais confiável do que só PEDIR pro modelo preservar.
# =============================================================================

_PLACEHOLDER_RE = re.compile(r"\{[^{}]*\}")


def _protect_placeholders(text: str):
    mapping = {}

    def repl(m):
        token = "\u27e6P{}\u27e7".format(len(mapping))  # ⟦P0⟧, ⟦P1⟧... improvável de colidir com texto real
        mapping[token] = m.group(0)
        return token

    protected = _PLACEHOLDER_RE.sub(repl, text)
    return protected, mapping


def _restore_placeholders(text: str, mapping: dict) -> str:
    for token, original in mapping.items():
        text = text.replace(token, original)
    return text


def _placeholders_intact(original: str, translated: str) -> bool:
    """Confere se todo {placeholder} do original ainda aparece no resultado final."""
    for tok in _PLACEHOLDER_RE.findall(original):
        if tok not in translated:
            return False
    return True


def _looks_like_drift(original: str, translated: str) -> bool:
    """
    Detecta quando o modelo "viajou" e escreveu uma historinha em vez de
    traduzir a frase — comum em modelos de roleplay/uncensored, que tendem
    a elaborar/narrar em vez de responder direto. Heurística simples: uma
    tradução PT-BR raramente passa de ~1.5x o tamanho do original; acima
    de 2.5x é sinal forte de invenção, não de tradução.
    """
    orig_len = len(original.strip())
    trans_len = len(translated.strip())
    if orig_len < 5:
        return False  # textos muito curtos têm razão de expansão instável, não vale checar
    return (trans_len / max(orig_len, 1)) > 2.5


# =============================================================================
# DETECÇÃO DE RECUSA — um modelo que recusa não costuma dar erro HTTP, ele
# devolve um 200 normal com uma resposta tipo "Desculpe, não posso...". Isso
# precisa ser pego manualmente pra disparar o fallback pro próximo engine.
# =============================================================================

_REFUSAL_RE = re.compile(
    r"^\s*(i can'?t|i cannot|i won'?t|i'?m (not able|sorry)|as an ai|"
    r"desculpe,?\s*(mas\s*)?n[ãa]o posso|n[ãa]o posso (ajudar|gerar|traduzir)|"
    r"i'?m unable to)",
    re.IGNORECASE,
)


def _looks_like_refusal(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    return bool(_REFUSAL_RE.match(t))


# =============================================================================
# CATÁLOGO DE ENGINES
# =============================================================================
# Cada engine "openai_compat" fala a mesma API (estilo OpenAI /chat/completions),
# então Groq, OpenRouter, Featherless e o Ollama do celular usam o MESMO
# código — só muda base_url / api_key / nome do modelo.

ENGINES = {
    "google": {
        "kind": "google",
        "label": "Google Translate",
        "always_available": True,
    },
    "groq-llama": {
        "kind": "openai_compat",
        "label": "Groq (Llama 3.3 70B)",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "model": "llama-3.3-70b-versatile",
    },

    # Opção grátis: em vez de fixar um modelo ":free" específico (que some
    # sem aviso o tempo todo), usa o auto-router do próprio OpenRouter — ele
    # escolhe, do lado deles, algum modelo free disponível agora. Não é
    # "uncensored" garantido (o modelo escolhido pode recusar tom explícito,
    # nesse caso o fallback normal decide), mas custa $0 e não quebra
    # sozinho quando um modelo free específico sai do catálogo.
    "openrouter-free": {
        "kind": "openai_compat",
        "label": "OpenRouter — auto-router grátis",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "model": "openrouter/free",
    },

    # OpenRouter + Venice Uncensored (Dolphin-Mistral-24B) — pré-pago por
    # token (bem barato: ~$0,20/M entrada, ~$0,90/M saída), sem mensalidade.
    # Baixa taxa de recusa divulgada pela própria Venice.
    "openrouter-venice": {
        "kind": "openai_compat",
        "label": "OpenRouter — Venice Uncensored (Dolphin-Mistral-24B)",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "model": "cognitivecomputations/dolphin-mistral-24b-venice-edition",
    },

    # Os 3 modelos "sem censura" ficam na Featherless.ai (assinatura fixa,
    # API compatível com OpenAI — mesmo esquema da Groq). Slugs confirmados
    # pelos links da Featherless:
    "featherless-rocinante": {
        "kind": "openai_compat",
        "label": "Featherless — Rocinante-X-12B (Heretic Uncensored)",
        "base_url": "https://api.featherless.ai/v1",
        "api_key_env": "FEATHERLESS_API_KEY",
        "model": "DavidAU/Rocinante-X-12B-v1-Heretic-Uncensored",
    },
    "featherless-stormseeker": {
        "kind": "openai_compat",
        "label": "Featherless — StormSeeker-24B",
        "base_url": "https://api.featherless.ai/v1",
        "api_key_env": "FEATHERLESS_API_KEY",
        "model": "Naphula/StormSeeker-24B-v1",
    },
    "featherless-floppa": {
        "kind": "openai_compat",
        "label": "Featherless — Floppa-12B-Gemma3 (Uncensored)",
        "base_url": "https://api.featherless.ai/v1",
        "api_key_env": "FEATHERLESS_API_KEY",
        "model": "Ryex/Floppa-12B-Gemma3-Uncensored",
    },

    # Modelos rodando no celular (Termux + Ollama). Só funcionam quando O
    # SEU celular está de pé — mas ficam visíveis pra todo mundo no
    # dropdown mesmo assim (always_visible=True ignora a checagem normal de
    # "tem chave configurada?"). Pra qualquer outro usuário que selecionar
    # uma dessas, a chamada falha e cai automaticamente pro resto da cadeia
    # (Groq/OpenRouter/etc) — não trava a tradução, só não usa o modelo do
    # celular mesmo.
    "phone-hauhau": {
        "kind": "openai_compat",
        "label": "📱 Qwen3-4B HauhauCS Aggressive (celular)",
        "base_url": os.getenv("LOCAL_PHONE_BASE_URL", ""),
        "api_key_env": "LOCAL_PHONE_API_KEY",  # geralmente vazio (Ollama não exige)
        "model": "hf.co/HauhauCS/Qwen3-4B-2507-Instruct-Uncensored-HauhauCS-Aggressive",
        "always_visible": True,
    },
    "phone-ministral": {
        "kind": "openai_compat",
        "label": "📱 Ministral-3B Heresy (celular)",
        "base_url": os.getenv("LOCAL_PHONE_BASE_URL", ""),
        "api_key_env": "LOCAL_PHONE_API_KEY",
        "model": "hf.co/Abiray/Huihui-Ministral-3B-Instruct-2512-abliterated-GGUF",
        "always_visible": True,
    },
}

# Ordem de tentativa quando o front não pede uma engine específica. As
# engines do celular ficam de fora de propósito — só entram se o usuário
# escolher manualmente (senão, toda tradução automática de todo mundo
# ficaria tentando alcançar um celular desligado antes de seguir adiante).
DEFAULT_FALLBACK_CHAIN = [
    "groq-llama",
    "openrouter-free",
    "openrouter-venice",
    "featherless-rocinante",
    "featherless-stormseeker",
    "featherless-floppa",
    "google",
]

LANG_NAMES = {
    "POR_BR": "português brasileiro", "ENG_US": "inglês", "ENG_UK": "inglês britânico",
    "SPA_ES": "espanhol", "SPA_MX": "espanhol mexicano", "FRE_FR": "francês",
    "GER_DE": "alemão", "ITA_IT": "italiano",
}


def list_available_engines() -> list[dict]:
    """
    Devolve as engines pra popular o dropdown "Selecionar Tradutor" no front.
    Normalmente só mostra o que está de fato configurado (chave + modelo),
    pra não oferecer opção que vai falhar na certa — EXCETO engines com
    always_visible=True, que aparecem sempre (mesmo sem funcionar ainda),
    porque são pessoais e o funcionamento delas não depende de configuração
    de servidor, depende do dono do site estar com o celular ligado.
    """
    available = []
    for name, cfg in ENGINES.items():
        if cfg.get("always_visible"):
            available.append({"id": name, "label": cfg["label"]})
            continue
        if cfg.get("always_available"):
            available.append({"id": name, "label": cfg["label"]})
            continue
        if cfg["kind"] == "openai_compat":
            has_key = bool(os.getenv(cfg["api_key_env"], "")) or "localhost" in cfg["base_url"]
            has_model = bool(cfg.get("model"))
            if has_key and has_model:
                available.append({"id": name, "label": cfg["label"]})
    return available


# =============================================================================
# CHAMADAS ÀS ENGINES, COM RETRY + BACKOFF
# =============================================================================

class EngineRefused(Exception):
    """O modelo respondeu, mas com uma recusa (não é erro de rede/rate limit)."""


class EngineUnavailable(Exception):
    """Erro de rede, timeout, ou esgotou as tentativas de retry."""


def _call_openai_compat(cfg: dict, system_prompt: str, text: str, max_retries: int = 4) -> str:
    api_key = os.getenv(cfg["api_key_env"], "") or "not-needed"
    client = OpenAI(base_url=cfg["base_url"], api_key=api_key)

    last_err = None
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=cfg["model"],
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                temperature=0.2,  # tradução pede fidelidade, não criatividade —
                                  # temperature alta é a principal causa do
                                  # modelo "inventar história" em vez de traduzir
                timeout=30,
            )
            out = (resp.choices[0].message.content or "").strip()
            if _looks_like_refusal(out):
                # recusa não é um problema de rede — não adianta tentar de
                # novo com o MESMO modelo, quem decide o fallback é o caller
                raise EngineRefused(f"{cfg['label']} recusou: {out[:120]!r}")
            return out

        except EngineRefused:
            raise

        except RateLimitError as e:
            # respeita o Retry-After do provedor quando ele manda; senão,
            # backoff exponencial com um mínimo de 5s pra rate limit
            retry_after = _extract_retry_after(e)
            last_err = e
            if attempt == max_retries - 1:
                break
            sleep_for = retry_after if retry_after else max(5, (2 ** attempt) + random.uniform(0, 1))
            log.warning("%s: rate limit, aguardando %.1fs (tentativa %d/%d)",
                        cfg["label"], sleep_for, attempt + 1, max_retries)
            time.sleep(sleep_for)

        except (APIError, requests.RequestException, TimeoutError) as e:
            last_err = e
            status = getattr(e, "status_code", None)
            if status is not None and 400 <= status < 500 and status != 429:
                # erro permanente (ex.: modelo não existe mais) — tentar de
                # novo não vai resolver, desiste já e deixa o CALLER pular
                # pro próximo engine da cadeia
                break
            if attempt == max_retries - 1:
                break
            sleep_for = (2 ** attempt) + random.uniform(0, 0.5)  # 1s, 2s, 4s, 8s...
            log.warning("%s: erro '%s', tentando de novo em %.1fs (tentativa %d/%d)",
                        cfg["label"], e, sleep_for, attempt + 1, max_retries)
            time.sleep(sleep_for)

    raise EngineUnavailable(f"{cfg['label']} esgotou {max_retries} tentativas: {last_err}")


def _extract_retry_after(rate_limit_error) -> float | None:
    try:
        headers = rate_limit_error.response.headers
        val = headers.get("retry-after") or headers.get("Retry-After")
        return float(val) if val else None
    except Exception:
        return None


def _translate_google(text: str, target_lang_name: str) -> str:
    # Mantém a implementação existente do projeto (googleapis.com/translate_a).
    # Reaproveite a função _translate_google já presente no translator.py atual.
    from translator import Translator  # import local pra evitar ciclo
    result = Translator()._translate_google(text, "en", _lang_name_to_code(target_lang_name))
    if isinstance(result, dict):
        return result.get("text", "")
    return str(result)


def _lang_name_to_code(name: str) -> str:
    reverse = {v: k for k, v in LANG_NAMES.items()}
    code = reverse.get(name, "POR_BR")
    return {"POR_BR": "pt", "ENG_US": "en"}.get(code, "pt")


# =============================================================================
# FUNÇÃO PRINCIPAL — é isso que o endpoint do Flask chama
# =============================================================================

def translate_with_fallback(
    text: str,
    target_lang: str = "POR_BR",
    tone: str = "formal",
    preferred_engine: str | None = None,
) -> dict:
    """
    Retorna {"text": ..., "engine_used": ...}. Levanta RuntimeError só se
    TODAS as engines da cadeia falharem/recusarem.
    """
    if not text or not text.strip():
        return {"text": "", "engine_used": None}

    target_lang_name = LANG_NAMES.get(target_lang, "português brasileiro")
    protected, mapping = _protect_placeholders(text)
    system_prompt = build_system_prompt(target_lang_name, tone)

    chain = DEFAULT_FALLBACK_CHAIN[:]
    if preferred_engine and preferred_engine in ENGINES:
        chain = [preferred_engine] + [e for e in chain if e != preferred_engine]

    errors = {}
    for engine_name in chain:
        cfg = ENGINES.get(engine_name)
        if not cfg:
            continue
        try:
            if cfg["kind"] == "google":
                out = _translate_google(protected, target_lang_name)
            else:
                if not cfg.get("model") or not cfg.get("base_url"):
                    # engine sem modelo ou sem base_url configurado (ex.: as
                    # do celular quando LOCAL_PHONE_BASE_URL não está setado
                    # pra esse usuário) — pula direto, sem tentar chamar
                    errors[engine_name] = "engine não configurada (sem base_url/modelo)"
                    continue
                out = _call_openai_compat(cfg, system_prompt, protected)

            restored = _restore_placeholders(out, mapping)

            if not _placeholders_intact(text, restored):
                # o modelo bagunçou os placeholders — não aceita esse
                # resultado, tenta a próxima engine da cadeia
                errors[engine_name] = "placeholders corrompidos no resultado"
                log.warning("%s corrompeu placeholders, tentando próxima engine", engine_name)
                continue

            if _looks_like_drift(text, restored):
                # o modelo "viajou" e escreveu uma historinha em vez de
                # traduzir — mesma lógica: não aceita, tenta a próxima
                errors[engine_name] = "resposta longa demais, parece invenção (não tradução literal)"
                log.warning("%s pareceu inventar texto em vez de traduzir, tentando próxima engine", engine_name)
                continue

            return {"text": restored, "engine_used": engine_name}

        except (EngineRefused, EngineUnavailable) as e:
            errors[engine_name] = str(e)
            log.info("Engine %s falhou (%s), tentando próxima", engine_name, e)
            continue

    raise RuntimeError(f"Todas as engines falharam ou recusaram: {errors}") 
