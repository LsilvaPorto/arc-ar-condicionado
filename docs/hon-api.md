# API hOn e comportamento real dos aparelhos

Tudo aqui foi verificado contra a conta e os aparelhos reais em **2026-10-27**, salvo
indicação. "Validado no aparelho" = com o Lucas olhando o aparelho físico.

## Histórico da API

- A biblioteca `pyhOn` (Andre0512) está abandonada desde 2024-08 e **não funciona mais**:
  a Haier trocou o login em **2026-06** (Salesforce/OAuth → CIAM com PKCE).
- Referência ativa usada para o protocolo: <https://github.com/gvigroux/hon>
  (`custom_components/hon/hon.py`).
- Login pelo Google não funciona em integrações não oficiais. Solução: definir senha do hOn
  pelo "Esqueci a senha" com o mesmo e-mail; a conta continua aceitando o Google.

## Endpoints (`https://api-iot.he.services`)

| Uso | Chamada |
|---|---|
| Login 1 | `GET /ciam/authorize?username&password&code_challenge` → `session_id` |
| Login 2 | `POST /ciam/token {session_id, code_verifier}` → `tokens.cognito_token`, `tokens.id_token` (~15 min) |
| Aparelhos | `POST /unified-api/v1/view/appliance-list {deviceId}` → `modules.applianceList.payload.appliances` |
| Catálogo | `GET /commands/v1/retrieve?applianceType&code&applianceModelId&firmwareId&macAddress&fwVersion&os&appVersion&series` |
| Estado | `GET /commands/v1/context?macAddress&applianceType&category=CYCLE` → `shadow.parameters.<p>.parNewVal`, `lastConnEvent` |
| Enviar | `POST /commands/v1/send` → `payload.resultCode == "0"` |
| Estatísticas | `GET /commands/v1/statistics` → vem vazio para AC |

Cabeçalhos autenticados: `cognito-token`, `id-token`. O endpoint antigo
`/commands/v1/appliance` ainda responde, mas não é usado.

Latência medida: login ~0,9 s; lista ~0,4 s; contexto ~0,85 s por aparelho; o aparelho
reflete o comando em até ~5 s.

## Catálogo dos aparelhos (AS25RBAHRA-3 e AS35RBAHRA-4, série Revive)

- `startProgram`: `IOT_SIMPLE_START`, `IOT_COOL`, `IOT_HEAT`, `IOT_DRY`, `IOT_AUTO`,
  `IOT_FAN`, `IOT_10_HEATING`, `IOT_SELF_CLEAN`, `IOT_SELF_CLEAN_56`.
- `settings.setParameters` (ajuste com o aparelho ligado; `onOffStatus` fixo em 1).
- `stopProgram`: `PROGRAMS.STOP`.

O payload que o app oficial manda (visto em `commandHistory`) tem `programName`
(`PROGRAMS.AC.IOT_HEAT`), `attributes.prStr` igual e **todos os parâmetros como texto**.

### Valores

| Parâmetro | Valores |
|---|---|
| `machMode` | 0 auto, 1 frio, 2 seco, 4 calor, 6 ventilar |
| `tempSel` | 16–30, passo 1 |
| `windSpeed` | 1 alto, 2 médio, 3 baixo, 5 auto (Ventilar não tem 5) |
| `windDirectionVertical` | 8 oscilar, 2 bem alto, 4 alto, 5 meio, 6 baixo, 7 bem baixo (Frio/Seco/Auto/Ventilar não têm 7) |
| `windDirectionHorizontal` | 7 oscilar, 3 bem à esq., 4 esquerda, 0 centro, 5 direita, 6 bem à dir. |
| `silentSleepStatus` | modo noturno (0/1) |
| `muteStatus` | silencioso (0/1) |
| `rapidMode` | turbo (0/1) |
| `screenDisplayStatus` | luz do painel (0/1), padrão 1 |
| `selfCleaningStatus` | antimofo/autolimpeza (0/1) |
| `10degreeHeatingStatus` | 1 no programa 10° anti-gelo |

### Funções por modo (catálogo)

| Modo | Turbo | Silencioso | Noturno | Antimofo | Vent. Auto |
|---|:-:|:-:|:-:|:-:|:-:|
| Frio | ✅ | ✅ | ✅ | ✅* | ✅ |
| Calor | ✅ | ✅ | ✅ | ❌ | ✅ |
| Seco | ❌ | ❌ | ✅ | ✅* | ✅ |
| Auto | ❌ | ❌ | ✅ | ✅* | ✅ |
| Ventilar | ❌ | ❌ | ❌ | ✅* | ❌ |
| 10° anti-gelo | ✅ | ✅ | ✅ | ❌ | ✅ |

\* Só em split simples. Ver `programRules` abaixo.

### `programRules` (regras condicionais do catálogo)

- `$installationType = 1toN` (multi-split, `unitConfiguration` ≠ `1to1`): `selfCleaningStatus`
  e `selfCleaning56Status` ficam fixos em 0 no `settings`/`IOT_SIMPLE_START`. O programa
  `IOT_COOL` não traz essa regra; tratamos como bloqueado se qualquer um bloquear.
- `ecoMode = 1` (Frio/Calor): temperatura fixa 26 (frio) / 20 (calor), paleta fixa.
  Eco ainda não é exposto na UI.

## Comportamento observado (não documentado no catálogo)

Validado no **Escritório (AS35RBAHRA-4)**:

- **Turbo** força o ventilador para auto (5) e trava a velocidade.
- **Silencioso** também força auto e trava a velocidade.
- O aparelho **recusa silencioso com turbo ligado**; ligar turbo **desliga** o silencioso.
  Padronizamos "liga um, desliga o outro": ligar silencioso manda `rapidMode=0` no mesmo
  comando (validado: funciona).
- **Noturno + turbo** convivem.
- Trocar para **Seco** desliga o turbo e mantém o noturno.
- **Luz do painel** (validado no aparelho):
  - via `settings` com o aparelho ligado: **funciona** (apaga/acende), mas a nuvem **não
    reporta** a mudança (`screenDisplayStatus` continua 1);
  - via `startProgram` com `screenDisplayStatus=0`: funciona e é reportado;
  - **modo noturno não apaga a luz sozinho**;
  - ao desligar, o estado volta para 1.
  - Causa do bug do agendamento no app hOn: ligava com noturno sem mandar a luz apagada.
- Toda ação recebida faz o aparelho apitar (bip).

Multi-split (Sala + Escritório, ambos `1to2`): Sala em Calor com Escritório em Frio foi
aceito sem erro pela nuvem e o Lucas não notou problema. **Inconclusivo**: não se sabe se
dividem a mesma externa nem se um entra em espera.

Em aberto: no **Calor + Turbo** a paleta vertical pode estar sendo forçada (viu-se 2 = bem
alto após um ajuste); confirmar com alguém olhando.

## O que a nuvem não informa

- **Consumo de energia:** `power`, `totalElectricityUsed`, `totalWorkTime` estão em 0 e sem
  atualização desde o pareamento; `compressorCurrent` fica fixo em 51,10. Estatísticas vazias.
- Telemetria de compressor/unidade externa atualiza no máximo de hora em hora
  (`stdTransRate` 3600); não serve para tempo real.
