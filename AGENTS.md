# AGENTS.md

Instruções para qualquer assistente de IA que trabalhe neste repositório
(Claude Code, Codex, Cursor, Copilot, Gemini…). Visão de usuário: [README.md](README.md).
Conhecimento da API e dos aparelhos: [docs/hon-api.md](docs/hon-api.md).

## Regras que não se negociam

1. **Comandos mexem em aparelhos reais na casa do Lucas.** Nunca envie comando (ligar,
   desligar, ajustar, "testar agora" de agendamento) sem o Lucas pedir ou autorizar naquela
   conversa. Leituras (`arc status`, `arc diagnostico`, `GET /api/...`) são livres.
   Use `--simular` na CLI para ver o payload sem enviar.
2. **Segredos:** nunca imprima senha do hOn, tokens, cookies de sessão ou chaves `arc_…`.
   Credenciais do hOn ficam no Keychain; o resto em `~/.config/arc/auth.json` (só hashes).
   Usuário/senha do webapp é o Lucas quem cria (`arc usuario criar`), no terminal dele.
3. **Servidor de produção é o LaunchAgent** `com.arc.servidor` na porta 8787. Não rode outro
   servidor na 8787 (o instalado entra em loop e dois agendadores disparariam em dobro).
   Depois de mudar código Python do servidor: `uv run arc servidor reiniciar`.
   Mudanças só em `src/arc/static/` não precisam de reinício (recarregar a página basta).
4. A porta **8765 é de outro app** do Mac (Modly). Não mate processos que não são do arc.
5. Idioma: **português do Brasil** em mensagens, UI, docstrings e comentários.

## Stack

- Python 3.12 com **uv** (`uv run …`, `uv add …`). Sem pip/venv manual.
- `httpx` (síncrono) para a nuvem hOn, `typer` na CLI, `fastapi` + `uvicorn` no servidor,
  `keyring` para o Keychain.
- Frontend **sem build**: `src/arc/static/` com HTML/CSS/JS puros (módulo ES). Não adicione
  bundler nem framework.
- Testes: `uv run pytest` (rápidos, sem rede; a nuvem é simulada com `httpx.MockTransport`
  e serviços falsos). Rode antes de dizer que terminou.

## Mapa do código (`src/arc/`)

| Arquivo | Responsabilidade |
|---|---|
| `hon.py` | Cliente da nuvem hOn: login CIAM (PKCE), lista de aparelhos, catálogo de comandos, contexto/estado, envio. |
| `climate.py` | Modelo do aparelho (`AirConditioner`), montagem de payloads a partir do catálogo, `apply_rules` (regras do catálogo) e `observed_rules` (regras validadas no aparelho real). |
| `service.py` | Camada usada por CLI, Siri e webapp: carrega aparelhos (em paralelo), cache de 15 s, sobreposição otimista do que foi enviado (20 s), preferências. |
| `web.py` | API JSON do webapp (`/api/aparelhos`, `/api/agendamentos`), validação "o que é permitido em cada modo" (`check_allowed`), execução de eventos de agendamento. |
| `server.py` | App FastAPI: autenticação (sessão por cookie ou chave Bearer), rotas da Siri (`/ligar`, `/desligar`, `/ajustar`, `/status`), login, thread do agendador. |
| `schedule.py` | Agendamentos: cálculo puro de horários (testável com `now` injetado), armazenamento, `Scheduler` (thread, tick de 20 s, janela de 10 min). |
| `auth.py` | Usuários (scrypt), sessões e chaves de API (SHA-256), limite de tentativas. |
| `names.py` | Apelidos e preferências por aparelho (`~/.config/arc/config.json`), resolução de nomes. |
| `credentials.py` | E-mail/senha do hOn no Keychain. |
| `cli.py` | Comandos `arc …`. |
| `static/` | Webapp (PWA): `index.html`, `app.js`, `app.css`, `entrar.html`. |

## Como as coisas funcionam (o que não é óbvio)

- **Montagem de comando:** cada comando do catálogo declara todos os parâmetros como
  `fixed`/`range`/`enum`. Enviamos **todos**: valor fixo › pedido do usuário › estado atual
  (shadow) › default. Parâmetro bloqueado pedido explicitamente gera `ValueError` (não é
  ignorado em silêncio).
- **Três comandos:** `startProgram` (liga num modo, ex. `PROGRAMS.AC.IOT_COOL`),
  `settings` (ajusta ligado; **força `onOffStatus=1`**, então nunca use com o aparelho
  desligado) e `stopProgram`.
- **"Permitido no modo"** = parâmetro ajustável no programa do modo **e** no `settings`
  (`web.allowed_in`). O webapp só mostra isso e o servidor recusa o resto.
- **Luz do painel** (`screenDisplayStatus`): a nuvem **não reporta** mudanças feitas com o
  aparelho ligado. O valor confiável é o último enviado, guardado em `config.json`
  (`preferencias`) e reenviado em todo `startProgram`.
- **Estado otimista:** depois de enviar, o servidor sobrepõe os parâmetros enviados por 20 s,
  porque a nuvem demora alguns segundos para refletir.
- **Agendamentos** rodam no servidor, não no app. Evento perdido (Mac dormindo > 10 min) é
  registrado como "perdido". Aparelho offline é tentado de novo dentro da janela.

## Convenções

- Siga o estilo do código ao redor: comentários curtos em português explicando o *porquê*.
- Regra nova descoberta no aparelho real? Registre em `observed_rules` (ou equivalente)
  **com data e aparelho** no docstring, adicione teste e documente em `docs/hon-api.md`.
- UI: mobile-first, tokens de cor em `:root` (claro/escuro), sem `<select>` (o celular fecha
  o seletor se a tela redesenhar; use botões/chips), sem `alert/confirm` (confirmações inline).
- Não renderize a tela inteira enquanto um campo estiver em foco (`renderWhenIdle`).

## Verificação

- `uv run pytest` sempre.
- Para conferir a UI: o servidor instalado está em `http://127.0.0.1:8787`. Para logar sem a
  senha do Lucas, crie um usuário temporário (`teste-…`) e **remova ao terminar**
  (`uv run arc usuario remover …`).
- O Chrome do Lucas tem a extensão Dark Reader; as páginas têm `<meta name="darkreader-lock">`.

## Estado atual e próximos passos

Feito: CLI, Siri, webapp com controle completo, login, regras de modo, agendamentos.
Pendentes/ideias: favoritos (predefinições), grupos de aparelhos, histórico de temperatura,
confirmar se a paleta é travada no Calor+Turbo, aviso de conflito de modo no multi-split
(inconclusivo, ver docs), HTTPS/acesso remoto via Tailscale, controle local via ESP32.
