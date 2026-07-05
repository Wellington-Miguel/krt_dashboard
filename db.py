"""
Camada de acesso a dados do Dashboard KRT.

Usa Neon.tech (PostgreSQL Serverless) quando há uma connection string
configurada em st.secrets["NEON_CONN_STRING"]. Caso contrário, cai para um
banco SQLite local (krt_telemetry.db) — útil para rodar e testar o app sem
precisar de credenciais de nuvem, mantendo o mesmo esquema relacional
descrito no documento de especificação.
"""

import io
import streamlit as st
import pandas as pd
from sqlalchemy import (
    create_engine, MetaData, Table, Column, Integer, String, Text,
    Date, DateTime, Float, ForeignKey, select, func, text
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
    return metadata, sessoes_testes, telemetria_dados


def init_db():
    engine, backend = get_engine()
    metadata, sessoes_testes, telemetria_dados = get_metadata()
    metadata.create_all(engine)
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


def insert_session(data_teste, nome_piloto, config_carro, observacoes, telemetry_df: pd.DataFrame):
    """Insere uma nova sessão de teste + bulk insert dos dados de telemetria."""
    engine, backend = get_engine()
    metadata, sessoes_testes, telemetria_dados = get_metadata()
    metadata.create_all(engine)

    with engine.begin() as conn:
        result = conn.execute(
            sessoes_testes.insert().values(
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

    return id_sessao


@st.cache_data(ttl=30, show_spinner=False)
def list_sessions(_engine_marker=None):
    engine, backend = get_engine()
    metadata, sessoes_testes, telemetria_dados = get_metadata()
    metadata.create_all(engine)
    with engine.connect() as conn:
        df = pd.read_sql(select(sessoes_testes).order_by(sessoes_testes.c.data_teste.desc()), conn)
    return df


@st.cache_data(ttl=30, show_spinner=False)
def load_session_telemetry(id_sessao, _engine_marker=None):
    engine, backend = get_engine()
    metadata, sessoes_testes, telemetria_dados = get_metadata()
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
    load_session_telemetry.clear()
