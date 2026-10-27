"""CLI `arc`: controle dos ar-condicionados Haier pela nuvem hOn."""

from __future__ import annotations

import json
import time
from pathlib import Path

import typer

from arc import credentials
from arc.auth import AuthError, AuthStore
from arc.climate import MODES
from arc.hon import CLIMATE_TYPE_ID, HonAuthError, HonClient, HonError, shadow_parameters
from arc.names import normalize
from arc.service import Service, ServiceError

app = typer.Typer(no_args_is_help=True, add_completion=False)

DATA_DIR = Path.home() / ".config" / "arc"


def _client() -> HonClient:
    creds = credentials.load()
    if not creds:
        typer.secho("Sem credenciais. Rode `arc login` primeiro.", fg="red")
        raise typer.Exit(1)
    return HonClient(*creds)


def _parse(extra: list[str]) -> tuple[str | None, float | None]:
    """Aceita `22`, `frio`, `22 frio` em qualquer ordem."""
    mode = temp = None
    for token in extra:
        word = normalize(token)
        try:
            temp = float(word.replace(",", ".").rstrip("°"))
        except ValueError:
            if word not in MODES:
                typer.secho(f"Não entendi '{token}'. Modos: {', '.join(MODES)}", fg="red")
                raise typer.Exit(2)
            mode = word
    return mode, temp


def _print(lines: list[str]) -> None:
    for line in lines:
        typer.echo(line)


def _service(action):
    try:
        with Service() as service:
            return action(service)
    except (ServiceError, HonError) as e:
        typer.secho(str(e), fg="red")
        raise typer.Exit(1)


Aparelho = typer.Argument(help="Nome, apelido ou 'tudo'")
Extra = typer.Argument(None, help="Temperatura e/ou modo (auto, frio, seco, calor, ventilar)")
Vent = typer.Option(None, "--vent", "-v", help="Ventilador: alto, medio, baixo, auto")
DryRun = typer.Option(False, "--simular", help="Mostra o comando sem enviar")
Force = typer.Option(False, "--forcar", help="Envia mesmo se o aparelho parecer offline")


@app.command()
def status(aparelho: str = typer.Argument(None, help="Nome, apelido ou vazio para todos")) -> None:
    """Mostra o estado dos aparelhos."""
    _print(_service(lambda s: s.status(aparelho)))


@app.command()
def on(aparelho: str = Aparelho, extra: list[str] = Extra, vent: str = Vent, simular: bool = DryRun, forcar: bool = Force) -> None:
    """Liga. Ex.: arc on quarto 22 frio"""
    mode, temp = _parse(extra or [])
    _print(_service(lambda s: s.turn_on(aparelho, mode, temp, vent and normalize(vent), simular, forcar)))


@app.command()
def off(aparelho: str = Aparelho, simular: bool = DryRun, forcar: bool = Force) -> None:
    """Desliga. Ex.: arc off tudo"""
    _print(_service(lambda s: s.turn_off(aparelho, simular, forcar)))


@app.command("set")
def set_(aparelho: str = Aparelho, extra: list[str] = Extra, vent: str = Vent, simular: bool = DryRun, forcar: bool = Force) -> None:
    """Ajusta um aparelho já ligado. Ex.: arc set sala 24, arc set quarto seco -v baixo"""
    mode, temp = _parse(extra or [])
    _print(_service(lambda s: s.adjust(aparelho, mode, temp, vent and normalize(vent), simular, forcar)))


@app.command()
def apelido(aparelho: str = typer.Argument(help="Nome atual"), nome: str = typer.Argument(help="Apelido novo")) -> None:
    """Dá um apelido a um aparelho. Ex.: arc apelido AS25RBAHRA-3 sala"""
    typer.echo(_service(lambda s: s.set_alias(aparelho, nome)))


servidor = typer.Typer(no_args_is_help=True, help="Servidor local para os Atalhos/Siri.")
app.add_typer(servidor, name="servidor")

PORT = 8787
LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.arc.servidor.plist"
PROJECT_DIR = Path(__file__).resolve().parents[2]


def _lan_ip() -> str:
    import socket

    # IP da rede local: o nome .local nem sempre resolve no iPhone.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 80))  # não envia nada; só escolhe a interface de saída
        return s.getsockname()[0]


@servidor.command("rodar")
def servidor_rodar(porta: int = PORT) -> None:
    """Roda o servidor em primeiro plano."""
    import uvicorn

    if not AuthStore().has_users():
        typer.secho("Nenhum usuário do webapp ainda. Crie com `arc usuario criar <nome>`.", fg="yellow")
    uvicorn.run("arc.server:app", host="0.0.0.0", port=porta, log_level="info")


@servidor.command("endereco")
def servidor_endereco(porta: int = PORT) -> None:
    """Endereço do webapp na rede de casa."""
    typer.echo(f"http://{_lan_ip()}:{porta}")


@servidor.command("sessoes")
def servidor_sessoes() -> None:
    """Lista os aparelhos logados no webapp."""
    from datetime import datetime

    sessions = AuthStore().sessions()
    if not sessions:
        typer.echo("Nenhuma sessão aberta.")
    for s in sessions:
        seen = datetime.fromtimestamp(s["visto"]).strftime("%d/%m %H:%M")
        typer.echo(f"{s['id']}  {s['usuario']:<10} visto {seen}  {s['aparelho'][:60]}")


@servidor.command("encerrar")
def servidor_encerrar(sessao: str = typer.Argument(help="Id da sessão (de `arc servidor sessoes`)")) -> None:
    """Desloga um aparelho do webapp."""
    typer.echo("Sessão encerrada." if AuthStore().revoke_session(sessao) else "Sessão não encontrada.")


usuario = typer.Typer(no_args_is_help=True, help="Usuários do webapp.")
app.add_typer(usuario, name="usuario")


def _ask_password() -> str:
    password = typer.prompt("Senha", hide_input=True)
    if password != typer.prompt("Repita a senha", hide_input=True):
        typer.secho("As senhas não conferem.", fg="red")
        raise typer.Exit(1)
    return password


@usuario.command("criar")
def usuario_criar(nome: str) -> None:
    """Cria um usuário (ou troca a senha, se já existir)."""
    try:
        AuthStore().set_password(nome.strip().lower(), _ask_password())
    except AuthError as e:
        typer.secho(str(e), fg="red")
        raise typer.Exit(1)
    typer.secho(f"Usuário {nome.lower()} salvo. Sessões antigas dele foram encerradas.", fg="green")


@usuario.command("listar")
def usuario_listar() -> None:
    for name in AuthStore().users():
        typer.echo(name)


@usuario.command("remover")
def usuario_remover(nome: str) -> None:
    try:
        AuthStore().remove_user(nome.lower())
    except AuthError as e:
        typer.secho(str(e), fg="red")
        raise typer.Exit(1)
    typer.echo(f"Usuário {nome} removido.")


chave = typer.Typer(no_args_is_help=True, help="Chaves de API para os Atalhos/Siri.")
app.add_typer(chave, name="chave")


@chave.command("criar")
def chave_criar(nome: str = typer.Argument(help="Ex.: siri-iphone")) -> None:
    """Cria uma chave. Ela aparece só agora: copie direto para o Atalho."""
    try:
        token = AuthStore().create_token(nome)
    except AuthError as e:
        typer.secho(str(e), fg="red")
        raise typer.Exit(1)
    typer.echo(token)
    typer.secho("Guarde agora: ela não é mostrada de novo. Dica: `arc chave criar nome | pbcopy`.", fg="yellow", err=True)


@chave.command("listar")
def chave_listar() -> None:
    from datetime import datetime

    for name, entry in AuthStore().tokens().items():
        typer.echo(f"{name}  criada {datetime.fromtimestamp(entry['criado']).strftime('%d/%m/%Y')}")


@chave.command("revogar")
def chave_revogar(nome: str) -> None:
    typer.echo("Chave revogada." if AuthStore().revoke_token(nome) else "Chave não encontrada.")


@servidor.command("instalar")
def servidor_instalar() -> None:
    """Inicia o servidor junto com o Mac (LaunchAgent)."""
    import shutil
    import subprocess

    uv = shutil.which("uv") or "/opt/homebrew/bin/uv"
    logs = DATA_DIR / "servidor.log"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
    LAUNCH_AGENT.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.arc.servidor</string>
  <key>ProgramArguments</key>
  <array>
    <string>{uv}</string><string>run</string><string>--directory</string><string>{PROJECT_DIR}</string>
    <string>arc</string><string>servidor</string><string>rodar</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{logs}</string>
  <key>StandardErrorPath</key><string>{logs}</string>
</dict>
</plist>
""")
    subprocess.run(["launchctl", "unload", str(LAUNCH_AGENT)], capture_output=True)
    subprocess.run(["launchctl", "load", str(LAUNCH_AGENT)], check=True)
    typer.secho(f"Servidor instalado na porta {PORT}. Logs: {logs}", fg="green")


@servidor.command("reiniciar")
def servidor_reiniciar() -> None:
    """Reinicia o servidor instalado (para carregar código novo)."""
    import os
    import subprocess

    if not LAUNCH_AGENT.exists():
        typer.secho("Servidor não está instalado. Use `arc servidor instalar`.", fg="red")
        raise typer.Exit(1)
    subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.arc.servidor"], check=True)
    typer.secho("Servidor reiniciado.", fg="green")


@servidor.command("desinstalar")
def servidor_desinstalar() -> None:
    """Remove o LaunchAgent."""
    import subprocess

    if LAUNCH_AGENT.exists():
        subprocess.run(["launchctl", "unload", str(LAUNCH_AGENT)], capture_output=True)
        LAUNCH_AGENT.unlink()
    typer.echo("Servidor removido.")


@app.command()
def login() -> None:
    """Salva e-mail/senha do hOn no Keychain e testa o login."""
    email = typer.prompt("E-mail do hOn")
    password = typer.prompt("Senha", hide_input=True)
    with HonClient(email, password) as client:
        try:
            client.authorize()
        except HonAuthError as e:
            typer.secho(f"Falhou: {e}", fg="red")
            raise typer.Exit(1)
    credentials.save(email, password)
    typer.secho("Login OK, credenciais salvas no Keychain.", fg="green")


@app.command()
def logout() -> None:
    """Remove as credenciais do Keychain."""
    credentials.clear()
    typer.echo("Credenciais removidas.")


@app.command()
def diagnostico() -> None:
    """Verifica o que a nuvem hOn tem na sua conta, sem depender do app."""
    report: dict = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S")}

    with _client() as client:
        typer.echo("1/4 Login CIAM...", nl=False)
        try:
            client.authorize()
        except Exception as e:
            typer.secho(f" FALHOU\n    {e}", fg="red")
            raise typer.Exit(1)
        typer.secho(" ok", fg="green")

        typer.echo("2/4 Lista de aparelhos (API nova)...", nl=False)
        raw = client.appliance_list_raw()
        report["appliance_list_raw"] = raw
        try:
            all_items = raw["modules"]["applianceList"]["payload"]["appliances"]
        except (KeyError, TypeError):
            all_items = None
        if all_items is None:
            typer.secho(" formato inesperado (salvo no relatório)", fg="yellow")
            all_items = []
        else:
            typer.secho(f" {len(all_items)} item(ns)", fg="green" if all_items else "yellow")

        typer.echo("3/4 Lista de aparelhos (endpoint antigo)...", nl=False)
        try:
            legacy = client.legacy_appliances_raw()
            report["legacy_appliances"] = legacy
            body = legacy["body"]
            n = len(body.get("payload", {}).get("appliances", [])) if isinstance(body, dict) else "?"
            typer.echo(f" HTTP {legacy['status']}, {n} item(ns)")
        except Exception as e:
            typer.echo(f" erro: {e}")

        climates = [a for a in all_items if a.get("applianceTypeId") == CLIMATE_TYPE_ID]
        typer.echo(f"4/4 Estado dos ar-condicionados ({len(climates)})...")
        report["climates"] = []
        for a in climates:
            name = a.get("nickName") or a.get("modelName") or a.get("macAddress")
            entry = {"appliance": a}
            try:
                ctx = client.context(a)
                entry["context"] = ctx
                state = shadow_parameters(ctx)
                online = ctx.get("lastConnEvent", {}).get("category")
                typer.echo(
                    f"    • {name} ({a.get('modelName')}) — conexão: {online or '?'}, "
                    f"liga: {state.get('onOffStatus', '?')}, modo: {state.get('machMode', '?')}, "
                    f"alvo: {state.get('tempSel', '?')}°, ambiente: {state.get('tempIndoor', '?')}°"
                )
                entry["commands"] = client.commands(a)
            except HonError as e:
                typer.secho(f"    • {name}: erro {e}", fg="red")
            except Exception as e:
                typer.secho(f"    • {name}: erro {type(e).__name__}: {e}", fg="red")
            report["climates"].append(entry)

    others = [a for a in all_items if a.get("applianceTypeId") != CLIMATE_TYPE_ID]
    for a in others:
        typer.echo(f"    (outro aparelho: tipo {a.get('applianceTypeId')} {a.get('applianceTypeName')} {a.get('nickName', '')})")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = DATA_DIR / f"diagnostico-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    out.chmod(0o600)

    typer.echo("")
    if climates:
        typer.secho("Conclusão: a nuvem ainda tem seus aparelhos. O problema é o app.", fg="green", bold=True)
    elif all_items:
        typer.secho("Conclusão: a conta tem aparelhos, mas nenhum do tipo ar-condicionado (11).", fg="yellow", bold=True)
    else:
        typer.secho("Conclusão: a nuvem não lista nenhum aparelho nessa conta.", fg="red", bold=True)
    typer.echo(f"Relatório completo (sem senha/tokens): {out}")


if __name__ == "__main__":
    app()
