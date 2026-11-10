# Dollar Ads Club

Endereço: **https://loudhouse.com.br/dollar-ads-club/** (login de empresa, afiliado e admin).

- **Empresa:** conecta N contas do Mercado Livre (OAuth, sem senha), cadastra produtos (fundo branco feito no navegador), ativa/pausa e compra créditos (Mercado Pago).
- **Afiliado:** vê produtos ativos de empresas com crédito e com conta livre, confere e envia. O sistema publica na próxima conta livre.
- **Regras:** 1 crédito = 1 anúncio; o crédito é reservado no envio, devolvido se falhar e só conta como gasto quando a API confirma `active`. 1 anúncio por produto por conta (o ML proíbe duplicados). O afiliado ganha R$ 0,10 por anúncio ativo, liberado após `LIBERAR_DIAS` se o anúncio continuar ativo. Saque mínimo de R$ 10, por Pix, pago pelo admin.
- Uma rotina a cada 10 min confere anúncios em análise e libera ganhos.

## Colocar no ar (servidor local, o mesmo do site do Erivelto)

1. **Servidor:** `git clone` → `cp .env.example .env` → preencher → `docker compose up -d --build`.
   Teste local: `curl http://localhost:8001/dollar-ads-club/saude`.
2. **Admin:** `docker compose exec dac python app.py admin seu@email.com SENHA-FORTE`.
3. **Túnel (cloudflared, já usado pelo Erivelto):** adicionar o hostname `dac-origem.loudhouse.com.br` → `http://localhost:8001`.
4. **Worker (Cloudflare → Workers):** criar com `cloudflare/worker.js`; variáveis `ORIGEM=https://dac-origem.loudhouse.com.br` e secret `SEGREDO` (= `ORIGEM_SEGREDO`). Em *Settings → Domains & Routes* adicionar a rota `loudhouse.com.br/dollar-ads-club*` (zona loudhouse.com.br). A rota tem prioridade sobre o Pages; o resto do site não muda.
5. **Mercado Livre (DevCenter):** redirect `https://loudhouse.com.br/dollar-ads-club/oauth/callback`; notificações `https://loudhouse.com.br/dollar-ads-club/avisos` (tópico *items*); escopos de leitura, escrita e oferta de produtos.
6. **Mercado Pago:** credenciais de produção no `.env`; webhook `https://loudhouse.com.br/dollar-ads-club/mp/webhook` (evento *Pagamentos*) e a chave secreta em `MP_WEBHOOK_SECRET`.
7. **Backup diário** da pasta `dados/`.

## Atualizar
`git pull && docker compose up -d --build`

## Antes do primeiro cliente
- Revisão jurídica de `static/termos.html` e `static/privacidade.html` (modelos, campos entre colchetes).
- Teste real: 1 empresa, 2 contas ML, 1 afiliado, 1 produto.
- Campos obrigatórios variam por categoria; use "Avançado: atributos extras" (ex.: `{"attributes":[{"id":"MODEL","value_name":"X"}]}` ou `family_name`).
