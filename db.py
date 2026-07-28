"""
Camada de acesso a dados do Dashboard KRT.

Usa Neon.tech (PostgreSQL Serverless) quando há uma connection string
configurada em st.secrets["NEON_CONN_STRING"]. Caso contrário, cai para um
banco SQLite local (krt_telemetry.db).

Nesta versão, o schema de telemetria foi ampliado para um SUPERCONJUNTO de
colunas, cobrindo tanto o datalog "clássico" da ESP32 (Ax/Ay/Az, Gx/Gy/Gz,
Temp DD/TD/DE/TE, Thermocouple, Peso, Velocidade) quanto o novo datalog com
Ângulo de Volante, Pressão de Fluido de Freio e GPS (Latitude/Longitude/
Satélites). O parser de CSV é orientado a ALIASES: cada coluna do arquivo
é reconhecida por nome (com variações de acentuação/maiúsculas/underscore)
e mapeada para o nome canônico do banco — colunas não reconhecidas são
ignoradas, e colunas canônicas ausentes no arquivo simplesmente ficam nulas.
Isso permite subir datalogs com QUALQUER subconjunto de sensores, e cada
tela/gráfico decide sozinha o que exibir de acordo com o que está disponível.

Além disso, o parser é resiliente a RUÍDO ELÉTRICO no barramento serial da
ESP32: linhas corrompidas (bytes inválidos, número de campos incorreto ou
falha de conversão numérica) são isoladas como "eventos de ruído" — não
quebram a ingestão, e ficam registradas na tabela eventos_ruido para
diagnóstico posterior.
"""

import csv
import unicodedata
from datetime import datetime

import numpy as np
import pandas as pd
import streamlit as st
from sqlalchemy import (
    create_engine, MetaData, Table, Column, Integer, String, Text, inspect,
    Date, DateTime, Float, ForeignKey, select, text
)
from sqlalchemy.exc import OperationalError

LOCAL_SQLITE_PATH = "sqlite:///krt_telemetry.db"

# ---------------------------------------------------------------------------
# SCHEMA CANÔNICO DE TELEMETRIA (superconjunto — cobre datalog antigo e novo)
# ---------------------------------------------------------------------------
CANONICAL_COLUMNS = [
    "timestamp_ms",
    "ax", "ay", "az",
    "gx", "gy", "gz",
    "temp_dd", "temp_td", "temp_de", "temp_te",
    "thermocouple",
    "peso",
    "velocidade",
    "angulo_volante",
    "pressao_fluido",
    "latitude", "longitude", "satelites",
]

COLUMN_LABELS = {
    "timestamp_ms": "Tempo (ms)",
    "ax": "Aceleração X", "ay": "Aceleração Y", "az": "Aceleração Z",
    "gx": "Giroscópio X", "gy": "Giroscópio Y", "gz": "Giroscópio Z",
    "temp_dd": "Temp. Dianteira Direita", "temp_td": "Temp. Traseira Direita",
    "temp_de": "Temp. Dianteira Esquerda", "temp_te": "Temp. Traseira Esquerda",
    "thermocouple": "Termopar (Escapamento)",
    "peso": "Peso (célula de carga)",
    "velocidade": "Velocidade (Km/h)",
    "angulo_volante": "Ângulo de Volante",
    "pressao_fluido": "Pressão de Fluido de Freio",
    "latitude": "Latitude", "longitude": "Longitude", "satelites": "Satélites (GPS)",
}

# Aliases aceitos por coluna canônica (comparados após normalização do header)
_ALIASES = {
    "timestamp_ms": {"timestamp", "tempoms", "tempo", "time", "timems", "t"},
    "ax": {"ax", "accelx", "acelx"},
    "ay": {"ay", "accely", "acely"},
    "az": {"az", "accelz", "acelz"},
    "gx": {"gx", "girox", "gyrox"},
    "gy": {"gy", "giroy", "gyroy"},
    "gz": {"gz", "giroz", "gyroz"},
    "temp_dd": {"tempdd"},
    "temp_td": {"temptd"},
    "temp_de": {"tempde"},
    "temp_te": {"tempte"},
    "thermocouple": {"thermocouple", "termopar"},
    "peso": {"peso", "weight"},
    "velocidade": {"velociadekmh", "velocidadekmh", "velocidade", "speed", "velocidadekm"},
    "angulo_volante": {"angulovolante", "steeringangle", "anglvolante"},
    "pressao_fluido": {"pressaofluido", "brakepressure", "pressaofreio"},
    "latitude": {"latitude", "lat"},
    "longitude": {"longitude", "lon", "lng"},
    "satelites": {"satelites", "satellites", "sats", "nsatelites"},
}

# Mapeamento de colunas por índice para datalogs SEM CABEÇALHO.
# A chave é o número de colunas detectado na linha.
# NOTA: Se houver múltiplos formatos com o mesmo número de colunas,
# a heurística pode não ser capaz de diferenciá-los.
HEADERLESS_COLUMN_MAP = {
    # Datalog "clássico" (pré-2024) - 13 colunas
    "13_classic": [
        "timestamp_ms", "ax", "ay", "az", "gx", "gy", "gz",
        "temp_dd", "temp_td", "temp_de", "temp_te",
        "thermocouple", "velocidade"
    ],
    # Datalog novo (sem giroscópio, com GPS) - 13 colunas
    "13_new": [
        "timestamp_ms", "temp_dd", "temp_td", "temp_te", "temp_de", "ax", "ay", "az",
        "angulo_volante", "pressao_fluido", "latitude", "longitude", "satelites"
    ],
    # Datalog "novo" (com GPS, sem giroscópio/termopar)
    11: [
        "timestamp_ms", "ax", "ay", "az",
        "temp_dd", "temp_td", "temp_de", "temp_te",
        "angulo_volante", "pressao_fluido", "velocidade"
        # GPS é adicionado separadamente se tiver mais colunas
    ],
    14: [ # Datalog novo com GPS
        "timestamp_ms", "ax", "ay", "az",
        "temp_dd", "temp_td", "temp_de", "temp_te",
        "angulo_volante", "pressao_fluido", "velocidade",
        "latitude", "longitude", "satelites"
    ]
}

NUMERIC_COLUMNS = [c for c in CANONICAL_COLUMNS if c != "timestamp_ms"]

MAX_NOISE_SAMPLES_STORED = 200  # limite de eventos de ruído guardados por sessão


def _normalize_header(name: str) -> str:
    """Remove acentos, espaços, underscores e pontuação; deixa minúsculo."""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _build_column_index_map(header_fields):
    """Retorna {indice_da_coluna: nome_canonico} e lista de colunas não reconhecidas.
    Assume que a primeira coluna (índice 0) é sempre o timestamp."""
    reverse = {}
    for canon, aliases in _ALIASES.items():
        for a in aliases:
            reverse[a] = canon

    col_map = {0: "timestamp_ms"}  # Regra: primeira coluna é sempre o tempo.
    unrecognized = []
    # Começa a mapear a partir da segunda coluna (índice 1)
    for idx, raw in enumerate(header_fields[1:], start=1):
        key = _normalize_header(raw)
        canon = reverse.get(key)
        # Caso especial: se outro alias de tempo for encontrado em outra coluna, ignorar.
        if canon == "timestamp_ms":
            unrecognized.append(raw.strip())
            continue
        if canon:
            col_map[idx] = canon
        else:
            unrecognized.append(raw.strip())
    return col_map, unrecognized


@st.cache_resource(show_spinner=False)
def get_engine():
    """Retorna engine SQLAlchemy: Neon.tech se configurado, senão SQLite local."""
    try:
        conn_str = st.secrets.get("NEON_CONN_STRING", None)
    except Exception:
        conn_str = None
    if conn_str:
        try:
            engine = create_engine(conn_str, pool_pre_ping=True)
            with engine.connect():
                pass
            return engine, "neon"
        except OperationalError:
            st.warning(
                "Não foi possível conectar ao Neon.tech com a connection string "
                "fornecida. Usando banco local SQLite temporário (dados não serão "
                "persistidos na nuvem)."
            )
    engine = create_engine(LOCAL_SQLITE_PATH)
    return engine, "sqlite"


def get_metadata():
    metadata = MetaData()

    sessoes_testes = Table(
        "sessoes_testes", metadata,
        Column("id_sessao", Integer, primary_key=True, autoincrement=True),
        Column("nome_teste", String(150), nullable=False),
        Column("data_teste", Date, nullable=False),
        Column("nome_piloto", String(100), nullable=True),
        Column("config_carro", Text),
        Column("observacoes", Text),
        Column("csv_header", Text),
        Column("data_upload", DateTime, default=datetime.utcnow),
    )

    telem_cols = [
        Column("id_registro", Integer, primary_key=True, autoincrement=True),
        Column("id_sessao", Integer, ForeignKey("sessoes_testes.id_sessao", ondelete="CASCADE")),
        Column("timestamp_ms", Integer),
    ]
    for c in NUMERIC_COLUMNS:
        telem_cols.append(Column(c, Float))
    telemetria_dados = Table("telemetria_dados", metadata, *telem_cols)

    grupos_teste = Table(
        "grupos_teste", metadata,
        Column("id_grupo", Integer, primary_key=True, autoincrement=True),
        Column("nome_grupo", String(150), nullable=False),
        Column("descricao", Text),
        Column("data_criacao", DateTime, default=datetime.utcnow),
    )

    grupo_sessoes = Table(
        "grupo_sessoes", metadata,
        Column("id_grupo", Integer, ForeignKey("grupos_teste.id_grupo", ondelete="CASCADE"), primary_key=True),
        Column("id_sessao", Integer, ForeignKey("sessoes_testes.id_sessao", ondelete="CASCADE"), primary_key=True),
    )

    eventos_ruido = Table(
        "eventos_ruido", metadata,
        Column("id_evento", Integer, primary_key=True, autoincrement=True),
        Column("id_sessao", Integer, ForeignKey("sessoes_testes.id_sessao", ondelete="CASCADE")),
        Column("linha_arquivo", Integer),
        Column("timestamp_ms_referencia", Integer),
        Column("amostra_bruta", Text),
    )

    return metadata, sessoes_testes, telemetria_dados, grupos_teste, grupo_sessoes, eventos_ruido


def _ensure_schema_upgrades(engine):
    """Aplica upgrades incrementais em bancos já existentes (colunas novas
    adicionadas após o banco já ter sido criado com um schema anterior).
    É mais robusto que `metadata.create_all(checkfirst=True)` porque inspeciona
    as colunas faltantes e as adiciona, em vez de apenas checar a existência da tabela."""
    inspector = inspect(engine)
    metadata, sessoes_testes, telemetria_dados, *_ = get_metadata()

    with engine.begin() as conn:
        # Adiciona colunas faltantes em `sessoes_testes`
        existing_cols_sessao = {c["name"] for c in inspector.get_columns("sessoes_testes")}
        for col in sessoes_testes.columns:
            if col.name not in existing_cols_sessao:
                try:
                    # A sintaxe de `ADD COLUMN` é padrão, mas o tipo pode precisar de compilação
                    col_type = col.type.compile(engine.dialect)
                    conn.execute(text(f"ALTER TABLE sessoes_testes ADD COLUMN {col.name} {col_type}"))
                except Exception:
                    pass  # Evita falha se a coluna já existir (condição de corrida)

        # Adiciona colunas faltantes em `telemetria_dados`
        existing_cols_telemetria = {c["name"] for c in inspector.get_columns("telemetria_dados")}
        for col in telemetria_dados.columns:
            if col.name not in existing_cols_telemetria:
                try:
                    col_type = col.type.compile(engine.dialect)
                    conn.execute(text(f"ALTER TABLE telemetria_dados ADD COLUMN {col.name} {col_type}"))
                except Exception:
                    pass

        # Garante que `nome_piloto` seja opcional (nullable)
        if engine.dialect.name == "postgresql":
            conn.execute(text("ALTER TABLE sessoes_testes ALTER COLUMN nome_piloto DROP NOT NULL"))
        else:  # sqlite
            try:
                conn.execute(text("ALTER TABLE sessoes_testes RENAME TO _sessoes_testes_old"))
                metadata.tables["sessoes_testes"].create(conn)
                cols = ", ".join([c.name for c in sessoes_testes.columns if c.name in existing_cols_sessao])
                conn.execute(text(f"INSERT INTO sessoes_testes ({cols}) SELECT {cols} FROM _sessoes_testes_old"))
                conn.execute(text("DROP TABLE _sessoes_testes_old"))
            except Exception:
                pass


def init_db():
    engine, backend = get_engine()
    metadata, *_ = get_metadata()
    metadata.create_all(engine)
    _ensure_schema_upgrades(engine)
    return engine, backend


def _decode_line(raw_bytes: bytes) -> str:
    """Decodifica uma linha como UTF-8 estrito (lança exceção se houver bytes
    inválidos — exatamente o tipo de corrupção causada por ruído elétrico na serial)."""
    return raw_bytes.decode("utf-8")


def _is_numeric(s: str) -> bool:
    """Verifica se uma string pode ser convertida para um número (int ou float)."""
    try:
        float(s.strip())
        return True
    except (ValueError, TypeError):
        return False


def _is_numeric_or_blank(s: str) -> bool:
    """Como _is_numeric, mas aceita campo vazio como válido.
    Datalogs sem cabeçalho frequentemente têm campos em branco na primeira
    linha (sensor não conectado nesse ensaio, GPS ainda sem fix, etc.) —
    um campo vazio não descaracteriza a linha como sendo "dado numérico"."""
    return s.strip() == "" or _is_numeric(s)

def parse_datalog_csv(file_bytes_or_buffer):
    """Lê um CSV bruto da ESP32 (formato antigo OU novo, ou qualquer subconjunto de
    colunas reconhecidas) e devolve um dicionário com:
        (df, noise_events, unrecognized_columns)

    df: DataFrame já normalizado para o schema canônico do banco (colunas ausentes
        no arquivo entram como NaN).
    noise_events: lista de dicts {"linha", "timestamp_ms_referencia", "amostra_bruta"}
        — trechos do arquivo que sofreram corrupção (ruído elétrico / falha de
        comunicação serial da ESP32) e foram isolados da ingestão.
    unrecognized_columns: colunas do cabeçalho que não foram reconhecidas (apenas
        informativo — não impede a ingestão).
    """
    if hasattr(file_bytes_or_buffer, "read"):
        raw = file_bytes_or_buffer.read()
    else:
        raw = file_bytes_or_buffer
    if isinstance(raw, str):
        raw = raw.encode("utf-8", errors="replace")

    raw = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    lines = raw.split(b"\n")
    while lines and lines[-1].strip() == b"":
        lines.pop()

    if not lines:
        raise ValueError("Arquivo CSV vazio.")

    header_line = ""
    header_fields = []
    data_start_line_index = 1
    first_line_is_header = False

    try:
        first_line_str = _decode_line(lines[0])
        first_line_fields = next(csv.reader([first_line_str]))

        # Heurística: é um datalog sem cabeçalho se o timestamp (1ª coluna) é
        # numérico e todos os demais campos são numéricos OU vazios (campo em
        # branco = sensor não conectado/sem leitura nessa linha — não é texto
        # de cabeçalho). Antes essa checagem exigia TODOS os campos numéricos,
        # o que rejeitava incorretamente qualquer 1ª linha com sensor vazio.
        if first_line_fields and _is_numeric(first_line_fields[0]) and \
                all(_is_numeric_or_blank(f) for f in first_line_fields[1:]):
            num_cols = len(first_line_fields)
            map_key = None
            if num_cols == 13:
                # Heurística para diferenciar os dois formatos de 13 colunas:
                # O formato "clássico" tem giroscópio (valores pequenos) na 5ª coluna (idx 4),
                # enquanto o formato "novo" tem temperatura (valores maiores).
                try:
                    quinta_coluna_valor = float(first_line_fields[4])
                    if abs(quinta_coluna_valor) < 1.0: # Provavelmente giroscópio
                        map_key = "13_classic"
                    else: # Provavelmente temperatura
                        map_key = "13_new"
                except (ValueError, IndexError):
                    map_key = "13_new" # Fallback para o mais recente
            elif num_cols in HEADERLESS_COLUMN_MAP:
                map_key = num_cols
            
            if map_key in HEADERLESS_COLUMN_MAP:
                header_fields = HEADERLESS_COLUMN_MAP[map_key]
                header_line = ",".join(header_fields)
                data_start_line_index = 0 # Começa a ler dados da primeira linha
            else:
                raise ValueError(f"Arquivo sem cabeçalho com número de colunas não reconhecido ({num_cols}).")
        else:
            first_line_is_header = True
            header_line = first_line_str
            header_fields = first_line_fields
            data_start_line_index = 1
    except (UnicodeDecodeError, ValueError) as e:
        raise ValueError(f"Falha ao processar a primeira linha do arquivo: {e}")

    col_map, unrecognized_columns = _build_column_index_map(header_fields)
    data_cols_found = [c for c in col_map.values() if c != "timestamp_ms"]
    if not data_cols_found:
        raise ValueError(
            "Nenhuma coluna de sensor reconhecida neste arquivo além do tempo. "
            "Verifique se é um datalog gerado pela ESP32 da KRT."
        )

    expected_fields = len(header_fields)
    rows = []
    noise_events = []
    last_good_ts = None

    for line_no, raw_line in enumerate(lines[data_start_line_index:], start=data_start_line_index + 1):
        if raw_line.strip() == b"":
            continue
        try:
            text_line = _decode_line(raw_line)
            fields = next(csv.reader([text_line]))
            if len(fields) != expected_fields:
                raise ValueError("número de campos inconsistente com o cabeçalho")

            row = {}
            for idx, canon in col_map.items():
                val = fields[idx].strip()
                if val == "":
                    row[canon] = None
                    continue
                if canon == "timestamp_ms":
                    row[canon] = int(round(float(val)))
                else:
                    row[canon] = float(val)
            rows.append(row)
            if row.get("timestamp_ms") is not None:
                last_good_ts = row["timestamp_ms"]
        except Exception:
            sample = raw_line.decode("latin-1", errors="replace")
            sample = sample[:100] + ("…" if len(sample) > 100 else "")
            noise_events.append({
                "linha": line_no,
                "timestamp_ms_referencia": last_good_ts,
                "amostra_bruta": sample,
            })
            continue

    if not rows:
        raise ValueError(
            "Nenhuma linha de telemetria válida pôde ser lida — o arquivo pode estar "
            "totalmente corrompido (ruído elétrico severo durante o registro)."
        )

    df = pd.DataFrame(rows)
    for col in CANONICAL_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan
    df = df[CANONICAL_COLUMNS]

    if len(noise_events) > MAX_NOISE_SAMPLES_STORED:
        noise_events = noise_events[:MAX_NOISE_SAMPLES_STORED]

    return {
        "df": df,
        "noise_events": noise_events,
        "unrecognized_columns": unrecognized_columns,
        "header_line": header_line if first_line_is_header else f"Gerado: {header_line}",
    }


def available_columns(df: pd.DataFrame) -> list:
    """Colunas de sensor (não-tempo) que possuem ao menos um valor não-nulo."""
    return [c for c in NUMERIC_COLUMNS if c in df.columns and df[c].notna().any()]


def _normalize_optional_text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        value = value.strip()
        return value or ""
    return value


def insert_session(nome_teste, data_teste, nome_piloto, config_carro, observacoes,
                    telemetry_df: pd.DataFrame, csv_header=None, noise_events=None, id_grupo=None):
    """Insere uma nova sessão de teste + bulk insert dos dados de telemetria e,
    se houver, dos eventos de ruído elétrico detectados durante o parse."""
    engine, backend = get_engine()
    metadata, sessoes_testes, telemetria_dados, grupos_teste, grupo_sessoes, eventos_ruido = get_metadata()
    metadata.create_all(engine)
    _ensure_schema_upgrades(engine)

    nome_piloto = _normalize_optional_text(nome_piloto)

    with engine.begin() as conn:
        result = conn.execute(
            sessoes_testes.insert().values(
                nome_teste=nome_teste,
                data_teste=data_teste,
                nome_piloto=nome_piloto,
                config_carro=config_carro,
                observacoes=observacoes,
                csv_header=csv_header,
                data_upload=datetime.utcnow(),
            )
        )
        id_sessao = result.inserted_primary_key[0]

        records = telemetry_df.copy()
        records["id_sessao"] = id_sessao
        records = records.where(pd.notnull(records), None)
        records = records.to_dict(orient="records")
        if records:
            conn.execute(telemetria_dados.insert(), records)

        if noise_events:
            noise_records = [
                {
                    "id_sessao": id_sessao,
                    "linha_arquivo": ev["linha"],
                    "timestamp_ms_referencia": ev["timestamp_ms_referencia"],
                    "amostra_bruta": ev["amostra_bruta"],
                }
                for ev in noise_events
            ]
            conn.execute(eventos_ruido.insert(), noise_records)

        if id_grupo is not None:
            conn.execute(grupo_sessoes.insert().values(id_grupo=id_grupo, id_sessao=id_sessao))

    return id_sessao


def create_test_group(nome_grupo, descricao=None):
    engine, backend = get_engine()
    metadata, sessoes_testes, telemetria_dados, grupos_teste, grupo_sessoes, eventos_ruido = get_metadata()
    metadata.create_all(engine)
    with engine.begin() as conn:
        result = conn.execute(
            grupos_teste.insert().values(
                nome_grupo=nome_grupo, descricao=descricao, data_criacao=datetime.utcnow()
            )
        )
        id_grupo = result.inserted_primary_key[0]
    return id_grupo


def link_session_to_group(id_sessao, id_grupo):
    engine, backend = get_engine()
    metadata, sessoes_testes, telemetria_dados, grupos_teste, grupo_sessoes, eventos_ruido = get_metadata()
    with engine.begin() as conn:
        conn.execute(grupo_sessoes.insert().values(id_grupo=id_grupo, id_sessao=id_sessao))


def insert_batch_sessions(rows: list, id_grupo=None):
    """`rows`: lista de dicts com nome_teste, data_teste, nome_piloto, config_carro,
    observacoes, df, noise_events (opcional). Retorna lista de id_sessao criados."""
    ids = []
    for row in rows:
        id_sessao = insert_session(
            nome_teste=row["nome_teste"],
            data_teste=row["data_teste"],
            nome_piloto=row["nome_piloto"],
            config_carro=row.get("config_carro"),
            observacoes=row.get("observacoes"),
            telemetry_df=row["df"],
            csv_header=row.get("csv_header"),
            noise_events=row.get("noise_events"),
            id_grupo=id_grupo,
        )
        ids.append(id_sessao)
    return ids


@st.cache_data(ttl=30, show_spinner=False)
def list_sessions(_engine_marker=None):
    engine, backend = get_engine()
    metadata, sessoes_testes, telemetria_dados, grupos_teste, grupo_sessoes, eventos_ruido = get_metadata()
    metadata.create_all(engine)
    with engine.connect() as conn:
        df = pd.read_sql(select(sessoes_testes).order_by(sessoes_testes.c.data_teste.desc()), conn)
    return df


@st.cache_data(ttl=30, show_spinner=False)
def list_test_groups(_engine_marker=None):
    engine, backend = get_engine()
    metadata, sessoes_testes, telemetria_dados, grupos_teste, grupo_sessoes, eventos_ruido = get_metadata()
    metadata.create_all(engine)
    with engine.connect() as conn:
        df = pd.read_sql(select(grupos_teste).order_by(grupos_teste.c.data_criacao.desc()), conn)
    return df


@st.cache_data(ttl=30, show_spinner=False)
def list_sessions_in_group(id_grupo, _engine_marker=None):
    engine, backend = get_engine()
    metadata, sessoes_testes, telemetria_dados, grupos_teste, grupo_sessoes, eventos_ruido = get_metadata()
    query = (
        select(sessoes_testes)
        .select_from(sessoes_testes.join(grupo_sessoes, sessoes_testes.c.id_sessao == grupo_sessoes.c.id_sessao))
        .where(grupo_sessoes.c.id_grupo == id_grupo)
        .order_by(sessoes_testes.c.data_teste)
    )
    with engine.connect() as conn:
        df = pd.read_sql(query, conn)
    return df


@st.cache_data(ttl=30, show_spinner=False)
def load_session_telemetry(id_sessao, _engine_marker=None):
    engine, backend = get_engine()
    metadata, sessoes_testes, telemetria_dados, grupos_teste, grupo_sessoes, eventos_ruido = get_metadata()
    with engine.connect() as conn:
        df = pd.read_sql(
            select(telemetria_dados).where(telemetria_dados.c.id_sessao == id_sessao).order_by(
                telemetria_dados.c.timestamp_ms
            ),
            conn,
        )
    return df


@st.cache_data(ttl=30, show_spinner=False)
def load_session_noise_events(id_sessao, _engine_marker=None):
    engine, backend = get_engine()
    metadata, sessoes_testes, telemetria_dados, grupos_teste, grupo_sessoes, eventos_ruido = get_metadata()
    metadata.create_all(engine)
    with engine.connect() as conn:
        df = pd.read_sql(
            select(eventos_ruido).where(eventos_ruido.c.id_sessao == id_sessao).order_by(
                eventos_ruido.c.linha_arquivo
            ),
            conn,
        )
    return df


def clear_caches():
    list_sessions.clear()
    list_test_groups.clear()
    list_sessions_in_group.clear()
    load_session_telemetry.clear()
    load_session_noise_events.clear()
