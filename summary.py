"""
Resumo textual automático dos destaques de uma sessão de telemetria.

Diferente de validation.py (que sinaliza PROBLEMAS de sensor), este módulo
gera frases descritivas sobre o que aconteceu de mais relevante no ensaio —
picos de aceleração, velocidade, frenagem, temperatura, etc. Cada frase só é
gerada se a coluna correspondente tiver dado real na sessão carregada.
"""

import pandas as pd

WHEEL_LABELS = {
    "temp_dd": "Dianteira Direita",
    "temp_td": "Traseira Direita",
    "temp_de": "Dianteira Esquerda",
    "temp_te": "Traseira Esquerda",
}


def _has_data(df: pd.DataFrame, col: str) -> bool:
    return col in df.columns and df[col].notna().any()


def _time_of(df: pd.DataFrame, idx) -> float:
    t0 = df["timestamp_ms"].min()
    return (df.loc[idx, "timestamp_ms"] - t0) / 1000.0


def build_session_highlights(df: pd.DataFrame) -> list:
    """Monta uma lista de frases descritivas sobre a sessão, com base apenas
    nos sensores que de fato têm dado no ensaio."""
    if df.empty:
        return []

    highlights = []

    duracao_s = (df["timestamp_ms"].max() - df["timestamp_ms"].min()) / 1000.0
    highlights.append(f"⏱️ Ensaio com {duracao_s:.0f} s de duração ({len(df)} registros).")

    if _has_data(df, "ay"):
        idx = df["ay"].abs().idxmax()
        highlights.append(
            f"↔️ Aceleração lateral máxima de {df.loc[idx, 'ay']:.2f} g aos "
            f"{_time_of(df, idx):.1f} s de ensaio."
        )
    if _has_data(df, "ax"):
        idx = df["ax"].abs().idxmax()
        sinal = "frenagem" if df.loc[idx, "ax"] < 0 else "aceleração"
        highlights.append(
            f"↕️ Pico de {sinal} longitudinal: {df.loc[idx, 'ax']:.2f} g aos "
            f"{_time_of(df, idx):.1f} s."
        )
    if _has_data(df, "velocidade"):
        idx = df["velocidade"].idxmax()
        highlights.append(
            f"🚀 Velocidade máxima de {df.loc[idx, 'velocidade']:.0f} km/h aos "
            f"{_time_of(df, idx):.1f} s."
        )
    if _has_data(df, "pressao_fluido"):
        idx = df["pressao_fluido"].idxmax()
        highlights.append(
            f"🛑 Frenagem mais forte identificada aos {_time_of(df, idx):.1f} s "
            f"(pressão de fluido de {df.loc[idx, 'pressao_fluido']:.1f})."
        )
    if _has_data(df, "angulo_volante"):
        amplitude = df["angulo_volante"].max() - df["angulo_volante"].min()
        highlights.append(f"🎯 Amplitude de esterçamento de {amplitude:.0f}° ao longo do ensaio.")

    present_wheels = {c: l for c, l in WHEEL_LABELS.items() if _has_data(df, c)}
    if present_wheels:
        peaks = {c: df[c].max() for c in present_wheels}
        hottest = max(peaks, key=peaks.get)
        highlights.append(
            f"🌡️ Roda mais quente: {present_wheels[hottest]}, pico de {peaks[hottest]:.0f}°C."
        )
    if _has_data(df, "thermocouple"):
        highlights.append(f"🔥 Termopar de escapamento com pico de {df['thermocouple'].max():.0f}°C.")

    if _has_data(df, "satelites"):
        sat_medio = df["satelites"].mean()
        highlights.append(f"🛰️ Sinal de GPS com média de {sat_medio:.1f} satélites durante o ensaio.")

    return highlights
