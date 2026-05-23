# -*- coding: utf-8 -*-
import os
import xml.etree.ElementTree as ElementTree
from collections import namedtuple
from singletons.config import config

Language = namedtuple('Language', 'locale code google deepl')

class Languages:
    def __init__(self):
        self.__locales = {}
        self.__codes = {}
        self.__load()

    def __load(self):
        base = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.join(base, '..', 'prefs', 'languages.xml'),
            os.path.join(base, 'prefs', 'languages.xml'),
            os.path.abspath('./prefs/languages.xml'),
        ]
        content = None
        for path in candidates:
            path = os.path.normpath(path)
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as fp:
                    content = fp.read()
                break
        if content is None:
            self.__load_builtin()
            return
        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError:
            self.__load_builtin()
            return
        for item in root.findall('language'):
            locale = item.get('locale')
            code = item.get('code')
            if locale and code:
                lang = Language(locale.upper(), code, item.get('google-code'), item.get('deepl-code'))
                self.__locales[lang.locale] = lang
                self.__codes[lang.code] = lang

    def __load_builtin(self):
        table = [
            ('ENG_US','0x00','en','EN'),('CHS_CN','0x01','zh-cn',''),
            ('CHT_CN','0x02','zh-tw','ZH'),('CZE_CZ','0x03','cs','CS'),
            ('DAN_DK','0x04','da','DA'),('DUT_NL','0x05','nl','NL'),
            ('FIN_FI','0x06','fi','FI'),('FRE_FR','0x07','fr','FR'),
            ('GER_DE','0x08','de','DE'),('ITA_IT','0x0B','it','IT'),
            ('JPN_JP','0x0C','ja','JA'),('KOR_KR','0x0D','ko','KO'),
            ('NOR_NO','0x0E','no','NB'),('POL_PL','0x0F','pl','PL'),
            ('POR_PT','0x10','pt','PT-PT'),('POR_BR','0x11','pt','PT-BR'),
            ('RUS_RU','0x12','ru','RU'),('SPA_ES','0x13','es','ES'),
            ('SWE_SE','0x15','sv','SV'),('THA_TH','0x16','th','TH'),
            ('UKR_UA','0x17','uk','UA'),
        ]
        for locale, code, google, deepl in table:
            lang = Language(locale, code, google, deepl)
            self.__locales[locale] = lang
            self.__codes[code] = lang

    @property
    def locales(self): return list(self.__locales.keys())
    @property
    def source(self): return self.by_locale(config.value('translation', 'source'))
    @property
    def destination(self): return self.by_locale(config.value('translation', 'destination'))
    def by_locale(self, locale): return self.__locales.get(locale) if locale else None
    def by_code(self, code): return self.__codes.get(code) if code else None

languages = Languages()
