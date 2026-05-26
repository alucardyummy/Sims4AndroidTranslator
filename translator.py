import requests

class Translator:
    def __init__(self):
        self.engines = {
            'google': 'https://translate.googleapis.com/translate_a/single',
            'deepl_free': 'https://api-free.deepl.com/v2/translate',
        }
        self.deepl_key = None
    
    def set_deepl_key(self, key):
        self.deepl_key = key
    
    def translate(self, engine, text, source_lang='en', target_lang='PT_BR'):
        if engine == 'google':
            return self._translate_google(text, source_lang, target_lang)
        elif engine == 'deepl_free' and self.deepl_key:
            return self._translate_deepl(text, source_lang, target_lang)
        else:
            return {'status_code': 400, 'text': 'Engine not supported or missing API key'}
    
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
            return {'status_code': response.status_code, 'text': 'Translation failed'}
        except Exception as e:
            return {'status_code': 500, 'text': str(e)}
    
    def _translate_deepl(self, text, source_lang, target_lang):
        try:
            headers = {'Authorization': f'DeepL-Auth-Key {self.deepl_key}'}
            data = {
                'text': [text],
                'source_lang': source_lang.upper(),
                'target_lang': target_lang.upper()
            }
            response = requests.post(self.engines['deepl_free'], headers=headers, json=data, timeout=10)
            if response.status_code == 200:
                result = response.json()
                return {'status_code': 200, 'text': result['translations'][0]['text']}
            return {'status_code': response.status_code, 'text': 'DeepL translation failed'}
        except Exception as e:
            return {'status_code': 500, 'text': str(e)}

translator = Translator()
