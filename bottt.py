import logging
import re
import os
import asyncio
import aiohttp
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
TOKEN = "8420841832:AAEQh1Gf2InTT8UBfFoL4ATD2BiGlA0BRJA"
SECRET_TOKEN = "my_secret_123"
WEBHOOK_URL = "https://abdellahb-2.onrender.com/webhook"
PORT = int(os.environ.get('PORT', 8443))
HEARTBEAT_INTERVAL = 10 * 60  # كل 10 دقائق (600 ثانية)

# بيانات قاعدة البيانات PostgreSQL
DATABASE_URL = "postgresql://mybotuser:prb09Wv3eU2OhkoeOXyR5n05IBBMEvhn@dpg-d2s5g4m3jp1c738svjfg-a.frankfurt-postgres.render.com/mybotdb_mqjm"

# إعدادات إضافية
ADMIN_IDS = [7635779264, 7453316860]  # الأيدي الخاص بك وللمشرفة الثانية
KEEP_ALIVE_URL = "https://abdellahb-2.onrender.com"  # رابط التطبيق الرئيسي

# اتصال مباشر بدون pool
@contextmanager
def get_connection():
    try:
        conn = psycopg.connect(DATABASE_URL, autocommit=True)
        try:
            yield conn
        finally:
            conn.close()
    except psycopg.Error as e:
        logger.error(f"❌ PostgreSQL Error: {e}")
        raise
    except Exception as e:
        logger.error(f"❌ Error getting connection: {e}")
        raise

# اختبار الاتصال بقاعدة البيانات
def test_connection():
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('SELECT 1')
                logger.info("✅ Database connection test successful")
                return True
    except Exception as e:
        logger.error(f"❌ Database connection test failed: {e}")
        return False

# التحقق من هيكل الجداول
def check_database_schema():
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                SELECT column_name, data_type 
                FROM information_schema.columns 
                WHERE table_name = 'members' 
                AND column_name = 'user_id'
                ''')
                result = cursor.fetchone()
                if result:
                    logger.info(f"Column user_id type: {result[1]}")
                
                cursor.execute('SELECT COUNT(*) FROM members')
                count = cursor.fetchone()[0]
                logger.info(f"Total members in database: {count}")
                    
    except Exception as e:
        logger.error(f"Error checking schema: {e}")

# إصلاح الجدول وإضافة الحقول المفقودة
def fix_database_schema():
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                # التحقق إذا كان الحقل warnings_enabled موجوداً
                cursor.execute('''
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'settings' 
                AND column_name = 'warnings_enabled'
                ''')
                exists = cursor.fetchone()
                
                if not exists:
                    # إضافة الحقل المفقود
                    cursor.execute('''
                    ALTER TABLE settings 
                    ADD COLUMN warnings_enabled BOOLEAN DEFAULT TRUE
                    ''')
                    logger.info("✅ تم إضافة حقل warnings_enabled إلى جدول settings")
                
                logger.info("✅ تم إصلاح جدول settings بنجاح")
        
        return True
    except Exception as e:
        logger.error(f"❌ Error fixing database schema: {e}")
        return False

# وظائف الاتصال بقاعدة البيانات
def init_database():
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
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
                
                cursor.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    id SERIAL PRIMARY KEY,
                    chat_id TEXT UNIQUE NOT NULL,
                    max_warns INTEGER DEFAULT 3,
                    delete_links BOOLEAN DEFAULT TRUE,
                    youtube_channel TEXT DEFAULT '@Mik_emm',
                    warnings_enabled BOOLEAN DEFAULT TRUE
                )
                ''')
                
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
        return True
    except Exception as e:
        logger.error(f"❌ Error initializing database: {e}")
        return False

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
    except psycopg.Error as e:
        logger.error(f"PostgreSQL Error adding member {user_id}: {e}")
        return False
    except Exception as e:
        logger.error(f"Error adding member {user_id} to database: {e}")
        return False

# الحصول على أعضاء مجموعة محددة مع تحسين الأداء
def get_members(chat_id, limit=5000):
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('''
                SELECT user_id, username, first_name, last_name 
                FROM members 
                WHERE chat_id = %s 
                ORDER BY last_seen DESC
                LIMIT %s
                ''', (chat_id, limit))
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
                # استعلام آمن يتجنب الأخطاء
                try:
                    cursor.execute('''
                    SELECT max_warns, delete_links, youtube_channel, warnings_enabled 
                    FROM settings 
                    WHERE chat_id = %s
                    ''', (chat_id,))
                    settings = cursor.fetchone()
                except:
                    # إذا فشل الاستخدام، استخدم الاستعلام الأساسي
                    cursor.execute('''
                    SELECT max_warns, delete_links, youtube_channel 
                    FROM settings 
                    WHERE chat_id = %s
                    ''', (chat_id,))
                    settings = cursor.fetchone()
                
                if settings:
                    if len(settings) == 4:  # إذا كان يحتوي على warnings_enabled
                        return {
                            "max_warns": settings[0],
                            "delete_links": bool(settings[1]),
                            "youtube_channel": settings[2],
                            "warnings_enabled": bool(settings[3]) if settings[3] is not None else True
                        }
                    else:  # إذا كان يحتوي على الحقول الأساسية فقط
                        return {
                            "max_warns": settings[0],
                            "delete_links": bool(settings[1]),
                            "youtube_channel": settings[2],
                            "warnings_enabled": True
                        }
        
        # الإعدادات الافتراضية إذا لم توجد إعدادات للمجموعة
        return {
            "max_warns": 40,
            "delete_links": True,
            "youtube_channel": "@Mik_emm",
            "warnings_enabled": True
        }
    except Exception as e:
        logger.error(f"Error getting settings for chat {chat_id}: {e}")
        return {
            "max_warns": 40,
            "delete_links": True,
            "youtube_channel": "@Mik_emm",
            "warnings_enabled": True
        }

# حفظ إعدادات المجموعة
def save_chat_settings(chat_id, max_warns=None, delete_links=None, youtube_channel=None, warnings_enabled=None):
    try:
        with get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute('SELECT chat_id FROM settings WHERE chat_id = %s', (chat_id,))
                exists = cursor.fetchone()
                
                if exists:
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
                    
                    if warnings_enabled is not None:
                        update_fields.append("warnings_enabled = %s")
                        params.append(warnings_enabled)
                    
                    if update_fields:
                        params.append(chat_id)
                        cursor.execute(f'''
                        UPDATE settings 
                        SET {', '.join(update_fields)} 
                        WHERE chat_id = %s
                        ''', params)
                else:
                    cursor.execute('''
                    INSERT INTO settings (chat_id, max_warns, delete_links, youtube_channel, warnings_enabled)
                    VALUES (%s, %s, %s, %s, %s)
                    ''', (chat_id, max_warns or 40, delete_links or True, youtube_channel or "@Mik_emm", warnings_enabled or True))
        
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
if test_connection():
    if init_database():
        fix_database_schema()  # إصلاح الجدول
        check_database_schema()
        logger.info("✅ Database setup completed successfully!")
    else:
        logger.error("❌ Failed to initialize database")
else:
    logger.error("❌ Database connection failed")

# الكلمات الممنوعة (مع تحسين المطابقة باستخدام regex)
banned_words = {
    r'\bكلب\b', r'\bحمار\b', r'\bقحب\b', r'\bزبي\b', r'\bخرا\b', r'\bبول\b',
    r'\bولد الحرام\b', r'\bولد القحبة\b', r'\bيا قحبة\b', r'\bنيك\b', r'\bمنيك\b',
    r'\bمخنث\b', r'\bقحبة\b', r'\bحقير\b', r'\bقذر\b'
}

# الردود التلقائية
auto_replies = {
    "السلام عليكم": "وعليكم السلام",
    "تصبح على خير": "وأنت من أهله",
}

# رسائل الترحيب
WELCOME_MESSAGES = {
    "ar": """
أهلا وسهلا بك في مجتمعنا الراقي      
""",
    "en": """
Welcome to our elite informatics community!  
Please adhere to the following rules:  
1- No sharing links without permission  
2- Avoid off-topic discussions except for studies, and maintain polite conversation  
3- Refrain from suspicious private communication (you can ask any questions in the group)  
We are only responsible for what happens within the group  
4- Compliance with administrators' decisions is necessary to maintain order  
Note: In case of necessity, you can contact the admins (females with the group owner, males with male admins)  
🫧 𝓣𝓸𝓾𝓴𝓪 ꨄ︎
"""
}

# تهيئة التطبيق
application = Application.builder().token(TOKEN).build()

# وظيفة نبض الحياة لمنع السيرفر من الإسبات
async def heartbeat_task():
    """إرسال طلبات دورية للحفاظ على التطبيق نشطاً"""
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            try:
                async with session.get(KEEP_ALIVE_URL) as response:
                    if response.status == 200:
                        logger.info("✅ تم إرسال نبضة حياة بنجاح")
                    else:
                        logger.info(f"⚠️ نبضة الحياة عادت برمز: {response.status}")
            except Exception as e:
                logger.error(f"❌ خطأ في نبضة الحياة: {e}")
            
            await asyncio.sleep(HEARTBEAT_INTERVAL)

# إرسال إشعارات للإدمن
async def send_admin_notification(context, message):
    """إرسال إشعار للمشرفين عند حدوث أحداث مهمة"""
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=message,
                parse_mode="HTML"
            )
            logger.info(f"✅ تم إرسال إشعار للإدمن {admin_id}")
        except Exception as e:
            logger.error(f"❌ فشل في إرسال إشعار للإدمن {admin_id}: {e}")

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # إذا كان المستخدم هو أنت (بالرقم المحدد) اسمح له دائماً
    if update.effective_user.id == 7635779264:
        return True
        
    if update.effective_chat.type == "private":
        return False
        
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        return member.status in ("administrator", "creator")
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        return False

async def warn_user(chat_id, user_id, reason=None, admin_id=None):
    try:
        add_warning(user_id, str(chat_id), reason, admin_id)
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
            # لا ترسل أي رسالة - صمت تام
            return
        return await handler(update, context)
    return wrapper

# وظيفة جديدة لجلب جميع الأعضاء باستخدام Telegram API
async def get_all_chat_members(chat_id, context):
    """جلب جميع أعضاء المجموعة باستخدام Telegram API"""
    try:
        # استخدام get_chat_member_count بدلاً من get_chat_members_count
        members_count = await context.bot.get_chat_member_count(chat_id)
        logger.info(f"📊 العدد الإجمالي لأعضاء المجموعة: {members_count}")
        
        all_members = []
        
        # جلب المشرفين أولاً (يمكننا جلبهم مباشرة)
        try:
            admins = await context.bot.get_chat_administrators(chat_id)
            for admin in admins:
                user = admin.user
                if not user.is_bot:  # تجاهل البوتات
                    all_members.append({
                        'user_id': user.id,
                        'username': user.username,
                        'first_name': user.first_name,
                        'last_name': user.last_name
                    })
            logger.info(f"✅ تم جلب {len(admins)} مشرف")
        except Exception as e:
            logger.error(f"❌ خطأ في جلب المشرفين: {e}")
        
        # للأسف، لا توجد طريقة مباشرة لجلب جميع الأعضاء في الإصدارات الحديثة
        # لذلك سنعتمد على حفظ الأعضاء عند تفاعلهم فقط
        
        logger.info(f"✅ تم جلب {len(all_members)} عضو (المشرفين فقط)")
        return all_members
        
    except Exception as e:
        logger.error(f"❌ خطأ في جلب عدد الأعضاء: {e}")
        return []

async def save_all_members(chat_id, context):
    """حفظ جميع أعضاء المجموعة في قاعدة البيانات"""
    try:
        logger.info(f"⏳ جاري معالجة أعضاء المجموعة {chat_id}...")
        
        # 1. جلب المشرفين فقط (لا يمكن جلب جميع الأعضاء مباشرة)
        all_members = await get_all_chat_members(chat_id, context)
        
        if not all_members:
            logger.error("❌ لم يتم جلب أي أعضاء من المجموعة")
            return False
        
        # 2. حفظ المشرفين في قاعدة البيانات
        saved_count = 0
        for member in all_members:
            try:
                add_member(
                    member['user_id'],
                    str(chat_id),
                    member['username'],
                    member['first_name'],
                    member['last_name']
                )
                saved_count += 1
            except Exception as e:
                logger.error(f"❌ خطأ في حفظ العضو {member['user_id']}: {e}")
                continue
        
        logger.info(f"✅ تم حفظ {saved_count} عضو في قاعدة البيانات")
        
        return saved_count > 0
        
    except Exception as e:
        logger.error(f"❌ Error in save_all_members: {e}")
        return False

# ================== الأوامر الأساسية ==================

@admin_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = """
👋 *مرحبا بك في بوت إدارة المجموعة المتقدم* ⚙️

📌 *أوامر المشرفين:*
• /admins - عرض قائمة المشرفين
• /tagall - منشن لجميع الأعضاء (يدعم 2000+ عضو)
• /sync - مزامنة الأعضاء مع قاعدة البيانات
• /warn - تحذير عضو (بالرد على رسالته)
• /unwarn - إزالة تحذيرات عضو
• /warns - عرض تحذيرات عضو
• /setwarns [عدد] - ضبط عدد التحذيرات للطرد (حتى 40)
• /delete_links on/off - التحكم بحذف الروابط
• /warn_list - قائمة المحذرين
• /warnings on/off - تفعيل/تعطيل نظام التحذيرات
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
├ /tagall - عمل منشن لجميع الأعضاء (2000+ عضو)
├ /sync - مزامنة الأعضاء مع قاعدة البيانات
├ /warn - تحذير عضو (بالرد + سبب)
├ /unwarn - إزالة تحذيرات عضو
├ /warns - عرض تحذيرات عضو
├ /setwarns [عدد] - تحديد عدد التحذيرات للطرد (حتى 40)
├ /delete_links on/off - التحكم بحذف الروابط
├ /warn_list - عرض قائمة المحذرين
├ /warnings on/off - تفعيل/تعطيل نظام التحذيرات
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
    """مزامنة جميع أعضاء المجموعة مع قاعدة البيانات"""
    try:
        await update.message.reply_text("⏳ جاري مزامنة الأعضاء مع قاعدة البيانات...")
        
        if await save_all_members(update.effective_chat.id, context):
            members = get_members(str(update.effective_chat.id))
            await update.message.reply_text(f"✅ تم مزامنة {len(members)} عضو في قاعدة البيانات.\n\n💾 سيتم حفظ الأعضاء الجدد عند تفاعلهم في المجموعة.")
        else:
            await update.message.reply_text("❌ فشل في مزامنة الأعضاء.")
            
    except Exception as e:
        logger.error(f"Error in sync_members: {e}")
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
            logger.error(f"Error checking admin status in callback: {e}")
            await query.edit_message_text("❌ حدث خطأ أثناء التحقق من الصلاحيات!")
            return
        
        parts = query.data.split("_")
        action = parts[1]
        user_id = int(parts[2])
        chat_id = int(parts[3])  # تحويل إلى integer
        
        if action == "approve":
            try:
                await context.bot.ban_chat_member(chat_id, user_id)
                await query.edit_message_text(f"✅ تم طرد العضو بنجاح.")
                
                # إرسال إشعار للإدمن
                admin_msg = f"🚨 <b>تم طرد عضو</b>\n\n" \
                           f"👤 العضو: {user_id}\n" \
                           f"👥 المجموعة: {chat_id}\n" \
                           f"🛠️ تم الطرد بواسطة: {query.from_user.first_name}"
                await send_admin_notification(context, admin_msg)
                
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
            # إصلاح عرض username مع الشرطات السفلية
            if user.username:
                # استخدام الهروب للشرطات السفلية في Markdown
                username_display = f"@{user.username.replace('_', r'\_')}"
            else:
                username_display = user.full_name
            
            status = "👑 منشئ" if admin.status == "creator" else "🔧 مشرف"
            msg += f"• {username_display} ({status})\n"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error in admins command: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء جلب قائمة المشرفين.")
@admin_only
async def tagall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = str(update.effective_chat.id)
        
        # أولاً: حفظ جميع الأعضاء الحاليين في قاعدة البيانات
        await update.message.reply_text("⏳ جاري تحديث قائمة الأعضاء...")
        await save_all_members(update.effective_chat.id, context)
        
        # ثانياً: جلب الأعضاء من قاعدة البيانات
        members = get_members(chat_id, limit=2000)
        
        if not members:
            await update.message.reply_text("📭 لا يوجد أعضاء مخزنون في هذه المجموعة.\nسيتم حفظ الأعضاء عند تفاعلهم في المجموعة.")
            return
        mentions = []
        for member in members:
            user_id, username, first_name, last_name = member
            name = username or f"{first_name} {last_name}".strip() or f"user_{user_id}"
            mentions.append(f"[{name}](tg://user?id={user_id})")
        
        # إرسال المنشن على دفعات مع تأخير (40 عضو لكل رسالة)
        total_mentioned = 0
        batch_size = 40
        
        for i in range(0, len(mentions), batch_size):
            batch = mentions[i:i+batch_size]
            message = "📢 منشن لجميع الأعضاء:\n\n" + "\n".join(batch)
            await update.message.reply_text(message, parse_mode="Markdown")
            total_mentioned += len(batch)
            
            # تأخير 1 ثانية بين كل دفعة لتجنب الحظر
            await asyncio.sleep(1)
        
        await update.message.reply_text(f"✅ تم عمل منشن لـ {total_mentioned} عضو.")
        
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
            keyboard = [
                [
                    InlineKeyboardButton("✅ نعم، طرده", callback_data=f"kick_approve_{user_id}_{update.effective_chat.id}"),
                    InlineKeyboardButton("❌ لا، إلغاء", callback_data=f"kick_reject_{user_id}_{update.effective_chat.id}")
                ]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"⚠️ {user_name} وصل إلى الحد الأقصى للتحذيرات ({warns}/{max_warns})\n"
                f"هل تريد طرده الآن?",
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                f"⚠️ تم تحذير {user_name} ({warns}/{max_warns})\n"
                f"السبب: {reason}"
            )
            
        # إرسال إشعار للإدمن
        admin_msg = f"⚠️ <b>تم تحذير عضو</b>\n\n" \
                   f"👤 العضو: {user_name} (ID: {user_id})\n" \
                   f"📊 عدد التحذيرات: {warns}/{max_warns}\n" \
                   f"📝 السبب: {reason}\n" \
                   f"👥 المجموعة: {update.effective_chat.title}"
        await send_admin_notification(context, admin_msg)
            
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
            
            # إرسال إشعار للإدمن
            admin_msg = f"✅ <b>تم إزالة تحذيرات عضو</b>\n\n" \
                       f"👤 العضو: {user_name} (ID: {user_id})\n" \
                       f"👥 المجموعة: {update.effective_chat.title}"
            await send_admin_notification(context, admin_msg)
            
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
async def warnings_setting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.args or context.args[0].lower() not in ["on", "off"]:
            await update.message.reply_text("⚠️ الصيغة: /warnings on/off")
            return

        setting = context.args[0].lower() == "on"
        chat_id = str(update.effective_chat.id)
        save_chat_settings(chat_id, warnings_enabled=setting)
        
        status = "تفعيل" if setting else "تعطيل"
        await update.message.reply_text(f"✅ تم {status} نظام التحذيرات تلقائياً")
    except Exception as e:
        logger.error(f"Error in warnings_setting: {e}")
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
            
            # إرسال رسالة ترحيب بالعربية
            await update.message.reply_text(WELCOME_MESSAGES["ar"], parse_mode="Markdown")
            
            # حفظ العضو في قاعدة البيانات عند الانضمام
            add_member(
                member.id, 
                str(update.effective_chat.id),
                member.username,
                member.first_name,
                member.last_name
            )
            
            # إرسال إشعار للإدمن
            admin_msg = f"👋 <b>عضو جديد انضم للمجموعة</b>\n\n" \
                       f"👤 العضو: {member.first_name} (ID: {member.id})\n" \
                       f"👥 المجموعة: {update.effective_chat.title}"
            await send_admin_notification(context, admin_msg)
            
    except Exception as e:
        logger.error(f"Error in welcome_new_member: {e}")

def contains_banned_word(text):
    if not text:
        return False
    
    text = text.lower()
    for word_pattern in banned_words:
        if re.search(word_pattern, text, re.IGNORECASE):
            return True
    return False

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        message = update.message
        if not message or not message.text:
            return
            
        text = message.text
        user = message.from_user
        
        if not text:
            return
        
        # حفظ العضو في قاعدة البيانات عند إرسال أي رسالة
        add_member(
            user.id,
            str(update.effective_chat.id),
            user.username,
            user.first_name,
            user.last_name
        )
        
        # التحقق من إعدادات المجموعة
        settings = get_chat_settings(str(update.effective_chat.id))
        
        # التحقق من الكلمات الممنوعة (إذا كان نظام التحذيرات مفعلاً)
        if settings["warnings_enabled"] and contains_banned_word(text):
            if not await is_admin(update, context):
                try:
                    await message.delete()
                    # إضافة تحذير للعضو
                    warn_count = await warn_user(update.effective_chat.id, user.id, "كلمات غير لائقة", context.bot.id)
                    
                    # إرسال رسالة توضيحية للمجموعة مع منشن للعضو
                    warning_msg = f"🚫 تم حذف رسالة العضو [{user.first_name}](tg://user?id={user.id}) لاحتوائها على كلمات غير لائقة.\n\n" \
                                 f"📊 عدد تحذيراته: {warn_count}/{settings['max_warns']}\n" \
                                 f"⚖️ سيتم طرده تلقائياً عند الوصول إلى {settings['max_warns']} تحذيرات"
                    
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=warning_msg,
                        parse_mode="Markdown"
                    )
                    
                    # إرسال إشعار خاص للإدمن
                    admin_msg = f"🚨 <b>تم حذف رسالة مسيئة</b>\n\n" \
                               f"👤 العضو: [{user.first_name}](tg://user?id={user.id}) (ID: `{user.id}`)\n" \
                               f"💬 الرسالة: `{text[:100]}{'...' if len(text) > 100 else ''}`\n" \
                               f"📊 عدد التحذيرات: {warn_count}/{settings['max_warns']}\n" \
                               f"👥 المجموعة: {update.effective_chat.title}"
                    await send_admin_notification(context, admin_msg)
                    
                except Exception as e:
                    logger.error(f"Error deleting message: {e}")
                return
        
        # التحقق من الروابط (إذا كان نظام التحذيرات مفعلاً)
        if settings["warnings_enabled"] and settings["delete_links"] and re.search(r'(https?://|www\.|t\.me/)', text, re.IGNORECASE):
            if not await is_admin(update, context):
                try:
                    await message.delete()
                    # إضافة تحذير للعضو
                    warn_count = await warn_user(update.effective_chat.id, user.id, "نشر روابط", context.bot.id)
                    
                    # إرسال رسالة توضيحية للمجموعة مع منشن للعضو
                    warning_msg = f"🔗 تم حذف رسالة العضو [{user.first_name}](tg://user?id={user.id}) لاحتوائها على روابط.\n\n" \
                                 f"📊 عدد تحذيراته: {warn_count}/{settings['max_warns']}\n" \
                                 f"⚖️ سيتم طرده تلقائياً عند الوصول إلى {settings['max_warns']} تحذيرات"
                    
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=warning_msg,
                        parse_mode="Markdown"
                    )
                    
                    # إرسال إشعار خاص للإدمن
                    admin_msg = f"🔗 <b>تم حذف رسالة تحتوي على روابط</b>\n\n" \
                               f"👤 العضو: [{user.first_name}](tg://user?id={user.id}) (ID: `{user.id}`)\n" \
                               f"💬 الرسالة: `{text[:100]}{'...' if len(text) > 100 else ''}`\n" \
                               f"📊 عدد التحذيرات: {warn_count}/{settings['max_warns']}\n" \
                               f"👥 المجموعة: {update.effective_chat.title}"
                    await send_admin_notification(context, admin_msg)
                    
                except Exception as e:
                    logger.error(f"Error deleting message with link: {e}")
                return
        
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

async def home_handler(request):
    return web.Response(text="🤖 Bot is running successfully!")

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
    
    # بدء مهمة نبض الحياة في الخلفية
    asyncio.create_task(heartbeat_task())
    
    logger.info("✅ Bot started successfully with webhook and heartbeat!")

async def on_shutdown(app):
    await application.stop()
    await application.shutdown()
    logger.info("Bot stopped successfully!")

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
    application.add_handler(CommandHandler("warn_list", warn_list))
    application.add_handler(CommandHandler("setwarns", set_max_warns))
    application.add_handler(CommandHandler("delete_links", delete_links_setting))
    application.add_handler(CommandHandler("warnings", warnings_setting))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    application.add_error_handler(error_handler)

    web_app = web.Application()
    web_app.router.add_get('/', home_handler)
    web_app.router.add_post('/webhook', webhook_handler)
    web_app.on_startup.append(on_startup)
    web_app.on_shutdown.append(on_shutdown)

    web.run_app(web_app, host='0.0.0.0', port=PORT)

if __name__ == "__main__":
    main()


