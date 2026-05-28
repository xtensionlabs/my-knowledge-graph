"""`synapse` CLI — Typer-based administrative interface.

Subcommands (PRD §10):
    init                 Initialize vault structure + SQLite database
    start                Run gateway + Telegram bot together (M0 surface)
    stop                 Stop all background daemons
    status               Print system health
    ingest [text]        Manual text capture from terminal
    daemon ...           Manage clipboard daemon (install/start/stop/status/uninstall)
    simulate ...         Simulate captures for testing (email, etc.)
    hooks install        (M3 stub) Install git post-commit hook
    ocr                  (M5 stub) OCR a local image
    librarian run        (M1 stub) Trigger Librarian
    brief                (M2 stub) Trigger Delta Briefing
    review               (M2 stub) Terminal review session
    ask [query]          (M1 stub) Knowledge-graph search
    logs                 Tail a component's log file
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import json
import os
import signal
import sys
from pathlib import Path
from typing import Annotated, Optional

# Force UTF-8 stdio on Windows consoles (cp1252 default cannot encode ✓ / box-drawing).
if sys.platform == "win32":
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, io.UnsupportedOperation):
                pass

import httpx
import typer
from loguru import logger
from rich.console import Console
from rich.table import Table

from synapse import __version__
from synapse.capture import daemon_installer
from synapse.capture.inbox import count_inbox_items, oldest_inbox_items, write_to_inbox
from synapse.config import VAULT_SUBDIRS, EMAIL_HMAC_HEADER, get_settings, reset_settings_cache
from synapse.graph.db import init_db
from synapse.logging_setup import configure_logging

app = typer.Typer(
    name="synapse",
    help="Synapse — personal cognitive operating system.",
    no_args_is_help=True,
    add_completion=False,
)
daemon_app = typer.Typer(help="Manage background daemons (clipboard, etc.)", no_args_is_help=True)
simulate_app = typer.Typer(help="Simulate captures for testing.", no_args_is_help=True)
app.add_typer(daemon_app, name="daemon")
app.add_typer(simulate_app, name="simulate")

console = Console()


# ── init ─────────────────────────────────────────────────────────────────────


@app.command()
def init(
    vault: Annotated[
        Optional[Path],
        typer.Option("--vault", "-v", help="Vault path (overrides SYNAPSE_VAULT_PATH)."),
    ] = None,
    force: Annotated[bool, typer.Option(help="Re-initialize DB even if present.")] = False,
) -> None:
    """Provision the vault directory tree and SQLite database."""
    if vault is not None:
        os.environ["SYNAPSE_VAULT_PATH"] = str(vault.resolve())
        reset_settings_cache()

    settings = get_settings()
    console.print(f"[bold]vault:[/bold] {settings.synapse_vault_path}")
    settings.synapse_vault_path.mkdir(parents=True, exist_ok=True)

    for sub in VAULT_SUBDIRS:
        (settings.synapse_vault_path / sub).mkdir(parents=True, exist_ok=True)

    settings.vault_internal_dir.mkdir(parents=True, exist_ok=True)
    settings.pid_dir.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)

    if settings.db_path.exists() and not force:
        console.print(f"[yellow]db already exists:[/yellow] {settings.db_path}")
    else:
        if force and settings.db_path.exists():
            settings.db_path.unlink()
        init_db()
        console.print(f"[green]✓ db initialized:[/green] {settings.db_path}")

    console.print("[green]✓ vault ready[/green]")
    console.print(f"  inbox: {settings.inbox_dir}")
    console.print(f"  attachments: {settings.attachments_dir}")
    console.print(f"  internal: {settings.vault_internal_dir}")


# ── start / stop ─────────────────────────────────────────────────────────────


def _bind_signals(stop_event: asyncio.Event) -> None:
    """Wire SIGINT/SIGTERM to the stop event (POSIX) or KeyboardInterrupt (Win)."""
    if sys.platform != "win32":
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except NotImplementedError:
                pass


@app.command()
def start(
    no_telegram: Annotated[bool, typer.Option("--no-telegram", help="Skip Telegram bot.")] = False,
    no_clipboard: Annotated[bool, typer.Option("--no-clipboard", help="Skip auto-starting the clipboard daemon.")] = False,
    no_scheduler: Annotated[bool, typer.Option("--no-scheduler", help="Skip APScheduler (Synthesizer + Librarian crons).")] = False,
) -> None:
    """Boot the gateway, Telegram bot, clipboard daemon, and scheduler."""
    configure_logging(component="cli")
    settings = get_settings()

    if not settings.synapse_vault_path.exists():
        console.print("[red]vault not initialized — run `synapse init` first.[/red]")
        raise typer.Exit(1)

    # Auto-start the clipboard daemon as a detached process (unless --no-clipboard).
    if not no_clipboard:
        try:
            pid = daemon_installer.start_clipboard_daemon()
            console.print(f"[green]✓ clipboard daemon[/green] (pid {pid})")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]clipboard daemon failed to start: {exc}[/yellow]")

    asyncio.run(_run_gateway_and_bot(no_telegram=no_telegram, no_scheduler=no_scheduler))


async def _run_gateway_and_bot(*, no_telegram: bool, no_scheduler: bool) -> None:
    """Run uvicorn + Telegram bot + scheduler concurrently. Stop all on cancel."""
    import uvicorn

    settings = get_settings()

    uv_config = uvicorn.Config(
        app="synapse.gateway.main:app",
        host=settings.synapse_gateway_host,
        port=settings.synapse_gateway_port,
        log_level=settings.synapse_log_level.lower(),
        access_log=False,
        loop="asyncio",
    )
    server = uvicorn.Server(uv_config)

    tasks: list[asyncio.Task[None]] = [asyncio.create_task(server.serve(), name="gateway")]

    if not no_telegram and settings.telegram_bot_token:
        from synapse.capture.telegram_bot import run_bot

        tasks.append(asyncio.create_task(run_bot(), name="telegram"))
    elif not no_telegram:
        console.print(
            "[yellow]TELEGRAM_BOT_TOKEN not set; telegram bot disabled.[/yellow]"
        )

    # APScheduler — Synthesizer, Librarian, Energy, Horizon.
    scheduler = None
    if not no_scheduler:
        try:
            from synapse.scheduler import shutdown_scheduler, start_scheduler

            scheduler = start_scheduler()
            console.print(f"[green]✓ scheduler[/green] ({len(scheduler.get_jobs())} jobs)")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[yellow]scheduler failed to start: {exc}[/yellow]")
            scheduler = None

    stop_event = asyncio.Event()
    _bind_signals(stop_event)

    async def _wait_for_stop() -> None:
        await stop_event.wait()

    try:
        if sys.platform != "win32":
            tasks.append(asyncio.create_task(_wait_for_stop(), name="stop-event"))
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    except KeyboardInterrupt:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        if scheduler is not None:
            from synapse.scheduler import shutdown_scheduler

            shutdown_scheduler(wait=False)


@app.command()
def stop() -> None:
    """Stop the clipboard daemon (gateway/Telegram run in the foreground)."""
    if daemon_installer.stop_clipboard_daemon():
        console.print("[green]✓ clipboard daemon stopped[/green]")
    else:
        console.print("[yellow]no clipboard daemon running[/yellow]")


# ── status ───────────────────────────────────────────────────────────────────


@app.command()
def status() -> None:
    """Print a tabular health snapshot."""
    settings = get_settings()
    table = Table(title=f"Synapse v{__version__}", show_header=True)
    table.add_column("component")
    table.add_column("state")
    table.add_column("detail")

    vault_ok = settings.synapse_vault_path.exists()
    table.add_row(
        "vault",
        "[green]ok[/green]" if vault_ok else "[red]missing[/red]",
        str(settings.synapse_vault_path),
    )
    table.add_row(
        "database",
        "[green]ok[/green]" if settings.db_path.exists() else "[red]missing[/red]",
        str(settings.db_path),
    )
    table.add_row("inbox", str(count_inbox_items()), "unprocessed items")

    cb = daemon_installer.daemon_status()
    state = "[green]running[/green]" if cb.running else "[yellow]stopped[/yellow]"
    detail = f"pid={cb.pid}, scheduler={'yes' if cb.scheduler_installed else 'no'}"
    if cb.heartbeat_age_s is not None:
        detail += f", heartbeat={cb.heartbeat_age_s:.0f}s ago"
    table.add_row("clipboard", state, detail)

    table.add_row(
        "telegram",
        "[green]configured[/green]" if settings.telegram_bot_token else "[yellow]token missing[/yellow]",
        f"allowed_user={settings.telegram_allowed_user_id or 'any'}",
    )
    table.add_row(
        "email webhook",
        "[green]configured[/green]" if settings.synapse_email_webhook_secret else "[yellow]secret missing[/yellow]",
        "",
    )
    console.print(table)


# ── ingest ───────────────────────────────────────────────────────────────────


@app.command()
def ingest(
    text: Annotated[str, typer.Argument(help="Text to capture.")],
    source: Annotated[str, typer.Option(help="Capture source.")] = "manual",
    title: Annotated[Optional[str], typer.Option(help="Frontmatter title.")] = None,
) -> None:
    """Capture arbitrary text from the terminal directly into `inbox/`."""
    extra: dict[str, object] = {}
    if title:
        extra["title"] = title
    try:
        path = write_to_inbox(source=source, content=text, extra=extra)
    except ValueError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]✓ {path.name}[/green]")


# ── daemon subcommands ───────────────────────────────────────────────────────


@daemon_app.command("install")
def daemon_install() -> None:
    """Register the clipboard daemon with the OS scheduler (boot autostart)."""
    try:
        daemon_installer.install_clipboard_daemon()
        console.print("[green]✓ scheduler entry installed[/green]")
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]install failed: {exc}[/red]")
        raise typer.Exit(1) from exc


@daemon_app.command("uninstall")
def daemon_uninstall() -> None:
    """Remove the scheduler entry. Does not stop a running process."""
    daemon_installer.uninstall_clipboard_daemon()
    console.print("[green]✓ scheduler entry removed[/green]")


@daemon_app.command("start")
def daemon_start() -> None:
    """Start the clipboard daemon now (detached)."""
    pid = daemon_installer.start_clipboard_daemon()
    console.print(f"[green]✓ clipboard daemon started (pid {pid})[/green]")


@daemon_app.command("stop")
def daemon_stop() -> None:
    """Stop the running clipboard daemon."""
    if daemon_installer.stop_clipboard_daemon():
        console.print("[green]✓ stopped[/green]")
    else:
        console.print("[yellow]no daemon running[/yellow]")


@daemon_app.command("status")
def daemon_status_cmd() -> None:
    """Show clipboard daemon status."""
    s = daemon_installer.daemon_status()
    console.print(
        f"name={s.name} pid={s.pid} running={s.running} "
        f"scheduler_installed={s.scheduler_installed} heartbeat_age_s={s.heartbeat_age_s}"
    )


@daemon_app.command("run")
def daemon_run() -> None:
    """Run the clipboard daemon in the foreground (debug only — no detach)."""
    from synapse.capture import clipboard as cb_mod

    cb_mod.run()


# ── simulate subcommands ─────────────────────────────────────────────────────


@simulate_app.command("email")
def simulate_email(
    from_: Annotated[str, typer.Option("--from", help="Sender address.")] = "professor@strathmore.edu",
    subject: Annotated[str, typer.Option("--subject", help="Email subject.")] = "Test subject",
    body: Annotated[str, typer.Option("--body", help="Email body.")] = "Hello — this is a test capture.",
    gateway: Annotated[str, typer.Option("--gateway", help="Gateway base URL.")] = "",
) -> None:
    """POST a synthetic email payload to `/ingest/email` with valid HMAC."""
    settings = get_settings()
    base = gateway or f"http://{settings.synapse_gateway_host}:{settings.synapse_gateway_port}"
    secret = settings.synapse_email_webhook_secret
    if not secret:
        console.print("[red]SYNAPSE_EMAIL_WEBHOOK_SECRET is not set[/red]")
        raise typer.Exit(1)

    payload = {
        "from": from_,
        "subject": subject,
        "body": body,
        "message_id": f"<sim-{os.urandom(6).hex()}@synapse>",
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()

    resp = httpx.post(
        f"{base}/ingest/email",
        content=body_bytes,
        headers={
            "content-type": "application/json",
            EMAIL_HMAC_HEADER: sig,
        },
        timeout=10.0,
    )
    if resp.status_code >= 400:
        console.print(f"[red]✗ {resp.status_code}: {resp.text}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]✓ {resp.status_code}[/green] {resp.json()}")


@simulate_app.command("text")
def simulate_text(
    content: Annotated[str, typer.Argument(help="Body text to capture.")],
    source: Annotated[str, typer.Option("--source")] = "manual",
    gateway: Annotated[str, typer.Option("--gateway")] = "",
) -> None:
    """POST to `/ingest/text` via the running gateway."""
    settings = get_settings()
    base = gateway or f"http://{settings.synapse_gateway_host}:{settings.synapse_gateway_port}"
    resp = httpx.post(
        f"{base}/ingest/text",
        json={"content": content, "source": source},
        timeout=10.0,
    )
    if resp.status_code >= 400:
        console.print(f"[red]✗ {resp.status_code}: {resp.text}[/red]")
        raise typer.Exit(1)
    console.print(f"[green]✓ {resp.status_code}[/green] {resp.json()}")


# ── stubs for later milestones (so the surface is complete) ──────────────────


@app.command()
def ocr(path: Annotated[Path, typer.Argument(help="Image file path.")]) -> None:
    """OCR a local image (M5)."""
    console.print(f"[yellow]ocr stub — M5 deliverable. file={path}[/yellow]")
    raise typer.Exit(1)


hooks_app = typer.Typer(help="Git hook management.", no_args_is_help=True)
app.add_typer(hooks_app, name="hooks")


@hooks_app.command("install")
def hooks_install(
    repo: Annotated[
        Optional[Path],
        typer.Option("--repo", "-r", help="Target repo path (default: current directory)."),
    ] = None,
    gateway: Annotated[
        str,
        typer.Option("--gateway", help="Synapse gateway base URL."),
    ] = "",
) -> None:
    """Install the Synapse post-commit hook in a git repository.

    The generated `.git/hooks/post-commit` script POSTs commit metadata to
    `POST /ingest/git` on the local Synapse gateway after every commit.
    """
    from synapse.capture.git_hook import install_hook
    from synapse.config import GIT_HOOK_DEFAULT_GATEWAY

    target = (repo or Path.cwd()).resolve()
    gw = gateway or GIT_HOOK_DEFAULT_GATEWAY
    try:
        hook_path = install_hook(target, gateway_url=gw)
        console.print(f"[green]✓ post-commit hook installed:[/green] {hook_path}")
        console.print(
            f"[dim]  gateway={gw}  repo={target}[/dim]\n"
            "  Now create a [bold]synapse.json[/bold] at the repo root to link "
            "CONCEPT nodes (optional but recommended)."
        )
    except ValueError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(1) from exc


insight_app = typer.Typer(help="Promote pending INSIGHT candidates to the knowledge graph.", no_args_is_help=True)
app.add_typer(insight_app, name="insight")


@insight_app.command("list")
def insight_list() -> None:
    """Show all pending INSIGHT candidates from `pending_insights.md`."""
    from synapse.agents.librarian import parse_pending_insights
    from synapse.config import LIBRARIAN_PENDING_INSIGHTS_FILE

    settings = get_settings()
    file = settings.synapse_vault_path / LIBRARIAN_PENDING_INSIGHTS_FILE
    entries = parse_pending_insights(file)
    if not entries:
        console.print("[green]no pending insights[/green]")
        return
    table = Table(title="Pending insights", show_header=True)
    table.add_column("#", style="bold")
    table.add_column("description")
    table.add_column("nodes")
    for e in entries:
        table.add_row(
            str(e.index),
            e.description[:80] + ("…" if len(e.description) > 80 else ""),
            ", ".join(e.node_titles) or "(none)",
        )
    console.print(table)


@insight_app.command("confirm")
def insight_confirm(
    number: Annotated[int, typer.Argument(help="Entry number from `synapse insight list`.")],
) -> None:
    """Promote an INSIGHT candidate to a real INSIGHT node in the graph.

    The confirmed entry is removed from `pending_insights.md`.
    """
    from synapse.agents.librarian import confirm_insight, parse_pending_insights
    from synapse.config import LIBRARIAN_PENDING_INSIGHTS_FILE

    configure_logging(component="cli")
    settings = get_settings()
    file = settings.synapse_vault_path / LIBRARIAN_PENDING_INSIGHTS_FILE
    entries = parse_pending_insights(file)
    if not entries:
        console.print("[yellow]no pending insights to confirm[/yellow]")
        raise typer.Exit(1)
    match = next((e for e in entries if e.index == number), None)
    if match is None:
        console.print(f"[red]no insight #{number} — run `synapse insight list` to see valid numbers[/red]")
        raise typer.Exit(1)
    console.print(f"\n[bold]Confirming insight #{number}:[/bold]")
    console.print(f"  {match.description}")
    if match.node_titles:
        console.print(f"  Nodes: {', '.join(match.node_titles)}")
    console.print()
    confirm = typer.confirm("Create INSIGHT node and remove from pending list?", default=True)
    if not confirm:
        console.print("[yellow]aborted[/yellow]")
        raise typer.Exit(0)
    node_id = confirm_insight(match, file)
    console.print(f"[green]✓ INSIGHT node created:[/green] {node_id}")
    console.print(f"[dim]  title: {match.description[:80]}[/dim]")


librarian_app = typer.Typer(help="Librarian agent controls.", no_args_is_help=True)
app.add_typer(librarian_app, name="librarian")


@librarian_app.command("run")
def librarian_run(
    max_items: Annotated[int, typer.Option("--max-items", "-n", help="Cap items to process.")] = 100,
) -> None:
    """Trigger the Librarian on the current inbox."""
    from synapse.agents.librarian import librarian as _librarian

    configure_logging(component="cli")
    result = asyncio.run(_librarian.run(max_items=max_items))
    style = "green" if result.ok else "yellow"
    console.print(f"[{style}]{result.summary}[/{style}]")
    table = Table(title="librarian run", show_header=True)
    table.add_column("metric")
    table.add_column("value")
    for k, v in result.artifacts.items():
        table.add_row(k, str(v))
    console.print(table)
    if result.errors:
        console.print("[red]errors:[/red]")
        for e in result.errors:
            console.print(f"  • {e}")
        raise typer.Exit(1)


@app.command()
def brief() -> None:
    """Trigger the Synthesizer and render today's Delta Briefing."""
    from synapse.agents.synthesizer import synthesizer

    configure_logging(component="cli")
    result = asyncio.run(synthesizer.run())
    if not result.ok:
        console.print(f"[red]✗ {result.summary}[/red]")
        for e in result.errors:
            console.print(f"  • {e}")
        raise typer.Exit(1)
    console.print(f"[green]{result.summary}[/green]")
    md = str(result.artifacts.get("brief_markdown", ""))
    if md:
        console.print()
        console.print(md)
    path = result.artifacts.get("daily_path")
    if path:
        console.print(f"\n[dim]written to {path}[/dim]")


@app.command()
def review() -> None:
    """Terminal review loop — serves due cards one at a time, accepts 1-5 ratings."""
    from synapse.config import SYNTHESIZER_RETENTION_ALERTS
    from synapse.graph.retention import apply_rating, get_due_reviews

    configure_logging(component="cli")
    while True:
        due = get_due_reviews(limit=SYNTHESIZER_RETENTION_ALERTS)
        if not due:
            console.print("[green]✓ no reviews due[/green]")
            return
        card = due[0]
        console.print()
        console.rule(f"[bold]{card.title}[/bold]")
        if card.application_question:
            console.print(card.application_question)
        else:
            console.print(
                "[dim](no application question yet — describe how you'd apply this concept)[/dim]"
            )
        console.print()
        raw = typer.prompt("rate 1-5 (q to quit)", default="5")
        if raw.strip().lower() in {"q", "quit", "exit"}:
            return
        try:
            quality = int(raw)
        except ValueError:
            console.print("[red]not a number; quitting[/red]")
            return
        if not 1 <= quality <= 5:
            console.print("[red]must be 1-5; quitting[/red]")
            return
        state = apply_rating(node_id=card.node_id, quality=quality)
        days = round(state.interval_days, 1)
        console.print(
            f"[green]✓[/green] rated {quality}/5 — next review in {days}d "
            f"(ease={state.ease_factor:.2f})"
        )


# ── M2: events + scheduler subcommands ───────────────────────────────────────

event_app = typer.Typer(help="Manual EVENT entry (Calendar OAuth lands in M4).", no_args_is_help=True)
app.add_typer(event_app, name="event")


@event_app.command("add")
def event_add(
    title: Annotated[str, typer.Option("--title", "-t", help="Event title.")],
    date: Annotated[str, typer.Option("--date", "-d", help="ISO-8601 datetime (e.g. 2026-05-30T14:00).")],
    concepts: Annotated[
        Optional[str],
        typer.Option("--concepts", help="Comma-separated CONCEPT titles to link."),
    ] = None,
    content: Annotated[str, typer.Option("--content", help="Optional markdown body.")] = "",
) -> None:
    """Create an EVENT node and add it to the horizon queue."""
    from datetime import datetime as _dt

    from synapse.context.horizon import add_event, refresh_horizon

    try:
        when = _dt.fromisoformat(date)
    except ValueError as exc:
        console.print(f"[red]invalid date: {exc}[/red]")
        raise typer.Exit(1) from exc
    titles = [t.strip() for t in (concepts.split(",") if concepts else []) if t.strip()]
    node = add_event(title=title, date=when, content=content, linked_concept_titles=titles)
    horizon_count = refresh_horizon()
    console.print(f"[green]✓ EVENT[/green] {node.title} ({node.id[:8]}) — horizon now {horizon_count}")


scheduler_app = typer.Typer(help="APScheduler inspection.", no_args_is_help=True)
app.add_typer(scheduler_app, name="scheduler")


@scheduler_app.command("jobs")
def scheduler_jobs_cmd() -> None:
    """List registered scheduler jobs with next-fire times."""
    from synapse.scheduler import list_jobs

    jobs = list_jobs()
    if not jobs:
        console.print("[yellow]no jobs registered[/yellow]")
        return
    table = Table(title="scheduler jobs", show_header=True)
    table.add_column("id")
    table.add_column("name")
    table.add_column("trigger")
    table.add_column("next_run")
    for j in jobs:
        table.add_row(j["id"], j["name"], j["trigger"], j["next_run_time"] or "(unscheduled)")
    console.print(table)


@app.command()
def ask(
    query: Annotated[str, typer.Argument(help="Search query.")],
    types: Annotated[Optional[str], typer.Option("--types", help="Comma-separated node types.")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 10,
) -> None:
    """Search the knowledge graph (semantic + centrality)."""
    from synapse.graph.operations import build_networkx_graph, compute_centrality
    from synapse.graph.search import search as _search

    type_filter = [t.strip() for t in (types.split(",") if types else []) if t.strip()]
    centrality = compute_centrality(build_networkx_graph())
    hits = _search(query, types=type_filter or None, limit=limit, centrality_lookup=centrality)
    if not hits:
        console.print("[yellow]no matches[/yellow]")
        return
    table = Table(title=f"ask: {query}", show_header=True)
    table.add_column("score")
    table.add_column("type")
    table.add_column("title")
    table.add_column("snippet")
    for h in hits:
        table.add_row(
            f"{h.score:.3f}",
            str(h.metadata.get("type", "")),
            str(h.metadata.get("title", "")),
            (h.document[:80] + "…") if len(h.document) > 80 else h.document,
        )
    console.print(table)


graph_app = typer.Typer(help="Knowledge graph inspection.", no_args_is_help=True)
app.add_typer(graph_app, name="graph")


@graph_app.command("stats")
def graph_stats_cmd() -> None:
    """Print node/edge counts + orphans + needs_review."""
    from synapse.graph.operations import graph_stats

    s = graph_stats()
    table = Table(title="graph stats", show_header=True)
    table.add_column("metric")
    table.add_column("value")
    table.add_row("nodes", str(s["node_count"]))
    table.add_row("edges", str(s["edge_count"]))
    for t, c in sorted(s["nodes_by_type"].items()):
        table.add_row(f"  {t}", str(c))
    table.add_row("needs_review", str(s["needs_review"]))
    table.add_row("orphans", str(s["orphan_count"]))
    console.print(table)


@graph_app.command("orphans")
def graph_orphans_cmd() -> None:
    """List nodes with zero edges."""
    from synapse.graph.operations import find_orphans

    orphans = find_orphans()
    if not orphans:
        console.print("[green]no orphans[/green]")
        return
    table = Table(show_header=True)
    table.add_column("id")
    table.add_column("type")
    table.add_column("title")
    for n in orphans:
        nt = n.type.value if hasattr(n.type, "value") else str(n.type)
        table.add_row(n.id, nt, n.title)
    console.print(table)


@graph_app.command("weak-edges")
def graph_weak_edges_cmd(
    threshold: Annotated[float, typer.Option("--threshold", "-t", help="Weight ceiling.")] = 0.1,
) -> None:
    """Show edges whose Hebbian weight has decayed below `threshold` (PRD Appendix A.1)."""
    from synapse.graph.hebbian import list_weak_edges
    from synapse.graph.operations import get_node

    weak = list_weak_edges(threshold=threshold)
    if not weak:
        console.print(f"[green]no edges below threshold {threshold}[/green]")
        return
    table = Table(title=f"weak edges (< {threshold})", show_header=True)
    table.add_column("source")
    table.add_column("→ target")
    table.add_column("relation")
    table.add_column("weight")
    table.add_column("last_strengthened")
    for e in weak:
        src = get_node(e.source_node_id)
        tgt = get_node(e.target_node_id)
        table.add_row(
            (src.title if src else e.source_node_id)[:30],
            (tgt.title if tgt else e.target_node_id)[:30],
            e.relation_type,
            f"{e.weight:.3f}",
            e.last_strengthened.isoformat() if e.last_strengthened else "(never)",
        )
    console.print(table)


@graph_app.command("cold")
def graph_cold_cmd(
    threshold: Annotated[float, typer.Option("--threshold", "-t", help="Freshness ceiling.")] = 0.1,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 25,
) -> None:
    """Show nodes whose freshness has fallen below `threshold` (PRD Appendix A.3)."""
    from synapse.graph.freshness import list_cold_nodes

    cold = list_cold_nodes(threshold=threshold, limit=limit)
    if not cold:
        console.print(f"[green]no nodes below freshness {threshold}[/green]")
        return
    table = Table(title=f"cold nodes (freshness < {threshold})", show_header=True)
    table.add_column("type")
    table.add_column("title")
    table.add_column("freshness")
    for n, f in cold:
        nt = n.type.value if hasattr(n.type, "value") else str(n.type)
        table.add_row(nt, n.title, f"{f:.3f}")
    console.print(table)


# ── M4: agent commands ───────────────────────────────────────────────────────

strategist_app = typer.Typer(help="Strategist agent — weekly planning + collision detection.", no_args_is_help=True)
app.add_typer(strategist_app, name="strategist")


@strategist_app.command("run")
def strategist_run_cmd(
    lookahead_hours: Annotated[int, typer.Option("--lookahead", "-l")] = 168,
) -> None:
    """Run the Strategist now (default lookahead: 7 days)."""
    from synapse.agents.strategist import strategist

    configure_logging(component="cli")
    result = asyncio.run(strategist.run(lookahead_hours=lookahead_hours))
    style = "green" if result.ok else "red"
    console.print(f"[{style}]{result.summary}[/{style}]")
    for k, v in result.artifacts.items():
        console.print(f"  {k}: {v}")
    if result.errors:
        for e in result.errors:
            console.print(f"  [red]error: {e}[/red]")
        raise typer.Exit(1)


guardian_app = typer.Typer(help="Guardian agent — burnout watchdog.", no_args_is_help=True)
app.add_typer(guardian_app, name="guardian")


@guardian_app.command("run")
def guardian_run_cmd() -> None:
    """Run the Guardian now (silent if no thresholds tripped)."""
    from synapse.agents.guardian import guardian

    configure_logging(component="cli")
    result = asyncio.run(guardian.run())
    console.print(result.summary)
    if result.artifacts.get("nudge"):
        console.print()
        console.print(f"[yellow]{result.artifacts.get('message', '')}[/yellow]")


@app.command()
def consolidate() -> None:
    """Run the nightly consolidation pass (Synthesizer abstraction mode)."""
    from synapse.agents.synthesizer import synthesizer

    configure_logging(component="cli")
    result = asyncio.run(synthesizer.consolidate())
    style = "green" if result.ok else "red"
    console.print(f"[{style}]{result.summary}[/{style}]")
    for k, v in result.artifacts.items():
        console.print(f"  {k}: {v}")
    if result.errors:
        for e in result.errors:
            console.print(f"  [red]error: {e}[/red]")
        raise typer.Exit(1)


# ── M4: auth commands ───────────────────────────────────────────────────────

auth_app = typer.Typer(help="OAuth integrations (Google, GitHub).", no_args_is_help=True)
app.add_typer(auth_app, name="auth")

google_app = typer.Typer(help="Google Calendar OAuth.", no_args_is_help=True)
auth_app.add_typer(google_app, name="google")


@google_app.command("start")
def auth_google_start_cmd() -> None:
    """Print the Google authorize URL (open it in a browser to consent)."""
    from synapse.gateway.auth import AuthError, start_authorization

    try:
        result = start_authorization("google_calendar")
    except AuthError as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(1) from exc
    console.print("[bold]Open this URL in a browser to authorize:[/bold]\n")
    console.print(result.authorize_url)
    console.print(
        "\n[dim]After consenting, Google redirects to the gateway callback "
        "(must be running) which stores the encrypted tokens.[/dim]"
    )


@google_app.command("status")
def auth_google_status_cmd() -> None:
    """Show whether Google Calendar credentials are stored."""
    from synapse.gateway.auth import credential_status

    status = credential_status("google_calendar")
    if not status.get("configured"):
        console.print("[yellow]google_calendar: not connected[/yellow]")
        console.print("[dim]  run `synapse auth google start` to begin OAuth[/dim]")
        return
    console.print("[green]google_calendar: connected[/green]")
    console.print(f"  expires_at: {status['expires_at']}")
    console.print(f"  refresh_token: {'yes' if status['has_refresh_token'] else 'no'}")
    console.print(f"  scopes: {', '.join(status['scopes'])}")


@google_app.command("sync")
def auth_google_sync_cmd(
    lookahead_hours: Annotated[int, typer.Option("--lookahead", "-l")] = 168,
) -> None:
    """Import upcoming Google Calendar events as EVENT nodes."""
    from synapse.gateway.auth import AuthError
    from synapse.integrations.google_calendar import CalendarError, sync_calendar_to_events

    configure_logging(component="cli")
    try:
        n = sync_calendar_to_events(lookahead_hours=lookahead_hours)
    except (AuthError, CalendarError) as exc:
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]✓ imported {n} event(s) from Google Calendar[/green]")


@app.command()
def logs(
    component: Annotated[str, typer.Argument(help="gateway | clipboard | cli")] = "gateway",
    lines: Annotated[int, typer.Option("--lines", "-n")] = 50,
) -> None:
    """Tail the last N lines of a component log."""
    settings = get_settings()
    path = settings.log_dir / f"{component}.log"
    if not path.exists():
        console.print(f"[yellow]no log at {path}[/yellow]")
        return
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in content[-lines:]:
        console.print(line)


@app.command()
def inbox(limit: Annotated[int, typer.Option("--limit", "-n")] = 5) -> None:
    """Show inbox count + the oldest N items."""
    count = count_inbox_items()
    console.print(f"[bold]inbox: {count} items[/bold]")
    for p in oldest_inbox_items(limit=limit):
        console.print(f"  • {p.name}")


@app.command()
def version() -> None:
    """Print the Synapse version."""
    console.print(__version__)


def main() -> None:
    """CLI entry point — referenced by `pyproject.toml [project.scripts]`."""
    app()


if __name__ == "__main__":
    main()
