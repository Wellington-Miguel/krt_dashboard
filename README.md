# KRT — Dashboard Cloud de Telemetria e Aquisição de Dados

Aplicação Streamlit para a Kamikaze Racing Team (KRT — Formula SAE UFBA), implementada
conforme o **Documento de Especificação Arquitetural e Requisitos do Dashboard de
Telemetria Cloud**.

## O que a aplicação faz

- **Persistência em nuvem (Neon.tech):** dados de sessões de teste e telemetria bruta
  ficam salvos em PostgreSQL Serverless, sobrevivendo aos reinícios do container
  efêmero do Streamlit Cloud. Se nenhuma credencial do Neon for configurada, a
  aplicação cai automaticamente para um banco SQLite local (`krt_telemetry.db`),
  permitindo testar tudo sem depender de nuvem.
- **Sanity check automático:** ao subir um datalog, a aplicação identifica e alerta
  (sem quebrar o app) os problemas de hardware já observados nos logs reais
  (`datalog415.csv` a `datalog481.csv`):
  - Sensor de Velocidade 100% nulo (NaN).
  - Sensor de Peso (célula de carga) travado em zero.
  - Sensor de temperatura infravermelho Temp DE com desvio padrão insignificante
    (sem modulação), indicando sensor travado — mesmo quando o Temp TD ultrapassa
    140°C no mesmo ensaio.
- **Diagrama G-G** (Ay x Ax, com filtro low-pass) e **gráfico de temperatura das 4
  rodas** sincronizado no tempo, seguindo a metodologia de softwares de aquisição
  como MoTeC i2 e RaceStudio.
- **4 telas:** Autenticação restrita → Home/Painel de Performance → Ingestão de
  Testes → Diagnóstico de Sensores.
- **Ingestão flexível:** cada sessão tem um **Nome do Teste** próprio (além do piloto).
  Dá para enviar um ensaio por vez (**Teste Único**) ou vários arquivos CSV de uma vez
  (**Grupo de Testes**), com uma tabela editável de metadados por arquivo. Sessões de um
  grupo podem ser de dias diferentes e ficam vinculadas para análise conjunta.
- **Análise comparativa por Grupo de Teste:** na Home, o modo "Grupo de Teste" mostra
  uma tabela de KPIs lado a lado, um Diagrama G-G sobrepondo todas as sessões
  selecionadas (cada uma com uma cor) e um gráfico de temperatura de pico por roda
  comparando as sessões — ideal para acompanhar evolução ao longo dos dias de teste.
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

### Testando com os dados reais da equipe

Os 9 arquivos `datalog*.csv` enviados já foram usados para validar as regras de
sanity check. Você pode subi-los pela tela **Ingestão de Testes** para popular o
dashboard com dados reais de ensaio.

## Configurando o Neon.tech (produção)

1. Copie `.streamlit/secrets.toml.example` para `.streamlit/secrets.toml`.
2. Preencha `NEON_CONN_STRING` com a connection string do seu projeto Neon
   (Dashboard Neon → Connection Details → "Pooled connection").
3. Defina uma `APP_PASSWORD` forte para a equipe.
4. **Nunca** commite `secrets.toml` no GitHub — ele já deve entrar no `.gitignore`.

## Deploy no Streamlit Cloud

1. Suba esta pasta para um repositório GitHub (sem o `secrets.toml` real).
2. Em [share.streamlit.io](https://share.streamlit.io), aponte para `app.py`.
3. Em **App settings → Secrets**, cole o conteúdo do seu `secrets.toml` com a
   connection string real do Neon.

## Estrutura de arquivos

```
krt_dashboard/
├── app.py                  # Navegação principal e as 4 telas
├── db.py                   # Conexão Neon.tech/SQLite + schema + bulk insert
├── validation.py            # Sanity check (velocidade, peso, sensores travados)
├── charts.py                 # Diagrama G-G, gráfico térmico das rodas, etc.
├── requirements.txt
├── .streamlit/
│   ├── config.toml          # Tema visual KRT
│   └── secrets.toml.example
└── sample_data/              # Os 9 datalogs enviados, para teste rápido
```
