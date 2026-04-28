# BAC BO — Coletor (Render.com)

Serviço separado responsável APENAS por coletar os resultados do Bac Bo
e salvar no banco PostgreSQL compartilhado com o Railway.

## Arquitetura

```
[Render - bacbo-coletor]          [Railway - bacbo-prime-mot]
  Playwright scraper          →      PostgreSQL (banco compartilhado)
  Salva resultados            ←      Analisa streak + envia Telegram
```

---

## Como subir no Render (passo a passo)

### 1. Suba este projeto no GitHub

Crie um repositório novo no GitHub chamado `bacbo-coletor` e suba estes arquivos.

### 2. Crie conta no Render

Acesse https://render.com e crie conta com seu GitHub (gratuito, sem cartão).

### 3. Crie o serviço

1. Clique em **"New +"** → **"Background Worker"**
2. Conecte o repositório `bacbo-coletor`
3. Render vai detectar o `Dockerfile` automaticamente
4. Clique em **"Create Background Worker"**

### 4. Configure a variável de ambiente

Na aba **"Environment"** do serviço no Render, adicione:

| Variável | Valor |
|---|---|
| `DATABASE_URL` | Cole aqui a mesma URL do banco que está no Railway |

> ⚠️ A DATABASE_URL deve ser a mesma do Railway — os dois serviços compartilham o mesmo banco PostgreSQL.

### 5. Deploy

Clique em **"Deploy"** e aguarde o build (pode demorar ~5 minutos na primeira vez por causa do Playwright).

---

## Como pegar a DATABASE_URL do Railway

1. Acesse seu projeto no Railway
2. Clique no serviço do banco PostgreSQL
3. Aba **"Variables"**
4. Copie o valor de `DATABASE_URL`

---

## Verificando se está funcionando

Nos logs do Render você deve ver:

```
✅ Banco de dados pronto.
⏱️  Coleta configurada a cada 30s
✅ Scheduler iniciado — aguardando ciclos...
🔄 Iniciando ciclo de coleta...
[SCRAPER] Acessando TipMiner...
[SCRAPER] ✅ 30 resultados — mais recente: vermelho
💾 Salvo: vermelho
```

E no Railway (análise) você verá o streak sendo atualizado a cada ciclo.
