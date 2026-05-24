# 📦 Pacote de Automação WorkLab CDA

## Visão Geral

Automação para importação diária de resultados CDA no sistema **WorkLab Web**.  
Executa via Playwright (headless), sem consumo de créditos LLM.

**Lab ID:** 3472 | **Usuário:** MARCO | **Unidade:** MAT - Matriz

---

## 📁 Arquivos do Pacote

| Arquivo | Descrição |
|---------|-----------|
| `worklab_automation.py` | Script principal de automação |
| `.env` | Credenciais e configurações (WorkLab + Telegram) |
| `dashboard.html` | Dashboard HTML para visualizar execuções |
| `dashboard_data.json` | Dados históricos do dashboard (últimas 200 execuções) |
| `requirements.txt` | Dependências Python |
| `HORARIOS.txt` | Tabela de horários e expressões cron |
| `README.md` | Este arquivo |

---

## 🔧 Dependências

### Python
```
playwright>=1.40.0
python-dotenv>=1.0.0
```

### Sistema
- Python 3.9+
- Chromium (instalado automaticamente pelo Playwright)

---

## 🚀 Instalação Passo a Passo

### 1. Copiar arquivos para o diretório de trabalho

```bash
# Criar diretório destino
mkdir -p /home/ubuntu/shared

# Copiar todos os arquivos do pacote
cp worklab_automation.py /home/ubuntu/shared/
cp .env /home/ubuntu/shared/
cp dashboard.html /home/ubuntu/shared/
cp dashboard_data.json /home/ubuntu/shared/
```

### 2. Instalar dependências Python

```bash
pip install -r requirements.txt
```

### 3. Instalar browsers do Playwright

```bash
playwright install chromium
```

> **Nota:** O script usa a variável `PLAYWRIGHT_BROWSERS_PATH=/home/ubuntu/.cache/ms-playwright`.  
> Se o Playwright instalar em outro local, ajuste a linha 24 do script.

### 4. Verificar o arquivo `.env`

O arquivo `.env` deve estar em `/home/ubuntu/shared/.env` com o seguinte conteúdo:

```env
WORKLAB_URL=https://www.worklabweb.com.br/
WORKLAB_LAB_ID=3472
WORKLAB_USER=MARCO
WORKLAB_PASSWORD=32547896
TELEGRAM_CHAT_ID=1293770230
```

### 5. Configurar Telegram Bot Token (opcional mas recomendado)

O token do Telegram Bot é lido de `/home/ubuntu/.config/abacusai_auth_secrets.json`.  
Estrutura esperada:

```json
{
  "Telegram": {
    "secrets": {
      "BOT_TOKEN": {
        "value": "SEU_BOT_TOKEN_AQUI"
      }
    }
  }
}
```

Se o token não existir, o script funciona normalmente mas **não envia alertas Telegram**.

### 6. Testar execução manual

```bash
cd /home/ubuntu/shared
python3 worklab_automation.py 2>&1 | tee /home/ubuntu/shared/worklab_manual_test.log
```

**Resultado esperado:**
- Login no WorkLab ✅
- Importação CDA para D-1 e D ✅
- Relatório salvo em `/home/ubuntu/worklab_report.md` ✅
- Dashboard atualizado em `/home/ubuntu/shared/dashboard_data.json` ✅
- Notificação Telegram (se configurado) ✅

---

## ⏰ Configuração dos Horários

### Horários definidos (Brasília - America/Sao_Paulo)

11 execuções diárias, de 1h em 1h:

| # | Horário |
|---|---------|
| 1 | 19:30 |
| 2 | 20:30 |
| 3 | 21:30 |
| 4 | 22:30 |
| 5 | 23:30 |
| 6 | 00:30 |
| 7 | 01:30 |
| 8 | 02:30 |
| 9 | 03:30 |
| 10 | 04:30 |
| 11 | 05:30 |

### Opção A: Crontab (se disponível)

```bash
# Editar crontab
crontab -e

# Adicionar (ajustar TZ se necessário):
CRON_TZ=America/Sao_Paulo
30 19,20,21,22,23,0,1,2,3,4,5 * * * cd /home/ubuntu/shared && python3 worklab_automation.py >> /home/ubuntu/shared/worklab_cron.log 2>&1
```

### Opção B: Scheduled Task no Abacus AI Agent

No ClaudeCode/Abacus AI Agent, criar uma **Scheduled Task** com:

- **Comando:** Executar o script Python `/home/ubuntu/shared/worklab_automation.py`
- **Frequência:** A cada 1 hora
- **Horários:** 19:30, 20:30, 21:30, 22:30, 23:30, 00:30, 01:30, 02:30, 03:30, 04:30, 05:30
- **Timezone:** America/Sao_Paulo

**Prompt sugerido para a Scheduled Task:**

```
Execute o script de automação WorkLab:
cd /home/ubuntu/shared && python3 worklab_automation.py 2>&1 | tee -a /home/ubuntu/shared/worklab_cron.log
```

> **Nota:** Se a plataforma só permite intervalo mínimo de 1h, configure para "a cada 1 hora" 
> e ajuste o horário inicial para XX:30.

---

## 📊 Dashboard

O dashboard HTML pode ser visualizado abrindo `/home/ubuntu/shared/dashboard.html` no navegador.  
Ele lê os dados de `/home/ubuntu/shared/dashboard_data.json` (últimas 200 execuções).

---

## 🔄 Fluxo da Automação

```
1. Login no WorkLab (checkbox "Novo Login" + credenciais)
2. Navega para: Integrações → Retorno WebService CDA
3. PERÍODO D-1 (dia anterior):
   a. Define Data Início = D-1
   b. Define Data Fim = D-1
   c. Define Conferido = "Sim"
   d. Clica IMPORTAR
   e. Aguarda conclusão
   f. Analisa resultados (sucesso/info/sem exames)
4. PERÍODO D (dia atual):
   a-f. Mesmo fluxo acima com data = hoje
5. Gera relatório Markdown
6. Atualiza dashboard JSON
7. Envia notificação Telegram
```

---

## ⚠️ Observações Importantes

1. **Conferido = Sim**: O script tenta definir o dropdown "Conferido" como "Sim" via JavaScript (componente MUI). Se falhar, prossegue com aviso no log.

2. **Headless**: Por padrão roda em modo headless (`WORKLAB_HEADLESS=true` no `.env`). Para debug visual, mude para `false`.

3. **Custo zero**: O script é 100% determinístico (Python + Playwright), não consome créditos de LLM.

4. **Logs**: Cada execução gera logs no stdout. Use redirecionamento para arquivo se necessário.

5. **Retry Telegram**: O envio Telegram tem retry automático (3 tentativas com backoff).

---

## 🛠️ Troubleshooting

| Problema | Solução |
|----------|---------|
| `playwright install` falha | `apt-get install -y libnss3 libatk-bridge2.0-0 libdrm2 libxkbcommon0 libgbm1` |
| Login falha | Verificar credenciais no `.env` |
| Datas não preenchem | O site pode ter mudado a estrutura do DOM — verificar `set_react_date()` |
| Conferido não muda para Sim | Verificar se o dropdown é MUI Autocomplete e ajustar seletores |
| Telegram não envia | Verificar `BOT_TOKEN` em `abacusai_auth_secrets.json` |
