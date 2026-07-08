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
    create_engine, MetaData, Table, Column, Integer, String, Text,
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

NUMERIC_COLUMNS = [c for c in CANONICAL_COLUMNS if c != "timestamp_ms"]

MAX_NOISE_SAMPLES_STORED = 200  # limite de eventos de ruído guardados por sessão


def _normalize_header(name: str) -> str:
    """Remove acentos, espaços, underscores e pontuação; deixa minúsculo."""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _build_column_index_map(header_fields):
    """Retorna {indice_da_coluna: nome_canonico} e lista de colunas não reconhecidas."""
    reverse = {}
    for canon, aliases in _ALIASES.items():
        for a in aliases:
            reverse[a] = canon

    col_map = {}
    unrecognized = []
    for idx, raw in enumerate(header_fields):
        key = _normalize_header(raw)
        canon = reverse.get(key)
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
        Column("nome_teste", String(150)),
        Column("data_teste", Date, nullable=False),
        Column("nome_piloto", String(100), nullable=True),
        Column("config_carro", Text),
        Column("observacoes", Text),
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
    adicionadas após o banco já ter sido criado com um schema anterior)."""
    with engine.begin() as conn:
        upgrade_stmts = ["ALTER TABLE sessoes_testes ADD COLUMN nome_teste VARCHAR(150)"]
        for c in NUMERIC_COLUMNS:
            upgrade_stmts.append(f"ALTER TABLE telemetria_dados ADD COLUMN {c} FLOAT")
        for stmt in upgrade_stmts:
            try:
                conn.execute(text(stmt))
            except Exception:
                pass  # coluna já existe (ou banco recém-criado já a inclui)


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


def parse_datalog_csv(file_bytes_or_buffer):
    """Lê um CSV bruto da ESP32 (formato antigo OU novo, ou qualquer subconjunto de
    colunas reconhecidas) e devolve:
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

    try:
        header_line = _decode_line(lines[0])
    except UnicodeDecodeError:
        raise ValueError("O cabeçalho do CSV está corrompido e não pôde ser lido.")

    header_fields = next(csv.reader([header_line]))
    col_map, unrecognized_columns = _build_column_index_map(header_fields)

    if "timestamp_ms" not in col_map.values():
        raise ValueError(
            "O arquivo CSV não contém uma coluna de tempo reconhecível "
            "(ex: 'Timestamp' ou 'Tempo(ms)'). Verifique se é um datalog da ESP32 da KRT."
        )
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

    for line_no, raw_line in enumerate(lines[1:], start=2):
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

    return df, noise_events, unrecognized_columns


def available_columns(df: pd.DataFrame) -> list:
    """Colunas de sensor (não-tempo) que possuem ao menos um valor não-nulo."""
    return [c for c in NUMERIC_COLUMNS if c in df.columns and df[c].notna().any()]


def _normalize_optional_text(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def insert_session(nome_teste, data_teste, nome_piloto, config_carro, observacoes,
                    telemetry_df: pd.DataFrame, noise_events=None, id_grupo=None):
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
