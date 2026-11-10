# Dollar Ads Club · MVP

Cadastrar → IA prepara (Claude API) → conferir → aprovar → publicar no Mercado Livre (fundo branco, categoria, anúncio e descrição).
FastAPI + SQLite, um contêiner.

## Subir (Docker)
```bash
cp .env.example .env      # preencher
docker compose up -d --build
docker compose logs -f
```
- Porta só em `127.0.0.1:8000`. No cloudflared: `ads.<domínio>` → `http://localhost:8000`.
- Dados em `./dados/` (volume): `dados.sqlite3`, `tokens.json` (acesso à conta do ML), `avisos.log`. Pôr essa pasta na cópia diária.
- 1ª foto com fundo branco baixa o modelo do rembg (~180 MB) para `./dados/modelos/`.

## Mercado Livre
1. App no DevCenter: redirect `https://ads.<domínio>/oauth/callback`, leitura e escrita, "Publicação e sincronização", avisos em `https://ads.<domínio>/avisos`.
2. ID e senha do app no `.env`.
3. Fernando abre `https://ads.<domínio>/oauth/inicio` (com o login) e autoriza 1 vez.
4. Teste: 1 produto → conferir → aprovar. Resultado ou motivo da recusa em Medidas.

## Atualizar
```bash
git pull && docker compose up -d --build
```

## Pendente
- Conferir `ANTHROPIC_MODEL` na documentação da Anthropic.
- Login simples (Basic Auth): trocar pelo login da Fase 1 antes de outra empresa.
- Nunca testado contra o ML (falta o app). `domain_discovery` a confirmar no DevCenter. GTIN nunca é inventado.
