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

async def get_warns(chat_id, user_id):
    return warns_data.get(str(chat_id), {}).get(str(user_id), {"count": 0, "reasons": []})

async def reset_warns(chat_id, user_id):
    chat_id = str(chat_id)
    user_id = str(user_id)
    if chat_id in warns_data and user_id in warns_data[chat_id]:
        warns_data[chat_id].pop(user_id)
        save_data(warns_data, WARN_FILE)
        return True
    return False

def admin_only(handler):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await is_admin(update, context):
            if update.effective_chat.type != "private":
                await update.message.reply_text("❌ هذا الأمر خاص بالمشرفين فقط.")
                return
        return await handler(update, context)
    return wrapper

# ================== الأوامر ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_subscription(update.effective_user.id):
        keyboard = [
            [InlineKeyboardButton("اشترك في القناة", url="https://www.youtube.com/@Mik_emm")],
            [InlineKeyboardButton("تمت الاشتراك", callback_data="check_sub")]
        ]
        await update.message.reply_text(
            "⚠️ يجب الاشتراك أولاً",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    await update.message.reply_text("👋 مرحبا بك في بوت إدارة المجموعة ⚙️")

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "check_sub" and await check_subscription(query.from_user.id):
        await query.edit_message_text("✅ شكراً للاشتراك! يمكنك الآن استخدام البوت.")

@admin_only
async def admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    admins_list = await context.bot.get_chat_administrators(update.effective_chat.id)
    msg = "👮‍♂️ قائمة الإداريين:\n"
    for admin in admins_list:
        user = admin.user
        name = f"@{user.username}" if user.username else user.full_name
        msg += f"• {name}\n"
    await update.message.reply_text(msg)

@admin_only
async def tagall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user_ids = users_by_chat.get(chat_id, [])
    mentions = [f"[.](tg://user?id={uid})" for uid in user_ids]
    max_per_msg = 10
    for i in range(0, len(mentions), max_per_msg):
        await update.message.reply_text(" ".join(mentions[i:i+max_per_msg]), parse_mode="Markdown")

@admin_only
async def warn_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        user_id = update.message.reply_to_message.from_user.id
    else:
        await update.message.reply_text("⚠️ يرجى الرد على رسالة المستخدم للتحذير")
        return
    reason = " ".join(context.args) if context.args else None
    warns = await warn_user(update.effective_chat.id, user_id, reason)
    max_warns = settings.get(str(update.effective_chat.id), {}).get("max_warns", 3)
    if warns >= max_warns:
        await update.effective_chat.ban_member(user_id)
        await update.message.reply_text(f"🚷 تم طرد العضو لتجاوز حد التحذيرات ({max_warns})")
    else:
        await update.message.reply_text(f"⚠️ تم تحذير العضو ({warns}/{max_warns})")

@admin_only
async def unwarn_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        user_id = update.message.reply_to_message.from_user.id
        await reset_warns(update.effective_chat.id, user_id)
        await update.message.reply_text("✅ تم إزالة جميع التحذيرات")
    else:
        await update.message.reply_text("⚠️ يرجى الرد على رسالة العضو")

@admin_only
async def get_warns_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.reply_to_message:
        user_id = update.message.reply_to_message.from_user.id
        info = await get_warns(update.effective_chat.id, user_id)
        await update.message.reply_text(f"⚠️ التحذيرات: {info['count']}")
    else:
        await update.message.reply_text("⚠️ يرجى الرد على رسالة العضو")

@admin_only
async def set_max_warns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args and context.args[0].isdigit():
        max_warns = int(context.args[0])
        chat_id = str(update.effective_chat.id)
        settings.setdefault(chat_id, {})["max_warns"] = max_warns
        save_data(settings, SETTINGS_FILE)
        await update.message.reply_text(f"✅ تم ضبط الحد الأقصى للتحذيرات: {max_warns}")

@admin_only
async def delete_links_setting(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args and context.args[0].lower() in ["on", "off"]:
        setting = context.args[0].lower() == "on"
        chat_id = str(update.effective_chat.id)
        settings.setdefault(chat_id, {})["delete_links"] = setting
        save_data(settings, SETTINGS_FILE)
        status = "تفعيل" if setting else "تعطيل"
        await update.message.reply_text(f"✅ تم {status} حذف الروابط تلقائياً")

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 البوت يعمل بشكل طبيعي!")

# ================== التعامل مع الرسائل ==================
async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if not text:
        return
    # الردود التلقائية
    for key, val in auto_replies.items():
        if key in text:
            await update.message.reply_text(val)
            break
    # حذف الكلمات الممنوعة
    if any(word in text for word in banned_words):
        if await is_admin(update, context):
            return
        await update.message.delete()
        await warn_user_command(update, context)

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
        logger.error(f"Webhook error: {e}")
        return web.Response(text="Error", status=500)

async def set_webhook():
    await application.bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=SECRET_TOKEN,
        drop_pending_updates=True
    )
    logger.info("Webhook set successfully")

async def on_startup(app):
    await application.initialize()
    await set_webhook()
    await application.start()
    logger.info("Application started and initialized")

def main():
    # إضافة جميع handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(CommandHandler("admins", admins))
    application.add_handler(CommandHandler("tagall", tagall))
    application.add_handler(CommandHandler("warn", warn_user_command))
    application.add_handler(CommandHandler("unwarn", unwarn_user_command))
    application.add_handler(CommandHandler("warns", get_warns_command))
    application.add_handler(CommandHandler("setwarns", set_max_warns))
    application.add_handler(CommandHandler("delete_links", delete_links_setting))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_messages))

    web_app = web.Application()
    web_app.router.add_post('/webhook', webhook_handler)
    web_app.on_startup.append(on_startup)

    web.run_app(web_app, host='0.0.0.0', port=PORT)

if __name__ == "__main__":
    main()
