"""
Camada de validação e consistência (Sanity Check).

Implementa as regras descritas no Documento de Especificação Arquitetural:
 - Velocidade 100% nula (NaN) e Peso estático zerado -> alerta amigável, sem
   quebrar a renderização dos gráficos.
 - Sensor Temp DE (infravermelho dianteiro esquerdo) travado: desvio padrão
   insignificante (baixa variância) próximo de ~29-36°C mesmo em ensaios
   dinâmicos de alta temperatura -> indica ausência de modulação / falha de
   leitura.
"""

import pandas as pd
import numpy as np

# Limiares de detecção (ajustáveis conforme calibração real dos sensores)
TEMP_DE_STD_THRESHOLD = 1.0     # °C — abaixo disso, consideramos sensor "travado"
TEMP_OTHER_MIN_STD_DYNAMIC = 3.0  # °C — usado para confirmar que outros sensores SÃO dinâmicos
VELOCIDADE_NULL_THRESHOLD = 0.95   # 95%+ de nulos = sensor não está entregando dado
PESO_ZERO_THRESHOLD = 0.95


def check_velocidade_peso(df: pd.DataFrame) -> dict:
    result = {"ok": True, "warnings": []}
    if df.empty:
        return result

    vel_nan_pct = df["velocidade"].isna().mean() if "velocidade" in df else 1.0
    peso_zero_pct = (df["peso"] == 0).mean() if "peso" in df else 1.0

    if vel_nan_pct >= VELOCIDADE_NULL_THRESHOLD:
        result["ok"] = False
        result["warnings"].append(
            f"⚠️ Sensor de Velocidade sem leitura válida nesta sessão "
            f"({vel_nan_pct*100:.0f}% dos registros nulos). O gráfico de "
            f"velocidade não será exibido para evitar dados enganosos."
        )
    if peso_zero_pct >= PESO_ZERO_THRESHOLD:
        result["ok"] = False
        result["warnings"].append(
            f"⚠️ Sensor de Peso (célula de carga) retornando valor estático "
            f"zerado em {peso_zero_pct*100:.0f}% dos registros. Verifique a "
            f"instalação/calibração da célula de carga antes do próximo ensaio."
        )

    result["vel_nan_pct"] = vel_nan_pct
    result["peso_zero_pct"] = peso_zero_pct
    return result


def check_temp_sensors(df: pd.DataFrame) -> dict:
    """Verifica travamento de cada sensor de temperatura de roda (DD, TD, DE, TE)."""
    wheels = {
        "temp_dd": "Dianteira Direita (DD)",
        "temp_td": "Traseira Direita (TD)",
        "temp_de": "Dianteira Esquerda (DE)",
        "temp_te": "Traseira Esquerda (TE)",
    }
    status = {}
    if df.empty:
        for col, label in wheels.items():
            status[col] = {"label": label, "std": None, "mean": None, "stuck": None}
        return status

    for col, label in wheels.items():
        if col not in df:
            status[col] = {"label": label, "std": None, "mean": None, "stuck": None}
            continue
        std = df[col].std()
        mean = df[col].mean()
        stuck = bool(std is not None and not np.isnan(std) and std < TEMP_DE_STD_THRESHOLD)
        status[col] = {"label": label, "std": std, "mean": mean, "stuck": stuck}
    return status


def build_sensor_diagnostics(df: pd.DataFrame) -> list:
    """Monta a lista de status de sensores para a Tela 4 (Diagnóstico de Sensores)."""
    diagnostics = []

    vp = check_velocidade_peso(df)
    diagnostics.append({
        "sensor": "Sensor de Velocidade (Hall - roda)",
        "status": "FALHA" if not vp.get("vel_nan_pct", 1) < VELOCIDADE_NULL_THRESHOLD else "OK",
        "detalhe": f"{vp.get('vel_nan_pct', 1)*100:.0f}% de leituras nulas na última sessão."
                   if not df.empty else "Sem dados carregados.",
    })
    diagnostics.append({
        "sensor": "Célula de Carga (Peso)",
        "status": "FALHA" if not df.empty and vp.get("peso_zero_pct", 1) >= PESO_ZERO_THRESHOLD else "OK",
        "detalhe": f"{vp.get('peso_zero_pct', 1)*100:.0f}% dos registros com valor estático zerado."
                   if not df.empty else "Sem dados carregados.",
    })

    temp_status = check_temp_sensors(df)
    for col, info in temp_status.items():
        if info["std"] is None:
            diagnostics.append({
                "sensor": f"Temp. Infravermelho — {info['label']}",
                "status": "SEM DADOS",
                "detalhe": "Sem dados carregados.",
            })
        else:
            diagnostics.append({
                "sensor": f"Temp. Infravermelho — {info['label']}",
                "status": "TRAVADO" if info["stuck"] else "OK",
                "detalhe": f"σ = {info['std']:.2f}°C, média = {info['mean']:.1f}°C"
                           + (" (variação insignificante — possível falha de leitura)"
                              if info["stuck"] else " (variação dinâmica normal)"),
            })

    for col in ["ax", "ay", "az", "gx", "gy", "gz", "thermocouple"]:
        if col not in df or df.empty:
            diagnostics.append({"sensor": col.upper(), "status": "SEM DADOS", "detalhe": "Sem dados carregados."})
            continue
        std = df[col].std()
        diagnostics.append({
            "sensor": {
                "ax": "Acelerômetro X", "ay": "Acelerômetro Y", "az": "Acelerômetro Z",
                "gx": "Giroscópio X", "gy": "Giroscópio Y", "gz": "Giroscópio Z",
                "thermocouple": "Termopar (Escapamento)",
            }[col],
            "status": "OK" if std and std > 0 else "SUSPEITO (sem variação)",
            "detalhe": f"σ = {std:.3f}" if std is not None else "N/A",
        })

    return diagnostics
