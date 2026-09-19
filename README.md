# KRT — Dashboard Cloud de Telemetria e Aquisição de Dados

Aplicação Streamlit para a Kamikaze Racing Team (KRT — Formula SAE UFBA).

## O que mudou nesta versão

O datalog mais recente da ESP32 troca os sensores de **Peso**, **Velocidade**,
**Giroscópio** e **Termopar** por **Ângulo de Volante**, **Pressão de Fluido de
Freio** e **GPS** (Latitude/Longitude/Satélites) — e o dashboard precisava
continuar funcionando tanto com o formato antigo quanto com o novo, incluindo
arquivos com qualquer subconjunto de colunas.

- **Schema em superconjunto + parser por aliases:** o banco agora guarda um
  superconjunto de todas as colunas já vistas (antigas e novas). O parser de
  CSV reconhece cada coluna do cabeçalho por nome (tolerando variação de
  acentuação/maiúsculas/underscore) e mapeia para o nome canônico — colunas
  ausentes no arquivo ficam nulas, e colunas desconhecidas são apenas
  ignoradas (com aviso informativo). Isso significa que **qualquer arquivo
  com qualquer subconjunto de sensores é aceito**, e cada tela/gráfico exibe
  só o que aquele ensaio realmente possui (abas e KPIs são montados
  dinamicamente).
- **Detecção de ruído elétrico na comunicação serial:** ao ingerir um datalog,
  linhas corrompidas (bytes inválidos, número de campos incorreto ou falha de
  conversão numérica — sintomas típicos de ruído elétrico/EMI na serial da
  ESP32) são isoladas automaticamente, sem quebrar a ingestão do restante do
  arquivo. Cada evento fica registrado (linha do arquivo, tempo de referência
  e amostra bruta) e é exibido:
  - como alerta na tela de Ingestão e no Painel de Performance;
  - com **amostragem bruta** dos trechos corrompidos no Diagnóstico de Sensores;
  - em um **gráfico de linha do tempo** que sobrepõe os instantes de ruído à
    magnitude de aceleração da sessão, ajudando a equipe de Eletrônica a ver
    se o ruído coincide com impacto/vibração (zebra, buraco) ou parece um
    problema puramente elétrico (aterramento, EMI de ignição/motor).
- **Novos gráficos:** Ângulo de Volante, Pressão de Fluido de Freio, um
  gráfico combinado Direção x Frenagem (útil para trail-braking), traçado do
  percurso via GPS e qualidade do sinal (nº de satélites).
- **Sanity check automático:** mantém as regras já validadas nos datalogs
  reais — Velocidade 100% nula, Peso travado em zero, sensor de temperatura
  travado (baixa variância) — e adiciona verificação de fix de GPS.
- **4 telas:** Autenticação restrita → Home/Painel de Performance → Ingestão de
  Testes → Diagnóstico de Sensores.
- **Identidade visual KRT:** fundo escuro (#111111), destaque em amarelo ouro
  (#FFD700), textos em branco.

## Como rodar localmente

```bash
cd krt_dashboard
pip install -r requirements.txt
streamlit run app.py
```

Sem configurar `secrets.toml`, a senha padrão de acesso é `krt2026` e os dados vão
para um SQLite local — ótimo para testar antes de publicar.

## Configurando o Neon.tech (produção)

1. Copie `.streamlit/secrets.toml.example` para `.streamlit/secrets.toml`.
2. Preencha `NEON_CONN_STRING` com a connection string do seu projeto Neon
   (Dashboard Neon → Connection Details → "Pooled connection").
3. Defina uma `APP_PASSWORD` forte para a equipe.
4. **Nunca** commite `secrets.toml` no GitHub — ele já deve entrar no `.gitignore`.

Bancos já existentes recebem as colunas novas automaticamente na inicialização
(`_ensure_schema_upgrades` em `db.py`), sem precisar recriar o banco.

## Deploy no Streamlit Cloud

1. Suba esta pasta para um repositório GitHub (sem o `secrets.toml` real).
2. Em [share.streamlit.io](https://share.streamlit.io), aponte para `app.py`.
3. Em **App settings → Secrets**, cole o conteúdo do seu `secrets.toml` com a
   connection string real do Neon.

## Estrutura de arquivos

```
krt_dashboard/
├── app.py                  # Navegação principal e as 4 telas (tabs/KPIs dinâmicos)
├── db.py                   # Conexão Neon.tech/SQLite + schema superconjunto +
│                            #   parser CSV por aliases + detecção de ruído elétrico
├── validation.py            # Sanity check (velocidade, peso, sensores travados,
│                            #   GPS, resumo de eventos de ruído)
├── charts.py                 # Diagrama G-G, térmico das rodas, volante, freio,
│                            #   GPS e linha do tempo de ruído elétrico
├── requirements.txt
├── .streamlit/
│   ├── config.toml          # Tema visual KRT
│   └── secrets.toml.example
└── summary.py               # Resumo textual automático dos destaques do ensaio
```
