"""Telegram capture bot — primary daily-use interface.

Accepts text / voice / photo / document / forwarded-message and funnels each
into `write_to_inbox()`. Voice and photo bodies are saved to
`${VAULT}/attachments/` and a placeholder inbox markdown carries the file
reference (Whisper transcription and OCR run in later milestones).

Commands available in M0:
    /review  — inbox count + 3 oldest items
    /status  — system health summary
    /inbox   — alias for /review

The bot replies with a single ✓ on capture success — nothing more (PRD §4.2).
On any write failure the capture is enqueued via the inbox retry queue, and
the bot replies with a clear error so the user knows to /retry later. M0
does not implement automatic retry yet — only persistence.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest   # ← Added for custom timeouts

from synapse.capture.inbox import (
    InboxWriteError,
    count_inbox_items,
    oldest_inbox_items,
    write_to_inbox,
)
from synapse.config import (
    TELEGRAM_CONNECT_TIMEOUT_SECONDS,
    TELEGRAM_POOL_TIMEOUT_SECONDS,
    TELEGRAM_READ_TIMEOUT_SECONDS,
    TELEGRAM_REPLY_ACK,
    TELEGRAM_WRITE_TIMEOUT_SECONDS,
    get_settings,
)


# ── User authorization ───────────────────────────────────────────────────────


def _user_allowed(update: Update) -> bool:
    """Single-user gate: if TELEGRAM_ALLOWED_USER_ID is set, enforce it."""
    allowed = get_settings().telegram_allowed_user_id
    if allowed is None:
        return True
    return bool(update.effective_user and update.effective_user.id == allowed)


async def _ack(update: Update, text: str = TELEGRAM_REPLY_ACK) -> None:
    """Send a short acknowledgement reply."""
    if update.effective_message:
        try:
            await update.effective_message.reply_text(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("telegram reply failed: {exc}", exc=exc)


# ── Capture handlers ─────────────────────────────────────────────────────────


def _forward_metadata(update: Update) -> dict[str, Any]:
    """Extract any forwarded-message provenance Telegram exposes."""
    msg = update.effective_message
    if msg is None:
        return {}
    extra: dict[str, Any] = {}
    # python-telegram-bot v21 surfaces forward info via `forward_origin`
    origin = getattr(msg, "forward_origin", None)
    if origin is not None:
        extra["forward_origin_type"] = origin.__class__.__name__
        for attr in ("sender_user_name", "chat", "message_id", "date"):
            val = getattr(origin, attr, None)
            if val is not None:
                extra[f"forward_{attr}"] = str(val)
    if msg.entities:
        urls = [
            msg.text[e.offset : e.offset + e.length]
            for e in msg.entities
            if e.type in ("url", "text_link") and msg.text
        ]
        if urls:
            extra["urls"] = urls
    return extra


async def handle_text(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Plain text message → inbox."""
    if not _user_allowed(update) or update.effective_message is None:
        return
    msg = update.effective_message
    text = msg.text or msg.caption or ""
    if not text.strip():
        return

    extra = _forward_metadata(update)
    extra["telegram_chat_id"] = update.effective_chat.id if update.effective_chat else None
    extra["telegram_message_id"] = msg.message_id

    try:
        write_to_inbox(source="telegram", content=text, extra=extra)
    except InboxWriteError:
        await _ack(update, "✗ queued for retry — see /status")
        return
    except ValueError as exc:
        await _ack(update, f"✗ {exc}")
        return

    await _ack(update)


async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Voice note → save audio + placeholder inbox entry."""
    if not _user_allowed(update) or update.effective_message is None:
        return
    msg = update.effective_message
    voice = msg.voice or msg.audio
    if voice is None:
        return

    settings = get_settings()
    target_dir = settings.attachments_dir / "voice"
    target_dir.mkdir(parents=True, exist_ok=True)
    capture_id = str(uuid.uuid4())
    ext = ".ogg"
    target = target_dir / f"{capture_id}{ext}"

    try:
        file = await ctx.bot.get_file(voice.file_id)
        await file.download_to_drive(custom_path=target.as_posix())
    except Exception as exc:  # noqa: BLE001
        logger.exception("voice download failed: {exc}", exc=exc)
        await _ack(update, "✗ voice download failed")
        return

    body = (
        "_[voice message — pending transcription]_\n\n"
        f"audio file: `{target.relative_to(settings.synapse_vault_path).as_posix()}`\n"
        f"duration: {voice.duration}s"
    )
    extra = {
        "audio_path": target.relative_to(settings.synapse_vault_path).as_posix(),
        "duration_s": voice.duration,
        "pending_transcription": True,
        "telegram_message_id": msg.message_id,
    }
    try:
        write_to_inbox(source="voice", content=body, extra=extra)
    except InboxWriteError:
        await _ack(update, "✗ queued for retry")
        return

    await _ack(update)


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Photo → save image + placeholder inbox entry (pending OCR)."""
    if not _user_allowed(update) or update.effective_message is None:
        return
    msg = update.effective_message
    if not msg.photo:
        return

    settings = get_settings()
    target_dir = settings.attachments_dir / "images"
    target_dir.mkdir(parents=True, exist_ok=True)
    capture_id = str(uuid.uuid4())
    target = target_dir / f"{capture_id}.jpg"

    photo = msg.photo[-1]  # largest size
    try:
        file = await ctx.bot.get_file(photo.file_id)
        await file.download_to_drive(custom_path=target.as_posix())
    except Exception as exc:  # noqa: BLE001
        logger.exception("photo download failed: {exc}", exc=exc)
        await _ack(update, "✗ photo download failed")
        return

    rel = target.relative_to(settings.synapse_vault_path).as_posix()
    body_lines = [f"_[image — pending OCR]_\n\nimage file: `{rel}`"]
    if msg.caption:
        body_lines.append("")
        body_lines.append(f"caption: {msg.caption}")

    extra = {
        "image_path": rel,
        "pending_ocr": True,
        "telegram_message_id": msg.message_id,
    }
    if msg.caption:
        extra["caption"] = msg.caption

    try:
        write_to_inbox(source="image-pending-ocr", content="\n".join(body_lines), extra=extra)
    except InboxWriteError:
        await _ack(update, "✗ queued for retry")
        return

    await _ack(update)


async def handle_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Generic document → save bytes + placeholder inbox entry."""
    if not _user_allowed(update) or update.effective_message is None:
        return
    msg = update.effective_message
    doc = msg.document
    if doc is None:
        return

    settings = get_settings()
    target_dir = settings.attachments_dir / "docs"
    target_dir.mkdir(parents=True, exist_ok=True)
    capture_id = str(uuid.uuid4())
    safe_name = "".join(c if c.isalnum() or c in ".-_" else "_" for c in (doc.file_name or "attachment"))
    target = target_dir / f"{capture_id}_{safe_name}"

    try:
        file = await ctx.bot.get_file(doc.file_id)
        await file.download_to_drive(custom_path=target.as_posix())
    except Exception as exc:  # noqa: BLE001
        logger.exception("document download failed: {exc}", exc=exc)
        await _ack(update, "✗ document download failed")
        return

    rel = target.relative_to(settings.synapse_vault_path).as_posix()
    body = (
        f"_[document attachment]_\n\n"
        f"file: `{rel}`\n"
        f"mime: {doc.mime_type or 'unknown'}\n"
        f"size: {doc.file_size or 0} bytes"
    )
    if msg.caption:
        body += f"\n\ncaption: {msg.caption}"

    extra = {
        "document_path": rel,
        "mime_type": doc.mime_type,
        "original_filename": doc.file_name,
        "telegram_message_id": msg.message_id,
    }

    try:
        write_to_inbox(source="manual", content=body, extra=extra)
    except InboxWriteError:
        await _ack(update, "✗ queued for retry")
        return

    await _ack(update)


# ── Commands ─────────────────────────────────────────────────────────────────


async def cmd_inbox(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Return inbox count + 3 oldest unprocessed items (PRD §8.3)."""
    if not _user_allowed(update) or update.effective_message is None:
        return
    count = count_inbox_items()
    oldest = oldest_inbox_items(limit=3)
    lines = [f"📥 inbox: {count} unprocessed"]
    if oldest:
        lines.append("\noldest:")
        for p in oldest:
            lines.append(f"• `{p.name}`")
    await update.effective_message.reply_text("\n".join(lines))


async def cmd_status(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Compact health summary."""
    if not _user_allowed(update) or update.effective_message is None:
        return
    settings = get_settings()
    vault_ok = settings.synapse_vault_path.exists()
    db_ok = settings.db_path.exists()
    lines = [
        f"vault: {'✅' if vault_ok else '❌'} `{settings.synapse_vault_path}`",
        f"db: {'✅' if db_ok else '❌'}",
        f"inbox: {count_inbox_items()} items",
    ]
    await update.effective_message.reply_text("\n".join(lines))


# ── /brief, /review, callback handler ────────────────────────────────────────


# Telegram message bodies are capped at 4096 chars; chunk long briefs.
_TELEGRAM_MSG_LIMIT = 3800


def _chunk_markdown(text: str, *, limit: int = _TELEGRAM_MSG_LIMIT) -> list[str]:
    """Split text into Telegram-sized chunks at paragraph boundaries when possible."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut == -1:
            cut = remaining.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _review_keyboard(node_id: str) -> InlineKeyboardMarkup:
    """1-5 SM-2 rating buttons for a CONCEPT review card."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("1 ✗", callback_data=f"rate:{node_id}:1"),
                InlineKeyboardButton("2", callback_data=f"rate:{node_id}:2"),
                InlineKeyboardButton("3", callback_data=f"rate:{node_id}:3"),
                InlineKeyboardButton("4", callback_data=f"rate:{node_id}:4"),
                InlineKeyboardButton("5 ✓", callback_data=f"rate:{node_id}:5"),
            ]
        ]
    )


def _render_review_card(due) -> str:  # type: ignore[no-untyped-def]
    """Format one due card for Telegram."""
    q = due.application_question or (
        "_(no application question generated yet — describe how you'd apply this concept)_"
    )
    return (
        f"📚 *{due.title}*\n\n"
        f"{q}\n\n"
        f"_Rate your recall 1 (blackout) → 5 (perfect)_"
    )


async def cmd_brief(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Trigger the Synthesizer on demand and deliver the Delta Briefing here."""
    if not _user_allowed(update) or update.effective_message is None:
        return
    from synapse.agents.synthesizer import synthesizer

    await update.effective_message.reply_text("🧠 thinking…")
    try:
        result = await synthesizer.run()
    except Exception as exc:  # noqa: BLE001
        logger.exception("synthesizer call failed: {exc}", exc=exc)
        await update.effective_message.reply_text(f"✗ synthesizer error: {exc}")
        return

    if not result.ok:
        await update.effective_message.reply_text(f"✗ {result.summary}")
        return
    md = str(result.artifacts.get("brief_markdown", "")) or result.summary
    for chunk in _chunk_markdown(md):
        await update.effective_message.reply_text(chunk)


async def cmd_review(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """OVERRIDES the M0 inbox-summary `/review`: now serves the next due card."""
    if not _user_allowed(update) or update.effective_message is None:
        return
    from synapse.config import SYNTHESIZER_RETENTION_ALERTS
    from synapse.graph.retention import get_due_reviews

    due = get_due_reviews(limit=SYNTHESIZER_RETENTION_ALERTS)
    if not due:
        await update.effective_message.reply_text(
            "✅ no reviews due. Use /brief for today's Delta Briefing."
        )
        return
    card = due[0]
    await update.effective_message.reply_text(
        _render_review_card(card),
        reply_markup=_review_keyboard(card.node_id),
        parse_mode=ParseMode.MARKDOWN,
    )


async def on_review_rating(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle a 1-5 button tap from `/review` cards."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    if not _user_allowed(update):
        await query.answer("not authorized", show_alert=False)
        return

    try:
        prefix, node_id, quality_str = query.data.split(":", 2)
        if prefix != "rate":
            return
        quality = int(quality_str)
    except ValueError:
        await query.answer("malformed callback", show_alert=False)
        return

    from synapse.graph.operations import get_node
    from synapse.graph.retention import apply_rating

    try:
        state = apply_rating(node_id=node_id, quality=quality)
    except ValueError as exc:
        await query.answer(f"✗ {exc}", show_alert=True)
        return

    node = get_node(node_id)
    title = node.title if node else "(unknown)"

    days = round(state.interval_days, 1)
    reveal = (
        f"✓ *{title}* rated {quality}/5\n"
        f"next review in {days}d (ease={state.ease_factor:.2f})\n"
    )
    if node is not None and node.content.strip():
        reveal += f"\n---\n{node.content[:600]}"
        if len(node.content) > 600:
            reveal += "…"

    # Acknowledge button tap immediately, then edit the card.
    await query.answer(f"rated {quality}/5 — next in {days}d", show_alert=False)
    if query.message is not None:
        try:
            await query.edit_message_text(text=reveal, parse_mode=ParseMode.MARKDOWN)
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not edit review card: {exc}", exc=exc)

    # Queue up the next due card if any (delivered as a new message).
    from synapse.config import SYNTHESIZER_RETENTION_ALERTS
    from synapse.graph.retention import get_due_reviews

    remaining = get_due_reviews(limit=SYNTHESIZER_RETENTION_ALERTS)
    # Filter out the one we just rated (its next_review is now in the future).
    remaining = [d for d in remaining if d.node_id != node_id]
    if remaining and query.message is not None:
        next_card = remaining[0]
        await query.message.reply_text(
            _render_review_card(next_card),
            reply_markup=_review_keyboard(next_card.node_id),
            parse_mode=ParseMode.MARKDOWN,
        )


# ── Bootstrap ────────────────────────────────────────────────────────────────


def build_application(token: str) -> Application:
    """Construct the python-telegram-bot Application with all handlers wired."""
    # Timeouts tuned for variable Nairobi internet; see synapse/config.py.
    request = HTTPXRequest(
        connect_timeout=TELEGRAM_CONNECT_TIMEOUT_SECONDS,
        read_timeout=TELEGRAM_READ_TIMEOUT_SECONDS,
        write_timeout=TELEGRAM_WRITE_TIMEOUT_SECONDS,
        pool_timeout=TELEGRAM_POOL_TIMEOUT_SECONDS,
    )

    app: Application = (
        ApplicationBuilder()
        .token(token)
        .request(request)
        .build()
    )

    app.add_handler(CommandHandler("review", cmd_review))   # M2: due review card
    app.add_handler(CommandHandler("inbox", cmd_inbox))     # PRD §8.3: inbox summary
    app.add_handler(CommandHandler("brief", cmd_brief))     # M2: on-demand Delta Briefing
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(on_review_rating, pattern=r"^rate:"))

    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    # Text + forwarded text + captions land here. Must come AFTER more specific filters.
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )
    
    return app


async def run_bot() -> None:
    """Start the Telegram bot polling loop. Blocks until cancelled."""
    settings = get_settings()
    token = settings.telegram_bot_token
    if not token:
        logger.warning(
            "TELEGRAM_BOT_TOKEN not set — telegram bot disabled. "
            "Set it in .env to enable Telegram capture."
        )
        return

    app = build_application(token)
    logger.info("telegram bot starting (allowed user: {u})", u=settings.telegram_allowed_user_id or "any")
    await app.initialize()
    await app.start()
    if app.updater is not None:
        await app.updater.start_polling(drop_pending_updates=False)
    try:
        # Park forever — caller cancels via asyncio.CancelledError.
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        logger.info("telegram bot stopping")
    finally:
        if app.updater is not None and app.updater.running:
            await app.updater.stop()
        await app.stop()
        await app.shutdown()


def run_bot_sync() -> None:
    """Sync entry point for `synapse start --telegram-only` or pythonw daemon."""
    asyncio.run(run_bot())