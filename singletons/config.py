# -*- coding: utf-8 -*-
from typing import Union

class ConfigManager:
    DEFAULTS = {
        'group':       {'highbit': False},
        'translation': {'source': 'ENG_US', 'destination': 'POR_BR'},
    }

    def __init__(self):
        self.__config = {s: dict(o) for s, o in self.DEFAULTS.items()}

    def value(self, section: str, option: str) -> Union[str, int, bool, None]:
        return self.__config.get(section, {}).get(option)

    def set_value(self, section: str, option: str, value) -> None:
        self.__config.setdefault(section, {})[option] = value

config = ConfigManager()
