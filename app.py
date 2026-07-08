"""
KRT — Dashboard Cloud de Telemetria e Aquisição de Dados
Kamikaze Racing Team — Formula SAE UFBA

Implementa o Documento de Especificação Arquitetural:
 - Persistência via Neon.tech (PostgreSQL Serverless), com fallback SQLite local
 - Schema de telemetria em superconjunto: convive com o datalog "clássico"
   (Ax/Ay/Az, Gx/Gy/Gz, Temp DD/TD/DE/TE, Thermocouple, Peso, Velocidade) e o
   datalog novo (Ângulo de Volante, Pressão de Fluido de Freio, GPS) — cada
   tela/gráfico exibe só o que o ensaio carregado realmente possui.
 - Camada de validação e consistência (Sanity Check), incluindo detecção de
   RUÍDO ELÉTRICO na comunicação serial da ESP32 (linhas corrompidas do datalog).
 - Diagrama G-G, Gradiente Térmico das Rodas, Direção x Frenagem, Traçado GPS
   (metodologia MoTeC i2 / RaceStudio).
 - 4 telas: Autenticação, Home/Performance, Ingestão de Testes, Diagnóstico de Sensores
 - Identidade visual KRT: fundo #111111, destaque em amarelo ouro #FFD700
"""

import streamlit as st
import pandas as pd
from datetime import date

import db
import validation
import charts


def _session_label(sessions_df, id_sessao):
    row = sessions_df[sessions_df["id_sessao"] == id_sessao].iloc[0]
    titulo = row.get("nome_teste") or "(sem título)"
    piloto = row.get("nome_piloto")
    piloto_text = piloto.strip() if isinstance(piloto, str) else ""
    if piloto_text:
        return f"{titulo} · {piloto_text} ({row['data_teste']})"
    return f"{titulo} · (sem piloto) ({row['data_teste']})"


def _session_kpis(id_sessao, label, telemetry):
    if telemetry.empty:
        return None
    duracao_s = (telemetry["timestamp_ms"].max() - telemetry["timestamp_ms"].min()) / 1000.0
    row = {"sessao": label, "id_sessao": id_sessao, "duracao_s": round(duracao_s, 1)}
    if db.available_columns(telemetry):
        pass  # apenas para deixar claro que o resto é condicional
    if "ax" in telemetry and telemetry["ax"].notna().any():
        row["ax_max"] = round(telemetry["ax"].max(), 2)
    if "ay" in telemetry and telemetry["ay"].notna().any():
        row["ay_max"] = round(telemetry["ay"].abs().max(), 2)
    for col in ("temp_dd", "temp_td", "temp_de", "temp_te"):
        if col in telemetry and telemetry[col].notna().any():
            row[col] = round(telemetry[col].max(), 1)
    if "thermocouple" in telemetry and telemetry["thermocouple"].notna().any():
        row["thermocouple_max"] = round(telemetry["thermocouple"].max(), 1)
    if "pressao_fluido" in telemetry and telemetry["pressao_fluido"].notna().any():
        row["pressao_max"] = round(telemetry["pressao_fluido"].max(), 2)
    return row


st.set_page_config(
    page_title="KRT — Dashboard de Telemetria",
    page_icon="🏁",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# ESTILO — Identidade visual KRT (Preto, Amarelo Ouro, Branco)
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    .stApp { background-color: #111111; }
    section[data-testid="stSidebar"] { background-color: #0a0a0a; border-right: 1px solid #FFD700; }
    h1, h2, h3 { color: #FFD700 !important; }
    p, span, label, li, div { color: #FFFFFF; }
    .stButton>button {
        background-color: #FFD700; color: #111111; font-weight: 700;
        border: none; border-radius: 4px;
    }
    .stButton>button:hover { background-color: #e6c200; color: #111111; }
    div[data-baseweb="select"] > div { background-color: #1c1c1c; }
    .krt-alert-ok { background-color: #142d1a; border-left: 4px solid #4CAF50; padding: 10px 14px; border-radius: 4px; margin-bottom: 8px;}
    .krt-alert-warn { background-color: #332b00; border-left: 4px solid #FFD700; padding: 10px 14px; border-radius: 4px; margin-bottom: 8px;}
    .krt-alert-fail { background-color: #3a1414; border-left: 4px solid #EF5350; padding: 10px 14px; border-radius: 4px; margin-bottom: 8px;}
    .krt-alert-noise { background-color: #2a1030; border-left: 4px solid #BA68C8; padding: 10px 14px; border-radius: 4px; margin-bottom: 8px;}
    .krt-kpi { background-color: #1a1a1a; border: 1px solid #333; border-radius: 8px; padding: 14px; text-align:center;}
    .krt-kpi .value { font-size: 26px; font-weight: 800; color: #FFD700; }
    .krt-kpi .label { font-size: 13px; color: #ccc; }
    hr { border-color: #333; }
</style>
""", unsafe_allow_html=True)

try:
    APP_PASSWORD = st.secrets.get("APP_PASSWORD", "krt2026")
except Exception:
    APP_PASSWORD = "krt2026"


# ---------------------------------------------------------------------------
# TELA 1 — AUTENTICAÇÃO RESTRITA
# ---------------------------------------------------------------------------
def tela_autenticacao():
    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.markdown("## 🏁 KRT — Kamikaze Racing Team")
        st.markdown("#### Dashboard Cloud de Telemetria")
        st.markdown("---")
        senha = st.text_input("Senha de acesso", type="password")
        if st.button("Entrar", use_container_width=True):
            if senha == APP_PASSWORD:
                st.session_state["autenticado"] = True
                st.rerun()
            else:
                st.error("Senha incorreta. Contate a liderança de Eletrônica.")
        st.caption("Acesso restrito aos membros da equipe KRT — Formula SAE UFBA.")


# ---------------------------------------------------------------------------
# TELA 2 — HOME / PAINEL DE PERFORMANCE
# ---------------------------------------------------------------------------
def tela_home():
    st.markdown("## 📊 Painel de Performance")

    sessions = db.list_sessions()
    if sessions.empty:
        st.info("Nenhuma sessão de teste cadastrada ainda. Use a aba **Ingestão de Testes** "
                "para enviar o primeiro datalog.")
        return

    modo = st.radio(
        "Modo de análise",
        ["Sessão individual", "Grupo de Teste (comparativo)"],
        horizontal=True,
    )
    st.markdown("---")

    if modo == "Sessão individual":
        _tela_home_individual(sessions)
    else:
        _tela_home_grupo()


def _render_alerts(telemetry, temp_status, noise_events_df):
    vp_check = validation.check_velocidade_peso(telemetry)
    for w in vp_check["warnings"]:
        st.markdown(f'<div class="krt-alert-warn">{w}</div>', unsafe_allow_html=True)

    for col, info in temp_status.items():
        if info["stuck"]:
            st.markdown(
                f'<div class="krt-alert-warn">⚠️ Sensor de temperatura '
                f'<b>{info["label"]}</b> aparenta estar travado '
                f'(σ = {info["std"]:.2f}°C, média {info["mean"]:.1f}°C) — sem modulação térmica. '
                f'Verifique o sensor infravermelho antes do próximo ensaio.</div>',
                unsafe_allow_html=True
            )

    gps = validation.check_gps(telemetry)
    if gps["present"] and gps["sem_fix"]:
        st.markdown(
            f'<div class="krt-alert-warn">⚠️ Sem fix de GPS válido em '
            f'{gps["no_fix_pct"]*100:.0f}% dos registros — o traçado do percurso pode '
            f'estar incompleto ou ausente.</div>',
            unsafe_allow_html=True
        )

    noise_summary = validation.summarize_noise_events(noise_events_df)
    for w in noise_summary["warnings"]:
        st.markdown(f'<div class="krt-alert-noise">{w}</div>', unsafe_allow_html=True)


def _tela_home_individual(sessions):
    col_f1, col_f2, col_f3 = st.columns([1.5, 1, 1])
    pilotos_raw = [p for p in sessions["nome_piloto"].fillna("").astype(str).str.strip().tolist() if p]
    pilotos = ["Todos"] + sorted(set(pilotos_raw))
    with col_f1:
        nome_teste_filtro = st.text_input("Filtrar por nome do teste")
    with col_f2:
        piloto_sel = st.selectbox("Filtrar por piloto", pilotos)
    with col_f3:
        datas = sorted(sessions["data_teste"].unique().tolist())
        data_sel = st.selectbox("Filtrar por data", ["Todas"] + [str(d) for d in datas])

    filtered = sessions.copy()
    if nome_teste_filtro:
        filtered = filtered[filtered["nome_teste"].str.contains(nome_teste_filtro, case=False, na=False)]
    if piloto_sel != "Todos":
        filtered = filtered[filtered["nome_piloto"] == piloto_sel]
    if data_sel != "Todas":
        filtered = filtered[filtered["data_teste"].astype(str) == data_sel]

    if filtered.empty:
        st.warning("Nenhuma sessão encontrada para os filtros selecionados.")
        return

    id_sel = st.selectbox(
        "Selecione uma sessão para análise detalhada",
        filtered["id_sessao"].tolist(),
        format_func=lambda i: _session_label(filtered, i),
    )

    telemetry = db.load_session_telemetry(id_sel)
    if telemetry.empty:
        st.warning("Esta sessão não possui dados de telemetria associados.")
        return
    noise_events_df = db.load_session_noise_events(id_sel)

    # --- Sanity check ---
    temp_status = validation.check_temp_sensors(telemetry)
    _render_alerts(telemetry, temp_status, noise_events_df)

    st.markdown("---")

    # --- KPIs (só os que fazem sentido para os sensores presentes) ---
    duracao_s = (telemetry["timestamp_ms"].max() - telemetry["timestamp_ms"].min()) / 1000.0
    kpis = [("Duração do ensaio", f"{duracao_s:.0f} s")]
    if telemetry["ax"].notna().any():
        kpis.append(("Ax máximo", f"{telemetry['ax'].max():.2f} g"))
    if telemetry["ay"].notna().any():
        kpis.append(("Ay máximo", f"{telemetry['ay'].abs().max():.2f} g"))
    temp_cols_present = [c for c in ("temp_dd", "temp_td", "temp_de", "temp_te")
                         if telemetry[c].notna().any()]
    if temp_cols_present:
        kpis.append(("Temp. máx. roda", f"{telemetry[temp_cols_present].max().max():.0f} °C"))
    if telemetry["thermocouple"].notna().any():
        kpis.append(("Termopar máx.", f"{telemetry['thermocouple'].max():.0f} °C"))
    if telemetry["pressao_fluido"].notna().any():
        kpis.append(("Pressão freio máx.", f"{telemetry['pressao_fluido'].max():.1f}"))
    if telemetry["angulo_volante"].notna().any():
        kpis.append(("Amplitude volante", f"{telemetry['angulo_volante'].max() - telemetry['angulo_volante'].min():.0f}°"))
    if noise_events_df is not None and not noise_events_df.empty:
        kpis.append(("Eventos de ruído", f"{len(noise_events_df)}"))

    kpi_cols = st.columns(len(kpis))
    for col, (label, value) in zip(kpi_cols, kpis):
        with col:
            st.markdown(f'<div class="krt-kpi"><div class="value">{value}</div>'
                        f'<div class="label">{label}</div></div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # --- Gráficos: só entram na aba os que têm dado real nesta sessão ---
    tab_specs = [
        ("Diagrama G-G", charts.gg_diagram(telemetry),
         "Uma distribuição circular homogênea indica que o piloto consegue explorar "
         "eficientemente a frenagem combinada com o contorno de curva, mapeando o "
         "limite real dos pneus. (Filtro low-pass aplicado para reduzir ruído elétrico.)"),
        ("Temperatura das Rodas", charts.wheel_temp_chart(telemetry, temp_status),
         "Permite avaliar se os ângulos de cambagem e convergência estão corretos, "
         "garantindo que o pneu atinja a janela ideal de funcionamento homogêneo em pista."),
        ("Termopar", charts.thermocouple_chart(telemetry), None),
        ("Giroscópio", charts.imu_chart(telemetry), None),
        ("Ângulo de Volante", charts.steering_angle_chart(telemetry), None),
        ("Pressão de Freio", charts.brake_pressure_chart(telemetry), None),
        ("Direção x Frenagem", charts.steering_brake_combined_chart(telemetry),
         "Sobrepor ângulo de volante e pressão de freio ajuda a identificar trail-braking "
         "e a coordenação entre entrada de curva e frenagem."),
        ("Traçado GPS", charts.gps_track_chart(telemetry), None),
    ]
    available_tabs = [(title, fig, caption) for title, fig, caption in tab_specs if fig is not None]

    if not available_tabs:
        st.info("Esta sessão não possui dados suficientes para gerar gráficos.")
    else:
        tabs = st.tabs([t[0] for t in available_tabs])
        for tab, (title, fig, caption) in zip(tabs, available_tabs):
            with tab:
                st.plotly_chart(fig, use_container_width=True)
                if caption:
                    st.caption(caption)

    # --- Diagnóstico de ruído elétrico (com amostragem da linha de tempo) ---
    if noise_events_df is not None and not noise_events_df.empty:
        st.markdown("---")
        st.markdown("#### ⚡ Ruído elétrico detectado no datalog")
        noise_chart = charts.noise_events_timeline_chart(telemetry, noise_events_df)
        if noise_chart is not None:
            st.plotly_chart(noise_chart, use_container_width=True)
            st.caption("As linhas verticais marcam o instante aproximado de cada trecho corrompido "
                       "por ruído elétrico/falha de comunicação serial da ESP32 — compare com a "
                       "aceleração para ver se coincide com impactos/vibração ou parece um problema "
                       "puramente elétrico (aterramento, EMI de ignição, etc.).")
        with st.expander(f"Ver amostra bruta dos {len(noise_events_df)} evento(s) de ruído"):
            display_noise = noise_events_df[["linha_arquivo", "timestamp_ms_referencia", "amostra_bruta"]].rename(
                columns={"linha_arquivo": "Linha do arquivo", "timestamp_ms_referencia": "Tempo de referência (ms)",
                         "amostra_bruta": "Amostra bruta (truncada)"})
            st.dataframe(display_noise, use_container_width=True, hide_index=True)

    if telemetry["satelites"].notna().any():
        gps_chart = charts.gps_satellites_chart(telemetry)
        if gps_chart is not None:
            with st.expander("Qualidade do sinal GPS"):
                st.plotly_chart(gps_chart, use_container_width=True)


def _tela_home_grupo():
    grupos = db.list_test_groups()
    if grupos.empty:
        st.info("Nenhum Grupo de Teste cadastrado ainda. Crie um na aba **Ingestão de Testes** "
                "(sub-aba 'Grupo de Testes') para comparar vários ensaios juntos.")
        return

    id_grupo = st.selectbox(
        "Selecione o Grupo de Teste",
        grupos["id_grupo"].tolist(),
        format_func=lambda i: f"{grupos[grupos['id_grupo']==i]['nome_grupo'].values[0]} "
                              f"({grupos[grupos['id_grupo']==i]['data_criacao'].values[0]})",
    )
    descricao = grupos[grupos["id_grupo"] == id_grupo]["descricao"].values[0]
    if descricao:
        st.caption(descricao)

    sessions_in_group = db.list_sessions_in_group(id_grupo)
    if sessions_in_group.empty:
        st.warning("Este grupo ainda não possui sessões vinculadas.")
        return

    all_ids = sessions_in_group["id_sessao"].tolist()
    ids_sel = st.multiselect(
        "Sessões a incluir na comparação",
        all_ids,
        default=all_ids,
        format_func=lambda i: _session_label(sessions_in_group, i),
    )
    if not ids_sel:
        st.warning("Selecione ao menos uma sessão.")
        return

    telemetry_by_id = {}
    kpi_rows = []
    warnings_by_session = []
    for i in ids_sel:
        label = _session_label(sessions_in_group, i)
        tel = db.load_session_telemetry(i)
        if tel.empty:
            continue
        telemetry_by_id[label] = tel
        kpi_row = _session_kpis(i, label, tel)
        if kpi_row:
            kpi_rows.append(kpi_row)
        temp_status = validation.check_temp_sensors(tel)
        for col, info in temp_status.items():
            if info["stuck"]:
                warnings_by_session.append(f"⚠️ **{label}** — sensor **{info['label']}** travado "
                                           f"(σ = {info['std']:.2f}°C).")
        noise_df = db.load_session_noise_events(i)
        if noise_df is not None and not noise_df.empty:
            warnings_by_session.append(f"⚡ **{label}** — {len(noise_df)} evento(s) de ruído elétrico no datalog.")

    if warnings_by_session:
        with st.expander(f"⚠️ {len(warnings_by_session)} alerta(s) neste grupo", expanded=False):
            for w in warnings_by_session:
                st.markdown(f'<div class="krt-alert-warn">{w}</div>', unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("#### Comparativo de KPIs entre sessões")
    kpi_df = pd.DataFrame(kpi_rows)
    if not kpi_df.empty:
        rename_map = {
            "sessao": "Sessão", "duracao_s": "Duração (s)", "ax_max": "Ax máx (g)",
            "ay_max": "Ay máx (g)", "temp_dd": "Temp DD (°C)", "temp_td": "Temp TD (°C)",
            "temp_de": "Temp DE (°C)", "temp_te": "Temp TE (°C)", "thermocouple_max": "Termopar máx (°C)",
            "pressao_max": "Pressão freio máx.",
        }
        display_kpi = kpi_df.rename(columns=rename_map).drop(columns=["id_sessao"])
        st.dataframe(display_kpi, use_container_width=True, hide_index=True)

    st.markdown("<br>", unsafe_allow_html=True)
    group_tab_specs = [
        ("Diagrama G-G Comparativo", charts.gg_diagram_multi(telemetry_by_id),
         "Cada cor representa uma sessão do grupo — útil para comparar consistência "
         "de pilotagem ou efeito de mudanças de setup ao longo dos dias."),
        ("Temperatura de Pico por Roda", charts.group_peak_temp_bar(kpi_df) if not kpi_df.empty else None, None),
    ]
    available_group_tabs = [(t, f, c) for t, f, c in group_tab_specs if f is not None]
    if not available_group_tabs:
        st.info("Nenhuma das sessões selecionadas possui dados suficientes para os gráficos comparativos.")
    else:
        tabs = st.tabs([t[0] for t in available_group_tabs])
        for tab, (title, fig, caption) in zip(tabs, available_group_tabs):
            with tab:
                st.plotly_chart(fig, use_container_width=True)
                if caption:
                    st.caption(caption)


# ---------------------------------------------------------------------------
# TELA 3 — INGESTÃO DE TESTES
# ---------------------------------------------------------------------------
def tela_ingestao():
    st.markdown("## 📥 Ingestão de Testes")
    st.caption("Envie um único ensaio ou vários de uma vez, agrupando-os para análise conjunta "
               "(ex: uma sequência de testes ao longo de vários dias). O parser reconhece tanto "
               "o datalog clássico quanto o novo (com Ângulo de Volante, Pressão de Freio e GPS) "
               "e aceita arquivos com qualquer subconjunto de colunas reconhecidas.")

    tab_unico, tab_lote = st.tabs(["🔹 Teste Único", "📚 Grupo de Testes (múltiplos arquivos)"])
    with tab_unico:
        _tela_ingestao_unica()
    with tab_lote:
        _tela_ingestao_lote()


def _tela_ingestao_unica():
    with st.form("form_ingestao_unica"):
        nome_teste = st.text_input("Nome do teste", placeholder="Ex: Frenagem — Curva 3, ensaio 2")
        c1, c2 = st.columns(2)
        with c1:
            data_teste = st.date_input("Data do teste", value=date.today())
            nome_piloto = st.text_input("Nome do piloto (opcional)", placeholder="Opcional")
        with c2:
            config_carro = st.text_input("Configuração do carro (setup, pressão de pneu, etc.)")
        observacoes = st.text_area("Observações de campo")
        arquivo = st.file_uploader("Arquivo CSV do datalog (ESP32)", type=["csv"])

        submitted = st.form_submit_button("Enviar sessão", use_container_width=True)

    if submitted:
        if not nome_teste.strip() or not arquivo:
            st.error("Preencha o nome do teste e selecione um arquivo CSV.")
            return
        try:
            df, noise_events, unrecognized = db.parse_datalog_csv(arquivo)
        except ValueError as e:
            st.error(str(e))
            return

        with st.spinner("Validando e enviando dados para o banco..."):
            vp_check = validation.check_velocidade_peso(df)
            id_sessao = db.insert_session(nome_teste, data_teste, nome_piloto, config_carro, observacoes,
                                           df, noise_events=noise_events)
            db.clear_caches()

        cols_presentes = db.available_columns(df)
        labels_presentes = ", ".join(db.COLUMN_LABELS.get(c, c) for c in cols_presentes)
        st.success(f"✅ Sessão #{id_sessao} — \"{nome_teste}\" cadastrada com sucesso! "
                   f"({len(df)} registros de telemetria)")
        st.caption(f"Sensores detectados neste arquivo: {labels_presentes}")

        for w in vp_check["warnings"]:
            st.markdown(f'<div class="krt-alert-warn">{w}</div>', unsafe_allow_html=True)

        if noise_events:
            noise_summary = validation.summarize_noise_events(pd.DataFrame(noise_events).rename(
                columns={"linha": "linha_arquivo"}))
            for w in noise_summary["warnings"]:
                st.markdown(f'<div class="krt-alert-noise">{w}</div>', unsafe_allow_html=True)

        if unrecognized:
            st.info(f"Colunas do arquivo não reconhecidas (ignoradas): {', '.join(unrecognized)}")

        with st.expander("Pré-visualização dos dados enviados"):
            st.dataframe(df.head(20), use_container_width=True)


def _tela_ingestao_lote():
    st.caption("Envie vários datalogs de uma vez (ex: todos os ensaios de um dia de testes). "
               "Cada arquivo vira uma sessão própria, e todas ficam vinculadas ao Grupo de Teste "
               "escolhido abaixo — assim depois dá para comparar tudo junto no Painel de Performance.")

    grupos = db.list_test_groups()
    modo_grupo = st.radio(
        "Grupo de Teste de destino",
        ["Criar novo grupo", "Adicionar a um grupo existente"] if not grupos.empty else ["Criar novo grupo"],
        horizontal=True,
        key="modo_grupo_lote",
    )

    novo_nome_grupo, nova_descricao_grupo, id_grupo_existente = None, None, None
    if modo_grupo == "Criar novo grupo":
        c1, c2 = st.columns(2)
        with c1:
            novo_nome_grupo = st.text_input("Nome do grupo de teste",
                                            placeholder="Ex: Testes de frenagem — Semana 3")
        with c2:
            nova_descricao_grupo = st.text_input("Descrição (opcional)")
    else:
        id_grupo_existente = st.selectbox(
            "Selecione o grupo existente",
            grupos["id_grupo"].tolist(),
            format_func=lambda i: grupos[grupos["id_grupo"] == i]["nome_grupo"].values[0],
        )

    arquivos = st.file_uploader(
        "Arquivos CSV dos datalogs (ESP32) — selecione vários de uma vez",
        type=["csv"], accept_multiple_files=True, key="uploader_lote",
    )

    if not arquivos:
        st.info("Selecione um ou mais arquivos CSV para configurar os metadados de cada sessão.")
        return

    st.markdown("#### Metadados de cada sessão")
    st.caption("Edite livremente os campos abaixo — cada linha corresponde a um arquivo enviado.")

    default_rows = []
    for f in arquivos:
        nome_padrao = f.name.rsplit(".", 1)[0]
        default_rows.append({
            "arquivo": f.name,
            "nome_teste": nome_padrao,
            "data_teste": date.today(),
            "nome_piloto": "",
            "config_carro": "",
            "observacoes": "",
        })
    default_df = pd.DataFrame(default_rows)

    edited_df = st.data_editor(
        default_df,
        use_container_width=True,
        hide_index=True,
        disabled=["arquivo"],
        column_config={
            "arquivo": st.column_config.TextColumn("Arquivo"),
            "nome_teste": st.column_config.TextColumn("Nome do teste", required=True),
            "data_teste": st.column_config.DateColumn("Data do teste", required=True),
            "nome_piloto": st.column_config.TextColumn("Piloto"),
            "config_carro": st.column_config.TextColumn("Configuração do carro"),
            "observacoes": st.column_config.TextColumn("Observações"),
        },
        key="editor_lote",
    )

    if st.button("Enviar grupo de testes", use_container_width=True):
        if modo_grupo == "Criar novo grupo" and not novo_nome_grupo:
            st.error("Dê um nome ao novo grupo de teste.")
            return
        if edited_df["nome_teste"].isna().any() or (edited_df["nome_teste"] == "").any():
            st.error("Preencha o nome do teste para todas as sessões.")
            return
        with st.spinner("Processando arquivos e enviando para o banco..."):
            rows = []
            parse_errors = []
            for f, (_, meta) in zip(arquivos, edited_df.iterrows()):
                try:
                    df_tel, noise_events, unrecognized = db.parse_datalog_csv(f)
                except ValueError as e:
                    parse_errors.append(f"**{f.name}**: {e}")
                    continue
                rows.append({
                    "nome_teste": meta["nome_teste"],
                    "data_teste": meta["data_teste"],
                    "nome_piloto": meta["nome_piloto"],
                    "config_carro": meta["config_carro"],
                    "observacoes": meta["observacoes"],
                    "df": df_tel,
                    "noise_events": noise_events,
                    "unrecognized": unrecognized,
                })

            if parse_errors:
                for e in parse_errors:
                    st.error(e)
            if not rows:
                st.error("Nenhum arquivo pôde ser processado.")
                return

            if modo_grupo == "Criar novo grupo":
                id_grupo = db.create_test_group(novo_nome_grupo, nova_descricao_grupo)
            else:
                id_grupo = id_grupo_existente

            ids_sessao = db.insert_batch_sessions(rows, id_grupo=id_grupo)
            db.clear_caches()

        st.success(f"✅ {len(ids_sessao)} sessão(ões) cadastrada(s) e vinculada(s) ao grupo com sucesso!")

        for row, id_sessao in zip(rows, ids_sessao):
            vp_check = validation.check_velocidade_peso(row["df"])
            has_alerts = bool(vp_check["warnings"]) or bool(row["noise_events"]) or bool(row["unrecognized"])
            if has_alerts:
                with st.expander(f"⚠️ Alertas — Sessão #{id_sessao} \"{row['nome_teste']}\""):
                    for w in vp_check["warnings"]:
                        st.markdown(f'<div class="krt-alert-warn">{w}</div>', unsafe_allow_html=True)
                    if row["noise_events"]:
                        noise_summary = validation.summarize_noise_events(
                            pd.DataFrame(row["noise_events"]).rename(columns={"linha": "linha_arquivo"}))
                        for w in noise_summary["warnings"]:
                            st.markdown(f'<div class="krt-alert-noise">{w}</div>', unsafe_allow_html=True)
                    if row["unrecognized"]:
                        st.info(f"Colunas não reconhecidas (ignoradas): {', '.join(row['unrecognized'])}")


# ---------------------------------------------------------------------------
# TELA 4 — DIAGNÓSTICO DE SENSORES
# ---------------------------------------------------------------------------
def tela_diagnostico():
    st.markdown("## 🔧 Diagnóstico de Sensores")
    st.caption("Status de calibração de cada sensor físico conectado à ESP32 — facilita a manutenção no box. "
               "Apenas sensores presentes no arquivo do ensaio selecionado aparecem na lista.")

    sessions = db.list_sessions()
    if sessions.empty:
        st.info("Nenhuma sessão cadastrada ainda. Envie um teste na aba **Ingestão de Testes**.")
        return

    id_sel = st.selectbox(
        "Sessão de referência para diagnóstico",
        sessions["id_sessao"].tolist(),
        format_func=lambda i: _session_label(sessions, i),
    )
    telemetry = db.load_session_telemetry(id_sel)
    noise_events_df = db.load_session_noise_events(id_sel)
    diagnostics = validation.build_sensor_diagnostics(telemetry, noise_events_df)

    status_color = {
        "OK": "krt-alert-ok",
        "FALHA": "krt-alert-fail",
        "TRAVADO": "krt-alert-fail",
        "SEM DADOS": "krt-alert-warn",
        "SEM FIX": "krt-alert-warn",
        "SUSPEITO (sem variação)": "krt-alert-warn",
        "ALERTA": "krt-alert-noise",
    }
    status_icon = {
        "OK": "✅", "FALHA": "❌", "TRAVADO": "🔒", "SEM DADOS": "❔",
        "SEM FIX": "📡", "SUSPEITO (sem variação)": "⚠️", "ALERTA": "⚡",
    }

    for d in diagnostics:
        css = status_color.get(d["status"], "krt-alert-warn")
        icon = status_icon.get(d["status"], "•")
        st.markdown(
            f'<div class="{css}"><b>{icon} {d["sensor"]}</b> — {d["status"]}<br>'
            f'<span style="font-size:13px; color:#ccc;">{d["detalhe"]}</span></div>',
            unsafe_allow_html=True
        )

    if noise_events_df is not None and not noise_events_df.empty:
        with st.expander(f"Ver amostra bruta dos {len(noise_events_df)} evento(s) de ruído elétrico"):
            display_noise = noise_events_df[["linha_arquivo", "timestamp_ms_referencia", "amostra_bruta"]].rename(
                columns={"linha_arquivo": "Linha do arquivo", "timestamp_ms_referencia": "Tempo de referência (ms)",
                         "amostra_bruta": "Amostra bruta (truncada)"})
            st.dataframe(display_noise, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.markdown("#### Sensores do subsistema de Motorização (gerenciados pela ECU FT450)")
    st.caption("Monitorados diretamente pela Fueltech FT450 — fora do escopo deste dashboard de "
               "sensoriamento ESP32, listados aqui apenas para referência da equipe.")
    ecu_sensors = ["IAT (Temp. do Ar)", "ECT (Temp. do Motor)", "TPS (Abertura da Borboleta)",
                   "CKP (Rotação)", "Pressão de Combustível/Óleo", "Sonda Lambda",
                   "Pressão da Linha de Freio", "MAP (Pressão de Admissão)"]
    st.write(", ".join(ecu_sensors))


# ---------------------------------------------------------------------------
# NAVEGAÇÃO PRINCIPAL
# ---------------------------------------------------------------------------
def main():
    if "autenticado" not in st.session_state:
        st.session_state["autenticado"] = False

    if not st.session_state["autenticado"]:
        tela_autenticacao()
        return

    db.init_db()

    with st.sidebar:
        st.markdown("## 🏁 KRT Telemetria")
        st.caption("Kamikaze Racing Team — Formula SAE UFBA")
        st.markdown("---")
        pagina = st.radio(
            "Navegação",
            ["Home / Performance", "Ingestão de Testes", "Diagnóstico de Sensores"],
            label_visibility="collapsed",
        )
        st.markdown("---")
        engine, backend = db.get_engine()
        backend_label = "☁️ Neon.tech (PostgreSQL)" if backend == "neon" else "💾 SQLite local (modo demo)"
        st.caption(f"Banco de dados: {backend_label}")
        st.caption("Eletrônica KRT UFBA")
        if st.button("Sair"):
            st.session_state["autenticado"] = False
            st.rerun()

    if pagina == "Home / Performance":
        tela_home()
    elif pagina == "Ingestão de Testes":
        tela_ingestao()
    elif pagina == "Diagnóstico de Sensores":
        tela_diagnostico()


if __name__ == "__main__":
    main()
