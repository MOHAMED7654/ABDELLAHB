import logging
import re
import os
import asyncio
import asyncpg
from datetime import datetime
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

# إعدادات اللوغ
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# بيانات البوت
TOKEN = "8124498237:AAHipIHoU3W6OzYF2RiuxZvkc7ar8FWmyas"
SECRET_TOKEN = "my_secret_123"
WEBHOOK_URL = "https://abdellahb-2.onrender.com/webhook"
PORT = int(os.environ.get('PORT', 8443))

# بيانات قاعدة البيانات PostgreSQL
DATABASE_URL = "postgresql://mybotuser:prb09Wv3eU2OhkoeOXyR5n05IBBMEvhn@dpg-d2s5g4m3jp1c738svjfg-a.frankfurt-postgres.render.com/mybotdb_mqjm"

# كلمات ممنوعة
BANNED_WORDS = {
    "كلب", "حمار", "قحب", "زبي", "خرا", "بول",
    "ولد الحرام", "ولد القحبة", "يا قحبة", "نيك", "منيك",
    "مخنث", "قحبة", "حقير", "قذر"
}

# الردود التلقائية
AUTO_REPLIES = {
    "السلام عليكم": "وعليكم السلام",
    "تصبح على خير": "وأنت من أهله",
    "سلام": "وعليكم السلام 🖐"
}

# رسائل الترحيب
WELCOME_MESSAGES = {
    "ar": """
أهلا وسهلا بك في مجتمعنا الراقي للإعلام الآلي  
عليك الالتزام بهذه الجملة من القوانين:   
1- عدم نشر الروابط دون اذن   
2- عدم التحدث في مواضيع جانبية ما عدا الدراسة و الحرص على التحدث بلباقة
3- الامتناع عن التواصل المشبوه في الخاص (بإمكانك طرح اي أسئلة في المجموعة لذلك يمنع استخدام هذه الحجة )
كما نعلمكم اننا مسؤولون فقط عما يحدث داخل المجموعة 
4-  الامتثال لقرارات المشرفين ضروري للحفاظ على النظام
ملاحظة: في حالات الضرورة يمكن التواصل مع المشرفين ( الاناث مع مالكة المجموعة و الذكور مع المشرفين الذكور)
🫧 𝓣𝓸𝓾𝓴𝓪 ꨄ︎
"""
}

# تهيئة التطبيق
application = Application.builder().token(TOKEN).build()

# اتصال قاعدة البيانات
db_pool = None

async def init_db():
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        logger.info("✅ تم الاتصال بقاعدة البيانات بنجاح")
        
        # إنشاء الجداول
        async with db_pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS members (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    chat_id TEXT NOT NULL,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, chat_id)
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS warnings (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    chat_id TEXT NOT NULL,
                    reason TEXT,
                    warning_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    admin_id BIGINT
                )
            ''')
            
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    id SERIAL PRIMARY KEY,
                    chat_id TEXT UNIQUE NOT NULL,
                    max_warns INTEGER DEFAULT 3,
                    delete_links BOOLEAN DEFAULT TRUE,
                    youtube_channel TEXT DEFAULT '@Mik_emm'
                )
            ''')
            
        logger.info("✅ تم تهيئة الجداول بنجاح")
        return True
    except Exception as e:
        logger.error(f"❌ خطأ في تهيئة قاعدة البيانات: {e}")
        return False

async def add_member(user_id, chat_id, username, first_name, last_name):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO members (user_id, chat_id, username, first_name, last_name, last_seen)
                VALUES ($1, $2, $3, $4, $5, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id, chat_id) DO UPDATE SET
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                last_seen = CURRENT_TIMESTAMP
            ''', user_id, chat_id, username, first_name, last_name)
        return True
    except Exception as e:
        logger.error(f"❌ خطأ في إضافة العضو: {e}")
        return False

async def get_members(chat_id, limit=5000):
    try:
        async with db_pool.acquire() as conn:
            members = await conn.fetch('''
                SELECT user_id, username, first_name, last_name 
                FROM members 
                WHERE chat_id = $1 
                ORDER BY last_seen DESC
                LIMIT $2
            ''', chat_id, limit)
        return members
    except Exception as e:
        logger.error(f"❌ خطأ في جلب الأعضاء: {e}")
        return []

async def add_warning(user_id, chat_id, reason, admin_id=None):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO warnings (user_id, chat_id, reason, admin_id)
                VALUES ($1, $2, $3, $4)
            ''', user_id, chat_id, reason, admin_id)
        return True
    except Exception as e:
        logger.error(f"❌ خطأ في إضافة التحذير: {e}")
        return False

async def get_warning_count(user_id, chat_id):
    try:
        async with db_pool.acquire() as conn:
            count = await conn.fetchval('''
                SELECT COUNT(*) FROM warnings 
                WHERE user_id = $1 AND chat_id = $2
            ''', user_id, chat_id)
        return count
    except Exception as e:
        logger.error(f"❌ خطأ في جلب عدد التحذيرات: {e}")
        return 0

async def get_warning_reasons(user_id, chat_id):
    try:
        async with db_pool.acquire() as conn:
            reasons = await conn.fetch('''
                SELECT reason, warning_date, admin_id 
                FROM warnings 
                WHERE user_id = $1 AND chat_id = $2
                ORDER BY warning_date DESC
            ''', user_id, chat_id)
        return reasons
    except Exception as e:
        logger.error(f"❌ خطأ في جلب أسباب التحذيرات: {e}")
        return []

async def reset_warnings(user_id, chat_id):
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
                DELETE FROM warnings 
                WHERE user_id = $1 AND chat_id = $2
            ''', user_id, chat_id)
        return True
    except Exception as e:
        logger.error(f"❌ خطأ في إعادة تعيين التحذيرات: {e}")
        return False

async def get_chat_settings(chat_id):
    try:
        async with db_pool.acquire() as conn:
            settings = await conn.fetchrow('''
                SELECT max_warns, delete_links, youtube_channel 
                FROM settings 
                WHERE chat_id = $1
            ''', chat_id)
        
        if settings:
            return {
                "max_warns": settings["max_warns"],
                "delete_links": settings["delete_links"],
                "youtube_channel": settings["youtube_channel"]
            }
        else:
            return {
                "max_warns": 3,
                "delete_links": True,
                "youtube_channel": "@Mik_emm"
            }
    except Exception as e:
        logger.error(f"❌ خطأ في جلب إعدادات الدردشة: {e}")
        return {
            "max_warns": 3,
            "delete_links": True,
            "youtube_channel": "@Mik_emm"
        }

async def save_chat_settings(chat_id, max_warns=None, delete_links=None, youtube_channel=None):
    try:
        async with db_pool.acquire() as conn:
            current_settings = await get_chat_settings(chat_id)
            
            max_warns = max_warns if max_warns is not None else current_settings["max_warns"]
            delete_links = delete_links if delete_links is not None else current_settings["delete_links"]
            youtube_channel = youtube_channel if youtube_channel is not None else current_settings["youtube_channel"]
            
            await conn.execute('''
                INSERT INTO settings (chat_id, max_warns, delete_links, youtube_channel)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (chat_id) DO UPDATE SET
                max_warns = EXCLUDED.max_warns,
                delete_links = EXCLUDED.delete_links,
                youtube_channel = EXCLUDED.youtube_channel
            ''', chat_id, max_warns, delete_links, youtube_channel)
        
        return True
    except Exception as e:
        logger.error(f"❌ خطأ في حفظ إعدادات الدردشة: {e}")
        return False

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return False
        
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        return member.status in ("administrator", "creator")
    except Exception as e:
        logger.error(f"❌ خطأ في التحقق من صلاحية المشرف: {e}")
        return False

async def warn_user(chat_id, user_id, reason=None, admin_id=None):
    try:
        await add_warning(user_id, str(chat_id), reason, admin_id)
        warn_count = await get_warning_count(user_id, str(chat_id))
        return warn_count
    except Exception as e:
        logger.error(f"❌ خطأ في تحذير المستخدم: {e}")
        return 0

async def get_warns(chat_id, user_id):
    try:
        count = await get_warning_count(user_id, str(chat_id))
        reasons = await get_warning_reasons(user_id, str(chat_id))
        return {
            "count": count,
            "reasons": [reason["reason"] for reason in reasons]
        }
    except Exception as e:
        logger.error(f"❌ خطأ في جلب التحذيرات: {e}")
        return {"count": 0, "reasons": []}

async def reset_warns(chat_id, user_id):
    try:
        return await reset_warnings(user_id, str(chat_id))
    except Exception as e:
        logger.error(f"❌ خطأ في إعادة تعيين التحذيرات: {e}")
        return False

def admin_only(handler):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await is_admin(update, context):
            await update.message.reply_text("❌ هذا الأمر خاص بالمشرفين فقط.")
            return
        return await handler(update, context)
    return wrapper

# ================== الأوامر الأساسية ==================

@admin_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = """
👋 *مرحبا بك في بوت إدارة المجموعة المتقدم* ⚙️

📌 *أوامر المشرفين:*
• /admins - عرض قائمة المشرفين
• /tagall - منشن لجميع الأعضاء
• /sync - مزامنة الأعضاء مع قاعدة البيانات
• /warn - تحذير عضو (بالرد على رسالته)
• /unwarn - إزالة تحذيرات عضو
• /warns - عرض تحذيرات عضو
• /setwarns [عدد] - ضبط عدد التحذيرات للطرد
• /delete_links on/off - التحكم بحذف الروابط
• /ping - فحص حالة البوت

🚀 *صنع بواسطة:* [Mik_emm](https://t.me/Mik_emm) مع ❤️
"""
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

@admin_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
📚 *أوامر البوت المتاحة (للمشرفين فقط):*

👨‍💻 *أوامر الإدارة:*
├ /admins - عرض قائمة المشرفين
├ /tagall - عمل منشن لجميع الأعضاء
├ /sync - مزامنة الأعضاء مع قاعدة البيانات
├ /warn - تحذير عضو (بالرد + سبب)
├ /unwarn - إزالة تحذيرات عضو
├ /warns - عرض تحذيرات عضو
├ /setwarns [عدد] - تحديد عدد التحذيرات للطرد
├ /delete_links on/off - التحكم بحذف الروابط
└ /ping - فحص حالة البوت

🔧 *ميزات تلقائية:*
• حذف الروابط تلقائياً
• منع الكلمات المسيئة
• الترحيب بالأعضاء الجدد
• الردود التلقائية
• حفظ الأعضاء في قاعدة بيانات دائمة

📝 *للاستفسار:* @Mik_emm
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")

@admin_only
async def sync_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مزامنة المشرفين مع قاعدة البيانات"""
    try:
        await update.message.reply_text("⏳ جاري مزامنة المشرفين مع قاعدة البيانات...")
        
        chat_id = update.effective_chat.id
        admins = await context.bot.get_chat_administrators(chat_id)
        
        saved_count = 0
        for admin in admins:
            user = admin.user
            if not user.is_bot:
                await add_member(
                    user.id,
                    str(chat_id),
                    user.username,
                    user.first_name,
                    user.last_name
                )
                saved_count += 1
        
        await update.message.reply_text(f"✅ تم حفظ {saved_count} مشرف في قاعدة البيانات.")
            
    except Exception as e:
        logger.error(f"❌ خطأ في مزامنة الأعضاء: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء المزامنة.")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data.startswith("kick_"):
        try:
            user_status = await context.bot.get_chat_member(query.message.chat.id, query.from_user.id)
            if user_status.status not in ["administrator", "creator"]:
                await context.bot.send_message(
                    chat_id=query.from_user.id,
                    text="❌ هذا الزر للمشرفين فقط! لا يمكنك استخدامه."
                )
                return
        except Exception as e:
            logger.error(f"❌ خطأ في التحقق من صلاحية المشرف: {e}")
            await query.edit_message_text("❌ حدث خطأ أثناء التحقق من الصلاحيات!")
            return
        
        parts = query.data.split("_")
        action = parts[1]
        user_id = int(parts[2])
        chat_id = parts[3]
        
        if action == "approve":
            try:
                await context.bot.ban_chat_member(chat_id, user_id)
                await query.edit_message_text(f"✅ تم طرد العضو بنجاح.")
            except Exception as e:
                await query.edit_message_text(f"❌ لم أتمكن من طرد العضو: {e}")
        elif action == "reject":
            await query.edit_message_text("❌ تم رفض طلب الطرد.")

@admin_only
async def admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        admins_list = await context.bot.get_chat_administrators(update.effective_chat.id)
        msg = "👮‍♂️ *قائمة الإداريين:*\n\n"
        for admin in admins_list:
            user = admin.user
            username = f"@{user.username}" if user.username else user.full_name
            status = "👑 منشئ" if admin.status == "creator" else "🔧 مشرف"
            msg += f"• {username} ({status})\n"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"❌ خطأ في جلب قائمة المشرفين: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء جلب قائمة المشرفين.")

@admin_only
async def tagall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = str(update.effective_chat.id)
        
        # جلب الأعضاء من قاعدة البيانات
        members = await get_members(chat_id, limit=2000)

        if not members:
            await update.message.reply_text("📭 لا يوجد أعضاء مخزنون في هذه المجموعة.\nاستخدم /sync لمزامنة المشرفين أولاً.")
            return

        mentions = []
        for member in members:
            user_id = member["user_id"]
            username = member["username"]
            first_name = member["first_name"]
            last_name = member["last_name"]
            
            name = username or f"{first_name} {last_name}".strip() or f"user_{user_id}"
            mentions.append(f"[{name}](tg://user?id={user_id})")
        
        # إرسال المنشن على دفعات
        total_mentioned = 0
        batch_size = 15
        
        for i in range(0, len(mentions), batch_size):
            batch = mentions[i:i+batch_size]
            message = "📢 منشن لجميع الأعضاء:\n\n" + "\n".join(batch)
            await update.message.reply_text(message, parse_mode="Markdown")
            total_mentioned += len(batch)
            
            # تأخير 1 ثانية بين كل دفعة
            await asyncio.sleep(1)
        
        await update.message.reply_text(f"✅ تم عمل منشن لـ {total_mentioned} عضو.")
        
    except Exception as e:
        logger.error(f"❌ خطأ في عمل المنشن: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء عمل المنشن.")

@admin_only
async def warn_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message.reply_to_message:
            await update.message.reply_text("⚠️ يرجى الرد على رسالة المستخدم للتحذير")
            return

        user_id = update.message.reply_to_message.from_user.id
        user_name = update.message.reply_to_message.from_user.first_name
        reason = " ".join(context.args) if context.args else "بدون سبب"

        warns = await warn_user(update.effective_chat.id, user_id, reason, update.effective_user.id)
        settings = await get_chat_settings(str(update.effective_chat.id))
        max_warns = settings["max_warns"]

        if warns >= max_warns:
            keyboard = [
                [
                    InlineKeyboardButton("✅ نعم، طرده", callback_data=f"kick_approve_{user_id}_{update.effective_chat.id}"),
                    InlineKeyboardButton("❌ لا، إلغاء", callback_data=f"kick_reject_{user_id}_{update.effective_chat.id}")
                ]
            ]
            
            await update.message.reply_text(
                f"⚠️ {user_name} وصل إلى الحد الأقصى للتحذيرات ({warns}/{max_warns})\n"
                f"هل تريد طرده الآن؟",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_text(
                f"⚠️ تم تحذير {user_name} ({warns}/{max_warns})\n"
                f"السبب: {reason}"
            )
    except Exception as e:
        logger.error(f"❌ خطأ في تحذير المستخدم: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء تنفيذ الأمر.")

@admin_only
async def unwarn_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message.reply_to_message:
            await update.message.reply_text("⚠️ يرجى الرد على رسالة العضو")
            return

        user_id = update.message.reply_to_message.from_user.id
        user_name = update.message.reply_to_message.from_user.first_name

        if await reset_warns(update.effective_chat.id, user_id):
            await update.message.reply_text(f"✅ تم إزالة جميع التحذيرات لـ {user_name}")
        else:
            await update.message.reply_text(f"ℹ️ لا يوجد تحذيرات لـ {user_name}")
    except Exception as e:
        logger.error(f"❌ خطأ في إزالة التحذيرات: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء تنفيذ الأمر.")

@admin_only
async def get_warns_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message.reply_to_message:
            await update.message.reply_text("⚠️ يرجى الرد على رسالة العضو")
            return

        user_id = update.message.reply_to_message.from_user.id
        user_name = update.message.reply_to_message.from_user.first_name
        warns_info = await get_warns(update.effective_chat.id, user_id)
        settings = await get_chat_settings(str(update.effective_chat.id))
        max_warns = settings["max_warns"]

        if warns_info["count"] > 0:
            message = f"⚠️ تحذيرات {user_name}: {warns_info['count']}/{max_warns}\n"
            if warns_info["reasons"]:
                message += "الأسباب:\n" + "\n".join(f"• {reason}" for reason in warns_info["reasons"])
            await update.message.reply_text(message)
        else:
            await update.message.reply_text(f"ℹ️ لا يوجد تحذيرات لـ {user_name}")
    except Exception as e:
        logger.error(f"❌ خطأ في جلب التحذيرات: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء تنفيذ الأمر.")

@admin_only
async def set_max_warns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text("⚠️ الصيغة: /setwarns [عدد]")
            return

        max_warns = int(context.args[0])
        if max_warns < 1 or max_warns > 40:
            await update.message.reply_text("⚠️ عدد التحذيرات يجب أن يكون بين 1 و 40")
            return

        chat_id = str(update.effective_chat.id)
        await save_chat_settings(chat_id, max_warns=max_warns)
        
        await update.message.reply_text(f"✅ تم ضبط عدد التحذيرات القصوى إلى {max_warns}")
    except Exception as e:
        logger.error(f"❌ خطأ في ضبط عدد التحذيرات: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء ضبط عدد التحذيرات.")

@admin_only
async def delete_links_setting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.args or context.args[0].lower() not in ["on", "off"]:
            await update.message.reply_text("⚠️ الصيغة: /delete_links on/off")
            return

        setting = context.args[0].lower() == "on"
        chat_id = str(update.effective_chat.id)
        await save_chat_settings(chat_id, delete_links=setting)
        
        status = "تفعيل" if setting else "تعطيل"
        await update.message.reply_text(f"✅ تم {status} حذف الروابط تلقائياً")
    except Exception as e:
        logger.error(f"❌ خطأ في تعديل إعدادات الروابط: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء تعديل الإعداد.")

@admin_only
async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 البوت يعمل بشكل طبيعي! ✅")

# ================== معالجة الرسائل ==================

async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        for member in update.message.new_chat_members:
            if member.id == context.bot.id:
                continue
            
            await update.message.reply_text(WELCOME_MESSAGES["ar"], parse_mode="Markdown")
            
            # حفظ العضو في قاعدة البيانات عند الانضمام
            await add_member(
                member.id, 
                str(update.effective_chat.id),
                member.username,
                member.first_name,
                member.last_name
            )
    except Exception as e:
        logger.error(f"❌ خطأ في الترحيب بعضو جديد: {e}")

def contains_banned_word(text):
    if not text:
        return False
    text = text.lower()
    for word in BANNED_WORDS:
        if word.strip().lower() in text:
            return True
    return False

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        message = update.message
        text = message.text
        user = message.from_user
        
        if not text:
            return
        
        # حفظ العضو في قاعدة البيانات عند إرسال أي رسالة
        await add_member(
            user.id,
            str(update.effective_chat.id),
            user.username,
            user.first_name,
            user.last_name
        )
        
        # التحقق من الكلمات الممنوعة (لا تنطبق على المشرفين)
        if contains_banned_word(text):
            if not await is_admin(update, context):
                await message.delete()
                await message.reply_text("⚠️ تم حذف الرسالة لاحتوائها على كلمات غير لائقة.")
                return
        
        # التحقق من الروابط (لا تنطبق على المشرفين)
        settings = await get_chat_settings(str(update.effective_chat.id))
        if settings["delete_links"] and re.search(r'(https?://|www\.|t\.me/)', text, re.IGNORECASE):
            if not await is_admin(update, context):
                await message.delete()
                await message.reply_text("⚠️ يمنع نشر الروابط في هذه المجموعة.")
                return
        
        # الردود التلقائية
        if text in AUTO_REPLIES:
            await message.reply_text(AUTO_REPLIES[text])
            
    except Exception as e:
        logger.error(f"❌ خطأ في معالجة الرسالة: {e}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(msg="حدث خطأ في البوت", exc_info=context.error)
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ حدث خطأ غير متوقع في البوت. يرجى المحاولة لاحقاً.")
        except:
            pass

# ================== ويب هوك ==================

async def webhook_handler(request):
    token = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
    if token != SECRET_TOKEN:
        return web.Response(status=403, text="Forbidden")
    
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return web.Response(text="OK", status=200)
    except Exception as e:
        logger.error(f"❌ خطأ في معالج الويب هوك: {e}")
        return web.Response(text="Error", status=500)

async def set_webhook():
    try:
        await application.bot.set_webhook(
            url=WEBHOOK_URL,
            secret_token=SECRET_TOKEN,
            drop_pending_updates=True
        )
        logger.info("✅ تم تعيين الويب هوك بنجاح")
    except Exception as e:
        logger.error(f"❌ خطأ في تعيين الويب هوك: {e}")

async def on_startup(app):
    await application.initialize()
    await application.start()
    await init_db()
    await set_webhook()
    logger.info("✅ تم تشغيل البوت بنجاح مع الويب هوك!")

async def on_shutdown(app):
    await application.stop()
    await application.shutdown()
    if db_pool:
        await db_pool.close()
    logger.info("✅ تم إيقاف البوت بنجاح!")

def main():
    # إضافة handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("sync", sync_members))
    application.add_handler(CommandHandler("admins", admins))
    application.add_handler(CommandHandler("tagall", tagall))
    application.add_handler(CommandHandler("warn", warn_user_command))
    application.add_handler(CommandHandler("unwarn", unwarn_user_command))
    application.add_handler(CommandHandler("warns", get_warns_command))
    application.add_handler(CommandHandler("setwarns", set_max_warns))
    application.add_handler(CommandHandler("delete_links", delete_links_setting))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    application.add_error_handler(error_handler)

    web_app = web.Application()
    web_app.router.add_post('/webhook', webhook_handler)
    web_app.on_startup.append(on_startup)
    web_app.on_shutdown.append(on_shutdown)

    web.run_app(web_app, host='0.0.0.0', port=PORT)

if __name__ == "__main__":
    main()
