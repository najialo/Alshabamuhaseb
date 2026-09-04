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
# إعدادات السيرفر الوهمي (Render Port Binding)
# =========================================================

def run_dummy_server():
    port = int(os.getenv("PORT", 10000))
    class DummyHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is alive!")
    
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()


# =========================================================
# إعدادات البوت والقاعدة
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

DB_NAME = "finance_pro.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

RATES_CACHE = {
    "rates": None,
    "last_fetched": None
}


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
        INSERT OR IGNORE INTO users
        (user_id, first_name, username, created_at)
        VALUES (?, ?, ?, ?)
    """, (
        user.id,
        user.first_name or "",
        user.username or "",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ))

    conn.execute("""
        UPDATE users
        SET first_name = ?, username = ?
        WHERE user_id = ?
    """, (
        user.first_name or "",
        user.username or "",
        user.id,
    ))

    conn.commit()
    conn.close()


def get_live_rates():
    now = datetime.now()
    if (RATES_CACHE["last_fetched"] and 
            now - RATES_CACHE["last_fetched"] < timedelta(minutes=10) and 
            RATES_CACHE["rates"]):
        usd = RATES_CACHE["rates"].get("USD")
        eur = RATES_CACHE["rates"].get("EUR")
        return usd, eur

    try:
        url = "https://open.er-api.com/v6/latest/TRY"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get("result") != "success":
            return None, None

        rates = data.get("rates", {})
        RATES_CACHE["rates"] = rates
        RATES_CACHE["last_fetched"] = now

        usd = rates.get("USD")
        eur = rates.get("EUR")

        return usd, eur

    except Exception as e:
        logger.warning("Currency API error: %s", e)
        if RATES_CACHE["rates"]:
            return RATES_CACHE["rates"].get("USD"), RATES_CACHE["rates"].get("EUR")
        return None, None


def format_money(value):
    return f"{value:,.2f}"


def detect_category(text):
    t = text.lower()
    categories = {
        "🍔 طعام": ["مطعم", "اكل", "أكل", "غداء", "عشاء", "فطور", "برغر", "burger", "pizza", "بيتزا", "دجاج", "شاورما", "مقهى", "قهوة", "كافيه"],
        "🛒 تسوق": ["سوبرماركت", "ماركت", "بقالة", "ملابس", "حذاء", "شراء", "تسوق", "trendyol", "بيم", "bim"],
        "🚗 مواصلات": ["بنزين", "مازوت", "ديزل", "تكسي", "تاكسي", "باص", "مترو", "مواصلات", "سيارة", "غسيل سيارة"],
        "🏠 منزل": ["بيت", "منزل", "ايجار", "إيجار", "كهرباء", "ماء", "غاز", "صيانة"],
        "📱 اتصالات": ["ترك", "turkcell", "vodafone", "انترنت", "نت", "هاتف", "شحن"],
        "💊 صحة": ["صيدلية", "دواء", "دكتور", "طبيب", "مشفى", "مستشفى", "علاج"],
        "🎓 تعليم": ["مدرسة", "جامعة", "دورة", "كتاب", "درس", "تعليم"],
        "🎮 ترفيه": ["سينما", "العاب", "ألعاب", "game", "رحلة", "سفر", "ترفيه"],
    }
    for category, words in categories.items():
        for word in words:
            if word.lower() in t:
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


def add_transaction(user_id, transaction_type, item, amount, payment_method="💵 كاش", location="", note=""):
    category = detect_category(item)
    conn = get_db()
    cur = conn.execute("""
        INSERT INTO transactions (user_id, type, item, category, amount_try, payment_method, location, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, transaction_type, item, category, amount, payment_method, location, note, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    transaction_id = cur.lastrowid
    conn.commit()
    conn.close()
    return transaction_id, category


def get_report(user_id):
    conn = get_db()
    row = conn.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN type = 'income' THEN amount_try ELSE 0 END), 0) AS income,
            COALESCE(SUM(CASE WHEN type = 'expense' THEN amount_try ELSE 0 END), 0) AS expense
        FROM transactions WHERE user_id = ?
    """, (user_id,)).fetchone()
    conn.close()
    return row["income"], row["expense"], row["income"] - row["expense"]


def get_category_stats(user_id):
    conn = get_db()
    rows = conn.execute("""
        SELECT category, SUM(amount_try) AS total FROM transactions
        WHERE user_id = ? AND type = 'expense'
        GROUP BY category ORDER BY total DESC
    """, (user_id,)).fetchall()
    conn.close()
    return rows


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    keyboard = [
        [InlineKeyboardButton("💸 مصروف", callback_data="add_expense"), InlineKeyboardButton("💰 دخل", callback_data="add_income")],
        [InlineKeyboardButton("📊 التقرير", callback_data="report"), InlineKeyboardButton("📋 آخر العمليات", callback_data="recent")],
        [InlineKeyboardButton("💵 سعر الدولار", callback_data="usd"), InlineKeyboardButton("💶 سعر اليورو", callback_data="eur")],
        [InlineKeyboardButton("📈 إحصائيات", callback_data="stats"), InlineKeyboardButton("📥 Excel", callback_data="excel")],
        [InlineKeyboardButton("⚙️ الميزانية", callback_data="budget"), InlineKeyboardButton("❓ المساعدة", callback_data="help")],
    ]
    text = """
🧠 *مساعدك المالي PRO*
أهلاً بك 👋
يمكنك إدارة مصاريفك ودخلك بالكامل من داخل البوت.
"""
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "📚 *شرح البوت*\nاكتب المصروف أو الدخل مباشرة، أو استخدم الأزرار."
    await update.message.reply_text(text, parse_mode="Markdown")


async def process_transaction_text(update, transaction_type, text):
    user_id = update.effective_user.id
    amount = extract_amount(text)
    if amount is None or amount <= 0:
        await update.message.reply_text("❌ لم أستطع معرفة المبلغ.\nمثال:\n`مصروف 250 مطعم`", parse_mode="Markdown")
        return

    item = clean_text(text) or "عملية مالية"
    transaction_id, category = add_transaction(user_id, transaction_type, item, amount)
    title = "💸 تم تسجيل المصروف" if transaction_type == "expense" else "💰 تم تسجيل الدخل"
    
    keyboard = [
        [InlineKeyboardButton("💵 كاش", callback_data=f"cash_{transaction_id}"), InlineKeyboardButton("💳 بطاقة", callback_data=f"card_{transaction_id}")],
        [InlineKeyboardButton("❌ حذف هذه العملية", callback_data=f"delete_{transaction_id}")],
    ]
    
    usd_rate, eur_rate = get_live_rates()
    conversion_info = ""
    if usd_rate and usd_rate > 0:
        conversion_info += f"🇺🇸 المقابل بالدولار: *${amount * usd_rate:,.2f}*\n"
    if eur_rate and eur_rate > 0:
        conversion_info += f"🇪🇺 المقابل باليورو: *€{amount * eur_rate:,.2f}*\n"

    await update.message.reply_text(
        f"{title}\n\n💰 المبلغ:\n*{format_money(amount)} TL*\n{conversion_info}\n📝 البيان:\n{item}\n🏷 التصنيف:\n{category}\n🆔 رقم العملية:\n`{transaction_id}`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


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


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)
    text = update.message.text.strip()
    user_id = update.effective_user.id

    if text.startswith("حذف "):
        parts = text.split()
        if len(parts) > 1 and parts[1].isdigit():
            target_id = int(parts[1])
            if delete_transaction_by_id(target_id, user_id):
                await update.message.reply_text(f"🗑 تم حذف العملية رقم `{target_id}` بنجاح.", parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ لم أجد عملية بهذا الرقم أو أنها لا تخصك.", parse_mode="Markdown")
        return

    if text.startswith("بحث "):
        keyword = text[5:].strip()
        if keyword:
            conn = get_db()
            rows = conn.execute("SELECT * FROM transactions WHERE user_id = ? AND (item LIKE ? OR category LIKE ?)", (user_id, f"%{keyword}%", f"%{keyword}%")).fetchall()
            conn.close()
            res_text = f"🔎 *نتائج البحث: {keyword}*\n\n" + "".join([f"💸 `{r['id']}` {r['item']} — *{format_money(r['amount_try'])} TL*\n" for r in rows]) if rows else f"🔎 لا توجد نتائج عن: {keyword}"
            await update.message.reply_text(res_text, parse_mode="Markdown")
        return

    if text.startswith("ميزانية"):
        amount = extract_amount(text)
        if amount and amount > 0:
            conn = get_db()
            conn.execute("UPDATE users SET monthly_budget = ? WHERE user_id = ?", (amount, user_id))
            conn.commit()
            conn.close()
            await update.message.reply_text(f"⚙️ تم تحديد الميزانية:\n💰 *{format_money(amount)} TL*", parse_mode="Markdown")
        return

    amount = extract_amount(text)
    if amount is None:
        await update.message.reply_text("🤖 لم أفهم الأمر. مثال:\n`مصروف 200 مطعم`", parse_mode="Markdown")
        return

    is_income = any(w in text.lower() for w in ["دخل", "راتب", "راتبي", "قبضت", "استلمت", "ربح"])
    await process_transaction_text(update, "income" if is_income else "expense", text)


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    income, expense, balance = get_report(update.effective_user.id)
    target = update.message if update.message else update.callback_query.message
    await target.reply_text(f"📊 *التقرير المالي*\n\n💰 الدخل: *{format_money(income)} TL*\n💸 المصروف: *{format_money(expense)} TL*\n💵 الرصيد: *{format_money(balance)} TL*", parse_mode="Markdown")


async def usd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usd, _ = get_live_rates()
    if usd:
        await update.message.reply_text(f"🇺🇸 1 USD ≈ *{1 / usd:,.2f} TL*", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ تعذر جلب السعر.")


async def eur_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, eur = get_live_rates()
    if eur:
        await update.message.reply_text(f"🇪🇺 1 EUR ≈ *{1 / eur:,.2f} TL*", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ تعذر جلب السعر.")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_category_stats(update.effective_user.id)
    if not rows:
        await update.message.reply_text("📊 لا توجد مصاريف حتى الآن.")
        return
    total = sum(r["total"] for r in rows)
    text = "📈 *إحصائيات المصاريف*\n\n" + "".join([f"{r['category']}\n💰 {format_money(r['total'])} TL ({(r['total']/total)*100:.1f}%)\n\n" for r in rows])
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
    ws.append(["ID", "Type", "Item", "Category", "Amount TRY", "Payment", "Location", "Note", "Date"])
    for r in rows:
        ws.append([r["id"], r["type"], r["item"], r["category"], r["amount_try"], r["payment_method"], r["location"], r["note"], r["created_at"]])
    filename = f"finance_{user_id}.xlsx"
    wb.save(filename)
    wb.close()
    with open(filename, "rb") as f:
        await target.reply_document(document=f, filename=filename)
    os.remove(filename)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "report":
        await report_command(update, context)
    elif data == "usd":
        usd, _ = get_live_rates()
        await query.message.reply_text(f"🇺🇸 1 USD ≈ *{1 / usd:,.2f} TL*" if usd else "❌ تعذر السعر", parse_mode="Markdown")
    elif data == "eur":
        _, eur = get_live_rates()
        await query.message.reply_text(f"🇪🇺 1 EUR ≈ *{1 / eur:,.2f} TL*" if eur else "❌ تعذر السعر", parse_mode="Markdown")
    elif data == "stats":
        await stats_command(update, context)
    elif data == "excel":
        await excel_command(update, context)
    elif data.startswith("delete_"):
        tid = int(data.split("_")[1])
        if delete_transaction_by_id(tid, user_id):
            await query.message.reply_text(f"🗑 تم حذف العملية رقم `{tid}`", parse_Mode="Markdown")


# =========================================================
# التشغيل الأساسي (Main)
# =========================================================

def main():
    # تشغيل السيرفر الوهمي في الخلفية لحل مشكلة بورتات Render
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

    logger.info("Bot is starting...")
    application.run_polling()


if __name__ == "__main__":
    main()
