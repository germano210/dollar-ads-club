# Roteiro de teste real (custo zero)

## 0. Configurar fora do sistema
**Mercado Livre — DevCenter (developers.mercadolivre.com.br → Minhas aplicações → editar):**
| Campo | Valor |
|---|---|
| URI de redirect | `https://loudhouse.com.br/dollar-ads-club/oauth/callback` |
| Escopos | Leitura, Escrita, Acesso offline |
| Permissões | Publicação e sincronização (itens) — leitura e escrita |
| URL de retornos de chamada de notificação | `https://loudhouse.com.br/dollar-ads-club/avisos` |
| Tópicos | **Items** (só esse por enquanto) |

**Mercado Pago — Suas integrações → Dollar Ads Club:**
1. *Contas de teste*: criar **Vendedor** e **Comprador**.
2. Entrar como o vendedor de teste (janela anônima) → *Credenciais de produção* → copiar o Access Token para `MP_ACCESS_TOKEN`.
3. *Webhooks → Configurar notificações*: URL `https://loudhouse.com.br/dollar-ads-club/mp/webhook`, evento **Pagamentos**, salvar e copiar a *assinatura secreta* para `MP_WEBHOOK_SECRET`.

## 1. Subir
```bash
docker compose up -d --build
docker compose exec dac python app.py admin SEU@EMAIL SENHA-FORTE
curl http://localhost:8001/dollar-ads-club/saude
```
Túnel: `dac-origem.loudhouse.com.br → http://localhost:8001`. Worker com rota `loudhouse.com.br/dollar-ads-club*` (ver README).
Abrir `https://loudhouse.com.br/dollar-ads-club/` → deve aparecer a tela de entrar. `https://dac-origem.loudhouse.com.br/dollar-ads-club/` deve dar **403** (acesso direto bloqueado).

## 2. Roteiro (marque ✔)
- [ ] Criar conta **empresa** "Empresa Teste" (e-mail A). Aba Créditos mostra **10** (cortesia do 1º dia).
- [ ] Contas do ML → conectar **sua conta real** só para poder criar usuários de teste.
- [ ] Sair → entrar como **admin** → *Criar usuário de teste do ML* **2 vezes**. Anotar usuário e senha.
- [ ] Entrar como empresa → desconectar a conta real → conectar os 2 usuários de teste (fazer login no ML com cada um, em janela anônima).
- [ ] Novo produto: foto (fundo branco), título, preço, estoque 1, *Sugerir* categoria, marca, GTIN ou motivo. Salvar → Ativar.
- [ ] Criar conta **afiliado** (e-mail B). Produto aparece em "Para anunciar".
- [ ] Conferir → (opcional) *Sugerir outras palavras (IA)* → marcar conferi → Enviar. Esperado: "Publicado e ativo" e link do anúncio.
- [ ] Repetir com o mesmo produto: publica na 2ª conta. 3ª vez: produto some da lista.
- [ ] Empresa: Créditos = 8. Afiliado: Saldo disponível R$ 0,20.
- [ ] Comprar 5 créditos → pagar com o **comprador de teste** → voltar → saldo sobe para 13 (o webhook pode levar alguns segundos).
- [ ] Minha conta → Baixar meus dados (abre um JSON).
- [ ] Se algo falhar: admin → *Falhas recentes* mostra a resposta do ML. Me mande o texto.

Ao final, se usou a conta real em algum passo, finalize os anúncios dela no ML.
