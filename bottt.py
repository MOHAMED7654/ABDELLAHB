import logging
import json
import re 
import os
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    CallbackQueryHandler,
    CallbackContext
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

USER_FILE = "users.json"
WARN_FILE = "warns.json"
SETTINGS_FILE = "settings.json"

# تحميل البيانات
def load_data(filename, default={}):
    try:
        with open(filename, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

# حفظ البيانات
def save_data(data, filename):
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)

# تحميل البيانات الأولية
users_by_chat = load_data(USER_FILE)
warns_data = load_data(WARN_FILE)
settings = load_data(SETTINGS_FILE, {
    "max_warns": 3,
    "youtube_channel": "@Mik_emm",
    "delete_links": True
})

# الكلمات الممنوعة
banned_words = {
    "كلب", "حمار", "قحب", "زبي", "خرا", "بول",
    "ولد الحرام", "ولد القحبة", "يا قحبة", "نيك", "منيك",
    "مخنث", "قحبة", "حقير", "قذر"
}

# الردود التلقائية
auto_replies = {
    "سلام": "وعليكم السلام 🖐",
    "تصبح على خير": "وأنت من أهله 🤍🌙",
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
""",
    "en": """
Welcome to our refined Computer Science community.
You must adhere to the following set of rules:
1. Do not share links without permission
2. Avoid discussing off-topic subjects unless related to studies, and always speak politely
3. Refrain from suspicious private messaging
(You can ask any questions in the group, so this excuse is not acceptable)
Please note: we are only responsible for what happens within the group
4. Compliance with the supervisors’ decisions is essential to maintain order
Note: In necessary cases, you may contact the supervisors
(Females should reach out to the group owner, and males to the male admins)
🫧 𝓣𝓸𝓾𝓴𝓪 ꨄ︎
"""
}

# تهيئة التطبيق
application = None

async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return True
    try:
        member = await context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
        return member.status in ("administrator", "creator")
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
        return False

async def check_subscription(user_id):
    return True

async def warn_user(chat_id, user_id, reason=None):
    try:
        chat_id = str(chat_id)
        user_id = str(user_id)
        
        if chat_id not in warns_data:
            warns_data[chat_id] = {}
        
        if user_id not in warns_data[chat_id]:
            warns_data[chat_id][user_id] = {"count": 0, "reasons": []}
        
        warns_data[chat_id][user_id]["count"] += 1
        if reason:
            warns_data[chat_id][user_id]["reasons"].append(reason)
        
        save_data(warns_data, WARN_FILE)
        return warns_data[chat_id][user_id]["count"]
    except Exception as e:
        logger.error(f"Error in warn_user: {e}")
        return 0

async def get_warns(chat_id, user_id):
    try:
        chat_id = str(chat_id)
        user_id = str(user_id)
        return warns_data.get(chat_id, {}).get(user_id, {"count": 0, "reasons": []})
    except Exception as e:
        logger.error(f"Error in get_warns: {e}")
        return {"count": 0, "reasons": []}

async def reset_warns(chat_id, user_id):
    try:
        chat_id = str(chat_id)
        user_id = str(user_id)
        if chat_id in warns_data and user_id in warns_data[chat_id]:
            warns_data[chat_id].pop(user_id)
            save_data(warns_data, WARN_FILE)
            return True
        return False
    except Exception as e:
        logger.error(f"Error in reset_warns: {e}")
        return False

def admin_only(handler):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await is_admin(update, context):
            if update.effective_chat.type != "private":
                await update.message.reply_text("❌ هذا الأمر خاص بالمشرفين فقط.")
                return
        return await handler(update, context)
    return wrapper

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private" and not await is_admin(update, context):
        return
    
    if not await check_subscription(update.effective_user.id):
        keyboard = [
            [InlineKeyboardButton("اشترك في القناة", url="https://www.youtube.com/@Mik_emm")],
            [InlineKeyboardButton("تمت الاشتراك", callback_data="check_sub")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "⚠️ يجب الاشتراك في قناتنا أولاً:\nhttps://www.youtube.com/@Mik_emm",
            reply_markup=reply_markup
        )
        return
    
    await send_intro_message(update)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "check_sub":
        if await check_subscription(query.from_user.id):
            await query.edit_message_text("✅ شكراً للاشتراك! يمكنك الآن استخدام البوت.")
            await send_intro_message(update)
        else:
            await query.edit_message_text("❌ لم يتم العثور على اشتراكك. يرجى الاشتراك أولاً.")

async def send_intro_message(update: Update):
    await update.message.reply_text(
        "👋 مرحبا بك في بوت إدارة المجموعة المتقدم ⚙️\n\n"  
        "📌 يمكنك استخدام الأوامر التالية:\n\n"
        "📌 أوامر المشرفين:\n"
        "👮‍♂️ /admins - عرض الإداريين\n"
        "📢 /tagall - تاق لجميع الأعضاء\n"
        "⚠️ /warn @user - تحذير عضو\n"
        "📊 /warns @user - عرض تحذيرات عضو\n"
        "🔄 /unwarn @user - مسح تحذيرات عضو\n"
        "🔢 /setwarns 3 - ضبط عدد التحذيرات للطرد\n"
        "🔗 /delete_links on/off - التحكم بحذف الروابط\n"
        "📶 /ping - فحص حالة البوت\n\n"
        "🚀 صنع بواسطة mik_emm مع ❤️",
        parse_mode="Markdown"
    )

@admin_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
📚 **أوامر البوت للمشرفين فقط:**

👨‍💻 **أوامر الإدارة:**
├ /admins - عرض قائمة المشرفين
├ /tagall - عمل تاغ لجميع الأعضاء
├ /warn @المستخدم - تحذير مستخدم (+سبب)
├ /unwarn @المستخدم - إزالة تحذير من مستخدم
├ /warns @المستخدم - عرض تحذيرات مستخدم
├ /setwarns [عدد] - تحديد عدد التحذيرات للطرد
├ /delete_links [on/off] - التحكم بحذف الروابط
└ /warn_list - عرض قائمة المحذرين

🔗 **اشتراك القناة المطلوب:**
- يجب الاشتراك في قناة mik_emm لاستخدام البوت
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")

@admin_only
async def admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        admins_list = await context.bot.get_chat_administrators(update.effective_chat.id)
        msg = "👮‍♂️ قائمة الإداريين:\n\n"
        for admin in admins_list:
            user = admin.user
            username = f"@{user.username}" if user.username else user.full_name
            msg += f"• {username}\n"
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"Error in admins command: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء جلب قائمة المشرفين.")

@admin_only
async def tagall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = str(update.effective_chat.id)
        user_ids = users_by_chat.get(chat_id, [])

        if not user_ids:
            await update.message.reply_text("📭 لا يوجد أعضاء مخزنون في هذه المجموعة.")
            return

        mentions = [f"[.](tg://user?id={uid})" for uid in user_ids]
        max_per_msg = 10
        for i in range(0, len(mentions), max_per_msg):
            await update.message.reply_text(" ".join(mentions[i:i+max_per_msg]), parse_mode="Markdown")
        await update.message.reply_text(f"📢 تم عمل تاق لـ {len(user_ids)} عضو.")
    except Exception as e:
        logger.error(f"Error in tagall: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء عمل التاق.")

@admin_only
async def warn_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.args or len(context.args) < 1:
            await update.message.reply_text("⚠️ الصيغة: /warn @username [السبب]")
            return

        username = context.args[0]
        reason = " ".join(context.args[1:]) if len(context.args) > 1 else None

        if not username.startswith('@'):
            await update.message.reply_text("⚠️ يجب ذكر اسم المستخدم مع @")
            return

        if update.message.reply_to_message:
            user_id = update.message.reply_to_message.from_user.id
        else:
            try:
                chat_member = await context.bot.get_chat_member(update.effective_chat.id, username)
                user_id = chat_member.user.id
            except Exception:
                await update.message.reply_text("⚠️ لم يتم العثور على المستخدم")
                return

        warns = await warn_user(update.effective_chat.id, user_id, reason)
        max_warns = settings.get(str(update.effective_chat.id), {}).get("max_warns", 3)

        if warns >= max_warns:
            await update.effective_chat.ban_member(user_id)
            await update.message.reply_text(f"🚷 تم طرد {username} لتجاوزه حد التحذيرات ({max_warns})")
        else:
            await update.message.reply_text(f"⚠️ تم تحذير {username} ({warns}/{max_warns})" + (f"\nالسبب: {reason}" if reason else ""))

    except Exception as e:
        logger.error(f"Error in warn command: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء تنفيذ الأمر.")

@admin_only
async def unwarn_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.args or len(context.args) < 1:
            await update.message.reply_text("⚠️ الصيغة: /unwarn @username")
            return

        username = context.args[0]

        if not username.startswith('@'):
            await update.message.reply_text("⚠️ يجب ذكر اسم المستخدم مع @")
            return
                     
        if update.message.reply_to_message:
            user_id = update.message.reply_to_message.from_user.id
        else:
            try:
                chat_member = await context.bot.get_chat_member(update.effective_chat.id, username)
                user_id = chat_member.user.id
            except Exception:
                await update.message.reply_text("⚠️ لم يتم العثور على المستخدم")
                return

        if await reset_warns(update.effective_chat.id, user_id):
            await update.message.reply_text(f"✅ تم إزالة جميع التحذيرات لـ {username}")
        else:
            await update.message.reply_text(f"ℹ️ لا يوجد تحذيرات لـ {username}")

    except Exception as e:
        logger.error(f"Error in unwarn command: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء تنفيذ الأمر.")

@admin_only
async def get_warns_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.args or len(context.args) < 1:
            await update.message.reply_text("⚠️ الصيغة: /warns @username")
            return

        username = context.args[0]

        if not username.startswith('@'):
            await update.message.reply_text("⚠️ يجب ذكر اسم المستخدم مع @")
            return

        if update.message.reply_to_message:
            user_id = update.message.reply_to_message.from_user.id
        else:
            try:
                chat_member = await context.bot.get_chat_member(update.effective_chat.id, username)
                user_id = chat_member.user.id
            except Exception:
                await update.message.reply_text("⚠️ لم يتم العثور على المستخدم")
                return

        warns_info = await get_warns(update.effective_chat.id, user_id)
        max_warns = settings.get(str(update.effective_chat.id), {}).get("max_warns", 3)

        if warns_info["count"] > 0:
            message = f"⚠️ تحذيرات {username}: {warns_info['count']}/{max_warns}\n"
            if warns_info["reasons"]:
                message += "الأسباب:\n" + "\n".join(f"- {reason}" for reason in warns_info["reasons"])
            await update.message.reply_text(message)
        else:
            await update.message.reply_text(f"ℹ️ لا يوجد تحذيرات لـ {username}")

    except Exception as e:
        logger.error(f"Error in warns command: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء تنفيذ الأمر.")

@admin_only
async def set_max_warns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not context.args or not context.args[0].isdigit():
            await update.message.reply_text("⚠️ الصيغة: /setwarns [عدد]")
            return

        max_warns = int(context.args[0])
        chat_id = str(update.effective_chat.id)
        
        if chat_id not in settings:
            settings[chat_id] = {}
        
        settings[chat_id]["max_warns"] = max_warns
        save_data(settings, SETTINGS_FILE)
        
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
        
        if chat_id not in settings:
            settings[chat_id] = {}
        
        settings[chat_id]["delete_links"] = setting
        save_data(settings, SETTINGS_FILE)
        
        status = "تفعيل" if setting else "تعطيل"
        await update.message.reply_text(f"✅ تم {status} حذف الروابط تلقائياً")
    except Exception as e:
        logger.error(f"Error in delete_links_setting: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء تعديل الإعداد.")

@admin_only
async def warn_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = str(update.effective_chat.id)
        if chat_id not in warns_data or not warns_data[chat_id]:
            await update.message.reply_text("ℹ️ لا يوجد أعضاء محذرين حالياً")
            return

        message = "📋 قائمة الأعضاء المحذرين:\n\n"
        for user_id, warn_info in warns_data[chat_id].items():
            try:
                user = await context.bot.get_chat_member(chat_id, int(user_id))
                username = f"@{user.user.username}" if user.user.username else user.user.full_name
                message += f"• {username}: {warn_info['count']} تحذيرات\n"
            except Exception:
                message += f"• مستخدم (ID: {user_id}): {warn_info['count']} تحذيرات\n"

        await update.message.reply_text(message)
    except Exception as e:
        logger.error(f"Error in warn_list: {e}")
        await update.message.reply_text("⚠️ حدث خطأ أثناء جلب قائمة المحذرين.")

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 البوت يعمل بشكل طبيعي!")

async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        for member in update.message.new_chat_members:
            if member.id == context.bot.id:
                continue
            
            await update.message.reply_text(
                WELCOME_MESSAGES["ar"],
                parse_mode="Markdown"
            )
            
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=WELCOME_MESSAGES["en"],
                parse_mode="Markdown",
                reply_to_message_id=update.message.message_id
            )
            
            chat_id = str(update.effective_chat.id)
            if chat_id not in users_by_chat:
                users_by_chat[chat_id] = []
            if member.id not in users_by_chat[chat_id]:
                users_by_chat[chat_id].append(member.id)
                save_data(users_by_chat, USER_FILE)
    except Exception as e:
        logger.error(f"Error in welcome_new_member: {e}")

def contains_banned_word(text):
    if not text:
        return False
    text = text.lower()
    for word in banned_words:
        if word in text:
            return True
    return False

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        return
    
    try:
        message = update.message
        chat_id = str(update.effective_chat.id)
        user_id = update.effective_user.id
        is_adm = await is_admin(update, context)

        if settings.get(chat_id, {}).get("delete_links", True):
            if re.search(r'(https?://\S+|www\.\S+)', message.text or ""):
                if not is_adm:
                    try:
                        await message.delete()
                        await message.reply_text(
                            f"🚫 {update.effective_user.mention_html()} الروابط غير مسموح بها!",
                            parse_mode="HTML"
                        )
                        return
                    except Exception as e:
                        logger.error(f"Error deleting link: {e}")

        if contains_banned_word(message.text):
            if not is_adm:
                try:
                    await message.delete()
                    warns = await warn_user(chat_id, user_id, "كلمة مسيئة")
                    max_warns = settings.get(chat_id, {}).get("max_warns", 3)
                    
                    if warns >= max_warns:
                        await update.effective_chat.ban_member(user_id)
                        await update.effective_chat.send_message(
                            f"🚷 تم طرد {update.effective_user.mention_html()} لتجاوزه حد التحذيرات",
                            parse_mode="HTML"
                        )
                    else:
                        await update.effective_chat.send_message(
                            f"⚠️ تحذير لـ {update.effective_user.mention_html()}! ({warns}/{max_warns})\n" +
                            "الرجاء الالتزام بقوانين المجموعة",
                            parse_mode="HTML"
                        )
                except Exception as e:
                    logger.error(f"Error handling banned word: {e}")

        if message.text in auto_replies:
            await message.reply_text(auto_replies[message.text])

        if chat_id not in users_by_chat:
            users_by_chat[chat_id] = []
        if user_id not in users_by_chat[chat_id]:
            users_by_chat[chat_id].append(user_id)
            save_data(users_by_chat, USER_FILE)
    except Exception as e:
        logger.error(f"Error in handle_messages: {e}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(msg="حدث خطأ في البوت", exc_info=context.error)
    if update and update.effective_message:
        await update.effective_message.reply_text("⚠️ حدث خطأ غير متوقع في البوت. يرجى المحاولة لاحقاً.")

# ويب هوك handlers
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
    await application.bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=SECRET_TOKEN,
        drop_pending_updates=True
    )
    logger.info("Webhook set successfully")

async def on_startup(app):
    await set_webhook()

def setup_application():
    global application
    application = Application.builder().token(TOKEN).build()
    
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
    
    return application

def main():
    app = setup_application()
    
    web_app = web.Application()
    web_app.router.add_post('/webhook', webhook_handler)
    web_app.on_startup.append(on_startup)
    
    web.run_app(web_app, host='0.0.0.0', port=PORT)

if __name__ == "__main__":
    main()
