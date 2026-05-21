import asyncio
import io
import logging
import os
import re
import time

from dotenv import load_dotenv
load_dotenv()

from pyrogram import Client as PyroClient, raw
from pyrogram.enums import MessageMediaType, ParseMode
from pyrogram.errors import (
    ChannelPrivate,
    FloodWait,
    MessageIdInvalid,
    PeerIdInvalid,
    UsernameInvalid,
    UsernameNotOccupied,
)
from pyrogram.file_id import FileId, PHOTO_TYPES, DOCUMENT_TYPES
from pyrogram.session import Session

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
    InputMediaPhoto,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.constants import ParseMode as TgParseMode

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

API_ID = int(os.environ.get("API_ID", "4"))
API_HASH = os.environ.get("API_HASH", "014b35b6184100b085b0d0572f9b5103")
BOT_TOKEN = os.environ["BOT_TOKEN"]
SESSION_STRING = os.environ["SESSION_STRING"]

MAX_SIZE = 20 * 1024 * 1024  # 20 MB (telegram bot api limit)
CHUNK = 1024 * 1024          # 1 MB per chunk (MTProto max)
WORKERS = 8                  # parallel download workers

fetch = PyroClient(
    "bypass_fetch",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    no_updates=True,
)

# file_id cache: link -> (bot_api_file_id, type_name, size)
_cache = {}


# ─── turbo parallel downloader ────────────────────────────────────────

def _get_fid(msg):
    """extract file_id string from pyrogram message."""
    if msg.photo: return msg.photo.file_id
    if msg.video: return msg.video.file_id
    if msg.document: return msg.document.file_id
    if msg.audio: return msg.audio.file_id
    if msg.voice: return msg.voice.file_id
    if msg.video_note: return msg.video_note.file_id
    if msg.sticker: return msg.sticker.file_id
    if msg.animation: return msg.animation.file_id
    return None


def _make_location(fid):
    """build raw InputFileLocation from decoded FileId."""
    if fid.file_type in PHOTO_TYPES:
        return raw.types.InputPhotoFileLocation(
            id=fid.media_id,
            access_hash=fid.access_hash,
            file_reference=fid.file_reference,
            thumb_size=fid.thumbnail_size or "y",
        )
    return raw.types.InputDocumentFileLocation(
        id=fid.media_id,
        access_hash=fid.access_hash,
        file_reference=fid.file_reference,
        thumb_size=fid.thumbnail_size or "",
    )


async def _ensure_session(client, dc_id):
    """get or create a media session for the target DC."""
    session = client.media_sessions.get(dc_id)
    if session is not None:
        return session

    my_dc = await client.storage.dc_id()

    if dc_id == my_dc:
        # same DC as main session — reuse auth key
        session = Session(
            client, dc_id,
            await client.storage.auth_key(),
            await client.storage.test_mode(),
            is_media=True,
        )
        await session.start()
    else:
        # different DC — need fresh DH key exchange
        # pass empty auth key so Session generates a new one
        session = Session(
            client, dc_id,
            b"\x00" * 256,  # placeholder, triggers new key exchange
            await client.storage.test_mode(),
            is_media=True,
        )
        await session.start()

        # export auth from home DC and import to foreign DC
        exported = await client.invoke(
            raw.functions.auth.ExportAuthorization(dc_id=dc_id)
        )
        await session.invoke(
            raw.functions.auth.ImportAuthorization(
                id=exported.id,
                bytes=exported.bytes,
            )
        )

    client.media_sessions[dc_id] = session
    log.info(f"Media session created for DC{dc_id}")
    return session


async def turbo_download(client, message, progress=None):
    """parallel chunk download using raw MTProto GetFile.
    fires up to 8 concurrent 1MB chunk requests simultaneously.
    falls back to normal download if anything goes wrong."""

    fid_str = _get_fid(message)
    total = get_file_size(message)

    if not fid_str or total <= 0:
        return await client.download_media(message, in_memory=True, progress=progress)

    fid = FileId.decode(fid_str)
    location = _make_location(fid)

    try:
        session = await _ensure_session(client, fid.dc_id)
    except Exception as e:
        log.warning(f"session create failed: {e}, normal download")
        return await client.download_media(message, in_memory=True, progress=progress)

    n_chunks = (total + CHUNK - 1) // CHUNK
    parts = [None] * n_chunks
    done = [0]
    sem = asyncio.Semaphore(WORKERS)

    async def grab(idx):
        async with sem:
            offset = idx * CHUNK
            r = await session.invoke(
                raw.functions.upload.GetFile(
                    location=location,
                    offset=offset,
                    limit=CHUNK,
                )
            )
            parts[idx] = r.bytes
            done[0] += len(r.bytes)
            if progress:
                await progress(done[0], total)

    try:
        await asyncio.gather(*[grab(i) for i in range(n_chunks)])
    except Exception as e:
        log.warning(f"parallel chunks failed ({e}), falling back to normal")
        return await client.download_media(message, in_memory=True, progress=progress)

    buf = io.BytesIO()
    for part in parts:
        if part:
            buf.write(part)
    buf.seek(0)
    log.info(f"turbo download: {n_chunks} chunks, {WORKERS} workers")
    return buf


# ─── helpers ──────────────────────────────────────────────────────────

def make_bar(pct, w=12):
    f = int(w * pct / 100)
    return f"[{'█' * f}{'░' * (w - f)}]"

def fmt_size(b):
    if b < 1024 * 1024:
        return f"{b / 1024:.0f} KB"
    return f"{b / 1024 / 1024:.1f} MB"


_PRIVATE = re.compile(r"t\.me/c/(\d+)/(\d+)", re.I)
_PUBLIC = re.compile(r"t\.me/(?:s/)?([A-Za-z0-9_]{4,})/(\d+)", re.I)
_SKIP = {"joinchat", "addstickers", "share", "s", "boost", "invoice"}

def parse_link(text):
    m = _PRIVATE.search(text)
    if m:
        return int(f"-100{m.group(1)}"), int(m.group(2))
    m = _PUBLIC.search(text)
    if m and m.group(1).lower() not in _SKIP:
        return f"@{m.group(1)}", int(m.group(2))
    return None, None


def get_file_size(message):
    if not message.media:
        return 0
    media = message.media
    if media == MessageMediaType.PHOTO and message.photo:
        return message.photo.file_size
    elif media == MessageMediaType.VIDEO and message.video:
        return message.video.file_size
    elif media == MessageMediaType.DOCUMENT and message.document:
        return message.document.file_size
    elif media == MessageMediaType.AUDIO and message.audio:
        return message.audio.file_size
    elif media == MessageMediaType.VOICE and message.voice:
        return message.voice.file_size
    elif media == MessageMediaType.VIDEO_NOTE and message.video_note:
        return message.video_note.file_size
    elif media == MessageMediaType.STICKER and message.sticker:
        return message.sticker.file_size
    elif media == MessageMediaType.ANIMATION and message.animation:
        return message.animation.file_size
    return 0


# ─── keyboards ────────────────────────────────────────────────────────

def kb_main():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📖 How It Works", callback_data="how", style="success"),
            InlineKeyboardButton("💻 Commands", callback_data="help", style="primary"),
        ],
        [
            InlineKeyboardButton("👨‍💻 Contact Owner", url="https://t.me/letmesolo_her", style="danger"),
        ],
    ])

def kb_help():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔙 Back to Menu", callback_data="start", style="success"),
            InlineKeyboardButton("👨‍💻 Owner", callback_data="owner", style="danger"),
        ],
    ])

def kb_owner():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Chat with Owner", url="https://t.me/letmesolo_her", style="primary")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="start", style="success")],
    ])

def kb_how():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="start", style="success")],
    ])


# ─── message templates ───────────────────────────────────────────────

MSG_START = """<blockquote>🚀 <b>Welcome to Content Bypass Bot!</b></blockquote>

Yo! I'm the most powerful content saver bot on Telegram.

Got a channel with <b>Restrict Saving Content</b> turned on? \
Drop the post link here and I'll save it for you — clean, no watermarks, \
fully saveable. Just like that.

<blockquote>⚡ <b>What I can grab for you:</b>
● Photos &amp; Videos (up to 20 MB)
● Audio, Voice Notes, Stickers
● Documents &amp; Files
● Plain text posts — everything</blockquote>

<blockquote>👨‍💻 <b>Developed by</b> <a href="https://t.me/letmesolo_her">@letmesolo_her</a></blockquote>"""

MSG_HOW = """<blockquote>⚙️ <b>How Does It Actually Work?</b></blockquote>

Most bots fail here because they use the <i>Bot API</i> which respects Telegram's \
<code>protect_content</code> flag. I don't.

<b>Here's what happens under the hood:</b>

<blockquote>1️⃣ You send a <code>t.me/...</code> link
2️⃣ A hidden MTProto userbot fetches the raw message directly from Telegram's servers
3️⃣ The file gets downloaded using parallel chunk downloads — up to 8x faster
4️⃣ I re-upload it fresh to your chat — no restrictions attached whatsoever</blockquote>

<blockquote>📎 <b>Supported Link Formats:</b>
● <code>https://t.me/username/123</code>
  → public channels &amp; groups

● <code>https://t.me/c/1234567890/123</code>
  → private channels (account must be a member)</blockquote>

<blockquote>⚠️ <b>One rule:</b> The bot will <u>never</u> auto-join a channel or group on your behalf. For private channels, the account must already be a member.</blockquote>"""

MSG_HELP = """<blockquote>📋 <b>Command Reference</b></blockquote>

<b>● /start</b>
<code>   Opens the main menu.</code>

<b>● /help</b>
<code>   Shows this command list.</code>

<b>● /owner</b>
<code>   Info about the developer.</code>

<blockquote>📌 <b>How to use:</b>
No command needed for downloading!
Just paste any <code>t.me/...</code> link directly in chat and I'll handle the rest automatically.</blockquote>"""

MSG_OWNER = """<blockquote>👤 <b>Developer Info</b></blockquote>

<b>Name   ●</b>  Solo
<b>Handle ●</b>  <a href="https://t.me/letmesolo_her">@letmesolo_her</a>
<b>Role   ●</b>  Building things that shouldn't exist 😄

<blockquote>💬 Slide into DMs if the bot is broken, or if you just wanna chat.
I don't bite — unless the code does first.</blockquote>"""

# ─── photo paths ──────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PHOTO_START = os.path.join(BASE_DIR, "download (7).jpg")
PHOTO_HELP = os.path.join(BASE_DIR, "download (8).jpg")
PHOTO_OWNER = os.path.join(BASE_DIR, "download (9).jpg")

# In-memory cache for uploaded Telegram file IDs
_photo_cache = {
    "start": None,
    "help": None,
    "owner": None,
}


# ─── command handlers ─────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info(f"/start from {update.effective_user.id}")
    file_id = _photo_cache.get("start")
    if file_id:
        await update.message.reply_photo(
            photo=file_id,
            caption=MSG_START,
            parse_mode=TgParseMode.HTML,
            reply_markup=kb_main()
        )
    else:
        with open(PHOTO_START, "rb") as photo:
            sent = await update.message.reply_photo(
                photo=photo,
                caption=MSG_START,
                parse_mode=TgParseMode.HTML,
                reply_markup=kb_main()
            )
            if sent and sent.photo:
                _photo_cache["start"] = sent.photo[-1].file_id

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info(f"/help from {update.effective_user.id}")
    file_id = _photo_cache.get("help")
    if file_id:
        await update.message.reply_photo(
            photo=file_id,
            caption=MSG_HELP,
            parse_mode=TgParseMode.HTML,
            reply_markup=kb_help()
        )
    else:
        with open(PHOTO_HELP, "rb") as photo:
            sent = await update.message.reply_photo(
                photo=photo,
                caption=MSG_HELP,
                parse_mode=TgParseMode.HTML,
                reply_markup=kb_help()
            )
            if sent and sent.photo:
                _photo_cache["help"] = sent.photo[-1].file_id

async def cmd_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log.info(f"/owner from {update.effective_user.id}")
    file_id = _photo_cache.get("owner")
    if file_id:
        await update.message.reply_photo(
            photo=file_id,
            caption=MSG_OWNER,
            parse_mode=TgParseMode.HTML,
            reply_markup=kb_owner()
        )
    else:
        with open(PHOTO_OWNER, "rb") as photo:
            sent = await update.message.reply_photo(
                photo=photo,
                caption=MSG_OWNER,
                parse_mode=TgParseMode.HTML,
                reply_markup=kb_owner()
            )
            if sent and sent.photo:
                _photo_cache["owner"] = sent.photo[-1].file_id

async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t0 = time.time()
    msg = await update.message.reply_text("🏓 <b>Pinging...</b>", parse_mode=TgParseMode.HTML)
    elapsed = (time.time() - t0) * 1000
    await msg.edit_text(
        f"🏓 <b>Pong!</b>\n\n"
        f"⚡ <b>Latency:</b> <code>{elapsed:.0f} ms</code>",
        parse_mode=TgParseMode.HTML
    )

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cb = update.callback_query
    await cb.answer()
    d = cb.data
    log.info(f"Callback '{d}' from {cb.from_user.id}")

    if d == "start":
        file_id = _photo_cache.get("start")
        if file_id:
            await cb.message.edit_media(
                media=InputMediaPhoto(
                    media=file_id,
                    caption=MSG_START,
                    parse_mode=TgParseMode.HTML
                ),
                reply_markup=kb_main()
            )
        else:
            with open(PHOTO_START, "rb") as photo:
                sent = await cb.message.edit_media(
                    media=InputMediaPhoto(
                        media=photo,
                        caption=MSG_START,
                        parse_mode=TgParseMode.HTML
                    ),
                    reply_markup=kb_main()
                )
                if sent and sent.photo:
                    _photo_cache["start"] = sent.photo[-1].file_id
    elif d == "how":
        file_id = _photo_cache.get("start")
        if file_id:
            await cb.message.edit_media(
                media=InputMediaPhoto(
                    media=file_id,
                    caption=MSG_HOW,
                    parse_mode=TgParseMode.HTML
                ),
                reply_markup=kb_how()
            )
        else:
            with open(PHOTO_START, "rb") as photo:
                sent = await cb.message.edit_media(
                    media=InputMediaPhoto(
                        media=photo,
                        caption=MSG_HOW,
                        parse_mode=TgParseMode.HTML
                    ),
                    reply_markup=kb_how()
                )
                if sent and sent.photo:
                    _photo_cache["start"] = sent.photo[-1].file_id
    elif d == "help":
        file_id = _photo_cache.get("help")
        if file_id:
            await cb.message.edit_media(
                media=InputMediaPhoto(
                    media=file_id,
                    caption=MSG_HELP,
                    parse_mode=TgParseMode.HTML
                ),
                reply_markup=kb_help()
            )
        else:
            with open(PHOTO_HELP, "rb") as photo:
                sent = await cb.message.edit_media(
                    media=InputMediaPhoto(
                        media=photo,
                        caption=MSG_HELP,
                        parse_mode=TgParseMode.HTML
                    ),
                    reply_markup=kb_help()
                )
                if sent and sent.photo:
                    _photo_cache["help"] = sent.photo[-1].file_id
    elif d == "owner":
        file_id = _photo_cache.get("owner")
        if file_id:
            await cb.message.edit_media(
                media=InputMediaPhoto(
                    media=file_id,
                    caption=MSG_OWNER,
                    parse_mode=TgParseMode.HTML
                ),
                reply_markup=kb_owner()
            )
        else:
            with open(PHOTO_OWNER, "rb") as photo:
                sent = await cb.message.edit_media(
                    media=InputMediaPhoto(
                        media=photo,
                        caption=MSG_OWNER,
                        parse_mode=TgParseMode.HTML
                    ),
                    reply_markup=kb_owner()
                )
                if sent and sent.photo:
                    _photo_cache["owner"] = sent.photo[-1].file_id


# ─── main bypass handler ─────────────────────────────────────────────

async def on_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    text = msg.text or ""
    log.info(f"Text from {msg.from_user.id}: {text}")

    chat_id, msg_id = parse_link(text)
    if not chat_id:
        if msg.chat.type == "private":
            await msg.reply_text(
                "<blockquote>❓ <b>Invalid or unsupported link.</b>\n\n"
                "Send a link in one of these formats:\n"
                "● <code>https://t.me/username/123</code>\n"
                "● <code>https://t.me/c/1234567890/123</code></blockquote>",
                parse_mode=TgParseMode.HTML,
            )
        return

    status = await msg.reply_text(
        "⚡ <b>Grabbing...</b>", parse_mode=TgParseMode.HTML
    )
    t0 = time.time()

    try:
        target = await fetch.get_messages(chat_id, msg_id)

        if not target or target.empty:
            await status.edit_text(
                "<blockquote>❌ <b>Message not found.</b>\n"
                "Probably deleted or wrong link.</blockquote>",
                parse_mode=TgParseMode.HTML,
            )
            return

        media_type = target.media
        user_id = msg.from_user.id

        # text only
        if media_type is None:
            content = target.text or target.caption or "(empty)"
            elapsed = time.time() - t0
            await status.edit_text(
                f"<blockquote>📄 <b>Content:</b>\n\n{content}</blockquote>\n"
                f"<i>⚡ {elapsed:.1f}s</i>",
                parse_mode=TgParseMode.HTML,
            )
            return

        # size check
        file_size = get_file_size(target)
        if file_size > MAX_SIZE:
            mb = round(file_size / 1024 / 1024, 1)
            await status.edit_text(
                f"<blockquote>⚠️ <b>File too large!</b>\n\n"
                f"Size  ● <b>{mb} MB</b>\n"
                f"Limit ● <b>20 MB</b></blockquote>",
                parse_mode=TgParseMode.HTML,
            )
            return

        cache_key = f"{chat_id}:{msg_id}"

        # ── INSTANT: cached file_id ──
        if cache_key in _cache:
            cached_fid, cached_type, cached_size = _cache[cache_key]
            log.info(f"CACHE HIT {cache_key}")

            send = {
                "PHOTO": context.bot.send_photo,
                "VIDEO": context.bot.send_video,
                "DOCUMENT": context.bot.send_document,
                "AUDIO": context.bot.send_audio,
                "VOICE": context.bot.send_voice,
                "VIDEO_NOTE": context.bot.send_video_note,
                "STICKER": context.bot.send_sticker,
                "ANIMATION": context.bot.send_animation,
            }.get(cached_type)

            if send:
                kw = {"reply_to_message_id": msg.message_id}
                if cached_type not in ("STICKER", "VIDEO_NOTE"):
                    kw["caption"] = "<i>— bypass bot | dev: @letmesolo_her</i>"
                    kw["parse_mode"] = TgParseMode.HTML
                await send(msg.chat_id, cached_fid, **kw)
                elapsed = time.time() - t0
                await status.edit_text(
                    f"<blockquote>✅ <b>Done!</b>\n\n"
                    f"⚡ <b>{elapsed:.1f}s</b> (cached)\n"
                    f"📎 {cached_type.lower()} • {fmt_size(cached_size)}</blockquote>",
                    parse_mode=TgParseMode.HTML,
                )
                log.info(f"CACHED {cached_type} to {user_id} in {elapsed:.1f}s")
                return

        # ── TURBO DOWNLOAD: parallel chunks ──
        log.info(f"TURBO downloading {media_type.name} from {chat_id} msg {msg_id}")
        last_edit = [0]
        dl_start = time.time()

        async def dl_progress(current, total):
            now = time.time()
            if now - last_edit[0] < 2:
                return
            last_edit[0] = now
            pct = int(current * 100 / total) if total else 0
            try:
                await status.edit_text(
                    f"📥 <b>Downloading...</b>\n\n"
                    f"{make_bar(pct)} {pct}% • {fmt_size(current)}/{fmt_size(total)}",
                    parse_mode=TgParseMode.HTML,
                )
            except Exception:
                pass

        await status.edit_text(
            f"📥 <b>Downloading {media_type.name.lower()}...</b>\n"
            f"{make_bar(0)} 0%",
            parse_mode=TgParseMode.HTML,
        )

        buf = await turbo_download(fetch, target, progress=dl_progress)
        dl_time = time.time() - dl_start
        log.info(f"Downloaded in {dl_time:.1f}s")

        caption = target.caption or ""
        if caption:
            caption += "\n\n"
        caption += "<i>— bypass bot | dev: @letmesolo_her</i>"

        # ── UPLOAD via bot api ──
        chat = msg.chat_id
        sent = None
        if media_type == MessageMediaType.PHOTO:
            sent = await context.bot.send_photo(
                chat, buf, caption=caption, parse_mode=TgParseMode.HTML,
                reply_to_message_id=msg.message_id,
            )
        elif media_type == MessageMediaType.VIDEO:
            v = target.video
            sent = await context.bot.send_video(
                chat, buf, caption=caption, parse_mode=TgParseMode.HTML,
                reply_to_message_id=msg.message_id,
                duration=v.duration, width=v.width, height=v.height,
            )
        elif media_type == MessageMediaType.AUDIO:
            a = target.audio
            sent = await context.bot.send_audio(
                chat, buf, caption=caption, parse_mode=TgParseMode.HTML,
                reply_to_message_id=msg.message_id,
                duration=a.duration, performer=a.performer or "", title=a.title or "",
            )
        elif media_type == MessageMediaType.VOICE:
            sent = await context.bot.send_voice(
                chat, buf, caption=caption, parse_mode=TgParseMode.HTML,
                reply_to_message_id=msg.message_id,
            )
        elif media_type == MessageMediaType.VIDEO_NOTE:
            sent = await context.bot.send_video_note(
                chat, buf, reply_to_message_id=msg.message_id,
            )
        elif media_type == MessageMediaType.STICKER:
            sent = await context.bot.send_sticker(
                chat, buf, reply_to_message_id=msg.message_id,
            )
        elif media_type == MessageMediaType.ANIMATION:
            sent = await context.bot.send_animation(
                chat, buf, caption=caption, parse_mode=TgParseMode.HTML,
                reply_to_message_id=msg.message_id,
            )
        else:
            fname = (target.document.file_name if target.document else None) or "file"
            sent = await context.bot.send_document(
                chat, buf, caption=caption, parse_mode=TgParseMode.HTML,
                reply_to_message_id=msg.message_id, filename=fname,
            )

        # cache for instant repeat
        if sent:
            fid = None
            if sent.photo: fid = sent.photo[-1].file_id
            elif sent.video: fid = sent.video.file_id
            elif sent.document: fid = sent.document.file_id
            elif sent.audio: fid = sent.audio.file_id
            elif sent.voice: fid = sent.voice.file_id
            elif sent.video_note: fid = sent.video_note.file_id
            elif sent.sticker: fid = sent.sticker.file_id
            elif sent.animation: fid = sent.animation.file_id
            if fid:
                _cache[cache_key] = (fid, media_type.name, file_size)
                log.info(f"Cached {cache_key}")

        elapsed = time.time() - t0
        await status.edit_text(
            f"<blockquote>✅ <b>Done!</b>\n\n"
            f"⚡ <b>{elapsed:.1f}s</b> • {fmt_size(file_size)}\n"
            f"📎 {media_type.name.lower()}</blockquote>\n"
            f"<i>— bypass bot | dev: @letmesolo_her</i>",
            parse_mode=TgParseMode.HTML,
        )
        log.info(f"Sent {media_type.name} to {user_id} in {elapsed:.1f}s")

    except ChannelPrivate:
        await status.edit_text(
            "<blockquote>🔒 <b>Private Channel / Group</b>\n\n"
            "The userbot is not a member of that channel.\n"
            "⚠️ The bot will <u>never auto-join</u> channels.</blockquote>",
            parse_mode=TgParseMode.HTML,
        )
    except (PeerIdInvalid, UsernameInvalid, UsernameNotOccupied):
        await status.edit_text(
            "<blockquote>❌ <b>Channel/Group not found.</b>\n"
            "Double-check the link.</blockquote>",
            parse_mode=TgParseMode.HTML,
        )
    except MessageIdInvalid:
        await status.edit_text(
            "<blockquote>❌ <b>Message doesn't exist.</b>\n"
            "Probably deleted.</blockquote>",
            parse_mode=TgParseMode.HTML,
        )
    except FloodWait as e:
        log.warning(f"FloodWait: {e.value}s")
        await status.edit_text(
            f"<blockquote>⏳ <b>Rate limited.</b>\n"
            f"Try again in <b>{e.value}s</b>.</blockquote>",
            parse_mode=TgParseMode.HTML,
        )
    except Exception as e:
        log.exception("bypass error")
        await status.edit_text(
            f"<blockquote>❌ <b>Error.</b>\n\n"
            f"<code>{type(e).__name__}: {e}</code></blockquote>",
            parse_mode=TgParseMode.HTML,
        )


# ─── keep alive ───────────────────────────────────────────────────────

async def keep_alive_loop():
    while True:
        await asyncio.sleep(300)
        try:
            await fetch.get_me()
        except Exception:
            pass


async def post_init(application):
    # pre-warm the home DC media session only (safe and fast)
    # other DCs will be warmed lazily on first download
    try:
        my_dc = await fetch.storage.dc_id()
        await _ensure_session(fetch, my_dc)
        log.info(f"Home DC{my_dc} media session pre-warmed")
    except Exception as e:
        log.warning(f"Pre-warm failed: {e}")
    asyncio.create_task(keep_alive_loop())
    log.info("Keep-alive started")


def main():
    fetch.start()
    fu = fetch.get_me()
    log.info(f"Userbot ready: {fu.first_name} (id={fu.id})")

    from telegram.request import HTTPXRequest
    request = HTTPXRequest(
        read_timeout=120,
        write_timeout=120,
        connect_timeout=30,
        pool_timeout=30,
    )
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .request(request)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("owner", cmd_owner))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(
        MessageHandler(
            filters.Regex(r"(?i)t\.me/(?:c/\d+|s/[a-zA-Z0-9_]{4,}|[a-zA-Z0-9_]{4,})/\d+") &
            (filters.ChatType.PRIVATE | filters.ChatType.GROUPS),
            on_link
        )
    )

    log.info("Bot started. Send /start to test.")
    app.run_polling(drop_pending_updates=True)
    fetch.stop()


if __name__ == "__main__":
    main()
