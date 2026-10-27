# Atalhos da Siri

O iPhone fala com o servidor `arc` rodando no Mac, pela rede de casa.

## 1. No Mac (uma vez)

```bash
cd ~/Desktop/Projetos/Ar\ condicionado
uv run arc servidor instalar             # sobe junto com o Mac
uv run arc chave criar siri-iphone | pbcopy   # chave só dos Atalhos, já copiada
```

Se o macOS perguntar se o Python pode aceitar conexões de rede, permita.

Endereço do servidor: o que `uv run arc servidor endereco` mostrar (ex.: `http://192.168.1.10:8787`).
Nos exemplos abaixo, troque `ENDERECO` por ele.

## 2. No iPhone: atalho "Liga o ar"

App **Atalhos** → **+** → nome: **Liga o ar** (é a frase que você fala para a Siri).

1. **Escolher do Menu**, com o texto "Qual ar?" e as opções `Quarto`, `Sala`, `Escritório` e `Todos`.
2. **Pedir Entrada** (Número), com o texto "Qual temperatura?".
3. **Obter Conteúdo do URL**
   - URL: `ENDERECO/ligar`
   - Método: **POST**
   - Cabeçalhos: `Authorization` = `Bearer ` + a chave (começa com `arc_`)
   - Corpo da Solicitação: **JSON**
     - `aparelho` (Texto) = *Item Escolhido*
     - `temperatura` (Número) = *Entrada Fornecida*
     - `modo` (Texto) = `frio` (ou crie um menu com frio / calor / seco / ventilar / auto)
4. **Falar Texto**: *Conteúdo do URL*

A Siri pergunta qual ar e qual temperatura, e no fim fala, por exemplo, "Ar Quarto: ligado no frio em 22°".

## 3. Atalho "Desliga o ar"

Igual ao de cima, mas sem a temperatura. URL `.../desligar` e corpo só com `aparelho`.
Dica: o atalho **"Desliga tudo"** dispensa o menu e manda direto `aparelho` = `tudo`.

## 4. Atalho "Como está o ar"

1. **Obter Conteúdo do URL**: `ENDERECO/status` (GET, com o mesmo cabeçalho `Authorization`).
2. **Falar Texto**: *Conteúdo do URL*

## Outros endpoints

| Endpoint | Corpo JSON | Faz |
|---|---|---|
| `POST /ligar` | `aparelho`, `temperatura?`, `modo?`, `ventilador?` | Liga (já no modo e na temperatura) |
| `POST /desligar` | `aparelho` | Desliga |
| `POST /ajustar` | `aparelho`, `temperatura?`, `modo?`, `ventilador?` | Muda um aparelho já ligado |
| `GET /status?aparelho=` | — | Estado falado |

`aparelho` aceita `quarto`, `sala`, `escritorio` ou `tudo`. `ventilador` aceita `alto`, `medio`, `baixo` ou `auto`.

## Chaves

- `arc chave listar`: mostra as chaves que existem.
- `arc chave revogar siri-iphone`: corta o acesso daquela chave. Se o iPhone for perdido, é só revogar e criar outra.

## Limitações

- O Mac precisa estar ligado e acordado. Num MacBook com a tampa fechada na bateria, o servidor para.
- Só funciona na rede de casa. Para usar fora de casa, dá para instalar o Tailscale no Mac e no iPhone.
- Os comandos passam pela nuvem do hOn; se ela cair, o servidor responde com o erro dela.
