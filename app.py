"""
KRT — Dashboard Cloud de Telemetria e Aquisição de Dados
Kamikaze Racing Team — Formula SAE UFBA

Implementa o Documento de Especificação Arquitetural:
 - Persistência via Neon.tech (PostgreSQL Serverless), com fallback SQLite local
 - Camada de validação e consistência (Sanity Check)
 - Diagrama G-G e Gradiente Térmico das Rodas (metodologia MoTeC i2 / RaceStudio)
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
    return f"{titulo} · {row['nome_piloto']} ({row['data_teste']})"


def _session_kpis(id_sessao, label, telemetry):
    if telemetry.empty:
        return None
    duracao_s = (telemetry["timestamp_ms"].max() - telemetry["timestamp_ms"].min()) / 1000.0
    return {
        "sessao": label,
        "id_sessao": id_sessao,
        "duracao_s": round(duracao_s, 1),
        "ax_max": round(telemetry["ax"].max(), 2),
        "ay_max": round(telemetry["ay"].abs().max(), 2),
        "temp_dd": round(telemetry["temp_dd"].max(), 1),
        "temp_td": round(telemetry["temp_td"].max(), 1),
        "temp_de": round(telemetry["temp_de"].max(), 1),
        "temp_te": round(telemetry["temp_te"].max(), 1),
        "thermocouple_max": round(telemetry["thermocouple"].max(), 1),
    }

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
    .krt-kpi { background-color: #1a1a1a; border: 1px solid #333; border-radius: 8px; padding: 14px; text-align:center;}
    .krt-kpi .value { font-size: 28px; font-weight: 800; color: #FFD700; }
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


def _tela_home_individual(sessions):
    col_f1, col_f2, col_f3 = st.columns([1.5, 1, 1])
    pilotos = ["Todos"] + sorted(sessions["nome_piloto"].unique().tolist())
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

    # --- Sanity check ---
    vp_check = validation.check_velocidade_peso(telemetry)
    temp_status = validation.check_temp_sensors(telemetry)

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

    st.markdown("---")

    # --- KPIs ---
    duracao_s = (telemetry["timestamp_ms"].max() - telemetry["timestamp_ms"].min()) / 1000.0
    k1, k2, k3, k4, k5 = st.columns(5)
    kpis = [
        (k1, "Duração do ensaio", f"{duracao_s:.0f} s"),
        (k2, "Ax máximo", f"{telemetry['ax'].max():.2f} g"),
        (k3, "Ay máximo", f"{telemetry['ay'].abs().max():.2f} g"),
        (k4, "Temp. máx. roda", f"{telemetry[['temp_dd','temp_td','temp_de','temp_te']].max().max():.0f} °C"),
        (k5, "Termopar máx.", f"{telemetry['thermocouple'].max():.0f} °C"),
    ]
    for col, label, value in kpis:
        with col:
            st.markdown(f'<div class="krt-kpi"><div class="value">{value}</div>'
                        f'<div class="label">{label}</div></div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # --- Gráficos ---
    tab1, tab2, tab3, tab4 = st.tabs(["Diagrama G-G", "Temperatura das Rodas", "Termopar", "Giroscópio"])
    with tab1:
        st.plotly_chart(charts.gg_diagram(telemetry), use_container_width=True)
        st.caption("Uma distribuição circular homogênea indica que o piloto consegue explorar "
                   "eficientemente a frenagem combinada com o contorno de curva, mapeando o "
                   "limite real dos pneus. (Filtro low-pass aplicado para reduzir ruído elétrico.)")
    with tab2:
        st.plotly_chart(charts.wheel_temp_chart(telemetry, temp_status), use_container_width=True)
        st.caption("Permite avaliar se os ângulos de cambagem e convergência estão corretos, "
                   "garantindo que o pneu atinja a janela ideal de funcionamento homogêneo em pista.")
    with tab3:
        st.plotly_chart(charts.thermocouple_chart(telemetry), use_container_width=True)
    with tab4:
        st.plotly_chart(charts.imu_chart(telemetry), use_container_width=True)


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

    if warnings_by_session:
        with st.expander(f"⚠️ {len(warnings_by_session)} alerta(s) de sensores neste grupo", expanded=False):
            for w in warnings_by_session:
                st.markdown(f'<div class="krt-alert-warn">{w}</div>', unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("#### Comparativo de KPIs entre sessões")
    kpi_df = pd.DataFrame(kpi_rows)
    if not kpi_df.empty:
        display_kpi = kpi_df.rename(columns={
            "sessao": "Sessão", "duracao_s": "Duração (s)", "ax_max": "Ax máx (g)",
            "ay_max": "Ay máx (g)", "temp_dd": "Temp DD (°C)", "temp_td": "Temp TD (°C)",
            "temp_de": "Temp DE (°C)", "temp_te": "Temp TE (°C)", "thermocouple_max": "Termopar máx (°C)",
        }).drop(columns=["id_sessao"])
        st.dataframe(display_kpi, use_container_width=True, hide_index=True)

    st.markdown("<br>", unsafe_allow_html=True)
    tab1, tab2 = st.tabs(["Diagrama G-G Comparativo", "Temperatura de Pico por Roda"])
    with tab1:
        st.plotly_chart(charts.gg_diagram_multi(telemetry_by_id), use_container_width=True)
        st.caption("Cada cor representa uma sessão do grupo — útil para comparar consistência "
                   "de pilotagem ou efeito de mudanças de setup ao longo dos dias.")
    with tab2:
        if not kpi_df.empty:
            st.plotly_chart(charts.group_peak_temp_bar(kpi_df), use_container_width=True)


# ---------------------------------------------------------------------------
# TELA 3 — INGESTÃO DE TESTES
# ---------------------------------------------------------------------------
def tela_ingestao():
    st.markdown("## 📥 Ingestão de Testes")
    st.caption("Envie um único ensaio ou vários de uma vez, agrupando-os para análise conjunta "
               "(ex: uma sequência de testes ao longo de vários dias).")

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
            nome_piloto = st.text_input("Nome do piloto")
        with c2:
            config_carro = st.text_input("Configuração do carro (setup, pressão de pneu, etc.)")
        observacoes = st.text_area("Observações de campo")
        arquivo = st.file_uploader("Arquivo CSV do datalog (ESP32)", type=["csv"])

        submitted = st.form_submit_button("Enviar sessão", use_container_width=True)

    if submitted:
        if not nome_teste or not nome_piloto or not arquivo:
            st.error("Preencha o nome do teste, o nome do piloto e selecione um arquivo CSV.")
            return
        try:
            df = db.parse_datalog_csv(arquivo)
        except ValueError as e:
            st.error(str(e))
            return

        with st.spinner("Validando e enviando dados para o banco..."):
            vp_check = validation.check_velocidade_peso(df)
            id_sessao = db.insert_session(nome_teste, data_teste, nome_piloto, config_carro, observacoes, df)
            db.clear_caches()

        st.success(f"✅ Sessão #{id_sessao} — \"{nome_teste}\" cadastrada com sucesso! "
                   f"({len(df)} registros de telemetria)")
        for w in vp_check["warnings"]:
            st.markdown(f'<div class="krt-alert-warn">{w}</div>', unsafe_allow_html=True)

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
            "nome_piloto": st.column_config.TextColumn("Piloto", required=True),
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
        if edited_df["nome_piloto"].isna().any() or (edited_df["nome_piloto"] == "").any():
            st.error("Preencha o nome do piloto para todas as sessões.")
            return

        with st.spinner("Processando arquivos e enviando para o banco..."):
            rows = []
            parse_errors = []
            for f, (_, meta) in zip(arquivos, edited_df.iterrows()):
                try:
                    df_tel = db.parse_datalog_csv(f)
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
            if vp_check["warnings"]:
                with st.expander(f"⚠️ Alertas — Sessão #{id_sessao} \"{row['nome_teste']}\""):
                    for w in vp_check["warnings"]:
                        st.markdown(f'<div class="krt-alert-warn">{w}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# TELA 4 — DIAGNÓSTICO DE SENSORES
# ---------------------------------------------------------------------------
def tela_diagnostico():
    st.markdown("## 🔧 Diagnóstico de Sensores")
    st.caption("Status de calibração de cada sensor físico conectado à ESP32 — facilita a manutenção no box.")

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
    diagnostics = validation.build_sensor_diagnostics(telemetry)

    status_color = {
        "OK": "krt-alert-ok",
        "FALHA": "krt-alert-fail",
        "TRAVADO": "krt-alert-fail",
        "SEM DADOS": "krt-alert-warn",
        "SUSPEITO (sem variação)": "krt-alert-warn",
    }
    status_icon = {
        "OK": "✅", "FALHA": "❌", "TRAVADO": "🔒", "SEM DADOS": "❔", "SUSPEITO (sem variação)": "⚠️"
    }

    for d in diagnostics:
        css = status_color.get(d["status"], "krt-alert-warn")
        icon = status_icon.get(d["status"], "•")
        st.markdown(
            f'<div class="{css}"><b>{icon} {d["sensor"]}</b> — {d["status"]}<br>'
            f'<span style="font-size:13px; color:#ccc;">{d["detalhe"]}</span></div>',
            unsafe_allow_html=True
        )

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
        st.caption("by - Wellington Miguel | Eletrônica KRT UFBA")
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
