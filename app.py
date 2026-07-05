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

    col_f1, col_f2 = st.columns(2)
    pilotos = ["Todos"] + sorted(sessions["nome_piloto"].unique().tolist())
    with col_f1:
        piloto_sel = st.selectbox("Filtrar por piloto", pilotos)
    with col_f2:
        datas = sorted(sessions["data_teste"].unique().tolist())
        data_sel = st.selectbox("Filtrar por data", ["Todas"] + [str(d) for d in datas])

    filtered = sessions.copy()
    if piloto_sel != "Todos":
        filtered = filtered[filtered["nome_piloto"] == piloto_sel]
    if data_sel != "Todas":
        filtered = filtered[filtered["data_teste"].astype(str) == data_sel]

    if filtered.empty:
        st.warning("Nenhuma sessão encontrada para os filtros selecionados.")
        return

    st.markdown("#### Sessões disponíveis")
    display_cols = ["id_sessao", "data_teste", "nome_piloto", "config_carro", "observacoes"]
    st.dataframe(filtered[display_cols], use_container_width=True, hide_index=True)

    id_sel = st.selectbox(
        "Selecione uma sessão para análise detalhada",
        filtered["id_sessao"].tolist(),
        format_func=lambda i: f"#{i} — {filtered[filtered['id_sessao']==i]['nome_piloto'].values[0]} "
                              f"({filtered[filtered['id_sessao']==i]['data_teste'].values[0]})"
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

    if not vp_check.get("vel_nan_pct", 1) < validation.VELOCIDADE_NULL_THRESHOLD:
        pass  # já alertado acima — gráfico de velocidade omitido de propósito


# ---------------------------------------------------------------------------
# TELA 3 — INGESTÃO DE TESTES
# ---------------------------------------------------------------------------
def tela_ingestao():
    st.markdown("## 📥 Ingestão de Testes")
    st.caption("Formulário de metadados + upload do datalog CSV gerado pela ESP32. "
               "Upload em lote (Bulk Insert) para o banco de dados na nuvem.")

    with st.form("form_ingestao"):
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
        if not nome_piloto or not arquivo:
            st.error("Preencha o nome do piloto e selecione um arquivo CSV.")
            return
        try:
            df = db.parse_datalog_csv(arquivo)
        except ValueError as e:
            st.error(str(e))
            return

        with st.spinner("Validando e enviando dados para o banco..."):
            vp_check = validation.check_velocidade_peso(df)
            id_sessao = db.insert_session(data_teste, nome_piloto, config_carro, observacoes, df)
            db.clear_caches()

        st.success(f"✅ Sessão #{id_sessao} cadastrada com sucesso! ({len(df)} registros de telemetria)")
        for w in vp_check["warnings"]:
            st.markdown(f'<div class="krt-alert-warn">{w}</div>', unsafe_allow_html=True)

        with st.expander("Pré-visualização dos dados enviados"):
            st.dataframe(df.head(20), use_container_width=True)


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
        format_func=lambda i: f"#{i} — {sessions[sessions['id_sessao']==i]['nome_piloto'].values[0]} "
                              f"({sessions[sessions['id_sessao']==i]['data_teste'].values[0]})"
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
