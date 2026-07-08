"""
Camada de validação e consistência (Sanity Check).

Cada verificação é feita de forma DEFENSIVA em relação às colunas disponíveis:
como o schema de telemetria agora é um superconjunto (cobrindo tanto o datalog
antigo quanto o novo, ou qualquer subconjunto de sensores), uma coluna pode
simplesmente não existir no ensaio carregado (arquivo mais enxuto) ou existir
mas estar 100% nula (sensor não instalado/não conectado naquele ensaio). Os
dois casos são tratados como "SEM DADOS" e nunca quebram a tela — apenas os
sensores realmente presentes entram na análise.

Também é feita aqui a triagem dos EVENTOS DE RUÍDO ELÉTRICO detectados pelo
parser (db.parse_datalog_csv): trechos do datalog corrompidos por interferência
na comunicação serial da ESP32, que precisam ser sinalizados para a equipe de
Eletrônica investigar (aterramento, blindagem de cabos, fonte de ruído, etc.).
"""

import numpy as np
import pandas as pd

# Limiares de detecção (ajustáveis conforme calibração real dos sensores)
TEMP_STUCK_STD_THRESHOLD = 1.0     # °C — abaixo disso, consideramos sensor "travado"
VELOCIDADE_NULL_THRESHOLD = 0.95   # 95%+ de nulos = sensor não está entregando dado
PESO_ZERO_THRESHOLD = 0.95
GPS_NO_FIX_THRESHOLD = 0.95        # 95%+ dos registros com 0 satélites = sem fix de GPS

WHEEL_COLS = {
    "temp_dd": "Dianteira Direita (DD)",
    "temp_td": "Traseira Direita (TD)",
    "temp_de": "Dianteira Esquerda (DE)",
    "temp_te": "Traseira Esquerda (TE)",
}


def _col_present(df: pd.DataFrame, col: str) -> bool:
    return col in df.columns and df[col].notna().any()


def check_velocidade_peso(df: pd.DataFrame) -> dict:
    """Sanity check dos sensores de velocidade e peso (quando presentes no ensaio)."""
    result = {"ok": True, "warnings": []}
    if df.empty:
        return result

    if _col_present(df, "velocidade"):
        vel_nan_pct = df["velocidade"].isna().mean()
        result["vel_nan_pct"] = vel_nan_pct
        if vel_nan_pct >= VELOCIDADE_NULL_THRESHOLD:
            result["ok"] = False
            result["warnings"].append(
                f"⚠️ Sensor de Velocidade sem leitura válida nesta sessão "
                f"({vel_nan_pct*100:.0f}% dos registros nulos). O gráfico de "
                f"velocidade não será exibido para evitar dados enganosos."
            )

    if _col_present(df, "peso"):
        peso_zero_pct = (df["peso"] == 0).mean()
        result["peso_zero_pct"] = peso_zero_pct
        if peso_zero_pct >= PESO_ZERO_THRESHOLD:
            result["ok"] = False
            result["warnings"].append(
                f"⚠️ Sensor de Peso (célula de carga) retornando valor estático "
                f"zerado em {peso_zero_pct*100:.0f}% dos registros. Verifique a "
                f"instalação/calibração da célula de carga antes do próximo ensaio."
            )

    return result


def check_temp_sensors(df: pd.DataFrame) -> dict:
    """Verifica travamento de cada sensor de temperatura de roda (DD, TD, DE, TE).
    Sensores ausentes do arquivo ou 100% nulos entram como status None/"sem dados"."""
    status = {}
    for col, label in WHEEL_COLS.items():
        if not _col_present(df, col):
            status[col] = {"label": label, "std": None, "mean": None, "stuck": None}
            continue
        std = df[col].std()
        mean = df[col].mean()
        stuck = bool(std is not None and not np.isnan(std) and std < TEMP_STUCK_STD_THRESHOLD)
        status[col] = {"label": label, "std": std, "mean": mean, "stuck": stuck}
    return status


def check_gps(df: pd.DataFrame) -> dict:
    """Verifica qualidade do sinal de GPS (fix), quando o ensaio possui esses dados."""
    result = {"present": False}
    if not _col_present(df, "satelites"):
        return result
    result["present"] = True
    no_fix_pct = (df["satelites"].fillna(0) <= 0).mean()
    result["no_fix_pct"] = no_fix_pct
    result["sat_medio"] = df["satelites"].mean()
    result["sem_fix"] = no_fix_pct >= GPS_NO_FIX_THRESHOLD
    return result


def summarize_noise_events(noise_events_df: pd.DataFrame) -> dict:
    """Resume os eventos de ruído elétrico (linhas corrompidas na serial da ESP32)
    registrados durante a ingestão de uma sessão."""
    if noise_events_df is None or noise_events_df.empty:
        return {"count": 0, "warnings": []}

    count = len(noise_events_df)
    primeira_ref = noise_events_df["timestamp_ms_referencia"].dropna()
    warnings = [
        f"⚡ {count} trecho(s) do datalog corrompido(s) por ruído elétrico/falha de "
        f"comunicação serial da ESP32 e ignorado(s) na ingestão — os demais dados "
        f"da sessão não foram afetados."
    ]
    if not primeira_ref.empty:
        tempos = ", ".join(f"~{int(t)} ms" for t in primeira_ref.head(5))
        warnings.append(f"Ocorrência(s) próxima(s) ao tempo: {tempos}"
                         + (" (e outras…)" if count > 5 else "") + ".")
    return {"count": count, "warnings": warnings}


def build_sensor_diagnostics(df: pd.DataFrame, noise_events_df: pd.DataFrame = None) -> list:
    """Monta a lista de status de sensores para a Tela de Diagnóstico de Sensores.
    Só inclui na lista os sensores que de fato têm alguma presença no arquivo
    (evita poluir a tela com sensores que o ensaio nunca teve)."""
    diagnostics = []

    if df.empty:
        diagnostics.append({"sensor": "Telemetria", "status": "SEM DADOS",
                             "detalhe": "Nenhum dado carregado para esta sessão."})
        return diagnostics

    vp = check_velocidade_peso(df)
    if "vel_nan_pct" in vp:
        diagnostics.append({
            "sensor": "Sensor de Velocidade (Hall - roda)",
            "status": "FALHA" if vp["vel_nan_pct"] >= VELOCIDADE_NULL_THRESHOLD else "OK",
            "detalhe": f"{vp['vel_nan_pct']*100:.0f}% de leituras nulas nesta sessão.",
        })
    if "peso_zero_pct" in vp:
        diagnostics.append({
            "sensor": "Célula de Carga (Peso)",
            "status": "FALHA" if vp["peso_zero_pct"] >= PESO_ZERO_THRESHOLD else "OK",
            "detalhe": f"{vp['peso_zero_pct']*100:.0f}% dos registros com valor estático zerado.",
        })

    temp_status = check_temp_sensors(df)
    for col, info in temp_status.items():
        if info["std"] is None:
            continue  # sensor ausente neste ensaio — não polui o diagnóstico
        diagnostics.append({
            "sensor": f"Temp. Infravermelho — {info['label']}",
            "status": "TRAVADO" if info["stuck"] else "OK",
            "detalhe": f"σ = {info['std']:.2f}°C, média = {info['mean']:.1f}°C"
                       + (" (variação insignificante — possível falha de leitura)"
                          if info["stuck"] else " (variação dinâmica normal)"),
        })

    if _col_present(df, "thermocouple"):
        std = df["thermocouple"].std()
        diagnostics.append({
            "sensor": "Termopar (Escapamento)",
            "status": "OK" if std and std > 0 else "SUSPEITO (sem variação)",
            "detalhe": f"σ = {std:.3f}" if std is not None else "N/A",
        })

    for col, label in [("gx", "Giroscópio X"), ("gy", "Giroscópio Y"), ("gz", "Giroscópio Z"),
                        ("ax", "Acelerômetro X"), ("ay", "Acelerômetro Y"), ("az", "Acelerômetro Z")]:
        if not _col_present(df, col):
            continue
        std = df[col].std()
        diagnostics.append({
            "sensor": label,
            "status": "OK" if std and std > 0 else "SUSPEITO (sem variação)",
            "detalhe": f"σ = {std:.3f}" if std is not None else "N/A",
        })

    if _col_present(df, "angulo_volante"):
        std = df["angulo_volante"].std()
        diagnostics.append({
            "sensor": "Sensor de Ângulo de Volante",
            "status": "OK" if std and std > 0 else "SUSPEITO (sem variação)",
            "detalhe": f"σ = {std:.2f}°" if std is not None else "N/A",
        })

    if _col_present(df, "pressao_fluido"):
        std = df["pressao_fluido"].std()
        diagnostics.append({
            "sensor": "Sensor de Pressão de Fluido de Freio",
            "status": "OK" if std and std > 0 else "SUSPEITO (sem variação)",
            "detalhe": f"σ = {std:.3f}" if std is not None else "N/A",
        })

    gps = check_gps(df)
    if gps["present"]:
        diagnostics.append({
            "sensor": "Módulo GPS (fix de satélites)",
            "status": "SEM FIX" if gps["sem_fix"] else "OK",
            "detalhe": f"Média de {gps['sat_medio']:.1f} satélites; "
                       f"{gps['no_fix_pct']*100:.0f}% dos registros sem fix (0 satélites).",
        })

    noise_summary = summarize_noise_events(noise_events_df)
    if noise_summary["count"] > 0:
        diagnostics.append({
            "sensor": "Comunicação Serial ESP32 (Ruído Elétrico)",
            "status": "ALERTA",
            "detalhe": " ".join(noise_summary["warnings"]),
        })

    return diagnostics
