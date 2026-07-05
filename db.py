"""
Camada de acesso a dados do Dashboard KRT.

Usa Neon.tech (PostgreSQL Serverless) quando há uma connection string
configurada em st.secrets["NEON_CONN_STRING"]. Caso contrário, cai para um
banco SQLite local (krt_telemetry.db) — útil para rodar e testar o app sem
precisar de credenciais de nuvem, mantendo o mesmo esquema relacional
descrito no documento de especificação.

Além do esquema original (sessoes_testes / telemetria_dados), este módulo
adiciona:
 - Campo nome_teste em sessoes_testes (título/identificação do ensaio).
 - grupos_teste: agrupamento de sessões realizadas em dias diferentes para
   permitir análise conjunta (ex: "Testes de frenagem — Semana 3").
 - grupo_sessoes: tabela de associação N:N entre grupos e sessões, já que
   uma sessão pode fazer sentido em mais de um grupo de análise.
"""

import streamlit as st
import pandas as pd
from sqlalchemy import (
    create_engine, MetaData, Table, Column, Integer, String, Text,
    Date, DateTime, Float, ForeignKey, select, text
)
from sqlalchemy.exc import OperationalError
from datetime import datetime

LOCAL_SQLITE_PATH = "sqlite:///krt_telemetry.db"

# Colunas esperadas nos CSVs gerados pela ESP32 (ver Manual do Calouro / doc de projeto)
CSV_COLUMN_MAP = {
    "Timestamp": "timestamp_ms",
    "Ax": "ax", "Ay": "ay", "Az": "az",
    "Gx": "gx", "Gy": "gy", "Gz": "gz",
    "Temp DD": "temp_dd", "Temp TD": "temp_td",
    "Temp DE": "temp_de", "Temp TE": "temp_te",
    "Thermocouple": "thermocouple",
    "Peso": "peso",
    "Velociade (Km/h)": "velocidade",
}


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
        Column("nome_piloto", String(100), nullable=False),
        Column("config_carro", Text),
        Column("observacoes", Text),
        Column("data_upload", DateTime, default=datetime.utcnow),
    )

    telemetria_dados = Table(
        "telemetria_dados", metadata,
        Column("id_registro", Integer, primary_key=True, autoincrement=True),
        Column("id_sessao", Integer, ForeignKey("sessoes_testes.id_sessao", ondelete="CASCADE")),
        Column("timestamp_ms", Integer, nullable=False),
        Column("ax", Float), Column("ay", Float), Column("az", Float),
        Column("gx", Float), Column("gy", Float), Column("gz", Float),
        Column("temp_dd", Float), Column("temp_td", Float),
        Column("temp_de", Float), Column("temp_te", Float),
        Column("thermocouple", Float),
        Column("peso", Float),
        Column("velocidade", Float),
    )

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

    return metadata, sessoes_testes, telemetria_dados, grupos_teste, grupo_sessoes


def _ensure_schema_upgrades(engine):
    """Aplica upgrades incrementais em bancos já existentes (ex: coluna nome_teste
    adicionada após o banco já ter sido criado com o schema anterior)."""
    with engine.begin() as conn:
        try:
            conn.execute(text("ALTER TABLE sessoes_testes ADD COLUMN nome_teste VARCHAR(150)"))
        except Exception:
            pass  # coluna já existe (ou banco recém-criado já a inclui)


def init_db():
    engine, backend = get_engine()
    metadata, *_ = get_metadata()
    metadata.create_all(engine)
    _ensure_schema_upgrades(engine)
    return engine, backend


def parse_datalog_csv(file_bytes_or_buffer):
    """Lê um CSV bruto da ESP32 e devolve DataFrame já normalizado para o schema do banco."""
    df = pd.read_csv(file_bytes_or_buffer)
    df.columns = [c.strip() for c in df.columns]
    missing = [c for c in CSV_COLUMN_MAP if c not in df.columns]
    if missing:
        raise ValueError(
            f"O arquivo CSV não contém as colunas esperadas: {missing}. "
            f"Verifique se é um datalog gerado pela ESP32 da KRT."
        )
    df = df.rename(columns=CSV_COLUMN_MAP)
    df = df[list(CSV_COLUMN_MAP.values())]
    return df


def insert_session(nome_teste, data_teste, nome_piloto, config_carro, observacoes,
                    telemetry_df: pd.DataFrame, id_grupo=None):
    """Insere uma nova sessão de teste + bulk insert dos dados de telemetria.
    Se id_grupo for informado, a sessão já é vinculada a esse grupo de teste."""
    engine, backend = get_engine()
    metadata, sessoes_testes, telemetria_dados, grupos_teste, grupo_sessoes = get_metadata()
    metadata.create_all(engine)
    _ensure_schema_upgrades(engine)

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
        records = records.to_dict(orient="records")
        if records:
            conn.execute(telemetria_dados.insert(), records)

        if id_grupo is not None:
            conn.execute(grupo_sessoes.insert().values(id_grupo=id_grupo, id_sessao=id_sessao))

    return id_sessao


def create_test_group(nome_grupo, descricao=None):
    """Cria um novo grupo de teste (ex: agrupamento de vários ensaios ao longo dos dias)."""
    engine, backend = get_engine()
    metadata, sessoes_testes, telemetria_dados, grupos_teste, grupo_sessoes = get_metadata()
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
    metadata, sessoes_testes, telemetria_dados, grupos_teste, grupo_sessoes = get_metadata()
    with engine.begin() as conn:
        conn.execute(grupo_sessoes.insert().values(id_grupo=id_grupo, id_sessao=id_sessao))


def insert_batch_sessions(rows: list, id_grupo=None):
    """Insere várias sessões de uma vez (ingestão em lote).

    `rows` é uma lista de dicts, cada um com:
        nome_teste, data_teste, nome_piloto, config_carro, observacoes, df (DataFrame)

    Se id_grupo for informado, todas as sessões inseridas já são vinculadas a
    esse grupo. Retorna a lista de id_sessao criados.
    """
    ids = []
    for row in rows:
        id_sessao = insert_session(
            nome_teste=row["nome_teste"],
            data_teste=row["data_teste"],
            nome_piloto=row["nome_piloto"],
            config_carro=row.get("config_carro"),
            observacoes=row.get("observacoes"),
            telemetry_df=row["df"],
            id_grupo=id_grupo,
        )
        ids.append(id_sessao)
    return ids


@st.cache_data(ttl=30, show_spinner=False)
def list_sessions(_engine_marker=None):
    engine, backend = get_engine()
    metadata, sessoes_testes, telemetria_dados, grupos_teste, grupo_sessoes = get_metadata()
    metadata.create_all(engine)
    with engine.connect() as conn:
        df = pd.read_sql(select(sessoes_testes).order_by(sessoes_testes.c.data_teste.desc()), conn)
    return df


@st.cache_data(ttl=30, show_spinner=False)
def list_test_groups(_engine_marker=None):
    engine, backend = get_engine()
    metadata, sessoes_testes, telemetria_dados, grupos_teste, grupo_sessoes = get_metadata()
    metadata.create_all(engine)
    with engine.connect() as conn:
        df = pd.read_sql(select(grupos_teste).order_by(grupos_teste.c.data_criacao.desc()), conn)
    return df


@st.cache_data(ttl=30, show_spinner=False)
def list_sessions_in_group(id_grupo, _engine_marker=None):
    engine, backend = get_engine()
    metadata, sessoes_testes, telemetria_dados, grupos_teste, grupo_sessoes = get_metadata()
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
    metadata, sessoes_testes, telemetria_dados, grupos_teste, grupo_sessoes = get_metadata()
    with engine.connect() as conn:
        df = pd.read_sql(
            select(telemetria_dados).where(telemetria_dados.c.id_sessao == id_sessao).order_by(
                telemetria_dados.c.timestamp_ms
            ),
            conn,
        )
    return df


def clear_caches():
    list_sessions.clear()
    list_test_groups.clear()
    list_sessions_in_group.clear()
    load_session_telemetry.clear()
