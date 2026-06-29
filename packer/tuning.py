# -*- coding: utf-8 -*-
"""
packer/tuning.py
----------------
Gerador de recursos de tuning para mods do Sims 4.

Responsabilidades:
  - Calcular hashes FNV32 e FNV64 (usados como chaves de string e instance IDs)
  - Gerar XML de Trait (type 0x03B33DDF)
  - Gerar XML de Interaction / Social (type 0xE882D22F)
  - Empacotar tudo num ResourceID compatível com o DbpfPackage existente

Tipos de recurso relevantes:
  STBL  = 0x220557DA   (já existente)
  TRAIT = 0x03B33DDF
  SOCIAL (interaction) = 0xE882D22F
"""

import xml.etree.ElementTree as ET
from xml.dom import minidom

# ---------------------------------------------------------------------------
# Type IDs dos recursos de tuning
# ---------------------------------------------------------------------------

TYPE_TRAIT   = 0x03B33DDF
TYPE_SOCIAL  = 0xE882D22F   # Social / conversas
TYPE_STBL    = 0x220557DA   # já gerenciado pelo stbl.py, mas listado pra referência


# ---------------------------------------------------------------------------
# FNV hashing
# ---------------------------------------------------------------------------

_FNV32_PRIME  = 0x01000193
_FNV32_OFFSET = 0x811C9DC5
_FNV64_PRIME  = 0x00000100000001B3
_FNV64_OFFSET = 0xCBF29CE484222325
_MASK32 = 0xFFFFFFFF
_MASK64 = 0xFFFFFFFFFFFFFFFF


def fnv32(text: str) -> int:
    """
    Hash FNV-1a 32 bits.
    Usado como chave de string nas STBLs.
    O Sims 4 aplica FNV32 sobre o nome da string em lowercase.
    """
    h = _FNV32_OFFSET
    for byte in text.lower().encode('utf-8'):
        h ^= byte
        h = (h * _FNV32_PRIME) & _MASK32
    return h


def fnv64(text: str) -> int:
    """
    Hash FNV-1a 64 bits.
    Usado como instance ID dos recursos de tuning.
    O jogo aplica FNV64 sobre o nome completo do tuning (ex: 'meu_mod:trait_criativo').
    """
    h = _FNV64_OFFSET
    for byte in text.lower().encode('utf-8'):
        h ^= byte
        h = (h * _FNV64_PRIME) & _MASK64
    return h


def hash_string_key(name: str) -> int:
    """Alias legível: calcula a chave FNV32 de uma string de texto."""
    return fnv32(name)


def hash_instance(tuning_name: str) -> int:
    """Alias legível: calcula o instance ID FNV64 de um recurso de tuning."""
    return fnv64(tuning_name)


# ---------------------------------------------------------------------------
# Helpers de XML
# ---------------------------------------------------------------------------

def _pretty_xml(root: ET.Element) -> bytes:
    """Serializa um ElementTree para XML indentado e codificado em UTF-8."""
    raw = ET.tostring(root, encoding='unicode')
    reparsed = minidom.parseString(raw)
    pretty = reparsed.toprettyxml(indent='  ', encoding='utf-8')
    # toprettyxml adiciona '<?xml ...?>' — o jogo tolera isso
    return pretty


# ---------------------------------------------------------------------------
# Gerador de Trait
# ---------------------------------------------------------------------------

# Tipos de traço reconhecidos pelo jogo
TRAIT_TYPES = {
    'personality': 'PERSONALITY',   # traço de personalidade normal
    'bonus':       'BONUS',         # traço bônus (ex: Aspiração)
    'gameplay':    'GAMEPLAY',      # traço funcional/interno
    'social':      'SOCIAL',        # traço social
}

# Bitmask de disponibilidade (quais CAS tabs mostram o traço)
AVAILABILITY = {
    'teen_up': 2,        # Teen+  (mais comum)
    'young_adult_up': 6, # Young Adult+
    'all_ages': 1,       # Todas as idades
    'adult_up': 14,      # Adult+
}


def build_trait_xml(
    tuning_name: str,
    display_name_hash: int,
    description_hash: int,
    trait_type: str = 'personality',
    availability: str = 'teen_up',
    buy_price: int = 0,
) -> bytes:
    """
    Gera o XML de um Trait.

    Parâmetros:
        tuning_name       - nome completo do tuning, ex: 'meu_mod:trait_criativo'
                            Será usado como atributo 'n' e pra calcular o instance ID.
        display_name_hash - FNV32 do nome de exibição (chave na STBL)
        description_hash  - FNV32 da descrição (chave na STBL)
        trait_type        - 'personality' | 'bonus' | 'gameplay' | 'social'
        availability      - 'teen_up' | 'young_adult_up' | 'all_ages' | 'adult_up'
        buy_price         - preço em pontos de traço (0 = gratuito)

    Retorna os bytes do XML pronto pra ser inserido no .package.
    """
    instance_id = hash_instance(tuning_name)
    trait_type_value = TRAIT_TYPES.get(trait_type, 'PERSONALITY')
    avail_value = AVAILABILITY.get(availability, 2)

    # Raiz: <I c="Trait" i="trait" m="traits.trait" n="..." s="...">
    root = ET.Element('I')
    root.set('c', 'Trait')
    root.set('i', 'trait')
    root.set('m', 'traits.trait')
    root.set('n', tuning_name)
    root.set('s', str(instance_id))

    def T(parent, name, value):
        el = ET.SubElement(parent, 'T')
        el.set('n', name)
        el.text = str(value)
        return el

    def L(parent, name):
        el = ET.SubElement(parent, 'L')
        el.set('n', name)
        return el

    def V(parent, name, type_val, value=None):
        el = ET.SubElement(parent, 'V')
        el.set('n', name)
        el.set('t', type_val)
        if value is not None:
            el.text = str(value)
        return el

    # Nome de exibição e descrição (referenciados por hash na STBL)
    T(root, 'display_name', f'0x{display_name_hash:08X}')
    T(root, 'trait_description', f'0x{description_hash:08X}')

    # Tipo do traço
    T(root, 'trait_type', trait_type_value)

    # Disponibilidade por idade
    T(root, 'availability_flags', avail_value)

    # Preço (em pontos de traço)
    T(root, 'buy_price', buy_price)

    # Buffs: lista vazia por padrão (pode ser expandido futuramente)
    L(root, 'buffs')

    # Interações adicionadas por este traço: lista vazia por padrão
    L(root, 'trait_interactions')

    return _pretty_xml(root)


# ---------------------------------------------------------------------------
# Gerador de Social Interaction (conversa)
# ---------------------------------------------------------------------------

# Níveis de relação mínima pra interação aparecer
RELATIONSHIP_BITS = {
    'any':          None,
    'acquaintance': 'relationship_bits.RelBit_Friendly_Acquaintances',
    'friend':       'relationship_bits.RelBit_Friendly_Friends',
    'best_friend':  'relationship_bits.RelBit_Friendly_BestFriends',
    'romantic':     'relationship_bits.RelBit_Romance_Romantic',
}

# Categorias do menu social onde a interação aparece
SOCIAL_CATEGORIES = {
    'friendly':  '0x3E55',   # Amigável
    'mean':      '0x3E56',   # Malvado
    'funny':     '0x3E57',   # Engraçado
    'romantic':  '0x3E58',   # Romântico
    'mischief':  '0x3E59',   # Travessura
    'greeting':  '0x3E5A',   # Cumprimento
}


def build_social_xml(
    tuning_name: str,
    display_name_hash: int,
    category: str = 'friendly',
    min_relationship: str = 'any',
    relationship_change: int = 10,
    allow_autonomy: bool = True,
) -> bytes:
    """
    Gera o XML de uma Social Interaction (conversa/interação social).

    Parâmetros:
        tuning_name         - ex: 'meu_mod:social_contar_piada'
        display_name_hash   - FNV32 do nome da interação (chave na STBL)
        category            - 'friendly' | 'mean' | 'funny' | 'romantic' | 'mischief' | 'greeting'
        min_relationship    - 'any' | 'acquaintance' | 'friend' | 'best_friend' | 'romantic'
        relationship_change - quanto a relação muda ao usar (positivo = melhora)
        allow_autonomy      - se NPCs podem usar esta interação sozinhos

    Retorna os bytes do XML pronto pra ser inserido no .package.
    """
    instance_id = hash_instance(tuning_name)
    category_value = SOCIAL_CATEGORIES.get(category, '0x3E55')

    root = ET.Element('I')
    root.set('c', 'SocialMixerInteraction')
    root.set('i', 'interaction')
    root.set('m', 'interactions.social.social_mixer_interaction')
    root.set('n', tuning_name)
    root.set('s', str(instance_id))

    def T(parent, name, value):
        el = ET.SubElement(parent, 'T')
        el.set('n', name)
        el.text = str(value)
        return el

    def L(parent, name):
        el = ET.SubElement(parent, 'L')
        el.set('n', name)
        return el

    # Nome que aparece no menu
    T(root, 'display_name', f'0x{display_name_hash:08X}')

    # Categoria do menu social
    T(root, 'mixer_category_tag', category_value)

    # Quanto a relação muda
    T(root, 'relationship_change_with_tuning', relationship_change)

    # Autonomia
    T(root, 'allow_autonomous', 'True' if allow_autonomy else 'False')

    # Filtros de relação mínima (vazio = qualquer sim pode usar)
    rel = min_relationship if min_relationship != 'any' else None
    filters = L(root, 'target_filter')
    if rel and rel in RELATIONSHIP_BITS:
        bit_el = ET.SubElement(filters, 'T')
        bit_el.text = RELATIONSHIP_BITS[rel]

    # Animações: lista vazia (preencher futuramente com clip hashes)
    L(root, '_super_affordance_compatibility')

    return _pretty_xml(root)


# ---------------------------------------------------------------------------
# ResourceID helper para tuning
# ---------------------------------------------------------------------------

def make_tuning_rid(tuning_name: str, type_id: int, group: int = 0x80000000):
    """
    Cria um ResourceID compatível com o DbpfPackage para um recurso de tuning.

    O group padrão 0x80000000 é o que o S4Studio e o s4pe usam pra tunings
    personalizados (high-bit group).

    Retorna uma instância de ResourceID pronta pra usar com DbpfPackage.put().
    """
    # Importação local pra não criar dependência circular
    from packer.resource import ResourceID
    instance = hash_instance(tuning_name)
    return ResourceID(group=group, instance=instance, type=type_id)


# ---------------------------------------------------------------------------
# Builder de pacote completo de mod
# ---------------------------------------------------------------------------

def build_mod_package(
    output_path: str,
    traits: list,
    socials: list,
    stbl_strings: dict,
    target_lang: str = 'POR_BR',
):
    """
    Constrói um .package completo com traços, interações sociais e strings.

    Parâmetros:
        output_path   - caminho do arquivo .package a ser criado
        traits        - lista de dicts com os dados dos traços (ver abaixo)
        socials       - lista de dicts com os dados das interações sociais
        stbl_strings  - dict { hash_int: texto } com todas as strings do mod
        target_lang   - locale da STBL a gerar (ex: 'POR_BR', 'ENG_US')

    Estrutura de cada trait em 'traits':
        {
          'name': 'meu_mod:trait_criativo',      # nome completo do tuning
          'display_name': 'Criativo',             # texto exibido no jogo
          'description': 'Descrição do traço.',   # descrição exibida
          'trait_type': 'personality',            # ver TRAIT_TYPES
          'availability': 'teen_up',              # ver AVAILABILITY
          'buy_price': 0,
        }

    Estrutura de cada social em 'socials':
        {
          'name': 'meu_mod:social_piada',
          'display_name': 'Contar Piada',
          'category': 'funny',
          'min_relationship': 'any',
          'relationship_change': 10,
          'allow_autonomy': True,
        }
    """
    from packer.dbpf import DbpfPackage
    from packer.stbl import Stbl
    from packer.resource import ResourceID

    # Mapeia locale → language code (prefixo hexadecimal do instance ID da STBL)
    # Esses valores vêm do formato interno do jogo
    LANG_CODES = {
        'ENG_US': '0x00', 'ENG_UK': '0x01', 'FRE_FR': '0x02',
        'GER_DE': '0x03', 'ITA_IT': '0x06', 'SPA_ES': '0x07',
        'NED_NL': '0x08', 'POR_BR': '0x16', 'CHI_CN': '0x0D',
        'CHI_TW': '0x0E', 'CZE_CZ': '0x0F', 'DAN_DK': '0x09',
        'FIN_FI': '0x0B', 'JPN_JP': '0x11', 'KOR_KR': '0x12',
        'NOR_NO': '0x0C', 'POL_PL': '0x15', 'RUS_RU': '0x17',
        'SPA_MX': '0x13', 'SWE_SE': '0x0A', 'THA_TH': '0x1A',
    }

    # Calcula os hashes de todas as strings e monta o dicionário da STBL
    # A STBL do mod vai conter as strings de TODAS as traits e socials
    all_strings = {}  # { hash_int: texto }

    processed_traits = []
    for trait_data in traits:
        dn_hash = fnv32(trait_data['display_name'])
        desc_hash = fnv32(trait_data['description'])
        all_strings[dn_hash] = trait_data['display_name']
        all_strings[desc_hash] = trait_data['description']
        processed_traits.append({**trait_data, 'dn_hash': dn_hash, 'desc_hash': desc_hash})

    processed_socials = []
    for social_data in socials:
        dn_hash = fnv32(social_data['display_name'])
        all_strings[dn_hash] = social_data['display_name']
        processed_socials.append({**social_data, 'dn_hash': dn_hash})

    # Strings extras passadas diretamente (ex: buff texts)
    all_strings.update(stbl_strings)

    with DbpfPackage.write(output_path) as pkg:

        # 1. Escreve cada Trait XML
        for t in processed_traits:
            xml_bytes = build_trait_xml(
                tuning_name=t['name'],
                display_name_hash=t['dn_hash'],
                description_hash=t['desc_hash'],
                trait_type=t.get('trait_type', 'personality'),
                availability=t.get('availability', 'teen_up'),
                buy_price=t.get('buy_price', 0),
            )
            rid = make_tuning_rid(t['name'], TYPE_TRAIT)
            pkg.put(rid, xml_bytes)

        # 2. Escreve cada Social Interaction XML
        for s in processed_socials:
            xml_bytes = build_social_xml(
                tuning_name=s['name'],
                display_name_hash=s['dn_hash'],
                category=s.get('category', 'friendly'),
                min_relationship=s.get('min_relationship', 'any'),
                relationship_change=s.get('relationship_change', 10),
                allow_autonomy=s.get('allow_autonomy', True),
            )
            rid = make_tuning_rid(s['name'], TYPE_SOCIAL)
            pkg.put(rid, xml_bytes)

        # 3. Escreve a STBL com todas as strings do mod
        if all_strings:
            lang_code_hex = LANG_CODES.get(target_lang, '0x16')
            lang_code_int = int(lang_code_hex, 16)

            # Instance ID da STBL: language code (1 byte) + FNV32 das strings concatenadas
            # Convenção: usamos um hash fixo do nome do mod como base da instância
            mod_name = processed_traits[0]['name'].split(':')[0] if processed_traits else 'mod'
            base_hash = fnv32(mod_name) & 0x00FFFFFFFFFFFFFF
            stbl_instance = (lang_code_int << 56) | base_hash

            stbl_rid = ResourceID(
                group=0x80000000,
                instance=stbl_instance,
                type=TYPE_STBL,
            )

            stbl = Stbl(stbl_rid)
            for key_hash, text in all_strings.items():
                stbl.add(key_hash, text)

            pkg.put(stbl_rid, stbl.binary)
