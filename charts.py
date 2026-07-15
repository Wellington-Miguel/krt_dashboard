"""
Camada de visualização — metodologias de análise de motorsport
(inspirado em MoTeC i2 / RaceStudio), conforme especificado no documento
de arquitetura da KRT.

Todas as funções são NULL-AWARE: como o schema de telemetria agora cobre um
superconjunto de sensores (o ensaio pode ter só um subconjunto deles), cada
gráfico verifica se a(s) coluna(s) de que precisa têm dado real antes de
desenhar qualquer traço — sensores ausentes/100% nulos são simplesmente
ignorados (uma roda sem sensor não aparece na legenda, por exemplo), e uma
função retorna None quando NENHUM dos seus dados está disponível, para que
app.py decida não exibir a aba correspondente.
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

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


def _has_data(df: pd.DataFrame, col: str) -> bool:
    return col in df.columns and df[col].notna().any()


def _time_seconds(df: pd.DataFrame) -> pd.Series:
    return (df["timestamp_ms"] - df["timestamp_ms"].min()) / 1000.0


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
    """Diagrama G-G: Ay (lateral) x Ax (longitudinal), com filtro low-pass.
    Retorna None se não houver dados de acelerômetro nesta sessão."""
    if not (_has_data(df, "ax") and _has_data(df, "ay")):
        return None
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
    """Gradiente e modulação térmica das rodas disponíveis, sincronizado por tempo.
    Retorna None se nenhuma das 4 rodas tiver dado nesta sessão."""
    stuck_sensors = stuck_sensors or {}
    present_cols = [c for c in WHEEL_LABELS if _has_data(df, c)]
    if not present_cols:
        return None

    t = _time_seconds(df)
    fig = go.Figure()
    for col in present_cols:
        label = WHEEL_LABELS[col]
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
    if not _has_data(df, "thermocouple"):
        return None
    t = _time_seconds(df)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=t, y=df["thermocouple"], mode="lines",
                              line=dict(color=KRT_GOLD, width=2), name="Termopar (Escapamento)"))
    fig = _base_layout(fig, "Temperatura de Escapamento (Termopar)", "Tempo (s)", "Temperatura (°C)")
    return fig


def imu_chart(df: pd.DataFrame):
    """Giroscópio (Gx, Gy, Gz). Retorna None se o ensaio não tiver esses dados
    (ex: datalogs mais novos, que trazem Ângulo de Volante em vez de giroscópio)."""
    present = [a for a in ("gx", "gy", "gz") if _has_data(df, a)]
    if not present:
        return None
    t = _time_seconds(df)
    fig = go.Figure()
    colors = {"gx": "#FFD700", "gy": "#4FC3F7", "gz": "#EF5350"}
    for axis in present:
        fig.add_trace(go.Scatter(x=t, y=df[axis], mode="lines",
                                  line=dict(color=colors[axis], width=1.5), name=f"Giroscópio {axis.upper()}"))
    fig = _base_layout(fig, "Giroscópio (Gx, Gy, Gz)", "Tempo (s)", "°/s")
    return fig


def steering_angle_chart(df: pd.DataFrame):
    """Ângulo de volante ao longo do tempo (datalogs novos)."""
    if not _has_data(df, "angulo_volante"):
        return None
    t = _time_seconds(df)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=t, y=df["angulo_volante"], mode="lines",
                              line=dict(color=KRT_GOLD, width=2), name="Ângulo de Volante"))
    fig.add_hline(y=df["angulo_volante"].mean(), line_color=KRT_GRID, line_dash="dot")
    fig = _base_layout(fig, "Ângulo de Volante", "Tempo (s)", "Ângulo (°)")
    return fig


def speed_chart(df: pd.DataFrame):
    """Velocidade ao longo do tempo."""
    if not _has_data(df, "velocidade"):
        return None
    t = _time_seconds(df)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=t, y=df["velocidade"], mode="lines", fill="tozeroy",
                              line=dict(color=KRT_GOLD, width=2), name="Velocidade"))
    fig = _base_layout(fig, "Velocidade", "Tempo (s)", "Velocidade (km/h)")
    return fig


def weight_chart(df: pd.DataFrame):
    """Peso (célula de carga) ao longo do tempo."""
    if not _has_data(df, "peso"):
        return None
    t = _time_seconds(df)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=t, y=df["peso"], mode="lines",
                              line=dict(color="#4FC3F7", width=2), name="Peso"))
    fig = _base_layout(fig, "Peso (Célula de Carga)", "Tempo (s)", "Peso")
    return fig


def brake_pressure_chart(df: pd.DataFrame):
    """Pressão do fluido de freio ao longo do tempo (datalogs novos)."""
    if not _has_data(df, "pressao_fluido"):
        return None
    t = _time_seconds(df)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=t, y=df["pressao_fluido"], mode="lines", fill="tozeroy",
                              line=dict(color="#EF5350", width=2), name="Pressão de Fluido de Freio"))
    fig = _base_layout(fig, "Pressão de Fluido de Freio", "Tempo (s)", "Pressão")
    return fig


def steering_brake_combined_chart(df: pd.DataFrame):
    """Ângulo de volante x Pressão de freio sobrepostos — útil para avaliar
    trail-braking e coordenação entrada-de-curva/frenagem."""
    has_steer = _has_data(df, "angulo_volante")
    has_brake = _has_data(df, "pressao_fluido")
    if not (has_steer and has_brake):
        return None
    t = _time_seconds(df)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=t, y=df["angulo_volante"], mode="lines", name="Ângulo de Volante (°)",
                              line=dict(color=KRT_GOLD, width=2), yaxis="y1"))
    fig.add_trace(go.Scatter(x=t, y=df["pressao_fluido"], mode="lines", name="Pressão de Freio",
                              line=dict(color="#EF5350", width=2), yaxis="y2"))
    fig.update_layout(
        yaxis=dict(title="Ângulo de Volante (°)", gridcolor=KRT_GRID, color=KRT_WHITE),
        yaxis2=dict(title="Pressão de Freio", overlaying="y", side="right", color=KRT_WHITE,
                     showgrid=False),
    )
    fig = _base_layout(fig, "Direção x Frenagem", "Tempo (s)", "Ângulo de Volante (°)")
    fig.update_layout(yaxis=dict(title="Ângulo de Volante (°)", gridcolor=KRT_GRID, color=KRT_WHITE),
                       yaxis2=dict(title="Pressão de Freio", overlaying="y", side="right",
                                   color=KRT_WHITE, showgrid=False))
    return fig


GPS_COLOR_OPTIONS = [
    ("tempo", "Tempo (ordem cronológica)"),
    ("velocidade", "Velocidade"),
    ("ax", "Aceleração Longitudinal (Ax)"),
    ("ay", "Aceleração Lateral (Ay)"),
    ("pressao_fluido", "Pressão de Freio"),
    ("angulo_volante", "Ângulo de Volante"),
]

# (coluna, rótulo, formato numérico, unidade) — usado tanto para colorir o
# traçado quanto para montar o hover com a telemetria daquele ponto do circuito.
_GPS_HOVER_CHANNELS = [
    ("velocidade", "Velocidade", ".0f", "km/h"),
    ("ax", "Acel. Longitudinal (Ax)", ".2f", "g"),
    ("ay", "Acel. Lateral (Ay)", ".2f", "g"),
    ("pressao_fluido", "Pressão de Freio", ".1f", ""),
    ("angulo_volante", "Ângulo de Volante", ".0f", "°"),
]


def gps_track_color_options(df: pd.DataFrame) -> list:
    """Opções de variável para colorir o traçado GPS, considerando só o que
    tem dado real nesta sessão. Sempre inclui 'tempo' (ordem cronológica)."""
    options = [("tempo", "Tempo (ordem cronológica)")]
    for col, label, *_ in _GPS_HOVER_CHANNELS:
        if _has_data(df, col):
            options.append((col, label))
    return options


def gps_track_chart(df: pd.DataFrame, color_by: str = "tempo", max_checkpoints: int = 25):
    """Mapa do circuito percorrido via GPS: contorno suavizado (estilo pista,
    sem o jitter bruto do GPS) com alguns marcadores esparsos ("checkpoints")
    coloridos pela variável escolhida, cada um com hover exibindo a telemetria
    completa disponível (velocidade, G, freio, volante) naquele ponto do
    circuito. Retorna None se não houver fix de GPS válido nesta sessão."""
    if not (_has_data(df, "latitude") and _has_data(df, "longitude")):
        return None
    valid = df.dropna(subset=["latitude", "longitude"])
    valid = valid[(valid["latitude"] != 0) & (valid["longitude"] != 0)]
    if valid.empty:
        return None
    valid = valid.sort_values("timestamp_ms").reset_index(drop=True)

    t = _time_seconds(valid)
    # Suaviza o traçado para eliminar o jitter do GPS e desenhar um contorno
    # contínuo de pista, em vez de ligar todo ponto bruto (que cruza sobre
    # si mesmo quando o carro está parado/lento ou o fix oscila).
    lat_smooth = low_pass_filter(valid["latitude"], window=15)
    lon_smooth = low_pass_filter(valid["longitude"], window=15)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=lon_smooth, y=lat_smooth, mode="lines",
        line=dict(color="#999999", width=5),
        name="Traçado do circuito", hoverinfo="skip",
    ))

    # --- Checkpoints esparsos (poucos pontos, não todo registro bruto) ---
    duration_s = t.max() - t.min() if len(t) else 0
    interval = max(duration_s / max_checkpoints, 0.5) if duration_s > 0 else 1.0
    bins = (t // interval).astype(int)
    checkpoint_positions = valid.groupby(bins).apply(lambda g: g.index[len(g) // 2]).values

    checkpoints = valid.loc[checkpoint_positions].copy()
    checkpoints["_lat_smooth"] = lat_smooth.loc[checkpoint_positions].values
    checkpoints["_lon_smooth"] = lon_smooth.loc[checkpoint_positions].values
    t_cp = t.loc[checkpoint_positions]

    present_channels = [ch for ch in _GPS_HOVER_CHANNELS if _has_data(checkpoints, ch[0])]
    customdata = np.column_stack([t_cp.values] + [checkpoints[col].values for col, *_ in present_channels])

    hover_lines = ["<b>Tempo:</b> %{customdata[0]:.1f} s"]
    for i, (col, label, fmt, unit) in enumerate(present_channels, start=1):
        suffix = f" {unit}" if unit else ""
        hover_lines.append(f"<b>{label}:</b> %{{customdata[{i}]:{fmt}}}{suffix}")
    hovertemplate = "<br>".join(hover_lines) + "<extra></extra>"

    if color_by != "tempo" and _has_data(checkpoints, color_by):
        color_series = checkpoints[color_by]
        colorbar_title = dict(GPS_COLOR_OPTIONS).get(color_by, color_by)
    else:
        color_series = t_cp
        colorbar_title = "Tempo (s)"

    fig.add_trace(go.Scatter(
        x=checkpoints["_lon_smooth"], y=checkpoints["_lat_smooth"], mode="markers",
        marker=dict(size=13, color=color_series, colorscale=[[0, "#4FC3F7"], [0.5, KRT_GOLD], [1, "#EF5350"]],
                    line=dict(color=KRT_BG, width=1.5),
                    colorbar=dict(title=colorbar_title, tickfont=dict(color=KRT_WHITE))),
        customdata=customdata,
        hovertemplate=hovertemplate,
        name="Checkpoints",
    ))

    fig = _base_layout(fig, "Traçado do Percurso — GPS", "", "")
    fig.update_xaxes(showgrid=False, zeroline=False, showticklabels=False)
    fig.update_yaxes(showgrid=False, zeroline=False, showticklabels=False, scaleanchor="x", scaleratio=1)
    fig.update_layout(showlegend=False)
    return fig


def gps_satellites_chart(df: pd.DataFrame):
    """Número de satélites (qualidade do fix de GPS) ao longo do tempo."""
    if not _has_data(df, "satelites"):
        return None
    t = _time_seconds(df)
    fig = go.Figure()
    fig.add_trace(go.Bar(x=t, y=df["satelites"], marker_color=KRT_GOLD, name="Satélites"))
    fig = _base_layout(fig, "Qualidade do Sinal GPS (nº de satélites)", "Tempo (s)", "Satélites")
    return fig


def noise_events_timeline_chart(df: pd.DataFrame, noise_events_df: pd.DataFrame):
    """Sobrepõe os instantes aproximados dos eventos de ruído elétrico (falhas de
    comunicação serial da ESP32) sobre a magnitude de aceleração da sessão —
    ajuda a identificar se o ruído coincide com vibração/impacto (ex: zebras,
    buracos) ou é independente da dinâmica do carro (sugerindo causa elétrica
    pura, como aterramento ou EMI de ignição/motor)."""
    if noise_events_df is None or noise_events_df.empty:
        return None
    if not (_has_data(df, "ax") and _has_data(df, "ay")):
        return None

    t = _time_seconds(df)
    az = df["az"] if _has_data(df, "az") else 0
    magnitude = np.sqrt(df["ax"].fillna(0) ** 2 + df["ay"].fillna(0) ** 2 +
                         (az.fillna(0) if isinstance(az, pd.Series) else 0) ** 2)

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=t, y=magnitude, mode="lines",
                              line=dict(color=KRT_WHITE, width=1.5), name="Magnitude de Aceleração (g)"))

    t0 = df["timestamp_ms"].min()
    for _, ev in noise_events_df.iterrows():
        ref = ev.get("timestamp_ms_referencia")
        if ref is None or (isinstance(ref, float) and np.isnan(ref)):
            continue
        x_ev = (ref - t0) / 1000.0
        fig.add_vline(x=x_ev, line_color="#EF5350", line_dash="dot", line_width=2)

    fig.add_trace(go.Scatter(x=[None], y=[None], mode="lines",
                              line=dict(color="#EF5350", width=2, dash="dot"),
                              name="Evento de ruído elétrico"))
    fig = _base_layout(fig, "Linha do Tempo — Eventos de Ruído Elétrico x Dinâmica do Carro",
                        "Tempo (s)", "Aceleração (g)")
    return fig


def distribution_histograms(df: pd.DataFrame):
    """Histogramas de distribuição — quanto tempo/quantos registros o carro passa
    em cada faixa de força G, velocidade e pressão de freio. Complementa o
    Diagrama G-G (que mostra só o envelope) evidenciando a frequência de cada
    faixa. Só inclui os painéis cujos sensores têm dado real nesta sessão."""
    panels = []
    if _has_data(df, "ax") or _has_data(df, "ay"):
        panels.append("g")
    if _has_data(df, "velocidade"):
        panels.append("velocidade")
    if _has_data(df, "pressao_fluido"):
        panels.append("pressao_fluido")
    if not panels:
        return None

    titles = {
        "g": "Distribuição de G's (Ax/Ay)",
        "velocidade": "Distribuição de Velocidade",
        "pressao_fluido": "Distribuição de Pressão de Freio",
    }
    fig = make_subplots(rows=1, cols=len(panels), subplot_titles=[titles[p] for p in panels])

    for col_idx, p in enumerate(panels, start=1):
        if p == "g":
            if _has_data(df, "ax"):
                fig.add_trace(go.Histogram(x=df["ax"].dropna(), name="Ax", marker_color=KRT_GOLD,
                                            opacity=0.65, nbinsx=40), row=1, col=col_idx)
            if _has_data(df, "ay"):
                fig.add_trace(go.Histogram(x=df["ay"].dropna(), name="Ay", marker_color="#4FC3F7",
                                            opacity=0.65, nbinsx=40), row=1, col=col_idx)
        elif p == "velocidade":
            fig.add_trace(go.Histogram(x=df["velocidade"].dropna(), name="Velocidade",
                                        marker_color=KRT_GOLD, nbinsx=40, showlegend=False), row=1, col=col_idx)
        elif p == "pressao_fluido":
            fig.add_trace(go.Histogram(x=df["pressao_fluido"].dropna(), name="Pressão de Freio",
                                        marker_color="#EF5350", nbinsx=40, showlegend=False), row=1, col=col_idx)

    fig.update_layout(
        barmode="overlay",
        paper_bgcolor=KRT_BG, plot_bgcolor=KRT_BG,
        font=dict(color=KRT_WHITE),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=KRT_WHITE)),
        margin=dict(l=10, r=10, t=50, b=10),
        title=dict(text="Distribuições — Tempo em cada faixa", font=dict(color=KRT_GOLD, size=18)),
    )
    fig.update_annotations(font_color=KRT_GOLD)
    fig.update_xaxes(gridcolor=KRT_GRID, zerolinecolor=KRT_GRID, color=KRT_WHITE)
    fig.update_yaxes(gridcolor=KRT_GRID, zerolinecolor=KRT_GRID, color=KRT_WHITE, title_text="Contagem")
    return fig


SESSION_PALETTE = [
    "#FFD700", "#4FC3F7", "#EF5350", "#66BB6A", "#BA68C8",
    "#FF8A65", "#90A4AE", "#F06292", "#26A69A", "#FFCA28",
]


def gg_diagram_multi(session_dfs: dict, smooth_window: int = 7):
    """Diagrama G-G sobrepondo várias sessões (análise de Grupo de Teste).
    session_dfs: dict {label_da_sessao: dataframe_telemetria}. Sessões sem
    dado de acelerômetro são ignoradas automaticamente."""
    fig = go.Figure()
    any_data = False
    for i, (label, df) in enumerate(session_dfs.items()):
        if df.empty or not (_has_data(df, "ax") and _has_data(df, "ay")):
            continue
        any_data = True
        ax_f = low_pass_filter(df["ax"], smooth_window)
        ay_f = low_pass_filter(df["ay"], smooth_window)
        color = SESSION_PALETTE[i % len(SESSION_PALETTE)]
        fig.add_trace(go.Scatter(
            x=ay_f, y=ax_f, mode="markers",
            marker=dict(size=5, color=color, opacity=0.55),
            name=label,
        ))
    if not any_data:
        return None
    fig.add_hline(y=0, line_color=KRT_GRID)
    fig.add_vline(x=0, line_color=KRT_GRID)
    fig = _base_layout(fig, "Diagrama G-G Comparativo — Grupo de Teste",
                        "Aceleração Lateral — Ay (g)", "Aceleração Longitudinal — Ax (g)")
    fig.update_yaxes(scaleanchor="x", scaleratio=1)
    return fig


def group_peak_temp_bar(kpi_df: pd.DataFrame):
    """Gráfico de barras agrupadas: temperatura de pico por roda, por sessão.
    Só inclui rodas que tenham dado em ao menos uma sessão do grupo."""
    present_cols = [c for c in WHEEL_LABELS if c in kpi_df and kpi_df[c].notna().any()]
    if not present_cols:
        return None
    fig = go.Figure()
    for col in present_cols:
        label = WHEEL_LABELS[col]
        fig.add_trace(go.Bar(
            x=kpi_df["sessao"], y=kpi_df[col], name=label, marker_color=WHEEL_COLORS[col],
        ))
    fig.update_layout(barmode="group")
    fig = _base_layout(fig, "Temperatura Máxima por Roda — Comparativo entre Sessões",
                        "Sessão", "Temperatura de pico (°C)")
    return fig
