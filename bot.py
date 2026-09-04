import os
import re
import sqlite3
import logging
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

import requests
from openpyxl import Workbook

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# =========================================================
# سيرفر الويب الوهمي لإرضاء Render (Port Binding)
# =========================================================

def run_dummy_server():
    port = int(os.getenv("PORT", 10000))
    class DummyHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is alive and running!")
    
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()

# =========================================================
# الإعدادات العامة
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DB_NAME = "finance_pro.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

RATES_CACHE = {
    "TRY_TO_USD": None,
    "TRY_TO_EUR": None,
    "last_fetched": None
}

# =========================================================
# قاعدة البيانات
# =========================================================

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT,
            username TEXT,
            monthly_budget REAL DEFAULT 0,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            item TEXT NOT NULL,
            category TEXT,
            amount_try REAL NOT NULL,
            payment_method TEXT,
            location TEXT,
            note TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def register_user(user):
    conn = get_db()
    conn.execute("""
        INSERT OR IGNORE INTO users (user_id, first_name, username, created_at)
        VALUES (?, ?, ?, ?)
    """, (user.id, user.first_name or "", user.username or "", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.execute("""
        UPDATE users SET first_name = ?, username = ? WHERE user_id = ?
    """, (user.first_name or "", user.username or "", user.id))
    conn.commit()
    conn.close()

# =========================================================
# جلب أسعار العملات (مصحح ومضمون)
# =========================================================

def get_live_rates():
    now = datetime.now()
    if (RATES_CACHE["last_fetched"] and 
            now - RATES_CACHE["last_fetched"] < timedelta(minutes=15) and 
            RATES_CACHE["TRY_TO_USD"] is not None):
        return RATES_CACHE["TRY_TO_USD"], RATES_CACHE["TRY_TO_EUR"]

    try:
        # استخدام مصدر بديل ومستقر لأسعار الصرف
        url = "https://open.er-api.com/v6/latest/TRY"
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("result") == "success":
                rates = data.get("rates", {})
                usd = rates.get("USD") # كم تساوي 1 ليرة بالدولار
                eur = rates.get("EUR") # كم تساوي 1 ليرة باليورو
                if usd and eur:
                    RATES_CACHE["TRY_TO_USD"] = usd
                    RATES_CACHE["TRY_TO_EUR"] = eur
                    RATES_CACHE["last_fetched"] = now
                    return usd, eur
    except Exception as e:
        logger.warning("API Error: %s", e)

    # قيم احتياطية في حال تعذر الاتصال مؤقتاً لمنع توقف البوت
    return RATES_CACHE.get("TRY_TO_USD") or 0.03, RATES_CACHE.get("TRY_TO_EUR") or 0.028

def format_money(value):
    return f"{value:,.2f}"

def detect_category(text):
    t = text.lower()
    categories = {
        "🍔 طعام": ["مطعم", "اكل", "أكل", "غداء", "عشاء", "فطور", "برغر", "burger", "pizza", "بيتزا", "دجاج", "شاورما", "مقهى", "قهوة", "كافيه"],
        "🛒 تسوق": ["سوبرماركت", "ماركت", "بقالة", "ملابس", "حذاء", "شراء", "تسوق", "trendyol", "بيم", "bim"],
        "🚗 مواصلات": ["بنزين", "مازوت", "ديزل", "تكسي", "تاكسي", "باص", "مترو", "مواصلات", "سيارة"],
        "🏠 منزل": ["بيت", "منزل", "ايجار", "إيجار", "كهرباء", "ماء", "غاز", "صيانة"],
        "📱 اتصالات": ["ترك", "turkcell", "vodafone", "انترنت", "نت", "هاتف", "شحن"],
        "💊 صحة": ["صيدلية", "دواء", "دكتور", "طبيب", "مشفى", "مستشفى", "علاج"],
        "🎓 تعليم": ["مدرسة", "جامعة", "دورة", "كتاب", "درس", "تعليم"],
        "🎮 ترفيه": ["سينما", "العاب", "ألعاب", "game", "رحلة", "سفر", "ترفيه"],
    }
    for category, words in categories.items():
        for word in words:
            if word in t:
                return category
    return "📦 أخرى"

def extract_amount(text):
    pattern = r"(?<!\w)(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?!\w)"
    match = re.search(pattern, text)
    if not match:
        return None
    raw = match.group(1)
    try:
        if "," in raw and "." in raw:
            raw = raw.replace(".", "").replace(",", ".")
        elif "," in raw:
            parts = raw.split(",")
            if len(parts[-1]) == 2:
                raw = raw.replace(",", ".")
            else:
                raw = raw.replace(",", "")
        elif "." in raw:
            parts = raw.split(".")
            if len(parts) > 2:
                raw = raw.replace(".", "")
            elif len(parts[-1]) == 3:
                raw = raw.replace(".", "")
        return float(raw)
    except Exception:
        return None

def clean_text(text):
    text = re.sub(r"(?<!\w)(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?!\w)", "", text, count=1)
    text = re.sub(r"(ليرة|ليرات|tl|try|₺|دولار|دولارات|usd|eur|يورو)", "", text, flags=re.IGNORECASE)
    return " ".join(text.split()).strip()

def add_transaction(user_id, transaction_type, item, amount, payment_method="💵 كاش"):
    category = detect_category(item)
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO transactions (user_id, type, item, category, amount_try, payment_method, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (user_id, transaction_type, item, category, amount, payment_method, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    transaction_id = cur.lastrowid
    conn.commit()
    conn.close()
    return transaction_id, category

def delete_transaction_by_id(transaction_id, user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM transactions WHERE id = ? AND user_id = ?", (transaction_id, user_id))
    if not cursor.fetchone():
        conn.close()
        return False
    cursor.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (transaction_id, user_id))
    conn.commit()
    conn.close()
    return True

# =========================================================
# الأوامر والواجهات
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    keyboard = [
        [InlineKeyboardButton("💸 مصروف", callback_data="add_expense"), InlineKeyboardButton("💰 دخل", callback_data="add_income")],
        [InlineKeyboardButton("📊 التقرير", callback_data="report"), InlineKeyboardButton("📋 آخر العمليات", callback_data="recent")],
        [InlineKeyboardButton("💵 سعر الدولار", callback_data="usd"), InlineKeyboardButton("💶 سعر اليورو", callback_data="eur")],
        [InlineKeyboardButton("📈 إحصائيات", callback_data="stats"), InlineKeyboardButton("📥 Excel", callback_data="excel")],
        [InlineKeyboardButton("⚙️ الميزانية", callback_data="budget"), InlineKeyboardButton("❓ المساعدة", callback_data="help")],
    ]
    text = "🧠 *مساعدك المالي PRO*\n\nأهلاً بك 👋\nأدخل مصاريفك أو دخلك مباشرة (مثال: `مصروف 150 مطعم`)."
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
📚 *طريقة الاستخدام:*
• لإضافة مصروف: `مصروف 200 مطعم`
• لإضافة دخل: `دخل 5000 راتب`
• للحذف السريع: `حذف رقم_العملية` (مثال: `حذف 3`)
• أو استخدم الأزرار التفاعلية أسفل رسالة /start.
"""
    await update.message.reply_text(text, parse_mode="Markdown")

async def process_transaction_text(update, transaction_type, text):
    user_id = update.effective_user.id
    amount = extract_amount(text)
    if amount is None or amount <= 0:
        await update.message.reply_text("❌ لم أستطع تحديد المبلغ بشكل صحيح.\nمثال: `مصروف 250 مطعم`", parse_mode="Markdown")
        return

    item = clean_text(text) or "عملية مالية"
    transaction_id, category = add_transaction(user_id, transaction_type, item, amount)
    title = "💸 تم تسجيل المصروف بنجاح" if transaction_type == "expense" else "💰 تم تسجيل الدخل بنجاح"
    
    keyboard = [
        [InlineKeyboardButton("❌ حذف هذه العملية", callback_data=f"delete_{transaction_id}")]
    ]
    
    # حساب الدولار واليورو تلقائياً
    try_to_usd, try_to_eur = get_live_rates()
    usd_val = amount * try_to_usd
    eur_val = amount * try_to_eur

    msg = (
        f"{title}\n\n"
        f"💰 المبلغ: *{format_money(amount)} TL*\n"
        f"🇺🇸 بالدولار: *${usd_val:,.2f}*\n"
        f"🇪🇺 باليورو: *€{eur_val:,.2f}*\n"
        f"📝 البيان: {item}\n"
        f"🏷 التصنيف: {category}\n"
        f"🆔 رقم العملية: `{transaction_id}`"
    )

    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    text = update.message.text.strip()
    user_id = update.effective_user.id

    # معالجة الحذف بالنص (مثل: حذف 5)
    if text.startswith("حذف "):
        parts = text.split()
        if len(parts) > 1 and parts[1].isdigit():
            tid = int(parts[1])
            if delete_transaction_by_id(tid, user_id):
                await update.message.reply_text(f"🗑 تم حذف العملية رقم `{tid}` بنجاح.", parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ لم أجد عملية بهذا الرقم أو أنها لا تخصك.", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ يرجى كتابة رقم العملية صحيحاً. مثال: `حذف 3`", parse_mode="Markdown")
        return

    if text.startswith("ميزانية"):
        amount = extract_amount(text)
        if amount and amount > 0:
            conn = get_db()
            conn.execute("UPDATE users SET monthly_budget = ? WHERE user_id = ?", (amount, user_id))
            conn.commit()
            conn.close()
            await update.message.reply_text(f"⚙️ تم تحديد الميزانية الشهرية:\n💰 *{format_money(amount)} TL*", parse_mode="Markdown")
        return

    amount = extract_amount(text)
    if amount is None:
        await update.message.reply_text("🤖 لم أفهم هذا الأمر. اكتب المصروف مباشرة أو اضغط /start", parse_mode="Markdown")
        return

    is_income = any(w in text.lower() for w in ["دخل", "راتب", "راتبي", "قبضت", "استلمت", "ربح"])
    await process_transaction_text(update, "income" if is_income else "expense", text)

async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_db()
    row = conn.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN type = 'income' THEN amount_try ELSE 0 END), 0) AS income,
            COALESCE(SUM(CASE WHEN type = 'expense' THEN amount_try ELSE 0 END), 0) AS expense
        FROM transactions WHERE user_id = ?
    """, (user_id,)).fetchone()
    conn.close()

    income, expense = row["income"], row["expense"]
    balance = income - expense
    try_to_usd, try_to_eur = get_live_rates()

    target = update.message if update.message else update.callback_query.message
    await target.reply_text(
        f"📊 *التقرير المالي*\n\n"
        f"💰 الدخل: *{format_money(income)} TL*\n"
        f"💸 المصروف: *{format_money(expense)} TL*\n"
        f"💵 الرصيد الصافي: *{format_money(balance)} TL*\n"
        f"🇺🇸 بالدولار: *${balance * try_to_usd:,.2f}*\n"
        f"🇪🇺 باليورو: *€{balance * try_to_eur:,.2f}*",
        parse_mode="Markdown"
    )

async def recent_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_db()
    rows = conn.execute("SELECT * FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT 10", (user_id,)).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 لا توجد عمليات مسجلة حتى الآن.")
        return

    text = "📋 *آخر العمليات* (للحذف اكتب: `حذف رقم_العملية`)\n\n"
    for r in rows:
        emoji = "💸" if r["type"] == "expense" else "💰"
        text += f"{emoji} `{r['id']}` | {r['item']} — *{format_money(r['amount_try'])} TL*\n"

    await update.message.reply_text(text, parse_mode="Markdown")

async def usd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try_to_usd, _ = get_live_rates()
    if try_to_usd and try_to_usd > 0:
        usd_rate = 1 / try_to_usd
        await update.message.reply_text(f"🇺🇸 1 USD ≈ *{usd_rate:,.2f} TL*", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ تعذر جلب سعر الدولار حالياً.")

async def eur_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, try_to_eur = get_live_rates()
    if try_to_eur and try_to_eur > 0:
        eur_rate = 1 / try_to_eur
        await update.message.reply_text(f"🇪🇺 1 EUR ≈ *{eur_rate:,.2f} TL*", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ تعذر جلب سعر اليورو حالياً.")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_db()
    rows = conn.execute("SELECT category, SUM(amount_try) AS total FROM transactions WHERE user_id = ? AND type = 'expense' GROUP BY category ORDER BY total DESC", (user_id,)).fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📊 لا توجد مصاريف لتصنيفها حتى الآن.")
        return

    total = sum(r["total"] for r in rows)
    text = "📈 *إحصائيات المصاريف*\n\n"
    for r in rows:
        pct = (r["total"] / total * 100) if total > 0 else 0
        text += f"{r['category']} : *{format_money(r['total'])} TL* ({pct:.1f}%)\n"

    await update.message.reply_text(text, parse_mode="Markdown")

async def excel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_db()
    rows = conn.execute("SELECT * FROM transactions WHERE user_id = ? ORDER BY id DESC", (user_id,)).fetchall()
    conn.close()
    target = update.message if update.message else update.callback_query.message
    if not rows:
        await target.reply_text("📭 لا توجد بيانات لتصديرها.")
        return

    wb = Workbook()
    ws = wb.active
    ws.title = "Transactions"
    ws.append(["ID", "Type", "Item", "Category", "Amount TRY", "Payment", "Date"])
    for r in rows:
        ws.append([r["id"], r["type"], r["item"], r["category"], r["amount_try"], r["payment_method"], r["created_at"]])

    filename = f"finance_{user_id}.xlsx"
    wb.save(filename)
    wb.close()
    with open(filename, "rb") as f:
        await target.reply_document(document=f, filename=filename, caption="📊 ملف Excel لعملياتك المالية")
    os.remove(filename)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "report":
        await report_command(update, context)
    elif data == "recent":
        await recent_command(update, context)
    elif data == "usd":
        await usd_command(update, context)
    elif data == "eur":
        await eur_command(update, context)
    elif data == "stats":
        await stats_command(update, context)
    elif data == "excel":
        await excel_command(update, context)
    elif data == "budget":
        await query.message.reply_text("⚙️ لتحديد الميزانية اكتب:\n`ميزانية 10000`", parse_mode="Markdown")
    elif data == "help":
        await help_command(update, context)
    elif data == "add_expense":
        await query.message.reply_text("💸 أرسل المصروف هكذا:\n`مصروف 150 مطعم`", parse_mode="Markdown")
    elif data == "add_income":
        await query.message.reply_text("💰 أرسل الدخل هكذا:\n`دخل 10000 راتب`", parse_mode="Markdown")
    elif data.startswith("delete_"):
        tid = int(data.split("_")[1])
        if delete_transaction_by_id(tid, user_id):
            await query.message.reply_text(f"🗑 تم حذف العملية رقم `{tid}` بنجاح.", parse_mode="Markdown")
        else:
            await query.message.reply_text("❌ لم يتم العثور على العملية أو أنها محذوفة مسبقاً.", parse_mode="Markdown")

# =========================================================
# التشغيل الرئيسي
# =========================================================

def main():
    # تشغيل السيرفر الوهمي في الخلفية (إلزامي لـ Render)
    threading.Thread(target=run_dummy_server, daemon=True).start()

    init_db()

    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is missing!")
        return

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("report", report_command))
    application.add_handler(CommandHandler("usd", usd_command))
    application.add_handler(CommandHandler("eur", eur_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("excel", excel_command))
    
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    logger.info("Bot started successfully...")
    application.run_polling()

if __name__ == "__main__":
    main()
