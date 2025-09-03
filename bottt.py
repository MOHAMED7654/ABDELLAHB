import logging
import json
import re
import os
import psycopg
from contextlib import contextmanager
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

# معلومات الاتصال الإضافية
DB_HOST = "dpg-d2s5g4m3jp1c738svjfg-a.frankfurt-postgres.render.com"
DB_PORT = 5432
DB_NAME = "mybotdb_mqjm"
DB_USER = "mybotuser"
DB_PASSWORD = "prb09Wv3eU2OhkoeOXyR5n05IBBMEvhn"

# اتصال مباشر بدون pool
@contextmanager
def get_connection():
    try:
        conn = psycopg.connect(DATABASE_URL, autocommit=True)
        try:
            yield conn
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"❌ Error getting connection: {e}")
        raise

# إصلاح هيكل الجدول إذا كان ناقصاً
def fix_database_schema():
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                # إضافة العمود إذا لم يكن موجوداً
                cursor.execute('''
                DO $$ 
                BEGIN 
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name='members' AND column_name='last_seen'
                    ) THEN
                        ALTER TABLE members ADD COLUMN last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
                    END IF;
                END $$;
                ''')
                logger.info("✅ Database schema checked and fixed if needed")
    except Exception as e:
        logger.error(f"❌ Error fixing database schema: {e}")

# وظائف الاتصال بقاعدة البيانات
def init_database():
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                
                # جدول الأعضاء
                cursor.execute('''
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
                
                # جدول التحذيرات
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS warnings (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    chat_id TEXT NOT NULL,
                    reason TEXT,
                    warning_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    admin_id BIGINT
                )
                ''')
                
                # جدول الإعدادات
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    id SERIAL PRIMARY KEY,
                    chat_id TEXT UNIQUE NOT NULL,
                    max_warns INTEGER DEFAULT 3,
                    delete_links BOOLEAN DEFAULT TRUE,
                    youtube_channel TEXT DEFAULT '@Mik_emm'
                )
                ''')
                
                # جدول طلبات الطرد
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS kick_requests (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    chat_id TEXT NOT NULL,
                    admin_id BIGINT NOT NULL,
                    request_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    status TEXT DEFAULT 'pending'
                )
                ''')
        
        logger.info("✅ Database initialized successfully")
        fix_database_schema()  # إصلاح الهيكل بعد التهيئة
    except Exception as e:
        logger.error(f"❌ Error initializing database: {e}")

# إضافة عضو إلى قاعدة البيانات
def add_member(user_id, chat_id, username, first_name, last_name):
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                INSERT INTO members (user_id, chat_id, username, first_name, last_name, last_seen)
                VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id, chat_id) DO UPDATE SET
                username = EXCLUDED.username,
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                last_seen = CURRENT_TIMESTAMP
                ''', (user_id, chat_id, username, first_name, last_name))
        
        logger.debug(f"Member {user_id} added/updated in chat {chat_id}")
        return True
    except Exception as e:
        logger.error(f"Error adding member {user_id} to database: {e}")
        return False

# الحصول على أعضاء مجموعة محددة
def get_members(chat_id):
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                SELECT user_id, username, first_name, last_name 
                FROM members 
                WHERE chat_id = %s 
                ORDER BY last_seen DESC
                ''', (chat_id,))
                members = cursor.fetchall()
        
        return members
    except Exception as e:
        logger.error(f"Error getting members for chat {chat_id}: {e}")
        return []

# إضافة تحذير
def add_warning(user_id, chat_id, reason, admin_id=None):
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                INSERT INTO warnings (user_id, chat_id, reason, admin_id)
                VALUES (%s, %s, %s, %s)
                ''', (user_id, chat_id, reason, admin_id))
        
        logger.info(f"Warning added for user {user_id} in chat {chat_id}")
        return True
    except Exception as e:
        logger.error(f"Error adding warning for user {user_id}: {e}")
        return False

# الحصول على عدد التحذيرات
def get_warning_count(user_id, chat_id):
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                SELECT COUNT(*) FROM warnings 
                WHERE user_id = %s AND chat_id = %s
                ''', (user_id, chat_id))
                count = cursor.fetchone()[0]
        
        return count
    except Exception as e:
        logger.error(f"Error getting warning count for user {user_id}: {e}")
        return 0

# الحصول على أسباب التحذيرات
def get_warning_reasons(user_id, chat_id):
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                SELECT reason, warning_date, admin_id 
                FROM warnings 
                WHERE user_id = %s AND chat_id = %s
                ORDER BY warning_date DESC
                ''', (user_id, chat_id))
                reasons = cursor.fetchall()
        
        return reasons
    except Exception as e:
        logger.error(f"Error getting warning reasons for user {user_id}: {e}")
        return []

# إزالة جميع تحذيرات العضو
def reset_warnings(user_id, chat_id):
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                DELETE FROM warnings 
                WHERE user_id = %s AND chat_id = %s
                ''', (user_id, chat_id))
                affected = cursor.rowcount
        
        logger.info(f"Warnings reset for user {user_id} in chat {chat_id}")
        return affected > 0
    except Exception as e:
        logger.error(f"Error resetting warnings for user {user_id}: {e}")
        return False

# الحصول على قائمة المحذرين
def get_warned_members(chat_id):
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                SELECT user_id, COUNT(*) as warn_count 
                FROM warnings 
                WHERE chat_id = %s 
                GROUP BY user_id 
                HAVING COUNT(*) > 0
                ORDER BY warn_count DESC
                ''', (chat_id,))
                warned_members = cursor.fetchall()
        
        return warned_members
    except Exception as e:
        logger.error(f"Error getting warned members for chat {chat_id}: {e}")
        return []

# الحصول على إعدادات المجموعة
def get_chat_settings(chat_id):
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                SELECT max_warns, delete_links, youtube_channel 
                FROM settings 
                WHERE chat_id = %s
                ''', (chat_id,))
                settings = cursor.fetchone()
        
        if settings:
            return {
                "max_warns": settings[0],
                "delete_links": bool(settings[1]),
                "youtube_channel": settings[2]
            }
        else:
            # إعدادات افتراضية مع زيادة الحد الأقصى للتحذيرات
            return {
                "max_warns": 40,
                "delete_links": True,
                "youtube_channel": "@Mik_emm"
            }
    except Exception as e:
        logger.error(f"Error getting settings for chat {chat_id}: {e}")
        return {
            "max_warns": 40,
            "delete_links": True,
            "youtube_channel": "@Mik_emm"
        }

# حفظ إعدادات المجموعة
def save_chat_settings(chat_id, max_warns=None, delete_links=None, youtube_channel=None):
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                # التحقق من وجود الإعدادات
                cursor.execute('SELECT chat_id FROM settings WHERE chat_id = %s', (chat_id,))
                exists = cursor.fetchone()
                
                if exists:
                    # تحديث الإعدادات الحالية
                    update_fields = []
                    params = []
                    
                    if max_warns is not None:
                        update_fields.append("max_warns = %s")
                        params.append(max_warns)
                    
                    if delete_links is not None:
                        update_fields.append("delete_links = %s")
                        params.append(delete_links)
                    
                    if youtube_channel is not None:
                        update_fields.append("youtube_channel = %s")
                        params.append(youtube_channel)
                    
                    if update_fields:
                        params.append(chat_id)
                        cursor.execute(f'''
                        UPDATE settings 
                        SET {', '.join(update_fields)} 
                        WHERE chat_id = %s
                        ''', params)
                else:
                    # إدخال إعدادات جديدة
                    cursor.execute('''
                    INSERT INTO settings (chat_id, max_warns, delete_links, youtube_channel)
                    VALUES (%s, %s, %s, %s)
                    ''', (chat_id, max_warns or 40, delete_links or True, youtube_channel or "@Mik_emm"))
        
        logger.info(f"Settings saved for chat {chat_id}")
        return True
    except Exception as e:
        logger.error(f"Error saving settings for chat {chat_id}: {e}")
        return False

# إضافة طلب طرد
def add_kick_request(user_id, chat_id, admin_id):
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                INSERT INTO kick_requests (user_id, chat_id, admin_id)
                VALUES (%s, %s, %s)
                ''', (user_id, chat_id, admin_id))
        
        return True
    except Exception as e:
        logger.error(f"Error adding kick request for user {user_id}: {e}")
        return False

# تهيئة قاعدة البيانات عند التشغيل
init_database()

# الكلمات الممنوعة - تم تحسينها لتجنب الحذف الخاطئ
banned_words = {
    " كلب ", " حمار ", " قحب ", " زبي ", " خرا ", " بول ",
    "ولد الحرام", "ولد القحبة", "يا قحبة", " نيك ", " منيك ",
    " مخنث ", " قحبة ", " حقير ", " قذر "
}

# الردود التلقائية
auto_replies = {
    "السلام عليكم": "وعليكم السلام",
    "تصبح على خير": "وأنت من أهله",
}

# رسائل الترحيب
WELCOME_MESSAGES = {
    "ar": """
أهلا وسهلا بك في مجتمعنا الراقي للإعلام الآلي  
عليك اللتزام بهذه الجملة من القوانين:   
1- عدم نشر الروابط دون اذن   
2- عدم التحدث في مواضيع جانبية ما عدا الدراسة و الحرص على التحدث بلباقة
3- الامتناع عن التواصل المشبوه في الخاص (بإمكانك طرح اي أسئلة في المجموعة لذلك يمنع استخدام هذه الحجة )
كما نعلمكم اننا مسؤولون فقط عما يحدث داخل المجموعة 
4-  الامتثال لقرارات المشرفين ضروري للحفاظ على النظام
ملاحظة: في حالات الضرورة يمكن التواصل مع المشرفين ( الاناث مع مالكة المجموعة و الذكور مع المشرفين الذكور)
🫧 𝓣𝓸𝓾𝓴𝓪 ꨄ︎
""",
    "en": """
Welcome to our refined Computer Science community.
You must adhere to the following set of rules:
1. Do not share links without permission
2. Avoid discussing off-topic subjects unless related to studies, and always speak politely
3. Refrain from suspicious private messaging
(You can ask any questions in the group, so this excuse is not acceptable)
Please note: we are only responsible for what happens within the group
4. Compliance with the supervisors' decisions is essential to maintain order
Note: In necessary cases, you may contact the supervisors
(Females should reach out to the group owner, and males to the male admins)
🫧 𝓣𝓸𝓾𝓴𝓪 ꨄ︎
"""
}

# تهيئة التطبيق
application = Application.builder().token(TOKEN).build()

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return False  # جعل الأوامر تعمل فقط في المجموعات للمشرفين
        
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        return member.status in ("administrator", "creator")
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        return False

async def check_subscription(user_id):
    # هنا يمكن إضافة التحقق من الاشتراك في القناة
    return True

async def warn_user(chat_id, user_id, reason=None, admin_id=None):
    try:
        # إضافة التحذير إلى قاعدة البيانات
        add_warning(user_id, str(chat_id), reason, admin_id)
        
        # الحصول على عدد التحذيرات
        warn_count = get_warning_count(user_id, str(chat_id))
        
        return warn_count
    except Exception as e:
        logger.error(f"Error in warn_user: {e}")
        return 0

async def get_warns(chat_id, user_id):
    try:
        count = get_warning_count(user_id, str(chat_id))
        reasons = get_warning_reasons(user_id, str(chat_id))
        
        return {
            "count": count,
            "reasons": [reason[0] for reason in reasons]
        }
    except Exception as e:
        logger.error(f"Error in get_warns: {e}")
        return {"count": 0, "reasons": []}

async def reset_warns(chat_id, user_id):
    try:
        return reset_warnings(user_id, str(chat_id))
    except Exception as e:
        logger.error(f"Error in reset_warns: {e}")
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
• /warn - تحذير عضو (بالرد على رسالته)
• /unwarn - إزالة تحذيرات عضو
• /warns - عرض تحذيرات عضو
• /setwarns [عدد] - ضبط عدد التحذيرات للطرد (حتى 40)
• /delete_links on/off - التحكم بحذف الروابط
• /warn_list - قائمة المحذرين
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
├ /warn - تحذير عضو (بالرد + سبب)
├ /unwarn - إزالة تحذيرات عضو
├ /warns - عرض تحذيرات عضو
├ /setwarns [عدد] - تحديد عدد التحذيرات للطرد (حتى 40)
├ /delete_links on/off - التحكم بحذف الروابط
├ /warn_list - عرض قائمة المحذرين
└ /ping - فحص حالة البوت

🔧 *ميزات تلقائية:*
• حذف الروابط تلقائياً
• منع الكلمات المسيئة
• الترحيب بالأعضاء الجدد
• الردود التلقائية

📝 *للاستفسار:* @Mik_emm
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "check_sub":
        if await check_subscription(query.from_user.id):
            await query.edit_message_text("✅ شكراً للاشتراك! يمكنك الآن استخدام البوت.")
        else:
            await query.edit_message_text("❌ لم يتم العثور على اشتراكك. يرجى الاشتراك أولاً.")
    elif query.data.startswith("kick_"):
        # التحقق من أن المستخدم مشرف
        try:
            user_status = await context.bot.get_chat_member(query.message.chat.id, query.from_user.id)
            if user_status.status not in ["administrator", "creator"]:
                # إرسال رسالة للمستخدم العادي مع الحفاظ على الرسالة الأصلية للمشرفين
                await context.bot.send_message(
                    chat_id=query.from_user.id,
                    text="❌ هذا الزر للمشرفين فقط! لا يمكنك استخدامه."
                )
                return
        except Exception as e:
            logger.error(f"Error checking admin status in callback: {e}")
            await query.edit_message_text("❌ حدث خطأ أثناء التحقق من الصلاحيات!")
            return
        
        # معالجة طلبات الطرد (فقط للمشرفين)
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
        logger.error(f"Error in admins command: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء جلب قائمة المشرفين.")

@admin_only
async def tagall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = str(update.effective_chat.id)
        members = get_members(chat_id)

        if not members:
            await update.message.reply_text("📭 لا يوجد أعضاء مخزنون في هذه المجموعة.")
            return

        mentions = []
        for member in members:
            user_id, username, first_name, last_name = member
            name = username or f"{first_name} {last_name}".strip() or f"user_{user_id}"
            mentions.append(f"[{name}](tg://user?id={user_id})")
        
        max_per_msg = 5
        
        for i in range(0, len(mentions), max_per_msg):
            batch = mentions[i:i+max_per_msg]
            message = "📢 منشن لجميع الأعضاء:\n\n" + "\n".join(batch)
            await update.message.reply_text(message, parse_mode="Markdown")
        
        await update.message.reply_text(f"✅ تم عمل منشن لـ {len(members)} عضو.")
    except Exception as e:
        logger.error(f"Error in tagall: {e}")
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
        settings = get_chat_settings(str(update.effective_chat.id))
        max_warns = settings["max_warns"]

        if warns >= max_warns:
            # إرسال طلب تأكيد الطرد للمشرفين
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
        logger.error(f"Error in warn command: {e}")
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
        logger.error(f"Error in unwarn command: {e}")
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
        settings = get_chat_settings(str(update.effective_chat.id))
        max_warns = settings["max_warns"]

        if warns_info["count"] > 0:
            message = f"⚠️ تحذيرات {user_name}: {warns_info['count']}/{max_warns}\n"
            if warns_info["reasons"]:
                message += "الأسباب:\n" + "\n".join(f"• {reason}" for reason in warns_info["reasons"])
            await update.message.reply_text(message)
        else:
            await update.message.reply_text(f"ℹ️ لا يوجد تحذيرات لـ {user_name}")
    except Exception as e:
        logger.error(f"Error in warns command: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء تنفيذ الأمر.")

@admin_only
async def warn_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = str(update.effective_chat.id)
        warned_members = get_warned_members(chat_id)
        
        if not warned_members:
            await update.message.reply_text("ℹ️ لا يوجد أعضاء محذرين حالياً")
            return

        message = "📋 *قائمة الأعضاء المحذرين:*\n\n"
        for user_id, warn_count in warned_members:
            try:
                user = await context.bot.get_chat_member(chat_id, user_id)
                username = f"@{user.user.username}" if user.user.username else user.user.full_name
                message += f"• {username}: {warn_count} تحذيرات\n"
            except Exception:
                message += f"• مستخدم (ID: {user_id}): {warn_count} تحذيرات\n"

        await update.message.reply_text(message, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error in warn_list: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء جلب قائمة المحذرين.")

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
        save_chat_settings(chat_id, max_warns=max_warns)
        
        await update.message.reply_text(f"✅ تم ضبط عدد التحذيرات القصوى إلى {max_warns}")
    except Exception as e:
        logger.error(f"Error in set_max_warns: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء ضبط عدد التحذيرات.")

@admin_only
async def delete_links_setting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.args or context.args[0].lower() not in ["on", "off"]:
            await update.message.reply_text("⚠️ الصيغة: /delete_links on/off")
            return

        setting = context.args[0].lower() == "on"
        chat_id = str(update.effective_chat.id)
        save_chat_settings(chat_id, delete_links=setting)
        
        status = "تفعيل" if setting else "تعطيل"
        await update.message.reply_text(f"✅ تم {status} حذف الروابط تلقائياً")
    except Exception as e:
        logger.error(f"Error in delete_links_setting: {e}")
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
            
            # إرسال رسالة الترحيب العربية
            await update.message.reply_text(WELCOME_MESSAGES["ar"], parse_mode="Markdown")
            
            # تسجيل المستخدم في قاعدة البيانات
            add_member(
                member.id, 
                str(update.effective_chat.id),
                member.username,
                member.first_name,
                member.last_name
            )
    except Exception as e:
        logger.error(f"Error in welcome_new_member: {e}")

def contains_banned_word(text):
    if not text:
        return False
    text = text.lower()
    for word in banned_words:
        if word.strip().lower() in text:
            return True
    return False

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.effective_chat.type == "private":
            return
        
        message = update.message
        chat_id = str(update.effective_chat.id)
        user_id = update.effective_user.id
        text = message.text or ""
        is_adm = await is_admin(update, context)

        # تسجيل المستخدم في قاعدة البيانات
        add_member(
            user_id,
            chat_id,
            update.effective_user.username,
            update.effective_user.first_name,
            update.effective_user.last_name
        )

        # حذف الروابط
        settings = get_chat_settings(chat_id)
        if settings["delete_links"]:
            if re.search(r'(https?://\S+|www\.\S+)', text):
                if not is_adm:
                    try:
                        await message.delete()
                        warn_count = await warn_user(chat_id, user_id, "نشر روابط", context.bot.id)
                        max_warns = settings["max_warns"]
                        
                        warning_msg = f"🚫 {update.effective_user.mention_html()} الروابط غير مسموح بها!"
                        if warn_count >= max_warns:
                            # إرسال طلب تأكيد الطرد
                            keyboard = [
                                [
                                    InlineKeyboardButton("✅ نعم، طرده", callback_data=f"kick_approve_{user_id}_{chat_id}"),
                                    InlineKeyboardButton("❌ لا، إلغاء", callback_data=f"kick_reject_{user_id}_{chat_id}")
                                ]
                            ]
                            
                            warning_msg += f"\n⚠️ وصل إلى حد التحذيرات ({warn_count}/{max_warns})"
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=warning_msg,
                                parse_mode="HTML",
                                reply_markup=InlineKeyboardMarkup(keyboard)
                            )
                        else:
                            warning_msg += f"\n⚠️ تحذير ({warn_count}/{max_warns})"
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=warning_msg,
                                parse_mode="HTML"
                            )
                        return
                    except Exception as e:
                        logger.error(f"Error deleting link: {e}")

        # منع الكلمات المسيئة
        if contains_banned_word(text):
            if not is_adm:
                try:
                    await message.delete()
                    warn_count = await warn_user(chat_id, user_id, "كلمة مسيئة", context.bot.id)
                    max_warns = settings["max_warns"]
                    
                    warning_msg = f"🚫 {update.effective_user.mention_html()} الكلمات المسيئة ممنوعة!"
                    if warn_count >= max_warns:
                        # إرسال طلب تأكيد الطرد
                        keyboard = [
                            [
                                InlineKeyboardButton("✅ نعم، طرده", callback_data=f"kick_approve_{user_id}_{chat_id}"),
                                InlineKeyboardButton("❌ لا، إلغاء", callback_data=f"kick_reject_{user_id}_{chat_id}")
                            ]
                        ]
                        
                        warning_msg += f"\n⚠️ وصل إلى حد التحذيرات ({warn_count}/{max_warns})"
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=warning_msg,
                            parse_mode="HTML",
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                    else:
                        warning_msg += f"\n⚠️ تحذير ({warn_count}/{max_warns})"
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=warning_msg,
                            parse_mode="HTML"
                        )
                    return
                except Exception as e:
                    logger.error(f"Error handling banned word: {e}")

        # الردود التلقائية
        if text in auto_replies:
            await message.reply_text(auto_replies[text])

    except Exception as e:
        logger.error(f"Error in handle_messages: {e}")

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
        logger.error(f"Error in webhook handler: {e}")
        return web.Response(text="Error", status=500)

async def set_webhook():
    try:
        await application.bot.set_webhook(
            url=WEBHOOK_URL,
            secret_token=SECRET_TOKEN,
            drop_pending_updates=True
        )
        logger.info("Webhook set successfully")
    except Exception as e:
        logger.error(f"Error setting webhook: {e}")

async def on_startup(app):
    await application.initialize()
    await application.start()
    await set_webhook()
    logger.info("Bot started successfully with webhook!")

async def on_shutdown(app):
    await application.stop()
    await application.shutdown()
    logger.info("Bot stopped successfully!")

def main():
    # إضافة جميع handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("admins", admins))
    application.add_handler(CommandHandler("tagall", tagall))
    application.add_handler(CommandHandler("warn", warn_user_command))
    application.add_handler(CommandHandler("unwarn", unwarn_user_command))
    application.add_handler(CommandHandler("warns", get_warns_command))
    application.add_handler(CommandHandler("warn_list", warn_list))
    application.add_handler(CommandHandler("setwarns", set_max_warns))
    application.add_handler(CommandHandler("delete_links", delete_links_setting))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    application.add_error_handler(error_handler)

    # إعداد ويب هوك
    web_app = web.Application()
    web_app.router.add_post('/webhook', webhook_handler)
    web_app.on_startup.append(on_startup)
    web_app.on_shutdown.append(on_shutdown)

    # تشغيل الخادم
    web.run_app(web_app, host='0.0.0.0', port=PORT)

if __name__ == "__main__":
    main()
