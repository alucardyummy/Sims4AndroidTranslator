import os
import requests
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

class Translator:
    def __init__(self):
        self.engines = {
            'google': 'https://translate.googleapis.com/translate_a/single'
        }
        
        self.openai_key = os.getenv("OPENAI_API_KEY")
        self.openai_client = OpenAI(api_key=self.openai_key) if self.openai_key else None
        
        self.groq_key = os.getenv("GROQ_API_KEY")
        if self.groq_key:
            self.groq_client = OpenAI(
                base_url="https://api.groq.com/openai/v1",
                api_key=self.groq_key
            )
        else:
            self.groq_client = None

    def translate(self, engine, text, source_lang='en', target_lang='pt'):
        if not text or not text.strip():
            return {'status_code': 400, 'text': 'Texto vazio'}

        # Dicionário inteligente para converter os códigos do The Sims 4
        # para os códigos que o Google e as IAs entendem
        lang_map = {
            'ENG_US': 'en', 'ENG_UK': 'en',
            'FRE_FR': 'fr', 'GER_DE': 'de',
            'ITA_IT': 'it', 'SPA_ES': 'es',
            'NED_NL': 'nl', 'POR_BR': 'pt',
            'CHI_CN': 'zh-CN', 'CHI_TW': 'zh-TW',
            'CZE_CZ': 'cs', 'DAN_DK': 'da',
            'FIN_FI': 'fi', 'JPN_JP': 'ja',
            'KOR_KR': 'ko', 'NOR_NO': 'no',
            'POL_PL': 'pl', 'RUS_RU': 'ru',
            'SPA_MX': 'es', 'SWE_SE': 'sv',
            'THA_TH': 'th'
        }

        # Garante que os códigos fiquem em maiúsculo para bater com o dicionário
        s_lang_clean = str(source_lang).upper().strip()
        t_lang_clean = str(target_lang).upper().strip()

        # Pega a conversão correta. Se não achar, usa 'en' para origem e 'pt' para destino como segurança
        s_lang = lang_map.get(s_lang_clean, 'en')
        t_lang = lang_map.get(t_lang_clean, 'pt')

        if engine == 'google':
            return self._translate_google(text, s_lang, t_lang)
        elif engine == 'gpt' and self.openai_client:
            # Passamos o idioma final no prompt para a IA saber para onde traduzir
            return self._translate_llm(self.openai_client, "gpt-4o-mini", text, t_lang_clean)
        elif engine == 'groq' and self.groq_client:
            return self._translate_llm(self.groq_client, "llama3-8b-8192", text, t_lang_clean)
        else:
            return {'status_code': 400, 'text': f'Engine "{engine}" não suportada ou chave faltando'}

    def _translate_google(self, text, source_lang, target_lang):
        try:
            params = {
                'client': 'gtx',
                'sl': source_lang,
                'tl': target_lang,
                'dt': 't',
                'q': text
            }
            response = requests.get(self.engines['google'], params=params, timeout=10)
            if response.status_code == 200:
                result = response.json()
                translated = ''.join([item[0] for item in result[0] if item[0]])
                return {'status_code': 200, 'text': translated}
            return {'status_code': response.status_code, 'text': 'Google Translation failed'}
        except Exception as e:
            return {'status_code': 500, 'text': str(e)}

    def _translate_llm(self, client, model_name, text, target_lang_name):
        try:
            # Prompt dinâmico baseado na escolha do usuário na interface
            system_instruction = (
                f"Você é um tradutor especialista em localização de jogos, focado no jogo The Sims 4.\n"
                f"Traduza o texto fornecido estritamente para o idioma/região correspondente a: {target_lang_name}, "
                f"mantendo o tom natural, casual e criativo adequado para a comunidade do jogo.\n"
                "CRÍTICO: Mantenha absolutamente INTACTOS quaisquer placeholders, códigos ou formatações do jogo, "
                "como por exemplo: {0.String}, {M0.he}{F0.she}, \\n, {1.String}, etc. Não altere o que estiver dentro das chaves.\n"
                "Responda APENAS com a tradução direta, sem aspas, explicações ou notas."
            )

            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": text}
                ],
                temperature=0.3,
                max_tokens=1000
            )
            translated_text = response.choices[0].message.content.strip()
            return {'status_code': 200, 'text': translated_text}
        except Exception as e:
            return {'status_code': 500, 'text': f"LLM ({model_name}) Error: {str(e)}"}

translator = Translator()

