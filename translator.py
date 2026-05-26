import os
import requests
from openai import OpenAI
from dotenv import load_dotenv

# Carrega as chaves do arquivo .env automaticamente
load_dotenv()

class Translator:
    def __init__(self):
        self.engines = {
            'google': 'https://translate.googleapis.com/translate_a/single'
        }
        
        # Setup OpenAI (GPT-4o-mini)
        self.openai_key = os.getenv("OPENAI_API_KEY")
        self.openai_client = OpenAI(api_key=self.openai_key) if self.openai_key else None
        
        # Setup Groq (Usa o mesmo SDK da OpenAI com base_url diferente)
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

        if engine == 'google':
            return self._translate_google(text, source_lang, target_lang)
        elif engine == 'gpt' and self.openai_client:
            return self._translate_llm(self.openai_client, "gpt-4o-mini", text)
        elif engine == 'groq' and self.groq_client:
            return self._translate_llm(self.groq_client, "llama3-8b-8192", text)
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

    def _translate_llm(self, client, model_name, text):
        try:
            # Prompt cirúrgico para localização de The Sims 4
            system_instruction = (
                "Você é um tradutor especialista em localização de jogos, focado no jogo The Sims 4.\n"
                "Traduza o texto para Português do Brasil mantendo o tom natural, casual e criativo do jogo.\n"
                "CRÍTICO: Mantenha absolutamente INTACTOS quaisquer placeholders, códigos ou formatações do jogo, "
                "como por exemplo: {0.String}, {M0.he}{F0.she}, \n, {1.String}, etc. Não altere o que estiver dentro das chaves.\n"
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

