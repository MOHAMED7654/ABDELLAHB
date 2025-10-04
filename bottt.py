import logging
import re
import os
import asyncio
import aiohttp
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
HEARTBEAT_INTERVAL = 10 * 60

# إعدادات إضافية
ADMIN_IDS = [7635779264, 7453316860]
KEEP_ALIVE_URL = "https://abdellahb-2.onrender.com"

# تخزين البيانات في الذاكرة (بدون قاعدة بيانات)
chat_settings = {}
user_warnings = {}
active_members = {}

# الكلمات الممنوعة
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

WELCOME_MESSAGE = "أهلا وسهلا بك في مجتمعنا الراقي"

# تهيئة التطبيق
application = Application.builder().token(TOKEN).build()

# ================== نظام التخزين في الذاكرة ==================

def get_chat_settings(chat_id):
    """الحصول على إعدادات المجموعة"""
    chat_id_str = str(chat_id)
    if chat_id_str not in chat_settings:
        chat_settings[chat_id_str] = {
            "max_warns": 3,
            "delete_links": True,
            "warnings_enabled": True
        }
    return chat_settings[chat_id_str]

def save_chat_settings(chat_id, **kwargs):
    """حفظ إعدادات المجموعة"""
    chat_id_str = str(chat_id)
    if chat_id_str not in chat_settings:
        chat_settings[chat_id_str] = {}
    
    for key, value in kwargs.items():
        if value is not None:
            chat_settings[chat_id_str][key] = value

def add_warning(user_id, chat_id, reason, admin_id=None):
    """إضافة تحذير للعضو"""
    key = f"{chat_id}_{user_id}"
    if key not in user_warnings:
        user_warnings[key] = []
    
    user_warnings[key].append({
        "reason": reason,
        "date": asyncio.get_event_loop().time(),
        "admin_id": admin_id
    })

def get_warning_count(user_id, chat_id):
    """الحصول على عدد تحذيرات العضو"""
    key = f"{chat_id}_{user_id}"
    return len(user_warnings.get(key, []))

def get_warning_reasons(user_id, chat_id):
    """الحصول على أسباب التحذيرات"""
    key = f"{chat_id}_{user_id}"
    return [warn["reason"] for warn in user_warnings.get(key, [])]

def reset_warnings(user_id, chat_id):
    """إزالة جميع تحذيرات العضو"""
    key = f"{chat_id}_{user_id}"
    if key in user_warnings:
        del user_warnings[key]
        return True
    return False

def get_warned_members(chat_id):
    """الحصول على قائمة المحذرين"""
    warned = []
    chat_id_str = str(chat_id)
    
    for key, warnings in user_warnings.items():
        if key.startswith(chat_id_str + "_"):
            user_id = int(key.split("_")[1])
            warned.append((user_id, len(warnings)))
    
    return warned

def add_active_member(chat_id, user_id, username, first_name, last_name):
    """إضافة عضو نشط للتاق"""
    chat_id_str = str(chat_id)
    if chat_id_str not in active_members:
        active_members[chat_id_str] = {}
    
    active_members[chat_id_str][user_id] = {
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
        "last_seen": asyncio.get_event_loop().time()
    }

def get_active_members(chat_id, limit=200):
    """الحصول على الأعضاء النشطين"""
    chat_id_str = str(chat_id)
    if chat_id_str not in active_members:
        return []
    
    # ترتيب الأعضاء حسب آخر نشاط
    members = list(active_members[chat_id_str].items())
    members.sort(key=lambda x: x[1]["last_seen"], reverse=True)
    
    return members[:limit]

# ================== الوظائف المساعدة ==================

async def heartbeat_task():
    """نبض الحياة"""
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            try:
                async with session.get(KEEP_ALIVE_URL) as response:
                    if response.status == 200:
                        logger.info("✅ تم إرسال نبضة حياة بنجاح")
            except Exception as e:
                logger.error(f"❌ خطأ في نبضة الحياة: {e}")
            await asyncio.sleep(HEARTBEAT_INTERVAL)

async def send_admin_notification(context, message):
    """إرسال إشعار للمشرفين"""
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=message,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"❌ فشل في إرسال إشعار للإدمن {admin_id}: {e}")

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التحقق إذا كان المستخدم مشرف"""
    if update.effective_user.id in ADMIN_IDS:
        return True
        
    if update.effective_chat.type == "private":
        return False
        
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        return member.status in ("administrator", "creator")
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        return False

def admin_only(handler):
    """مشرفين فقط"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await is_admin(update, context):
            return
        return await handler(update, context)
    return wrapper

# ================== نظام التاق الشامل ==================

@admin_only
async def tagall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تاق شامل باستخدام جميع الطرق المتاحة"""
    try:
        chat_id = update.effective_chat.id
        
        # الحصول على عدد الأعضاء
        members_count = await context.bot.get_chat_member_count(chat_id)
        await update.message.reply_text(f"👥 جاري تجهيز تاق لـ {members_count} عضو...")
        
        mentions = []
        total_mentioned = 0
        
        # الطريقة 1: المشرفين (متاحة دائماً)
        try:
            admins = await context.bot.get_chat_administrators(chat_id)
            for admin in admins:
                user = admin.user
                if not user.is_bot:
                    name = f"@{user.username}" if user.username else user.first_name
                    mention_text = f"[{name}](tg://user?id={user.id})"
                    if mention_text not in mentions:
                        mentions.append(mention_text)
                        total_mentioned += 1
        except Exception as e:
            logger.error(f"Error getting admins: {e}")
        
        # الطريقة 2: الأعضاء النشطين من الذاكرة
        active_members_list = get_active_members(chat_id, limit=200)
        for user_id, member_info in active_members_list:
            name = f"@{member_info['username']}" if member_info['username'] else member_info['first_name']
            mention_text = f"[{name}](tg://user?id={user_id})"
            if mention_text not in mentions and len(mentions) < 200:
                mentions.append(mention_text)
                total_mentioned += 1
        
        # الطريقة 3: محاولة جلب أعضاء جدد من خلال الرد على الرسائل
        if len(mentions) < 50:  # إذا كان العدد قليلاً
            try:
                # استخدام معلومات المجموعة الأساسية
                chat = await context.bot.get_chat(chat_id)
                if chat.username:
                    mentions.append(f"[قناة المجموعة](https://t.me/{chat.username})")
            except:
                pass
        
        if mentions:
            # تقسيم إلى مجموعات صغيرة
            batch_size = 30
            batch_count = 0
            
            for i in range(0, len(mentions), batch_size):
                batch = mentions[i:i + batch_size]
                batch_count += 1
                message_text = f"📢 التاق الجماعي (الجزء {batch_count}):\n\n" + "\n".join(batch)
                await update.message.reply_text(message_text, parse_mode="Markdown")
                await asyncio.sleep(0.5)
            
            await update.message.reply_text(
                f"✅ تم الانتهاء من التاق\n"
                f"📊 عدد الأعضاء: {total_mentioned}\n"
            )
        else:
            await update.message.reply_text(
                "❌ لم أتمكن من جلب أعضاء للتاق\n\n"
                "💡 الحلول:\n"
                "• تأكد من تفعيل البوت كمشرف\n"
            )
        
    except Exception as e:
        logger.error(f"Error in tagall: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء التاق")

@admin_only
async def force_tagall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تاق قوي يحاول جلب أكبر عدد ممكن"""
    try:
        chat_id = update.effective_chat.id
        
        await update.message.reply_text("🔄 جاري تجهيز تاق قوي...")
        
        mentions = []
        
        # 1. المشرفين أولاً
        admins = await context.bot.get_chat_administrators(chat_id)
        for admin in admins:
            user = admin.user
            if not user.is_bot:
                name = f"@{user.username}" if user.username else user.first_name
                mentions.append(f"[{name}](tg://user?id={user.id})")
        
        # 2. الأعضاء النشطين
        active_members_list = get_active_members(chat_id, limit=150)
        for user_id, member_info in active_members_list:
            name = f"@{member_info['username']}" if member_info['username'] else member_info['first_name']
            mentions.append(f"[{name}](tg://user?id={user_id})")
        
        if mentions:
            # إرسال جميع المنشنات في رسالة واحدة إذا كان العدد معقول
            if len(mentions) <= 100:
                message_text = "📢 التاق القوي:\n\n" + "\n".join(mentions)
                await update.message.reply_text(message_text, parse_mode="Markdown")
            else:
                # تقسيم إذا كان العدد كبير
                for i in range(0, len(mentions), 50):
                    batch = mentions[i:i + 50]
                    message_text = f"📢 التاق القوي (الجزء {i//50 + 1}):\n\n" + "\n".join(batch)
                    await update.message.reply_text(message_text, parse_mode="Markdown")
                    await asyncio.sleep(0.5)
            
            await update.message.reply_text(f"✅ تم التاق لـ {len(mentions)} عضو")
        else:
            await update.message.reply_text("❌ لا يوجد أعضاء مسجلين بعد")
            
    except Exception as e:
        logger.error(f"Error in force_tagall: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء التاق القوي")

# ================== الأوامر الأساسية ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = """
👋 *مرحبا بك في بوت إدارة المجموعة* ⚙️

📌 *أوامر المشرفين:*
• /admins - عرض قائمة المشرفين
• /tagall - تاق شامل    
• /force_tagall - تاق  
• /warn - تحذير عضو
• /unwarn - إزالة تحذيرات عضو
• /warns - عرض تحذيرات عضو
• /warn_list - قائمة المحذرين
• /setwarns [عدد] - ضبط عدد التحذيرات
• /delete_links on/off - التحكم بحذف الروابط
• /warnings on/off - تفعيل/تعطيل التحذيرات
• /ping - فحص حالة البوت

🚀 *صنع بواسطة:* @Mik_emm
"""
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
📚 *أوامر البوت المتاحة:*

👨‍💻 *أوامر الإدارة (للمشرفين فقط):*
├ /admins - عرض قائمة المشرفين
├ /tagall - تاق شامل للمشرفين والنشطين
├ /force_tagall - تاق   
├ /warn - تحذير عضو (بالرد)
├ /unwarn - إزالة تحذيرات عضو
├ /warns - عرض تحذيرات عضو
├ /warn_list - قائمة المحذرين
├ /setwarns [عدد] - ضبط التحذيرات (1-40)
├ /delete_links on/off - حذف الروابط
├ /warnings on/off - نظام التحذيرات
└ /ping - فحص حالة البوت

🔧 *ميزات تلقائية:*
• حذف الروابط تلقائياً
• منع الكلمات المسيئة
• الترحيب بالأعضاء الجدد
• الردود التلقائية
• حفظ الأعضاء النشطين للتاق

📝 *للاستفسار:* @Mik_emm
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")

@admin_only
async def admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        admins_list = await context.bot.get_chat_administrators(update.effective_chat.id)
        msg = "👮‍♂️ *قائمة الإداريين:*\n\n"
        for admin in admins_list:
            user = admin.user
            if user.username:
                username_display = f"@{user.username}"
            else:
                username_display = user.full_name
            
            status = "👑 منشئ" if admin.status == "creator" else "🔧 مشرف"
            msg += f"• {username_display} ({status})\n"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error in admins command: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء جلب قائمة المشرفين.")

# ================== نظام التحذيرات ==================

@admin_only
async def warn_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message.reply_to_message:
            await update.message.reply_text("⚠️ يرجى الرد على رسالة المستخدم للتحذير")
            return

        user_id = update.message.reply_to_message.from_user.id
        user_name = update.message.reply_to_message.from_user.first_name
        reason = " ".join(context.args) if context.args else "بدون سبب"

        add_warning(user_id, update.effective_chat.id, reason, update.effective_user.id)
        warn_count = get_warning_count(user_id, update.effective_chat.id)
        settings = get_chat_settings(update.effective_chat.id)
        max_warns = settings["max_warns"]

        await update.message.reply_text(
            f"⚠️ تم تحذير {user_name} ({warn_count}/{max_warns})\n"
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

        if reset_warnings(user_id, update.effective_chat.id):
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
        warn_count = get_warning_count(user_id, update.effective_chat.id)
        reasons = get_warning_reasons(user_id, update.effective_chat.id)
        settings = get_chat_settings(update.effective_chat.id)
        max_warns = settings["max_warns"]

        if warn_count > 0:
            message = f"⚠️ تحذيرات {user_name}: {warn_count}/{max_warns}\n"
            if reasons:
                message += "الأسباب:\n" + "\n".join(f"• {reason}" for reason in reasons)
            await update.message.reply_text(message)
        else:
            await update.message.reply_text(f"ℹ️ لا يوجد تحذيرات لـ {user_name}")
    except Exception as e:
        logger.error(f"Error in warns command: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء تنفيذ الأمر.")

@admin_only
async def warn_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = update.effective_chat.id
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

        save_chat_settings(update.effective_chat.id, max_warns=max_warns)
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
        save_chat_settings(update.effective_chat.id, delete_links=setting)
        
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
        save_chat_settings(update.effective_chat.id, warnings_enabled=setting)
        
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
            
            await update.message.reply_text(WELCOME_MESSAGE, parse_mode="Markdown")
            
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
        
        # حفظ العضو النشط للتاق
        add_active_member(
            update.effective_chat.id,
            user.id,
            user.username,
            user.first_name,
            user.last_name
        )
        
        # التحقق من إعدادات المجموعة
        settings = get_chat_settings(update.effective_chat.id)
        
        # التحقق من الكلمات الممنوعة
        if settings["warnings_enabled"] and contains_banned_word(text):
            if not await is_admin(update, context):
                try:
                    await message.delete()
                    warn_count = get_warning_count(user.id, update.effective_chat.id)
                    
                    warning_msg = f"🚫 تم حذف رسالة العضو [{user.first_name}](tg://user?id={user.id}) لاحتوائها على كلمات غير لائقة.\n\n" \
                                 f"📊 عدد تحذيراته: {warn_count}/{settings['max_warns']}"
                    
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=warning_msg,
                        parse_mode="Markdown"
                    )
                    
                except Exception as e:
                    logger.error(f"Error deleting message: {e}")
                return
        
        # التحقق من الروابط
        if settings["warnings_enabled"] and settings["delete_links"] and re.search(r'(https?://|www\.|t\.me/)', text, re.IGNORECASE):
            if not await is_admin(update, context):
                try:
                    await message.delete()
                    warn_count = get_warning_count(user.id, update.effective_chat.id)
                    
                    warning_msg = f"🔗 تم حذف رسالة العضو [{user.first_name}](tg://user?id={user.id}) لاحتوائها على روابط.\n\n" \
                                 f"📊 عدد تحذيراته: {warn_count}/{settings['max_warns']}"
                    
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=warning_msg,
                        parse_mode="Markdown"
                    )
                    
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
    application.add_handler(CommandHandler("tagall", tagall))
    application.add_handler(CommandHandler("force_tagall", force_tagall))
    application.add_handler(CommandHandler("admins", admins))
    application.add_handler(CommandHandler("warn", warn_user_command))
    application.add_handler(CommandHandler("unwarn", unwarn_user_command))
    application.add_handler(CommandHandler("warns", get_warns_command))
    application.add_handler(CommandHandler("warn_list", warn_list))
    application.add_handler(CommandHandler("setwarns", set_max_warns))
    application.add_handler(CommandHandler("delete_links", delete_links_setting))
    application.add_handler(CommandHandler("warnings", warnings_setting))
    application.add_handler(CommandHandler("ping", ping))
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
