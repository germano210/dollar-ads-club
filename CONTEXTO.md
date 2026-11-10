# Contexto do projeto · Dollar Ads Club

Atualizado em 10/11/2026. Ler antes de continuar o trabalho.

## Decisões tomadas
- Endereço: `loudhouse.com.br/dollar-ads-club/` (subcaminho). A raiz está no Cloudflare Pages; o app roda no servidor local (o mesmo do site do Erivelto) via túnel + Worker com a rota `loudhouse.com.br/dollar-ads-club*`.
- Empresa: pode ter várias contas do ML, autorizadas por OAuth (não entrega chave nem senha). Cadastra produtos com foto de fundo branco, ativa/pausa e compra créditos pelo Mercado Pago.
- Afiliado: só confere os dados e envia. A The Loud House publica na conta da empresa.
- 1 crédito = 1 anúncio. O crédito só conta como gasto depois que a API confirma que o anúncio está ativo; se falhar, volta.
- 1 anúncio por produto por conta (regra anti-duplicado do ML). Sem crédito ou sem conta livre, o produto sai da aba do afiliado.
- Afiliado ganha R$ 0,10 por anúncio; saque mínimo R$ 10,00.
- Forma de trabalho: tarefas pequenas, questionar quando necessário; quadro kanban no artefato "Dollar Ads Club · Quadro".

## Decididas em 10/11
- Crédito = R$ 1,00. Empresas novas: 10 créditos/dia por 30 dias (cortesia).
- R$ 0,10 liberado assim que o anúncio fica ativo (`LIBERAR_DIAS=0`).
- Porta 8001 (Erivelto usa 8080). IA: claude-opus-5-5 com esforço low.
- Testes sem custo: usuários de teste do ML (botão no admin) e contas de teste do MP.

## Pendências / perguntas abertas
- Confirmar: os 10 créditos diários de cortesia acumulam (implementado assim).
- Credenciais Mercado Pago (conta de teste primeiro); roteiro em TESTE.md.
- Revisão jurídica de termos.html e privacidade.html.
- Teste real com 1 produto.

## Estado do código
- `app.py`: backend novo multi-empresa (FastAPI + SQLite `dados/dac.sqlite3`). O banco antigo `dados.sqlite3` não é mais usado.
- `static/index.html`: front novo (empresa / afiliado / admin / minha conta). Fundo branco no navegador (U²-Netp).
- `cloudflare/worker.js`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `README.md` com o passo a passo.
- Testado: cadastro, produto com foto, ativar, créditos, publicação (ML simulado), 1 por conta, ganho pendente, saque mínimo.
