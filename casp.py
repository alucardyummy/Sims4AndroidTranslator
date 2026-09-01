# -*- coding: utf-8 -*-
"""
casp.py — Parser e writer para recursos CASP (CAS Part) do Sims 4.

Baseado em CASPartResourceTS4.cs (s4ptacle/Sims4Tools)
e nos flags documentados pelo S4TK.

Uso básico:
    casp = CaspResource(raw_bytes)   # lê do binário
    casp.name = "MeuSkinDetail"      # modifica campos
    casp.age_gender = AgeGender.ALL  # todos gêneros/idades
    new_bytes = casp.to_bytes()      # serializa de volta
"""

import struct
import io


# ---------------------------------------------------------------------------
# Constantes de flags
# ---------------------------------------------------------------------------

class BodyType:
    """Tipos de corpo/slot do CAS."""
    SKIN_DETAIL     = 16   # 0x10  ← nosso alvo
    FACE_DETAIL     = 17
    BODY            = 0
    HEAD            = 1
    HAIR            = 2
    TORSO           = 3
    LEGS            = 4
    SHOES           = 5
    ACCESSORY       = 12
    MAKEUP          = 14
    TATTOO          = 15
    SCAR            = 18
    NOSE            = 19
    MOUTH           = 20
    EYEBROW         = 21
    EYE_COLOR       = 22
    EARRING         = 23


class AgeGender:
    """Flags de idade e gênero (bitmask uint32)."""
    HUMAN       = 0x00000001
    MALE        = 0x00000002
    FEMALE      = 0x00000004
    CHILD       = 0x00000020
    TEEN        = 0x00000040
    YOUNG_ADULT = 0x00000080
    ADULT       = 0x00000100
    ELDER       = 0x00000200

    # Combinações úteis
    ALL_GENDERS = MALE | FEMALE
    ALL_AGES    = CHILD | TEEN | YOUNG_ADULT | ADULT | ELDER
    ALL         = HUMAN | ALL_GENDERS | ALL_AGES   # 0x000003E7


class ParmFlag:
    """Flags gerais do CAS part."""
    DEFAULT_FOR_BODY_TYPE     = 0x01
    DEFAULT_THUMBNAIL_PART    = 0x02
    ALLOW_FOR_CAS_RANDOM      = 0x04
    SHOW_IN_UI                = 0x08
    SHOW_IN_SIM_INFO_PANEL    = 0x10
    SHOW_IN_CAS_DEMO          = 0x20
    ALLOW_FOR_LIVE_RANDOM     = 0x40
    RESTRICT_OPPOSITE_GENDER  = 0x80

    # Default para skin detail visível no CAS
    SKIN_DETAIL_DEFAULT = ALLOW_FOR_CAS_RANDOM | SHOW_IN_UI


# ---------------------------------------------------------------------------
# Helpers de leitura/escrita
# ---------------------------------------------------------------------------

def _read_uint32(f):   return struct.unpack('<I', f.read(4))[0]
def _read_int32(f):    return struct.unpack('<i', f.read(4))[0]
def _read_uint64(f):   return struct.unpack('<Q', f.read(8))[0]
def _read_uint16(f):   return struct.unpack('<H', f.read(2))[0]
def _read_float(f):    return struct.unpack('<f', f.read(4))[0]
def _read_byte(f):     return struct.unpack('B', f.read(1))[0]

def _write_uint32(f, v):  f.write(struct.pack('<I', v))
def _write_int32(f, v):   f.write(struct.pack('<i', v))
def _write_uint64(f, v):  f.write(struct.pack('<Q', v))
def _write_uint16(f, v):  f.write(struct.pack('<H', v))
def _write_float(f, v):   f.write(struct.pack('<f', v))
def _write_byte(f, v):    f.write(struct.pack('B', v))


def _read_big_endian_unicode_string(f):
    """Lê string UTF-16 LE com comprimento em bytes (uint16 LE) prefixado."""
    length_bytes = struct.unpack('<H', f.read(2))[0]  # LE, em bytes
    raw = f.read(length_bytes)
    return raw.decode('utf-16-le')


def _write_big_endian_unicode_string(f, s):
    """Escreve string UTF-16 LE com comprimento em bytes (uint16 LE) prefixado."""
    encoded = s.encode('utf-16-le')
    f.write(struct.pack('<H', len(encoded)))  # LE, em bytes
    f.write(encoded)


def _read_flag_list(f):
    """
    Lê a FlagList do CASP (lista de pares tag/value uint16).
    Formato: count (uint16) + count * (tag uint16 + value uint16)
    """
    count = _read_uint16(f)
    flags = []
    for _ in range(count):
        tag   = _read_uint16(f)
        value = _read_uint16(f)
        flags.append((tag, value))
    return flags


def _write_flag_list(f, flags):
    _write_uint16(f, len(flags))
    for tag, value in flags:
        _write_uint16(f, tag)
        _write_uint16(f, value)


def _read_swatch_color_list(f):
    """
    Lê a SwatchColorList (lista de cores RGBA uint32).
    Formato: count (byte) + count * uint32
    """
    count = _read_byte(f)
    colors = []
    for _ in range(count):
        colors.append(_read_uint32(f))
    return colors


def _write_swatch_color_list(f, colors):
    _write_byte(f, len(colors))
    for c in colors:
        _write_uint32(f, c)


def _read_lod_block_list(f, tgi_list):
    """
    Lê o LODBlockList — mantemos como bytes crus pois não precisamos
    modificar geometria, só repassar intacto.
    Retorna os bytes brutos do bloco até o final da seção LOD.
    """
    # LODBlockList: count (byte) + count LOD blocks
    # Cada LOD block: lodLevel (byte) + assets count (byte) + assets...
    # Cada asset: sorting (uint16) + id (byte) + levelOfDetail (byte)
    start = f.tell()
    count = _read_byte(f)
    for _ in range(count):
        _read_byte(f)   # lod level
        asset_count = _read_byte(f)
        for _ in range(asset_count):
            f.read(4)   # sorting uint16 + id byte + lod byte
    end = f.tell()
    f.seek(start)
    raw = f.read(end - start)
    return raw


# ---------------------------------------------------------------------------
# Classe principal
# ---------------------------------------------------------------------------

class CaspResource:
    """
    Parser e writer para recursos CASP do Sims 4.
    
    Campos principais modificáveis:
        name        (str)   — nome interno do item
        age_gender  (int)   — bitmask AgeGender.*
        body_type   (int)   — BodyType.*
        parm_flags  (int)   — ParmFlag.*
        sort_priority (float)
        swatch_colors (list of uint32 RGBA)
    """

    def __init__(self, data: bytes):
        self._raw = data
        self._parse()

    def _parse(self):
        f = io.BytesIO(self._raw)

        self.version            = _read_uint32(f)
        tgi_offset_raw          = _read_uint32(f)
        self._tgi_offset        = tgi_offset_raw + 8   # absoluto
        self.preset_count       = _read_uint32(f)

        self.name               = _read_big_endian_unicode_string(f)
        self.sort_priority      = _read_float(f)
        self.secondary_sort_idx = _read_uint16(f)
        self.property_id        = _read_uint32(f)
        self.aural_material     = _read_uint32(f)
        self.parm_flags         = _read_byte(f)
        self.exclude_part_flags = _read_uint64(f)
        self.exclude_modifier   = _read_uint32(f)
        self.flag_list          = _read_flag_list(f)
        self.simlolence_price   = _read_uint32(f)
        self.part_title_key     = _read_uint32(f)
        self.part_desc_key      = _read_uint32(f)
        self.unique_tex_space   = _read_byte(f)
        self.body_type          = _read_int32(f)
        self.unused1            = _read_int32(f)
        self.age_gender         = _read_uint32(f)
        self.unused2            = _read_byte(f)
        self.unused3            = _read_byte(f)
        self.swatch_colors      = _read_swatch_color_list(f)
        self.buff_res_key       = _read_byte(f)
        self.variant_thumb_key  = _read_byte(f)
        if self.version >= 0x1C:
            self.voice_effect   = _read_uint64(f)
        else:
            self.voice_effect   = 0
        self.naked_key          = _read_byte(f)
        self.parent_key         = _read_byte(f)
        self.sort_layer         = _read_int32(f)

        # LOD block — guardamos cru para repassar intacto
        self._lod_raw           = _read_lod_block_list(f, None)

        # Slot keys
        slot_count = _read_byte(f)
        self.slot_keys = [_read_byte(f) for _ in range(slot_count)]

        self.diffuse_shadow_key = _read_byte(f)
        self.shadow_key         = _read_byte(f)
        self.composition_method = _read_byte(f)
        self.region_map_key     = _read_byte(f)
        self.overrides          = _read_byte(f)
        self.normal_map_key     = _read_byte(f)
        self.specular_map_key   = _read_byte(f)
        if self.version >= 0x1B:
            self.shared_uv_map  = _read_uint32(f)
        else:
            self.shared_uv_map  = 0

        # TGI list (lemos do offset calculado)
        f.seek(self._tgi_offset)
        tgi_count = _read_byte(f)
        self.tgi_list = []
        for _ in range(tgi_count):
            # TGI block: type(uint32) + group(uint32) + instance(uint64)  = IGT order
            inst  = _read_uint64(f)
            group = _read_uint32(f)
            typ   = _read_uint32(f)
            self.tgi_list.append({'type': typ, 'group': group, 'instance': inst})

    def to_bytes(self) -> bytes:
        """Serializa o CASP de volta para bytes."""
        body = io.BytesIO()

        _write_uint32(body, self.version)
        _write_uint32(body, 0)           # tgi_offset placeholder
        _write_uint32(body, self.preset_count)

        _write_big_endian_unicode_string(body, self.name)
        _write_float(body, self.sort_priority)
        _write_uint16(body, self.secondary_sort_idx)
        _write_uint32(body, self.property_id)
        _write_uint32(body, self.aural_material)
        _write_byte(body, self.parm_flags)
        _write_uint64(body, self.exclude_part_flags)
        _write_uint32(body, self.exclude_modifier)
        _write_flag_list(body, self.flag_list)
        _write_uint32(body, self.simlolence_price)
        _write_uint32(body, self.part_title_key)
        _write_uint32(body, self.part_desc_key)
        _write_byte(body, self.unique_tex_space)
        _write_int32(body, self.body_type)
        _write_int32(body, self.unused1)
        _write_uint32(body, self.age_gender)
        _write_byte(body, self.unused2)
        _write_byte(body, self.unused3)
        _write_swatch_color_list(body, self.swatch_colors)
        _write_byte(body, self.buff_res_key)
        _write_byte(body, self.variant_thumb_key)
        if self.version >= 0x1C:
            _write_uint64(body, self.voice_effect)
        _write_byte(body, self.naked_key)
        _write_byte(body, self.parent_key)
        _write_int32(body, self.sort_layer)

        body.write(self._lod_raw)

        _write_byte(body, len(self.slot_keys))
        for sk in self.slot_keys:
            _write_byte(body, sk)

        _write_byte(body, self.diffuse_shadow_key)
        _write_byte(body, self.shadow_key)
        _write_byte(body, self.composition_method)
        _write_byte(body, self.region_map_key)
        _write_byte(body, self.overrides)
        _write_byte(body, self.normal_map_key)
        _write_byte(body, self.specular_map_key)
        if self.version >= 0x1B:
            _write_uint32(body, self.shared_uv_map)

        # Registra onde começa o TGI offset (relativo a posição 4, offset - 8)
        tgi_pos = body.tell()
        body.seek(4)
        _write_uint32(body, tgi_pos - 8)
        body.seek(tgi_pos)

        # TGI list
        _write_byte(body, len(self.tgi_list))
        for tgi in self.tgi_list:
            _write_uint64(body, tgi['instance'])
            _write_uint32(body, tgi['group'])
            _write_uint32(body, tgi['type'])

        return body.getvalue()

    def summary(self) -> dict:
        """Retorna um dicionário com os campos mais relevantes para debug."""
        return {
            'version':       hex(self.version),
            'name':          self.name,
            'body_type':     self.body_type,
            'age_gender':    hex(self.age_gender),
            'parm_flags':    hex(self.parm_flags),
            'sort_priority': self.sort_priority,
            'swatch_colors': [hex(c) for c in self.swatch_colors],
            'tgi_count':     len(self.tgi_list),
            'tgi_list':      [{'type': hex(t['type']), 'group': hex(t['group']), 'instance': hex(t['instance'])} for t in self.tgi_list],
        }


# ---------------------------------------------------------------------------
# Função de conveniência: clona um CASP e altera campos
# ---------------------------------------------------------------------------

def clone_casp(original_bytes: bytes, new_name: str, age_gender: int = None, body_type: int = None, parm_flags: int = None) -> bytes:
    """
    Clona um CASP existente e altera os campos especificados.
    
    Args:
        original_bytes: bytes do CASP original
        new_name:       novo nome interno
        age_gender:     novo bitmask AgeGender (default: AgeGender.ALL)
        body_type:      novo BodyType (default: mantém o original)
        parm_flags:     novos ParmFlag (default: ParmFlag.SKIN_DETAIL_DEFAULT)
    
    Returns:
        bytes do novo CASP
    """
    casp = CaspResource(original_bytes)
    casp.name = new_name
    if age_gender is not None:
        casp.age_gender = age_gender
    if body_type is not None:
        casp.body_type = body_type
    if parm_flags is not None:
        casp.parm_flags = parm_flags
    return casp.to_bytes()
