# bot.py
import os
import asyncio
import logging
import re
from html import escape
from datetime import datetime
import asyncpg
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler
)

# -------- Logging ----------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# -------- Config from ENV (required on Render) ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")  # ضع توكن البوت في متغير بيئة BOT_TOKEN
DATABASE_URL = os.environ.get("DATABASE_URL")  # مثال: postgresql://user:pass@host:5432/dbname
SECRET_TOKEN = os.environ.get("WEBHOOK_SECRET_TOKEN", "my_secret_123")
WEBHOOK_BASE = os.environ.get("WEBHOOK_URL", "https://abdellahb-2.onrender.com")
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH", "/webhook")  # Usually /webhook
WEBHOOK_URL = WEBHOOK_BASE.rstrip("/") + WEBHOOK_PATH
PORT = int(os.environ.get("PORT", "8443"))
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID")) if os.environ.get("ADMIN_USER_ID") else None
KEEPALIVE_SECONDS = int(os.environ.get("KEEPALIVE_SECONDS", "120"))

# Safety check
if not BOT_TOKEN or not DATABASE_URL:
    logger.critical("BOT_TOKEN and DATABASE_URL environment variables are required.")
    raise SystemExit("Set BOT_TOKEN and DATABASE_URL in environment.")

# -------- Constants & Defaults ----------
BANNED_WORDS = {
    "كلب", "حمار", "قحب", "زبي", "خرا", "بول",
    "ولد الحرام", "ولد القحبة", "يا قحبة", "نيك", "منيك",
    "مخنث", "قحبة", "حقير", "قذر"
}

AUTO_REPLIES = {
    "السلام عليكم": "وعليكم السلام",
    "تصبح على خير": "وأنت من أهله",
    "سلام": "وعليكم السلام 🖐"
}

WELCOME_MESSAGES = {
    "ar": """
أهلا وسهلا بك في مجتمعنا الراقي للإعلام الآلي  
عليك الالتزام بالقوانين المعلنة في المجموعة.
🫧 𝓣𝓸𝓾𝓴𝓪 ꨄ︎
""",
    "en": """
Welcome to our refined Computer Science community.
Please read and follow the group rules.
🫧 𝓣𝓸𝓾𝓴𝓪 ꨄ︎
"""
}

# -------- Database (asyncpg pool) ----------
db_pool: asyncpg.pool.Pool = None

async def init_db_pool():
    global db_pool
    if db_pool:
        return
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
    logger.info("✅ PostgreSQL connection pool created")
    # Initialize tables if not exist
    async with db_pool.acquire() as conn:
        # create tables
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS members (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            chat_id TEXT NOT NULL,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            joined_date TIMESTAMP WITH TIME ZONE DEFAULT now(),
            last_seen TIMESTAMP WITH TIME ZONE DEFAULT now(),
            UNIQUE(user_id, chat_id)
        );
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS warnings (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            chat_id TEXT NOT NULL,
            reason TEXT,
            warning_date TIMESTAMP WITH TIME ZONE DEFAULT now(),
            admin_id BIGINT
        );
        """)
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id SERIAL PRIMARY KEY,
            chat_id TEXT UNIQUE NOT NULL,
            max_warns INTEGER DEFAULT 3,
            delete_links BOOLEAN DEFAULT TRUE,
            youtube_channel TEXT DEFAULT '@Mik_emm',
            enable_warns BOOLEAN DEFAULT TRUE,
            send_sync_notice BOOLEAN DEFAULT FALSE
        );
        """)
        # Indexes
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_members_chat_lastseen ON members(chat_id, last_seen DESC);")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_warnings_chat_user ON warnings(chat_id, user_id);")
    logger.info("✅ Database tables ensured")

# DB helper functions
async def add_member_db(user_id, chat_id, username, first_name, last_name):
    chat_id = str(chat_id)
    async with db_pool.acquire() as conn:
        await conn.execute("""
        INSERT INTO members (user_id, chat_id, username, first_name, last_name, last_seen)
        VALUES($1,$2,$3,$4,$5, now())
        ON CONFLICT (user_id, chat_id) DO UPDATE
        SET username = EXCLUDED.username,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name,
            last_seen = now()
        """, user_id, chat_id, username, first_name, last_name)
    logger.debug(f"Member saved {user_id} in {chat_id}")

async def get_members_db(chat_id, limit=5000):
    chat_id = str(chat_id)
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
        SELECT user_id, username, first_name, last_name
        FROM members
        WHERE chat_id = $1
        ORDER BY last_seen DESC
        LIMIT $2
        """, chat_id, limit)
    return rows

async def add_warning_db(user_id, chat_id, reason, admin_id=None):
    chat_id = str(chat_id)
    async with db_pool.acquire() as conn:
        await conn.execute("""
        INSERT INTO warnings (user_id, chat_id, reason, admin_id)
        VALUES($1,$2,$3,$4)
        """, user_id, chat_id, reason, admin_id)
    logger.info(f"Warning added for {user_id} in chat {chat_id}")

async def get_warning_count_db(user_id, chat_id):
    chat_id = str(chat_id)
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("""
        SELECT COUNT(*) AS cnt FROM warnings WHERE user_id = $1 AND chat_id = $2
        """, user_id, chat_id)
    return row['cnt'] if row else 0

async def get_warning_reasons_db(user_id, chat_id):
    chat_id = str(chat_id)
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
        SELECT reason, warning_date, admin_id FROM warnings
        WHERE user_id = $1 AND chat_id = $2
        ORDER BY warning_date DESC
        """, user_id, chat_id)
    return rows

async def reset_warnings_db(user_id, chat_id):
    chat_id = str(chat_id)
    async with db_pool.acquire() as conn:
        res = await conn.execute("DELETE FROM warnings WHERE user_id=$1 AND chat_id=$2", user_id, chat_id)
    return True

async def get_chat_settings_db(chat_id):
    chat_id = str(chat_id)
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT max_warns, delete_links, youtube_channel, enable_warns, send_sync_notice FROM settings WHERE chat_id=$1", chat_id)
    if row:
        return {
            "max_warns": row["max_warns"],
            "delete_links": row["delete_links"],
            "youtube_channel": row["youtube_channel"],
            "enable_warns": row["enable_warns"],
            "send_sync_notice": row["send_sync_notice"]
        }
    return {
        "max_warns": 3,
        "delete_links": True,
        "youtube_channel": "@Mik_emm",
        "enable_warns": True,
        "send_sync_notice": False
    }

async def save_chat_settings_db(chat_id, max_warns=None, delete_links=None, youtube_channel=None, enable_warns=None, send_sync_notice=None):
    chat_id = str(chat_id)
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT chat_id FROM settings WHERE chat_id=$1", chat_id)
        if row:
            updates = []
            params = []
            if max_warns is not None:
                updates.append("max_warns=$" + str(len(params)+2)); params.append(max_warns)
            if delete_links is not None:
                updates.append("delete_links=$" + str(len(params)+2)); params.append(delete_links)
            if youtube_channel is not None:
                updates.append("youtube_channel=$" + str(len(params)+2)); params.append(youtube_channel)
            if enable_warns is not None:
                updates.append("enable_warns=$" + str(len(params)+2)); params.append(enable_warns)
            if send_sync_notice is not None:
                updates.append("send_sync_notice=$" + str(len(params)+2)); params.append(send_sync_notice)
            if updates:
                query = "UPDATE settings SET " + ", ".join(updates) + " WHERE chat_id=$1"
                await conn.execute(query, chat_id, *params)
        else:
            await conn.execute("""
            INSERT INTO settings (chat_id, max_warns, delete_links, youtube_channel, enable_warns, send_sync_notice)
            VALUES($1, $2, $3, $4, COALESCE($5, true), COALESCE($6, false))
            """, chat_id, max_warns or 3, delete_links if delete_links is not None else True, youtube_channel or "@Mik_emm", enable_warns, send_sync_notice)
    logger.info(f"Settings saved for {chat_id}")

# -------- Utilities ----------
def normalize_text_for_check(s: str):
    # remove punctuation, keep words (works for Arabic too)
    return re.sub(r"[^\w\s\u0600-\u06FF]", " ", s.lower())

def contains_banned_word(text: str) -> bool:
    if not text:
        return False
    txt = normalize_text_for_check(text)
    for w in BANNED_WORDS:
        w_clean = w.strip().lower()
        # use word boundaries (lookaround) to avoid partial matches
        if re.search(rf'(?<!\w){re.escape(w_clean)}(?!\w)', txt, flags=re.UNICODE):
            return True
    return False

def make_mention_html(user_id: int, name: str):
    return f'<a href="tg://user?id={user_id}">{escape(name)}</a>'

def chunk_by_chars(items, max_chars=3500):
    cur = []
    length = 0
    for it in items:
        l = len(it) + 1
        if length + l > max_chars and cur:
            yield cur
            cur = []
            length = 0
        cur.append(it)
        length += l
    if cur:
        yield cur

# -------- Telegram Application ----------
application: Application = Application.builder().token(BOT_TOKEN).build()

# admin check
async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return True
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        return member.status in ("administrator", "creator")
    except Exception as e:
        logger.exception("is_admin check failed")
        return False

# ----- Core handlers -----
@application.command("start")  # works as decorator alias with PTB v20; else register below
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """👋 مرحبا بك في بوت إدارة المجموعة المتقدم ⚙️

أوامر: /help
"""
    await update.message.reply_text(text)

@application.command("help")
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
أوامر المشرفين:
/admins - عرض الإداريين
/tagall - منشن لجميع الأعضاء المسجلين في قاعدة البيانات
/tagadmins - منشن للمشرفين
/sync - مزامنة المشرفين (يجمع المشرفين فقط)
 /warn (بالرد) - تحذير عضو
/unwarn (بالرد) - إزالة تحذيرات عضو
/warns (بالرد) - عرض تحذيرات عضو
/setwarns [عدد] - ضبط الحد للطرد
/delete_links on/off - تفعيل/تعطيل حذف الروابط
/warnings on/off - تفعيل/تعطيل تسجيل التحذيرات
/ping - حالة البوت
"""
    await update.message.reply_text(help_text)

@application.command("admins")
async def admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        admins_list = await context.bot.get_chat_administrators(update.effective_chat.id)
        lines = ["👮‍♂️ قائمة الإداريين:"]
        for a in admins_list:
            if a.user.is_bot:
                continue
            name = a.user.full_name or a.user.username or str(a.user.id)
            lines.append(f"• {escape(name)} ({'منشئ' if a.status=='creator' else 'مشرف'})")
        await update.message.reply_text("\n".join(lines))
    except Exception as e:
        logger.exception("Failed to fetch admins")
        await update.message.reply_text("خطأ في جلب قائمة المشرفين.")

@application.command("sync")
async def sync_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Save admins to DB and optionally send a notice (controlled by setting)
    chat_id = update.effective_chat.id
    await update.message.reply_text("⏳ جاري مزامنة المشرفين مع قاعدة البيانات...")
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        saved = 0
        for a in admins:
            u = a.user
            if u.is_bot:
                continue
            await add_member_db(u.id, str(chat_id), u.username, u.first_name, u.last_name)
            saved += 1
        settings = await get_chat_settings_db(str(chat_id))
        if settings.get("send_sync_notice"):
            await context.bot.send_message(chat_id=chat_id, text="🔔 تم مزامنة المشرفين. لإكمال قائمة الأعضاء، يرجى تفاعل الأعضاء.")
        await update.message.reply_text(f"✅ تم حفظ {saved} مشرف في قاعدة البيانات.")
    except Exception as e:
        logger.exception("sync failed")
        await update.message.reply_text("❌ فشل في المزامنة.")

@application.command("tagadmins")
async def tagadmins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        admins = await context.bot.get_chat_administrators(update.effective_chat.id)
        mentions = []
        for a in admins:
            u = a.user
            if u.is_bot:
                continue
            name = u.first_name or u.username or str(u.id)
            mentions.append(make_mention_html(u.id, name))
        if not mentions:
            await update.message.reply_text("لا يوجد مشرفين للمنشن.")
            return
        for chunk in chunk_by_chars(mentions, max_chars=3500):
            await update.message.reply_html(" ".join(chunk))
            await asyncio.sleep(1)
    except Exception:
        logger.exception("tagadmins failed")
        await update.message.reply_text("خطأ أثناء منشن المشرفين.")

@application.command("tagall")
async def tagall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = update.effective_chat.id
        await update.message.reply_text("⏳ جاري جلب الأعضاء من قاعدة البيانات...")
        members = await get_members_db(str(chat_id), limit=5000)
        if not members:
            await update.message.reply_text("📭 لا يوجد أعضاء مخزونين. تُخزَّن الأعضاء عند تفاعلهم.")
            return
        mentions = []
        for row in members:
            uid = row["user_id"]
            name = row["username"] or f"{row['first_name'] or ''} {row['last_name'] or ''}".strip() or f"user_{uid}"
            mentions.append(make_mention_html(uid, name))
        total = 0
        for chunk in chunk_by_chars(mentions, max_chars=3500):
            await update.message.reply_html(" ".join(chunk))
            total += len(chunk)
            await asyncio.sleep(1)
        await update.message.reply_text(f"✅ تم عمل منشن لـ {total} عضو.")
    except Exception:
        logger.exception("tagall failed")
        await update.message.reply_text("خطأ أثناء تنفيذ التاق. حاول مجدداً.")

# Warn related commands
@application.command("warn")
async def warn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ الرجاء الرد على رسالة العضو للتحذير.")
        return
    # only admins can warn
    if not await is_admin(update, context):
        await update.message.reply_text("❌ هذا الأمر للمشرفين فقط.")
        return
    target = update.message.reply_to_message.from_user
    reason = " ".join(context.args) if context.args else "بدون سبب"
    chat_id = update.effective_chat.id
    settings = await get_chat_settings_db(str(chat_id))
    if not settings.get("enable_warns", True):
        await update.message.reply_text("ℹ️ نظام التحذيرات معطل في هذه المجموعة.")
        return
    await add_warning_db(target.id, str(chat_id), reason, update.effective_user.id)
    cnt = await get_warning_count_db(target.id, str(chat_id))
    max_warns = settings.get("max_warns", 3)
    if cnt >= max_warns:
        # try to ban (ensure bot has perms)
        try:
            bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
            if bot_member.status in ("administrator", "creator") and getattr(bot_member, "can_restrict_members", True):
                await context.bot.ban_chat_member(chat_id, target.id)
                await context.bot.send_message(chat_id=chat_id, text=f"🚷 {make_mention_html(target.id, target.first_name)} تم طرده لتجاوزه حد التحذيرات ({cnt}/{max_warns}).", parse_mode="HTML")
            else:
                await update.message.reply_text("⚠️ لا أملك صلاحيات الحظر لإتمام الطرد.")
        except Exception:
            logger.exception("ban failed")
            await update.message.reply_text("خطأ أثناء محاولة الطرد.")
    else:
        await update.message.reply_text(f"⚠️ تم تحذير {target.first_name} ({cnt}/{max_warns}).\nالسبب: {reason}")

@application.command("unwarn")
async def unwarn_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ الرجاء الرد على رسالة العضو لمسح التحذيرات.")
        return
    if not await is_admin(update, context):
        await update.message.reply_text("❌ هذا الأمر للمشرفين فقط.")
        return
    target = update.message.reply_to_message.from_user
    await reset_warnings_db(target.id, str(update.effective_chat.id))
    await update.message.reply_text(f"✅ تم إزالة جميع التحذيرات لـ {target.first_name}.")

@application.command("warns")
async def warns_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ الرجاء الرد على رسالة العضو لعرض تحذيراته.")
        return
    target = update.message.reply_to_message.from_user
    rows = await get_warning_reasons_db(target.id, str(update.effective_chat.id))
    cnt = await get_warning_count_db(target.id, str(update.effective_chat.id))
    max_warns = (await get_chat_settings_db(str(update.effective_chat.id))).get("max_warns", 3)
    if cnt == 0:
        await update.message.reply_text(f"ℹ️ لا يوجد تحذيرات لـ {target.first_name}.")
        return
    msg = f"⚠️ تحذيرات {target.first_name}: {cnt}/{max_warns}\n"
    for r in rows[:10]:
        msg += f"• {r['reason']} — {r['warning_date']:%Y-%m-%d %H:%M}\n"
    await update.message.reply_text(msg)

@application.command("setwarns")
async def setwarns_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.message.reply_text("❌ هذا الأمر للمشرفين فقط.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("⚠️ الصيغة: /setwarns [عدد]")
        return
    n = int(context.args[0])
    if n < 1 or n > 40:
        await update.message.reply_text("⚠️ يجب أن يكون بين 1 و 40.")
        return
    await save_chat_settings_db(str(update.effective_chat.id), max_warns=n)
    await update.message.reply_text(f"✅ تم ضبط عدد التحذيرات القصوى إلى {n}.")

@application.command("delete_links")
async def delete_links_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.message.reply_text("❌ هذا الأمر للمشرفين فقط.")
        return
    if not context.args or context.args[0].lower() not in ("on", "off"):
        await update.message.reply_text("⚠️ الصيغة: /delete_links on/off")
        return
    val = context.args[0].lower() == "on"
    await save_chat_settings_db(str(update.effective_chat.id), delete_links=val)
    await update.message.reply_text(f"✅ تم {'تفعيل' if val else 'تعطيل'} حذف الروابط تلقائياً.")

@application.command("warnings")
async def toggle_warnings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.message.reply_text("❌ هذا الأمر للمشرفين فقط.")
        return
    if not context.args or context.args[0].lower() not in ("on", "off"):
        await update.message.reply_text("⚠️ الصيغة: /warnings on/off")
        return
    val = context.args[0].lower() == "on"
    await save_chat_settings_db(str(update.effective_chat.id), enable_warns=val)
    await update.message.reply_text(f"✅ تم {'تفعيل' if val else 'تعطيل'} نظام التحذيرات في هذه المجموعة.")

@application.command("ping")
async def ping_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 البوت يعمل بشكل طبيعي! ✅")

# Message handler
@application.handler()
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # guard for non-text
    if not update.message or not update.message.text:
        return
    if update.effective_chat.type == "private":
        # optional: handle private commands or replies
        return

    text = update.message.text
    user = update.message.from_user
    chat_id = update.effective_chat.id

    # Save user to DB on any interaction
    try:
        await add_member_db(user.id, str(chat_id), user.username, user.first_name, user.last_name)
    except Exception:
        logger.exception("Failed saving member")

    # check links
    settings = await get_chat_settings_db(str(chat_id))
    if settings.get("delete_links", True) and re.search(r'(https?://\S+|www\.\S+|t\.me/)', text, flags=re.IGNORECASE):
        # allow admins
        if not await is_admin(update, context):
            # try delete
            try:
                bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
                if bot_member.status in ("administrator", "creator") and getattr(bot_member, "can_delete_messages", True):
                    await update.message.delete()
                await context.bot.send_message(chat_id=chat_id, text=f"🚫 {make_mention_html(user.id, user.first_name)} الروابط غير مسموح بها!", parse_mode="HTML")
            except Exception:
                logger.exception("Failed delete link or notify")
            return

    # banned words
    if contains_banned_word(text):
        # if admin, ignore
        if await is_admin(update, context):
            return
        # Add warning if enabled
        if settings.get("enable_warns", True):
            try:
                await add_warning_db(user.id, str(chat_id), "كلمة مسيئة", None)
            except Exception:
                logger.exception("Failed to add warning")
        # delete message if bot has permission
        try:
            bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
            if bot_member.status in ("administrator", "creator") and getattr(bot_member, "can_delete_messages", True):
                await update.message.delete()
        except Exception:
            logger.exception("Failed to delete bad message")
        # notify group
        try:
            cnt = await get_warning_count_db(user.id, str(chat_id))
            await context.bot.send_message(chat_id=chat_id, text=f"🚫 تم حذف رسالة من {make_mention_html(user.id, user.first_name)} لاحتوائها على كلمات غير لائقة. التحذير: {cnt}", parse_mode="HTML")
        except Exception:
            logger.exception("Failed to send removal notice")
        return

    # auto replies (case-insensitive)
    if text.strip().lower() in (k.lower() for k in AUTO_REPLIES.keys()):
        reply = AUTO_REPLIES.get(text.strip(), None) or AUTO_REPLIES.get(text.strip().lower())
        if reply:
            await update.message.reply_text(reply)

# Callback handler (for kick confirmation)
@application.callback_query_handler()
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    # expecting formats like kick_approve_<user_id>_<chat_id> or kick_reject_...
    parts = data.split("_")
    if len(parts) >= 3 and parts[0] == "kick":
        action = parts[1]
        try:
            user_id = int(parts[2])
            chat_id = int(parts[3]) if len(parts) > 3 else query.message.chat.id
        except:
            await query.edit_message_text("تنسيق خاطئ للزر.")
            return
        if action == "approve":
            try:
                await context.bot.ban_chat_member(chat_id, user_id)
                await query.edit_message_text("✅ تم طرد العضو بنجاح.")
            except Exception:
                logger.exception("Failed to ban via callback")
                await query.edit_message_text("❌ فشل في طرد العضو.")
        else:
            await query.edit_message_text("❌ تم رفض طلب الطرد.")

# Error handler
@application.error_handler()
async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception("Unhandled exception")

# -------- Webhook & Aiohttp app ----------
async def webhook_handler(request: web.Request):
    # validate secret token header
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if token != SECRET_TOKEN:
        return web.Response(status=403, text="Forbidden")
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return web.Response(text="OK", status=200)
    except Exception:
        logger.exception("webhook process failed")
        return web.Response(text="Error", status=500)

async def set_webhook():
    try:
        await application.bot.set_webhook(
            url=WEBHOOK_URL,
            secret_token=SECRET_TOKEN,
            drop_pending_updates=True
        )
        logger.info("Webhook set to %s", WEBHOOK_URL)
    except Exception:
        logger.exception("Failed to set webhook")

# Keepalive task (sends a DM to ADMIN_USER_ID every KEEPALIVE_SECONDS) - optional
async def keepalive_task():
    if not ADMIN_USER_ID:
        logger.info("No ADMIN_USER_ID configured for keepalive.")
        return
    while True:
        try:
            await application.bot.send_message(ADMIN_USER_ID, f"🔔 keepalive ping {datetime.utcnow().isoformat()}")
        except Exception:
            logger.exception("Keepalive ping failed")
        await asyncio.sleep(KEEPALIVE_SECONDS)

# On startup / shutdown
async def on_startup(app):
    await init_db_pool()
    await application.initialize()
    await application.start()
    # set webhook
    await set_webhook()
    # start keepalive
    if ADMIN_USER_ID:
        application.create_task(keepalive_task())
    logger.info("Bot started successfully (webhook mode)")

async def on_shutdown(app):
    try:
        await application.stop()
        await application.shutdown()
        if db_pool:
            await db_pool.close()
    except Exception:
        logger.exception("Shutdown error")
    logger.info("Bot stopped")

def main():
    # register handlers for older PTB versions if decorator not available
    # (we already used decorators; ensure handlers present)
    # setup aiohttp web app
    web_app = web.Application()
    web_app.router.add_post(WEBHOOK_PATH, webhook_handler)
    web_app.on_startup.append(on_startup)
    web_app.on_shutdown.append(on_shutdown)
    # run
    web.run_app(web_app, host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
