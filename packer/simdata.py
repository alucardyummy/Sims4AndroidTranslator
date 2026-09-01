# -*- coding: utf-8 -*-
"""
packer/simdata.py
-----------------
Writer de SimData binário (tipo 0x545AC67A) para mods do Sims 4.

Baseado na implementação oficial do S4TK (sims4toolkit/models):
  src/lib/resources/simdata/serialization/write-simdata.ts

Suporta geração de SimData para Traits (schema "Trait", hash 0xDE2EAF66),
com todos os campos obrigatórios que o jogo exige.
"""

import struct
from .tuning import fnv32, fnv64

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

RELOFFSET_NULL  = -0x80000000  # ponteiro nulo
NO_NAME_HASH    = 0x811C9DC5   # fnv32('') — usado em tabelas sem nome
HEADER_SIZE     = 32           # bytes do header DATA
TABLE_HEADER_SIZE = 28         # bytes por entrada na tabela de headers
SCHEMA_HEADER_SIZE = 24        # bytes por schema header
COLUMN_SIZE     = 20           # bytes por coluna de schema

VERSION = 0x00000101           # versão suportada pelo jogo atual

# ---------------------------------------------------------------------------
# DataType enum (valores do S4TK data-type.ts)
# ---------------------------------------------------------------------------

class DT:
    # ------------------------------------------------------------------
    # ATENÇÃO: os valores abaixo foram corrigidos por comparação byte-a-byte
    # entre o SimData que este script gerava (bunda_seca.package) e o SimData
    # REAL de um trait extraído de VampireDoughnut_ClonesAddOn.package
    # (instance 0xFAAFC01F, schema "Trait"). A tabela antiga inteira estava
    # errada — não eram os valores do S4TK data-type.ts, e sim outra coisa
    # (possivelmente uma versão antiga/errada, ou confundida com outro enum).
    #
    # Valores marcados [CONFIRMADO] foram vistos diretamente nos bytes do
    # jogo, em múltiplas colunas/tabelas independentes (ver tabela abaixo).
    # Valores marcados [NÃO VERIFICADO] são os antigos, mantidos apenas
    # porque não aparecem no schema "Trait" e não há amostra real para
    # confirmá-los — NÃO confie neles sem checar contra um binário real
    # antes de usar em outro schema.
    #
    #   campo real e tipo semântico      -> dtype visto no binário
    #   ------------------------------------------------------------
    #   _collapsible / display_in_sim_profile (Boolean)     -> 0x00
    #   char table (strings brutas)     (Character)         -> 0x01
    #   trait_type / RawTable de Int64   (Int64)             -> 0x08
    #   cas_trait_asm_param/state (String, ponteiro só)      -> 0x0B
    #   ages/tags/genders/species/... (Vector)               -> 0x0E
    #   ObjectTable (linha da instância) (Object)             -> 0x0D
    #   cas_idle_asm_key/icon/... (ResourceKey)               -> 0x13
    #   display_name/trait_description/... (LocalizationKey) -> 0x14
    #   ui_category (Variant)                                -> 0x15
    #
    Boolean          = 0x00   # [CONFIRMADO]
    Character        = 0x01   # [CONFIRMADO]
    Int8             = 0x02   # [NÃO VERIFICADO]
    UInt8            = 0x03   # [NÃO VERIFICADO]
    Int16            = 0x04   # [NÃO VERIFICADO]
    UInt16           = 0x05   # [NÃO VERIFICADO]
    Int32            = 0x06   # [NÃO VERIFICADO]
    UInt32           = 0x07   # [NÃO VERIFICADO]
    Int64            = 0x08   # [CONFIRMADO]
    UInt64           = 0x09   # [NÃO VERIFICADO]
    Float            = 0x0A   # [NÃO VERIFICADO]
    String           = 0x0B   # [CONFIRMADO]
    Object           = 0x0D   # [CONFIRMADO]
    Vector           = 0x0E   # [CONFIRMADO]
    HashedString     = 0x0F   # [NÃO VERIFICADO]
    TableSetReference= 0x12   # [ALTA CONFIANÇA, não 100% confirmado — visto em
                               #  "_parent"/"mood_type", campos que são
                               #  referências/hashes pra outra linha/instância;
                               #  a semântica bate com o nome, mas não achei um
                               #  campo chamado literalmente "TableSetReference"]
    ResourceKey      = 0x13   # [CONFIRMADO]
    LocalizationKey  = 0x14   # [CONFIRMADO]
    Variant          = 0x15   # [CONFIRMADO]
    Float2           = 0x1A   # [NÃO VERIFICADO]
    Float3           = 0x1B   # [NÃO VERIFICADO]
    Float4           = 0x1C   # [NÃO VERIFICADO]

# Tamanho em bytes de cada tipo primitivo
DT_SIZE = {
    DT.Boolean:          1,
    DT.Character:        1,
    DT.Int8:             1,
    DT.UInt8:            1,
    DT.Int16:            2,
    DT.UInt16:           2,
    DT.Int32:            4,
    DT.UInt32:           4,
    DT.TableSetReference:8,
    DT.Float:            4,
    DT.LocalizationKey:  4,
    DT.Int64:            8,
    DT.UInt64:           8,
    DT.Float2:           8,
    DT.Float3:           12,
    DT.Float4:           16,
    DT.ResourceKey:      16,
    DT.String:           4,   # pointer
    DT.HashedString:     4,   # pointer
    DT.Object:           4,   # pointer
    DT.Vector:           8,   # pointer + count
    DT.Variant:          8,   # pointer + typeHash
}

# Alinhamento em bytes de cada tipo
DT_ALIGN = {
    DT.Boolean:          1,
    DT.Character:        1,
    DT.Int8:             1,
    DT.UInt8:            1,
    DT.Int16:            2,
    DT.UInt16:           2,
    DT.Int32:            4,
    DT.UInt32:           4,
    DT.TableSetReference:8,
    DT.Float:            4,
    DT.LocalizationKey:  4,
    DT.Int64:            8,
    DT.UInt64:           8,
    DT.Float2:           8,
    DT.Float3:           4,
    DT.Float4:           4,
    # [CONFIRMADO] ResourceKey precisa de alinhamento 8, não 4 — comprovado
    # comparando os offsets reais das colunas do Trait: com align=4 o campo
    # 'cas_selected_icon' cairia em offset 44, mas no binário real do jogo
    # ele está em offset 48 (padding de 4 bytes antes dele). Isso também
    # bate com o fato de ResourceKey começar com um uint64 (instance),
    # que naturalmente pede alinhamento de 8.
    DT.ResourceKey:      8,
    DT.String:           4,
    DT.HashedString:     4,
    DT.Object:           4,
    DT.Vector:           4,
    DT.Variant:          4,
}

def get_padding(pos: int, align: int) -> int:
    """Bytes de padding necessários para alinhar pos a align bytes."""
    return (-pos) & (align - 1)

# ---------------------------------------------------------------------------
# Schema do Trait — extraído do trait.xml do S4TK
# Campos na ordem de escrita (ASCII), valores padrão seguros
# ---------------------------------------------------------------------------

# [CONFIRMADO] O hash antigo (0xDE2EAF66) não bate com o schema "Trait" real.
# Extraído byte-a-byte do SimData do trait real (instance 0xFAAFC01F) dentro
# de VampireDoughnut_ClonesAddOn.package: campo schema_hash no schema header,
# offset +8.
TRAIT_SCHEMA_HASH = 0x53D584C8
TRAIT_SCHEMA_NAME = "Trait"

# [CONFIRMADO] O nome da ObjectTable (a linha que representa a instância do
# Trait) NÃO é o nome do tuning — é sempre o literal "Constructor" em TODOS
# os SimData reais verificados (Trait, Buff, RelationshipBit, PieMenuCategory,
# RelationshipTrack). Conferido inclusive despejando a string table crua do
# SimData real: ela contém as colunas, depois "Trait", depois "Constructor" —
# o nome do tuning/instância não aparece em lugar nenhum do binário. A versão
# antiga deste código escrevia `instance_name` nesse campo, o que gerava um
# name_hash errado (fnv32 do nome do tuning em vez de fnv32("Constructor")).
CONSTRUCTOR_NAME = "Constructor"

# (nome, DataType, flags)
TRAIT_COLUMNS = [
    ("ages",                    DT.Vector,           0),
    ("bb_filter_styles",        DT.Vector,           0),
    ("bb_filter_tags",          DT.Vector,           0),
    ("cas_idle_asm_key",        DT.ResourceKey,      0),
    ("cas_idle_asm_state",      DT.String,           0),
    ("cas_selected_icon",       DT.ResourceKey,      0),
    ("cas_trait_asm_param",     DT.String,           0),
    ("conflicting_traits",      DT.Vector,           0),
    ("display_name",            DT.LocalizationKey,  0),
    ("genders",                 DT.Vector,           0),
    ("icon",                    DT.ResourceKey,      0),
    ("species",                 DT.Vector,           0),
    ("tags",                    DT.Vector,           0),
    ("trait_description",       DT.LocalizationKey,  0),
    ("trait_origin_description",DT.LocalizationKey,  0),
    ("trait_type",              DT.Int64,            0),
    ("ui_category",             DT.Variant,          0),
]

# ages: quais faixas etárias podem ter o traço (valores Int64 do trait.xml)
# Teen=8, YoungAdult=16, Adult=32, Elder=64
AGE_TEEN_PLUS = [8, 16, 32, 64]
AGE_ALL       = [2, 4, 8, 16, 32, 64]  # Toddler+

# trait_type: 0=PERSONALITY, 1=BONUS, 2=GAMEPLAY, 3=SOCIAL
TRAIT_TYPE_MAP = {
    'personality': 0,
    'bonus':       1,
    'gameplay':    2,
    'social':      3,
}

# ---------------------------------------------------------------------------
# ByteWriter — helper mínimo de escrita binária
# ---------------------------------------------------------------------------

class ByteWriter:
    def __init__(self):
        self._buf = bytearray()

    def tell(self) -> int:
        return len(self._buf)

    def write_bytes(self, data: bytes):
        self._buf.extend(data)

    def write_u8(self, v: int):
        self._buf.extend(struct.pack('<B', v & 0xFF))

    def write_i32(self, v: int):
        self._buf.extend(struct.pack('<i', v))

    def write_u32(self, v: int):
        self._buf.extend(struct.pack('<I', v & 0xFFFFFFFF))

    def write_i64(self, v: int):
        self._buf.extend(struct.pack('<q', v))

    def write_u64(self, v: int):
        self._buf.extend(struct.pack('<Q', v & 0xFFFFFFFFFFFFFFFF))

    def write_u16(self, v: int):
        self._buf.extend(struct.pack('<H', v & 0xFFFF))

    def pad(self, n: int):
        self._buf.extend(b'\x00' * n)

    def patch_i32(self, offset: int, v: int):
        struct.pack_into('<i', self._buf, offset, v)

    def patch_u32(self, offset: int, v: int):
        struct.pack_into('<I', self._buf, offset, v & 0xFFFFFFFF)

    def patch_resource_key(self, offset: int, key) -> None:
        """
        Escreve um ResourceKey binário de 16 bytes no offset indicado.

        Ordem confirmada no código-fonte aberto do S4TK (@s4tk/models,
        src/lib/resources/simdata/cells.ts, ResourceKeyCell.encode/decode):

            encoder.uint64(self.instance)
            encoder.uint32(self.type)
            encoder.uint32(self.group)

        Ou seja: instance (8 bytes) -> type (4 bytes) -> group (4 bytes),
        tudo little-endian. Repare que é a ordem OPOSTA da notação textual
        usada no XML (que mostra "type-group-instance").

        'key' aceita qualquer objeto com atributos .type, .group, .instance
        — em particular, um packer.resource.ResourceID serve direto.
        """
        struct.pack_into(
            '<QII', self._buf, offset,
            key.instance & 0xFFFFFFFFFFFFFFFF,
            key.type & 0xFFFFFFFF,
            key.group & 0xFFFFFFFF,
        )

    def get_bytes(self) -> bytes:
        return bytes(self._buf)


# ---------------------------------------------------------------------------
# Gerador de SimData para Trait
# ---------------------------------------------------------------------------

def build_trait_simdata(
    instance_name: str,
    display_name_hash: int,
    description_hash: int,
    trait_type: str = 'personality',
    ages: list = None,
    cas_trait_asm_param: str = '',
    icon=None,
    cas_selected_icon=None,
    cas_idle_asm_key=None,
) -> bytes:
    """
    Gera o binário SimData (0x545AC67A) para um Trait.

    Parâmetros:
        instance_name       - nome do tuning, ex: 'teste_mod:trait_feliz'
        display_name_hash   - FNV32 do nome de exibição (LocalizationKey)
        description_hash    - FNV32 da descrição (LocalizationKey)
        trait_type          - 'personality'|'bonus'|'gameplay'|'social'
        ages                - lista de idades (padrão: Teen+)
        cas_trait_asm_param - parâmetro de animação CAS (pode ficar vazio)
        icon                - ResourceID (ou objeto com .type/.group/.instance)
                               do recurso de imagem (type 0x00B2D882) a usar
                               como ícone do traço no CAS. None = campo fica
                               zerado (comportamento antigo, sem ícone customizado).
        cas_selected_icon   - idem, para o ícone de "já selecionado" no CAS
                               (opcional, o jogo cai pro 'icon' se ficar None).
        cas_idle_asm_key    - ResourceID do ASM (type STATEMACHINE) usado pro
                               idle do CAS. None = campo fica zerado.

    Retorna bytes prontos para inserir no .package com type 0x545AC67A.
    """
    if ages is None:
        ages = AGE_TEEN_PLUS

    trait_type_int = TRAIT_TYPE_MAP.get(trait_type, 0)

    # -----------------------------------------------------------------------
    # 1. Calcula offsets dos campos no schema (seguindo o write-simdata.ts)
    # -----------------------------------------------------------------------

    col_offsets = []
    size = 0
    for (name, dt, flags) in TRAIT_COLUMNS:
        align = DT_ALIGN[dt]
        size += get_padding(size, align)
        col_offsets.append(size)
        size += DT_SIZE[dt]

    # padding final: alinha ao maior tipo do schema
    max_align = max(DT_ALIGN[dt] for (_, dt, _) in TRAIT_COLUMNS)
    size += get_padding(size, max_align)
    schema_size = size  # 192 bytes esperado para Trait

    # -----------------------------------------------------------------------
    # 2. Coleta todos os nomes que precisam ir na string table
    # -----------------------------------------------------------------------
    # Ordem importante: colunas primeiro (por hash ascendente), depois schema
    all_names = set()
    all_names.add(CONSTRUCTOR_NAME)
    all_names.add(TRAIT_SCHEMA_NAME)
    for (name, _, _) in TRAIT_COLUMNS:
        all_names.add(name)
    # strings de texto (cas_trait_asm_param, cas_idle_asm_state)
    all_names.add(cas_trait_asm_param)
    all_names.add('')  # string vazia para campos vazios

    # Constrói a string table: strings em ordem de aparição, null-terminated
    # O S4TK usa a ordem em que os nomes foram hasheados — vamos ordenar
    # por hash ascendente das colunas, depois schema, depois instance
    col_names_sorted = sorted(
        [(name, fnv32(name)) for (name, _, _) in TRAIT_COLUMNS],
        key=lambda x: x[1]
    )
    schema_name_pair = (TRAIT_SCHEMA_NAME, fnv32(TRAIT_SCHEMA_NAME))
    constructor_name_pair = (CONSTRUCTOR_NAME, fnv32(CONSTRUCTOR_NAME))
    asm_param_pair = (cas_trait_asm_param, fnv32(cas_trait_asm_param))
    empty_pair = ('', fnv32(''))

    # Conferido no binário real: a string table lista as colunas em ordem
    # ALFABÉTICA (não por hash — isso só se aplica à tabela de colunas do
    # schema), seguidas de "Trait" e por último "Constructor". O nome do
    # tuning/instância nunca aparece no SimData.
    ordered_names = [name for (name, _, _) in TRAIT_COLUMNS]
    for extra in [TRAIT_SCHEMA_NAME, cas_trait_asm_param, '', CONSTRUCTOR_NAME]:
        if extra not in ordered_names:
            ordered_names.append(extra)

    # Constrói string table bytes e mapeia offset de cada string
    st = ByteWriter()
    str_offsets = {}
    for name in ordered_names:
        if name not in str_offsets:
            str_offsets[name] = st.tell()
            st.write_bytes(name.encode('utf-8') + b'\x00')
    string_table = st.get_bytes()
    string_table_size = len(string_table)

    # -----------------------------------------------------------------------
    # 3. Calcula os offsets do schema buffer
    #    Schema buffer = [schema header 24B] + [columns 20B each]
    # -----------------------------------------------------------------------
    n_cols = len(TRAIT_COLUMNS)
    schema_buf_size = SCHEMA_HEADER_SIZE + (COLUMN_SIZE * n_cols)

    # -----------------------------------------------------------------------
    # 4. Determina quantas tabelas temos
    #    - 1 ObjectTable (a instância do Trait)
    #    - Tabelas de Vector para: ages, species (Int64 vectors)
    #      conflicting_traits (TableSetReference vector),
    #      genders, tags, bb_filter_styles, bb_filter_tags (vazias)
    #    - Tabela de Character (char table para strings de texto)
    # -----------------------------------------------------------------------

    # Para simplificar (e ser compatível), incluímos apenas os vetores não
    # vazios. O jogo aceita listas vazias como offset NULL + count 0.
    # Vetores não-vazios que precisamos: ages, species
    # (conflicting_traits fica vazio, tags fica vazio para traços simples)

    # Cada vetor não-vazio de Int64:
    #   - precisa de uma RawTable de Int64
    # O S4TK usa uma RawTable por DataType (não por campo)
    # Logo: ages (Int64 x N) + species (Int64 x 1) -> mesma RawTable Int64

    ages_values   = ages          # lista de int
    species_values = [1]          # Human = 1

    # ui_category: 0=Emocional, 1=Passatempo, 2=Estilo de Vida, 3=Social
    UI_CATEGORY_MAP = {
        'personality': 0,  # Emocional
        'hobby':       1,  # Passatempo
        'lifestyle':   2,  # Estilo de Vida
        'social':      3,  # Social
    }
    # tags: definem em qual aba do CAS o traço aparece
    # 234 = TraitGroup_Emotional, 756 = TraitGroup_Social
    TAGS_MAP = {
        'personality': [753],   # TraitGroup_Emotional
        'hobby':       [754],   # TraitGroup_Hobbies
        'lifestyle':   [755],   # TraitGroup_Lifestyle
        'social':      [756],   # TraitGroup_Social
        'bonus':       [753],   # default Emotional
        'gameplay':    [753],   # default Emotional
    }
    tags_values = TAGS_MAP.get(trait_type, [234])

    ui_category_value = UI_CATEGORY_MAP.get(trait_type, 0)
    ui_cat_idx_in_table = len(ages_values) + len(species_values)  # após ages e species
    tags_start_idx = ui_cat_idx_in_table + 1  # após ui_category

    int64_values = ages_values + species_values + [ui_category_value] + tags_values  # todos na mesma tabela

    # char table para strings (cas_trait_asm_param, cas_idle_asm_state='')
    char_strings = [cas_trait_asm_param, '']  # '' = cas_idle_asm_state
    char_table_data = b''
    char_offsets = {}  # string -> offset no char table
    char_pos = 0
    for s in char_strings:
        if s not in char_offsets:
            char_offsets[s] = char_pos
            encoded = s.encode('utf-8') + b'\x00'
            char_table_data += encoded
            char_pos += len(encoded)
    char_table_size = len(char_table_data)

    # -----------------------------------------------------------------------
    # 5. Calcula posições absolutas de cada seção no buffer final
    # -----------------------------------------------------------------------
    # Layout: [HEADER 32B] [TABLE HEADERS N*28B] [OBJECT DATA] [INT64 DATA]
    #         [CHAR DATA] [padding] [SCHEMA BUFFER] [STRING TABLE]

    # Quantas tabelas:
    # 1 ObjectTable (Trait instance)
    # 1 RawTable Int64 (ages + species)
    # 1 CharTable (strings)
    num_tables = 3

    tables_header_end = HEADER_SIZE + (num_tables * TABLE_HEADER_SIZE)
    # = 32 + 84 = 116

    # ObjectTable: 1 row de schema_size bytes, alinhada a 16
    obj_table_start = tables_header_end
    obj_table_start += get_padding(obj_table_start, 16)
    obj_table_start += get_padding(obj_table_start, schema_size - 1) if schema_size > 1 else 0
    obj_table_end   = obj_table_start + schema_size  # 1 row

    # Int64 RawTable: alinhada a 16 + alinhamento de Int64 (8)
    int64_table_start = obj_table_end
    int64_table_start += get_padding(int64_table_start, 16)
    int64_table_start += get_padding(int64_table_start, 8 - 1)
    int64_table_size  = len(int64_values) * 8
    int64_table_end   = int64_table_start + int64_table_size

    # Char table: alinhada a 16
    char_table_start = int64_table_end
    char_table_start += get_padding(char_table_start, 16)
    char_table_end   = char_table_start + char_table_size

    # Padding final (alinha a 16)
    data_end = char_table_end + get_padding(char_table_end, 16)

    # Schema e string table vêm depois
    schema_start  = data_end
    schema_end    = schema_start + schema_buf_size
    str_table_start = schema_end
    total_size    = str_table_start + string_table_size

    # -----------------------------------------------------------------------
    # 6. Monta o buffer final
    # -----------------------------------------------------------------------
    buf = ByteWriter()
    buf.pad(total_size)  # aloca tudo zerado

    def patch_i32(offset, value):
        buf.patch_i32(offset, value)

    def patch_u32(offset, value):
        buf.patch_u32(offset, value)

    def patch_u64(offset, value):
        struct.pack_into('<Q', buf._buf, offset, value & 0xFFFFFFFFFFFFFFFF)

    def patch_i64(offset, value):
        struct.pack_into('<q', buf._buf, offset, value)

    def patch_u16(offset, value):
        struct.pack_into('<H', buf._buf, offset, value & 0xFFFF)

    def patch_bytes(offset, data):
        buf._buf[offset:offset+len(data)] = data

    # --- HEADER ---
    pos = 0
    patch_bytes(pos, b'DATA');          pos += 4
    patch_u32(pos, VERSION);            pos += 4
    # tableInfoOffset: relativo à posição do próprio campo (pos, aqui = 8).
    # A primeira TableInfo sempre começa em HEADER_SIZE (32), então o valor
    # correto é HEADER_SIZE - pos. Antes era hardcoded como "24", o que só
    # dava certo por coincidência (HEADER_SIZE=32 e pos=8 nesse ponto fixo).
    patch_i32(pos, HEADER_SIZE - pos);  pos += 4
    patch_i32(pos, num_tables);         pos += 4
    # schema_offset: relativo à posição do PRÓPRIO campo (pos), não ao tell()
    # depois de lê-lo. Confirmado byte-a-byte num SimData real extraído do
    # jogo (SimulationDeltaBuild0.package): o campo tableInfoOffset (na
    # posição 8 do header) tinha o valor 24, e a TableInfo real ficava em
    # 8+24=32 — ou seja, relativo ao início do PRÓPRIO campo, igual à
    # convenção já usada em write_table_header() (name_offset, schema_offset,
    # row_offset todos calculados como "alvo - posição do campo").
    # A versão antiga aqui usava "(pos + 4)" (posição DEPOIS do campo),
    # o que deslocava o schema lido em -4 bytes e corrompia toda a leitura
    # das colunas — provável causa do traço sumir silenciosamente no CAS.
    patch_i32(pos, schema_start - pos); pos += 4
    patch_i32(pos, 1);                  pos += 4   # 1 schema
    patch_u32(pos, 0);                  pos += 4   # unused = 0
    # pos agora = 28, faltam 4 bytes de padding até 32
    # (já estão zerados)
    pos = HEADER_SIZE

    # --- TABLE HEADERS ---

    # Posição onde começa cada table header
    th_obj   = HEADER_SIZE + 0 * TABLE_HEADER_SIZE   # 32
    th_int64 = HEADER_SIZE + 1 * TABLE_HEADER_SIZE   # 60
    th_char  = HEADER_SIZE + 2 * TABLE_HEADER_SIZE   # 88

    # Helper: escreve um table header de 28 bytes na posição p
    def write_table_header(p, name_str, schema_off_abs, data_type, row_size, row_pos_abs, row_count):
        # name offset (relativo ao tell = p)
        if name_str is None:
            patch_i32(p, RELOFFSET_NULL)
            patch_u32(p+4, NO_NAME_HASH)
        else:
            # nome está na string table
            name_abs = str_table_start + str_offsets[name_str]
            patch_i32(p, name_abs - p)
            patch_u32(p+4, fnv32(name_str))
        # schema offset (relativo ao tell = p+8)
        if schema_off_abs is None:
            patch_i32(p+8, RELOFFSET_NULL)
        else:
            patch_i32(p+8, schema_off_abs - (p+8))
        patch_u32(p+12, data_type)
        patch_u32(p+16, row_size)
        # row offset (relativo ao tell = p+20)
        patch_i32(p+20, row_pos_abs - (p+20))
        patch_u32(p+24, row_count)

    # ObjectTable (Trait instance) — nome sempre "Constructor" (ver nota em
    # CONSTRUCTOR_NAME acima). Antes usava `instance_name`, o que gravava um
    # name_hash errado (fnv32 do nome do tuning em vez de fnv32("Constructor")).
    write_table_header(
        th_obj,
        name_str      = CONSTRUCTOR_NAME,
        schema_off_abs= schema_start,       # offset do schema buffer
        data_type     = DT.Object,
        row_size      = schema_size,
        row_pos_abs   = obj_table_start,
        row_count     = 1,
    )

    # Int64 RawTable
    write_table_header(
        th_int64,
        name_str      = None,
        schema_off_abs= None,
        data_type     = DT.Int64,
        row_size      = 8,
        row_pos_abs   = int64_table_start,
        row_count     = len(int64_values),
    )

    # Char Table
    write_table_header(
        th_char,
        name_str      = None,
        schema_off_abs= None,
        data_type     = DT.Character,
        row_size      = 1,
        row_pos_abs   = char_table_start,
        row_count     = char_table_size,
    )

    # --- INT64 DATA ---
    p = int64_table_start
    for v in int64_values:
        patch_i64(p, v)
        p += 8

    # --- CHAR TABLE DATA ---
    patch_bytes(char_table_start, char_table_data)

    # --- OBJECT TABLE DATA (o row do Trait) ---
    # Cada campo é escrito no offset correto dentro da row
    row_base = obj_table_start

    # Índices na int64_table para ages e species
    ages_start_idx   = 0
    species_start_idx = len(ages_values)

    for i, (col_name, dt, _) in enumerate(TRAIT_COLUMNS):
        field_pos = row_base + col_offsets[i]

        if col_name == 'ages':
            # Vector: [int32 offset relativo, uint32 count]
            target = int64_table_start + ages_start_idx * 8
            patch_i32(field_pos, target - field_pos)
            patch_u32(field_pos + 4, len(ages_values))

        elif col_name == 'species':
            # Vector: aponta para a entrada de species na int64_table
            target = int64_table_start + species_start_idx * 8
            patch_i32(field_pos, target - field_pos)
            patch_u32(field_pos + 4, 1)

        elif col_name in ('bb_filter_styles', 'bb_filter_tags',
                          'conflicting_traits', 'genders'):
            # Vector vazio: offset NULL, count 0
            patch_i32(field_pos, RELOFFSET_NULL)
            patch_u32(field_pos + 4, 0)

        elif col_name == 'tags':
            # Vector com as tags de grupo do CAS (ex: 234=Emocional, 756=Social)
            target = int64_table_start + tags_start_idx * 8
            patch_i32(field_pos, target - field_pos)
            patch_u32(field_pos + 4, len(tags_values))

        elif col_name == 'display_name':
            # LocalizationKey = uint32
            patch_u32(field_pos, display_name_hash & 0xFFFFFFFF)

        elif col_name == 'trait_description':
            patch_u32(field_pos, description_hash & 0xFFFFFFFF)

        elif col_name == 'trait_origin_description':
            patch_u32(field_pos, 0)

        elif col_name == 'cas_idle_asm_key':
            # ResourceKey: 16 bytes zerados se não informado (comportamento antigo)
            if cas_idle_asm_key is not None:
                buf.patch_resource_key(field_pos, cas_idle_asm_key)

        elif col_name == 'cas_selected_icon':
            # ResourceKey: 16 bytes zerados se não informado (comportamento antigo)
            if cas_selected_icon is not None:
                buf.patch_resource_key(field_pos, cas_selected_icon)

        elif col_name == 'icon':
            # ResourceKey: 16 bytes zerados se não informado (comportamento antigo,
            # sem ícone customizado). Se informado, escreve instance+type+group
            # reais na ordem confirmada pelo S4TK (ver patch_resource_key acima).
            if icon is not None:
                buf.patch_resource_key(field_pos, icon)

        elif col_name == 'cas_idle_asm_state':
            # String: [int32 offset relativo ao char table]
            target = char_table_start + char_offsets.get('', 0)
            patch_i32(field_pos, target - field_pos)

        elif col_name == 'cas_trait_asm_param':
            # String
            target = char_table_start + char_offsets.get(cas_trait_asm_param, 0)
            patch_i32(field_pos, target - field_pos)

        elif col_name == 'trait_type':
            # Int64
            patch_i64(field_pos, trait_type_int)

        elif col_name == 'ui_category':
            # Variant: [int32 offset relativo ao valor Int64 na rawTable, uint32 typeHash]
            # O valor Int64 do ui_category fica no fim da int64_table
            target = int64_table_start + ui_cat_idx_in_table * 8
            patch_i32(field_pos, target - field_pos)
            patch_u32(field_pos + 4, 0x603EAA6C)  # typeHash do variant (do trait.xml)

    # --- SCHEMA BUFFER ---
    sp = schema_start

    # Schema header (24 bytes)
    # name_offset: relativo ao tell = sp
    name_abs = str_table_start + str_offsets[TRAIT_SCHEMA_NAME]
    patch_i32(sp,    name_abs - sp)
    patch_u32(sp+4,  fnv32(TRAIT_SCHEMA_NAME))
    patch_u32(sp+8,  TRAIT_SCHEMA_HASH)
    patch_u32(sp+12, schema_size)
    # column_offset: relativo ao tell = sp+16, aponta para sp+24
    first_col_pos = sp + SCHEMA_HEADER_SIZE
    patch_i32(sp+16, first_col_pos - (sp+16))
    patch_u32(sp+20, n_cols)

    # Colunas (20 bytes cada), ordenadas por hash ascendente (como o S4TK faz)
    sorted_cols = sorted(
        enumerate(TRAIT_COLUMNS),
        key=lambda x: fnv32(x[1][0])
    )

    cp = first_col_pos
    for orig_idx, (col_name, dt, flags) in sorted_cols:
        col_name_abs = str_table_start + str_offsets[col_name]
        patch_i32(cp,    col_name_abs - cp)
        patch_u32(cp+4,  fnv32(col_name))
        patch_u16(cp+8,  dt)
        patch_u16(cp+10, flags)
        patch_u32(cp+12, col_offsets[orig_idx])
        patch_i32(cp+16, RELOFFSET_NULL)
        cp += COLUMN_SIZE

    # --- STRING TABLE ---
    patch_bytes(str_table_start, string_table)

    return buf.get_bytes()


# ---------------------------------------------------------------------------
# ResourceID helper para SimData
# ---------------------------------------------------------------------------

def make_simdata_rid(tuning_name: str, group: int = 0x0017E896, instance: int = None):
    """
    Cria um ResourceID para o SimData de um trait.

    O group padrão 0x0017E896 é o que o S4TK usa para SimData de Trait
    (SimDataGroup.Trait conforme visto no pacote de exemplo).

    instance - DEVE ser o mesmo valor usado no ResourceID do tuning XML
               correspondente (ver trait_instance_id() em tuning.py). Se
               None, cai no comportamento antigo: fnv64(tuning_name) puro,
               de 64 bits — isso é o que causava o traço não aparecer no
               CAS quando trait_type='personality' (regra do jogo exige
               instance de 32 bits pra traits de personalidade). Sempre
               passe o instance explicitamente ao montar um Trait a partir
               de build_mod_package().
    """
    from packer.resource import ResourceID
    from packer.tuning import fnv64
    if instance is None:
        instance = fnv64(tuning_name)
    return ResourceID(group=group, instance=instance, type=0x545AC67A)
