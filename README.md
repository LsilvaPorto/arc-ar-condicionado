# arc: controle dos ar-condicionados Haier

Substitui o app **hOn** (lento e cheio de bugs) por:

- **Webapp** para o celular (PWA): controle completo, agendamentos e histórico.
- **CLI** `arc` no terminal.
- **Siri/Atalhos do iOS** via API HTTP local.

Tudo fala com a **nuvem hOn** usando a mesma conta do app, mas sem depender do app.

## Aparelhos da casa

| Cômodo | Modelo | Instalação |
|---|---|---|
| Sala (aparece como `AS25RBAHRA-3`, apelido `sala`) | AS25RBAHRA-3 | multi-split (`1to2`) |
| Ar Quarto | AS25RBAHRA-3 | split simples (`1to1`) |
| Ar Escritorio | AS35RBAHRA-4 | multi-split (`1to2`) |

Linha Haier **Revive**. Detalhes da API e do que cada aparelho aceita: [docs/hon-api.md](docs/hon-api.md).

## Instalação (uma vez)

```bash
brew install uv
cd ~/Desktop/Projetos/Ar\ condicionado
uv run arc login                 # e-mail + senha do hOn (vai para o Keychain)
uv run arc usuario criar lucas   # usuário e senha do webapp
uv run arc servidor instalar     # servidor sobe junto com o Mac (LaunchAgent)
```

A conta hOn foi criada com login do Google. Para a API funcionar foi preciso definir uma
senha do hOn pelo "Esqueci a senha" com o mesmo Gmail (não é a senha do Google).

Para os agendamentos dispararem, o Mac precisa estar acordado:
Ajustes do Sistema → Bateria → Opções → evitar repouso automático na tomada.

## Webapp

```bash
uv run arc servidor endereco     # ex.: http://192.168.1.10:8787
```

No iPhone: abra o endereço no Safari, entre → Compartilhar → **Adicionar à Tela de Início**.

- Lista dos cômodos com temperatura interna/externa, liga/desliga com um toque, "Desligar todos".
- Tela do aparelho: temperatura (− +), modo, ventilador, paleta vertical/horizontal, funções
  (noturno, silencioso, turbo, antimofo, luz do painel). Cada modo só mostra o que aceita.
- Agendamentos: um ou mais aparelhos, uma vez / todo dia / dias da semana, início e fim
  opcional (passa da meia-noite). A luz do painel vai no comando de ligar.
- A tela não se atualiza sozinha: atualiza ao abrir/voltar, no botão ↻ e 6 s depois de cada comando.

## CLI

```bash
uv run arc status
uv run arc on quarto 22 frio     # modos: auto, frio, seco, calor, ventilar
uv run arc set sala 24 -v baixo  # ajusta um aparelho já ligado
uv run arc off tudo
uv run arc apelido AS25RBAHRA-3 sala
uv run arc diagnostico           # o que a nuvem vê na conta (salva relatório em ~/.config/arc)
```

`--simular` mostra o comando sem enviar.

## Siri / Atalhos

Passo a passo em [docs/atalhos.md](docs/atalhos.md). Os Atalhos usam uma **chave de API**:

```bash
uv run arc chave criar siri-iphone | pbcopy
```

## Servidor e acesso

```bash
uv run arc servidor reiniciar    # depois de mudar código do servidor
uv run arc servidor sessoes      # quem está logado no webapp
uv run arc servidor encerrar <id>
uv run arc chave listar | revogar <nome>
tail -f ~/.config/arc/servidor.log
```

Porta **8787** (a 8765 é usada por outro app do Mac, o Modly).

## Onde ficam os dados

| Arquivo | Conteúdo |
|---|---|
| Keychain (`arc-hon`) | e-mail e senha do hOn |
| `~/.config/arc/auth.json` | usuários do webapp (senha em scrypt), sessões e chaves (só hash) |
| `~/.config/arc/config.json` | apelidos e preferências por aparelho (ex.: luz do painel) |
| `~/.config/arc/agendamentos.json` | agendamentos e histórico de execuções |
| `~/.config/arc/diagnostico-*.json` | relatórios do `arc diagnostico` |
| `~/.config/arc/servidor.log` | log do servidor instalado |

## Desenvolvimento

```bash
uv run pytest
```

Instruções para assistentes de IA (Claude, Codex, Cursor etc.): [AGENTS.md](AGENTS.md).
