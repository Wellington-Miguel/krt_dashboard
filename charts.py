"""
Camada de visualização — metodologias de análise de motorsport
(inspirado em MoTeC i2 / RaceStudio), conforme especificado no documento
de arquitetura da KRT.
"""

import plotly.graph_objects as go
import pandas as pd
import numpy as np

KRT_GOLD = "#FFD700"
KRT_WHITE = "#FFFFFF"
KRT_GRID = "#444444"
KRT_BG = "#111111"

WHEEL_COLORS = {
    "temp_dd": "#FFD700",  # ouro
    "temp_td": "#FF6B35",  # laranja
    "temp_de": "#4FC3F7",  # azul claro
    "temp_te": "#EF5350",  # vermelho
}
WHEEL_LABELS = {
    "temp_dd": "Dianteira Direita (DD)",
    "temp_td": "Traseira Direita (TD)",
    "temp_de": "Dianteira Esquerda (DE)",
    "temp_te": "Traseira Esquerda (TE)",
}


def _base_layout(fig, title, x_title, y_title):
    fig.update_layout(
        title=dict(text=title, font=dict(color=KRT_GOLD, size=18)),
        paper_bgcolor=KRT_BG,
        plot_bgcolor=KRT_BG,
        font=dict(color=KRT_WHITE),
        xaxis=dict(title=x_title, gridcolor=KRT_GRID, zerolinecolor=KRT_GRID, color=KRT_WHITE),
        yaxis=dict(title=y_title, gridcolor=KRT_GRID, zerolinecolor=KRT_GRID, color=KRT_WHITE),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=KRT_WHITE)),
        margin=dict(l=10, r=10, t=50, b=10),
    )
    return fig


def low_pass_filter(series: pd.Series, window: int = 7) -> pd.Series:
    """Média móvel simples para atenuar ruído elétrico do acelerômetro (baixa passagem)."""
    return series.rolling(window=window, center=True, min_periods=1).mean()


def gg_diagram(df: pd.DataFrame, smooth_window: int = 7):
    """Diagrama G-G: Ay (lateral) x Ax (longitudinal), com filtro low-pass."""
    ax_f = low_pass_filter(df["ax"], smooth_window)
    ay_f = low_pass_filter(df["ay"], smooth_window)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=ay_f, y=ax_f, mode="markers",
        marker=dict(size=5, color=KRT_GOLD, opacity=0.6),
        name="Envelope G-G (filtrado)",
    ))
    fig.add_hline(y=0, line_color=KRT_GRID)
    fig.add_vline(x=0, line_color=KRT_GRID)
    fig = _base_layout(fig, "Diagrama G-G — Envelope de Aderência do Chassi",
                        "Aceleração Lateral — Ay (g)", "Aceleração Longitudinal — Ax (g)")
    fig.update_yaxes(scaleanchor="x", scaleratio=1)
    return fig


def wheel_temp_chart(df: pd.DataFrame, stuck_sensors=None):
    """Gradiente e modulação térmica das 4 rodas, sincronizado por tempo."""
    stuck_sensors = stuck_sensors or {}
    t = (df["timestamp_ms"] - df["timestamp_ms"].min()) / 1000.0

    fig = go.Figure()
    for col, label in WHEEL_LABELS.items():
        if col not in df:
            continue
        is_stuck = stuck_sensors.get(col, {}).get("stuck", False)
        name = f"{label}{' ⚠️ travado' if is_stuck else ''}"
        fig.add_trace(go.Scatter(
            x=t, y=df[col], mode="lines", name=name,
            line=dict(color=WHEEL_COLORS[col], width=2, dash="dot" if is_stuck else "solid"),
        ))
    fig = _base_layout(fig, "Gradiente e Modulação Térmica das Rodas",
                        "Tempo (s)", "Temperatura (°C)")
    return fig


def thermocouple_chart(df: pd.DataFrame):
    t = (df["timestamp_ms"] - df["timestamp_ms"].min()) / 1000.0
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=t, y=df["thermocouple"], mode="lines",
                              line=dict(color=KRT_GOLD, width=2), name="Termopar (Escapamento)"))
    fig = _base_layout(fig, "Temperatura de Escapamento (Termopar)", "Tempo (s)", "Temperatura (°C)")
    return fig


SESSION_PALETTE = [
    "#FFD700", "#4FC3F7", "#EF5350", "#66BB6A", "#BA68C8",
    "#FF8A65", "#90A4AE", "#F06292", "#26A69A", "#FFCA28",
]


def gg_diagram_multi(session_dfs: dict, smooth_window: int = 7):
    """Diagrama G-G sobrepondo várias sessões (análise de Grupo de Teste).
    session_dfs: dict {label_da_sessao: dataframe_telemetria}"""
    fig = go.Figure()
    for i, (label, df) in enumerate(session_dfs.items()):
        if df.empty or "ax" not in df or "ay" not in df:
            continue
        ax_f = low_pass_filter(df["ax"], smooth_window)
        ay_f = low_pass_filter(df["ay"], smooth_window)
        color = SESSION_PALETTE[i % len(SESSION_PALETTE)]
        fig.add_trace(go.Scatter(
            x=ay_f, y=ax_f, mode="markers",
            marker=dict(size=5, color=color, opacity=0.55),
            name=label,
        ))
    fig.add_hline(y=0, line_color=KRT_GRID)
    fig.add_vline(x=0, line_color=KRT_GRID)
    fig = _base_layout(fig, "Diagrama G-G Comparativo — Grupo de Teste",
                        "Aceleração Lateral — Ay (g)", "Aceleração Longitudinal — Ax (g)")
    fig.update_yaxes(scaleanchor="x", scaleratio=1)
    return fig


def group_peak_temp_bar(kpi_df: pd.DataFrame):
    """Gráfico de barras agrupadas: temperatura de pico por roda, por sessão.
    kpi_df deve ter colunas: sessao, temp_dd, temp_td, temp_de, temp_te (picos)."""
    fig = go.Figure()
    for col, label in WHEEL_LABELS.items():
        if col not in kpi_df:
            continue
        fig.add_trace(go.Bar(
            x=kpi_df["sessao"], y=kpi_df[col], name=label, marker_color=WHEEL_COLORS[col],
        ))
    fig.update_layout(barmode="group")
    fig = _base_layout(fig, "Temperatura Máxima por Roda — Comparativo entre Sessões",
                        "Sessão", "Temperatura de pico (°C)")
    return fig


def imu_chart(df: pd.DataFrame):
    t = (df["timestamp_ms"] - df["timestamp_ms"].min()) / 1000.0
    fig = go.Figure()
    colors = {"gx": "#FFD700", "gy": "#4FC3F7", "gz": "#EF5350"}
    for axis, color in colors.items():
        fig.add_trace(go.Scatter(x=t, y=df[axis], mode="lines",
                                  line=dict(color=color, width=1.5), name=f"Giroscópio {axis.upper()}"))
    fig = _base_layout(fig, "Giroscópio (Gx, Gy, Gz)", "Tempo (s)", "°/s")
    return fig
