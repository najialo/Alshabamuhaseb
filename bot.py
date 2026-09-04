import os
import re
import sqlite3
import logging
from datetime import datetime, timedelta

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
# إعدادات
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")

DB_NAME = "finance_pro.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)

# تخزين مؤقت لأسعار العملات (Caching)
RATES_CACHE = {
    "rates": None,
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


# =========================================================
# العملات مع نظام Caching
# =========================================================

def get_live_rates():
    now = datetime.now()
    # التحقق من وجود كاش صالح (أقل من 10 دقائق)
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
        # في حال حدوث خطأ، يتم إرجاع الكاش القديم إن وجد
        if RATES_CACHE["rates"]:
            return RATES_CACHE["rates"].get("USD"), RATES_CACHE["rates"].get("EUR")
        return None, None


# =========================================================
# الأدوات
# =========================================================

def format_money(value):
    return f"{value:,.2f}"


def detect_category(text):
    t = text.lower()

    categories = {
        "🍔 طعام": [
            "مطعم", "اكل", "أكل", "غداء", "عشاء",
            "فطور", "برغر", "burger", "pizza", "بيتزا",
            "دجاج", "شاورما", "مقهى", "قهوة", "كافيه"
        ],

        "🛒 تسوق": [
            "سوبرماركت", "ماركت", "بقالة", "ملابس",
            "حذاء", "شراء", "تسوق", "trendyol"
        ],

        "🚗 مواصلات": [
            "بنزين", "مازوت", "ديزل", "تكسي",
            "تاكسي", "باص", "مترو", "مواصلات",
            "سيارة", "غسيل سيارة"
        ],

        "🏠 منزل": [
            "بيت", "منزل", "ايجار", "إيجار",
            "كهرباء", "ماء", "غاز", "صيانة"
        ],

        "📱 اتصالات": [
            "ترك", "turkcell", "vodafone",
            "انترنت", "نت", "هاتف", "شحن"
        ],

        "💊 صحة": [
            "صيدلية", "دواء", "دكتور", "طبيب",
            "مشفى", "مستشفى", "علاج"
        ],

        "🎓 تعليم": [
            "مدرسة", "جامعة", "دورة", "كتاب",
            "درس", "تعليم"
        ],

        "🎮 ترفيه": [
            "سينما", "العاب", "ألعاب", "game",
            "رحلة", "سفر", "ترفيه"
        ],
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
    text = re.sub(
        r"(?<!\w)(\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)(?!\w)",
        "",
        text,
        count=1
    )

    text = re.sub(
        r"(ليرة|ليرات|tl|try|₺|دولار|دولارات|usd|eur|يورو)",
        "",
        text,
        flags=re.IGNORECASE
    )

    return " ".join(text.split()).strip()


# =========================================================
# إضافة معاملة
# =========================================================

def add_transaction(
    user_id,
    transaction_type,
    item,
    amount,
    payment_method="💵 كاش",
    location="",
    note=""
):
    category = detect_category(item)

    conn = get_db()

    cur = conn.execute("""
        INSERT INTO transactions
        (
            user_id,
            type,
            item,
            category,
            amount_try,
            payment_method,
            location,
            note,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        transaction_type,
        item,
        category,
        amount,
        payment_method,
        location,
        note,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ))

    transaction_id = cur.lastrowid

    conn.commit()
    conn.close()

    return transaction_id, category


# =========================================================
# التقرير
# =========================================================

def get_report(user_id):
    conn = get_db()

    row = conn.execute("""
        SELECT
            COALESCE(SUM(
                CASE WHEN type = 'income'
                THEN amount_try ELSE 0 END
            ), 0) AS income,

            COALESCE(SUM(
                CASE WHEN type = 'expense'
                THEN amount_try ELSE 0 END
            ), 0) AS expense

        FROM transactions
        WHERE user_id = ?
    """, (user_id,)).fetchone()

    conn.close()

    income = row["income"]
    expense = row["expense"]
    balance = income - expense

    return income, expense, balance


def get_category_stats(user_id):
    conn = get_db()

    rows = conn.execute("""
        SELECT
            category,
            SUM(amount_try) AS total
        FROM transactions
        WHERE user_id = ?
        AND type = 'expense'
        GROUP BY category
        ORDER BY total DESC
    """, (user_id,)).fetchall()

    conn.close()

    return rows


# =========================================================
# /start
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)

    keyboard = [
        [
            InlineKeyboardButton("💸 مصروف", callback_data="add_expense"),
            InlineKeyboardButton("💰 دخل", callback_data="add_income"),
        ],
        [
            InlineKeyboardButton("📊 التقرير", callback_data="report"),
            InlineKeyboardButton("📋 آخر العمليات", callback_data="recent"),
        ],
        [
            InlineKeyboardButton("💵 الدولار", callback_data="usd"),
            InlineKeyboardButton("💶 اليورو", callback_data="eur"),
        ],
        [
            InlineKeyboardButton("📈 إحصائيات", callback_data="stats"),
            InlineKeyboardButton("📥 Excel", callback_data="excel"),
        ],
        [
            InlineKeyboardButton("⚙️ الميزانية", callback_data="budget"),
            InlineKeyboardButton("❓ المساعدة", callback_data="help"),
        ],
    ]

    text = """
🧠 *مساعدك المالي PRO*

أهلاً بك 👋

يمكنك إدارة مصاريفك ودخلك بالكامل من داخل البوت.

━━━━━━━━━━━━━━

💸 تسجيل مصروف
💰 تسجيل دخل
📊 تقارير مالية
📈 إحصائيات
💵 سعر الدولار مباشر
💶 سعر اليورو مباشر
📥 تصدير Excel
⚙️ ميزانية شهرية
🔎 بحث بالعمليات

━━━━━━━━━━━━━━

مثال:

`دفعت 150 ليرة بنزين`

أو:

`مصروف 250 مطعم`

أو:

`دخل 15000 راتب`
"""

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================================================
# /help
# =========================================================

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
📚 *شرح البوت*

━━━━━━━━━━━━━━

💸 *إضافة مصروف*

مثال:
`مصروف 250 مطعم`

أو:
`دفعت 150 بنزين`

━━━━━━━━━━━━━━

💰 *إضافة دخل*

مثال:
`دخل 15000 راتب`

━━━━━━━━━━━━━━

📊 *التقرير*

/report

يعرض:
• مجموع الدخل
• مجموع المصاريف
• الرصيد

━━━━━━━━━━━━━━

📋 *آخر العمليات*

/recent

━━━━━━━━━━━━━━

🔎 *البحث*

مثال:

`بحث بنزين`

━━━━━━━━━━━━━━

💵 *العملات*

/usd
/eur

━━━━━━━━━━━━━━

⚙️ *الميزانية*

مثال:

`ميزانية 10000`

━━━━━━━━━━━━━━

📈 *الإحصائيات*

/stats

━━━━━━━━━━━━━━

📥 *Excel*

/excel
"""

    await update.message.reply_text(
        text,
        parse_mode="Markdown"
    )


# =========================================================
# إضافة مصروف / دخل
# =========================================================

async def process_transaction_text(
    update,
    transaction_type,
    text
):
    user_id = update.effective_user.id

    amount = extract_amount(text)

    if amount is None or amount <= 0:
        await update.message.reply_text(
            "❌ لم أستطع معرفة المبلغ.\n\n"
            "مثال:\n"
            "`مصروف 250 مطعم`",
            parse_mode="Markdown"
        )
        return

    item = clean_text(text)

    if not item:
        item = "عملية مالية"

    transaction_id, category = add_transaction(
        user_id=user_id,
        transaction_type=transaction_type,
        item=item,
        amount=amount
    )

    if transaction_type == "expense":
        title = "💸 تم تسجيل المصروف"
    else:
        title = "💰 تم تسجيل الدخل"

    keyboard = [
        [
            InlineKeyboardButton(
                "💵 كاش",
                callback_data=f"cash_{transaction_id}"
            ),
            InlineKeyboardButton(
                "💳 بطاقة",
                callback_data=f"card_{transaction_id}"
            ),
        ],
    ]

    await update.message.reply_text(
        f"""
{title}

━━━━━━━━━━━━━━

💰 المبلغ:
*{format_money(amount)} TL*

📝 البيان:
{item}

🏷 التصنيف:
{category}

🆔 رقم العملية:
`{transaction_id}`

يمكنك تحديد طريقة الدفع:
""",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# =========================================================
# الرسائل النصية
# =========================================================

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)

    text = update.message.text.strip()

    if text.startswith("بحث "):
        keyword = text[5:].strip()
        if keyword:
            await search_transactions(update, keyword)
        return

    if text.startswith("ميزانية"):
        await set_budget_from_text(update, text)
        return

    amount = extract_amount(text)

    if amount is None:
        await update.message.reply_text(
            "🤖 لم أفهم الأمر.\n\n"
            "مثال:\n"
            "`مصروف 200 مطعم`\n\n"
            "أو اضغط /start",
            parse_mode="Markdown"
        )
        return

    lower = text.lower()

    income_words = [
        "دخل",
        "راتب",
        "راتبي",
        "قبضت",
        "استلمت",
        "ربح",
        "دخلت"
    ]

    is_income = any(
        word in lower
        for word in income_words
    )

    await process_transaction_text(
        update,
        "income" if is_income else "expense",
        text
    )


# =========================================================
# التقرير
# =========================================================

async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)

    income, expense, balance = get_report(
        update.effective_user.id
    )

    usd, eur = get_live_rates()

    text = f"""
📊 *التقرير المالي*

━━━━━━━━━━━━━━

💰 الدخل:
*{format_money(income)} TL*

💸 المصروف:
*{format_money(expense)} TL*

💵 الرصيد:
*{format_money(balance)} TL*
"""

    if usd:
        text += f"\n🇺🇸 بالدولار: *${balance * usd:,.2f}*"

    if eur:
        text += f"\n🇪🇺 باليورو: *€{balance * eur:,.2f}*"

    await update.message.reply_text(
        text,
        parse_mode="Markdown"
    )


# =========================================================
# آخر العمليات
# =========================================================

async def recent_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM transactions
        WHERE user_id = ?
        ORDER BY id DESC
        LIMIT 15
    """, (update.effective_user.id,)).fetchall()

    conn.close()

    if not rows:
        await update.message.reply_text(
            "📭 لا توجد عمليات حتى الآن."
        )
        return

    text = "📋 *آخر العمليات*\n\n"

    for row in rows:
        emoji = "💸" if row["type"] == "expense" else "💰"

        text += (
            f"{emoji} `{row['id']}` "
            f"{row['item']}\n"
            f"   💰 {format_money(row['amount_try'])} TL\n"
            f"   🏷 {row['category']}\n"
            f"   🕒 {row['created_at']}\n\n"
        )

    await update.message.reply_text(
        text,
        parse_mode="Markdown"
    )


# =========================================================
# البحث
# =========================================================

async def search_transactions(update, keyword):
    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM transactions
        WHERE user_id = ?
        AND (
            item LIKE ?
            OR category LIKE ?
            OR note LIKE ?
            OR location LIKE ?
        )
        ORDER BY id DESC
        LIMIT 20
    """, (
        update.effective_user.id,
        f"%{keyword}%",
        f"%{keyword}%",
        f"%{keyword}%",
        f"%{keyword}%",
    )).fetchall()

    conn.close()

    if not rows:
        await update.message.reply_text(
            f"🔎 لا توجد نتائج عن: {keyword}"
        )
        return

    text = f"🔎 *نتائج البحث: {keyword}*\n\n"

    for row in rows:
        emoji = "💸" if row["type"] == "expense" else "💰"

        text += (
            f"{emoji} `{row['id']}` "
            f"{row['item']} — "
            f"*{format_money(row['amount_try'])} TL*\n"
        )

    await update.message.reply_text(
        text,
        parse_mode="Markdown"
    )


# =========================================================
# العملات
# =========================================================

async def usd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usd, _ = get_live_rates()

    if not usd:
        await update.message.reply_text(
            "❌ تعذر جلب سعر الدولار حالياً."
        )
        return

    await update.message.reply_text(
        f"🇺🇸 *الدولار مقابل الليرة التركية*\n\n"
        f"1 TL ≈ {usd:.6f} USD\n"
        f"1 USD ≈ {1 / usd:,.2f} TL",
        parse_mode="Markdown"
    )


async def eur_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _, eur = get_live_rates()

    if not eur:
        await update.message.reply_text(
            "❌ تعذر جلب سعر اليورو حالياً."
        )
        return

    await update.message.reply_text(
        f"🇪🇺 *اليورو مقابل الليرة التركية*\n\n"
        f"1 TL ≈ {eur:.6f} EUR\n"
        f"1 EUR ≈ {1 / eur:,.2f} TL",
        parse_mode="Markdown"
    )


# =========================================================
# الإحصائيات
# =========================================================

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_user(update.effective_user)

    rows = get_category_stats(
        update.effective_user.id
    )

    if not rows:
        await update.message.reply_text(
            "📊 لا توجد مصاريف حتى الآن."
        )
        return

    total = sum(row["total"] for row in rows)

    text = "📈 *إحصائيات المصاريف*\n\n"

    for row in rows:
        percentage = (
            row["total"] / total * 100
            if total > 0
            else 0
        )

        text += (
            f"{row['category']}\n"
            f"💰 {format_money(row['total'])} TL "
            f"({percentage:.1f}%)\n\n"
        )

    await update.message.reply_text(
        text,
        parse_mode="Markdown"
    )


# =========================================================
# الميزانية
# =========================================================

async def set_budget_from_text(update, text):
    amount = extract_amount(text)

    if amount is None or amount <= 0:
        await update.message.reply_text(
            "❌ مثال صحيح:\n"
            "`ميزانية 10000`",
            parse_mode="Markdown"
        )
        return

    conn = get_db()

    conn.execute("""
        UPDATE users
        SET monthly_budget = ?
        WHERE user_id = ?
    """, (
        amount,
        update.effective_user.id,
    ))

    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"⚙️ تم تحديد الميزانية الشهرية:\n\n"
        f"💰 *{format_money(amount)} TL*",
        parse_mode="Markdown"
    )


async def budget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db()

    user = conn.execute("""
        SELECT monthly_budget
        FROM users
        WHERE user_id = ?
    """, (update.effective_user.id,)).fetchone()

    conn.close()

    budget = user["monthly_budget"] if user else 0

    _, expense, _ = get_report(
        update.effective_user.id
    )

    if budget <= 0:
        await update.message.reply_text(
            "⚙️ لم تحدد ميزانية بعد.\n\n"
            "مثال:\n"
            "`ميزانية 10000`",
            parse_mode="Markdown"
        )
        return

    remaining = budget - expense
    percentage = (expense / budget * 100) if budget > 0 else 0

    if remaining >= 0:
        status = "🟢 ضمن الميزانية"
    else:
        status = "🔴 تجاوزت الميزانية"

    await update.message.reply_text(
        f"""
⚙️ *الميزانية*

━━━━━━━━━━━━━━

🎯 الميزانية:
*{format_money(budget)} TL*

💸 المصروف:
*{format_money(expense)} TL*

💰 المتبقي:
*{format_money(remaining)} TL*

📊 الاستهلاك:
*{percentage:.1f}%*

{status}
""",
        parse_mode="Markdown"
    )


# =========================================================
# Excel
# =========================================================

async def excel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    conn = get_db()

    rows = conn.execute("""
        SELECT
            id,
            type,
            item,
            category,
            amount_try,
            payment_method,
            location,
            note,
            created_at
        FROM transactions
        WHERE user_id = ?
        ORDER BY id DESC
    """, (user_id,)).fetchall()

    conn.close()

    message_target = update.message if update.message else update.callback_query.message

    if not rows:
        await message_target.reply_text(
            "📭 لا توجد بيانات لتصديرها."
        )
        return

    wb = Workbook()
    ws = wb.active
    ws.title = "Transactions"

    headers = [
        "ID",
        "Type",
        "Item",
        "Category",
        "Amount TRY",
        "Payment",
        "Location",
        "Note",
        "Date",
    ]

    ws.append(headers)

    for row in rows:
        ws.append([
            row["id"],
            row["type"],
            row["item"],
            row["category"],
            row["amount_try"],
            row["payment_method"],
            row["location"],
            row["note"],
            row["created_at"],
        ])

    for column in ws.columns:
        max_length = 0
        column_letter = column[0].column_letter

        for cell in column:
            try:
                max_length = max(
                    max_length,
                    len(str(cell.value))
                )
            except Exception:
                pass

        ws.column_dimensions[
            column_letter
        ].width = min(max_length + 2, 40)

    filename = f"finance_{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

    wb.save(filename)
    wb.close()  # إغلاق الكراس لتحرير الملف من الذاكرة

    try:
        with open(filename, "rb") as file:
            await message_target.reply_document(
                document=file,
                filename=filename,
                caption="📊 ملف Excel الخاص بحسابك"
            )
    finally:
        if os.path.exists(filename):
            try:
                os.remove(filename)
            except Exception as e:
                logger.error("Failed to remove temp file: %s", e)


# =========================================================
# Callback Buttons
# =========================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    await query.answer()

    data = query.data
    user_id = query.from_user.id

    if data == "report":
        income, expense, balance = get_report(user_id)

        await query.message.reply_text(
            f"""
📊 *التقرير*

💰 الدخل:
*{format_money(income)} TL*

💸 المصروف:
*{format_money(expense)} TL*

💵 الرصيد:
*{format_money(balance)} TL*
""",
            parse_mode="Markdown"
        )

    elif data == "recent":
        conn = get_db()

        rows = conn.execute("""
            SELECT *
            FROM transactions
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 10
        """, (user_id,)).fetchall()

        conn.close()

        if not rows:
            await query.message.reply_text(
                "📭 لا توجد عمليات."
            )
            return

        text = "📋 *آخر العمليات*\n\n"

        for row in rows:
            emoji = (
                "💸"
                if row["type"] == "expense"
                else "💰"
            )

            text += (
                f"{emoji} `{row['id']}` "
                f"{row['item']}\n"
                f"💰 {format_money(row['amount_try'])} TL\n"
                f"🏷 {row['category']}\n\n"
            )

        await query.message.reply_text(
            text,
            parse_mode="Markdown"
        )

    elif data == "usd":
        usd, _ = get_live_rates()

        if usd:
            await query.message.reply_text(
                f"🇺🇸 1 USD ≈ *{1 / usd:,.2f} TL*",
                parse_mode="Markdown"
            )
        else:
            await query.message.reply_text(
                "❌ تعذر جلب السعر."
            )

    elif data == "eur":
        _, eur = get_live_rates()

        if eur:
            await query.message.reply_text(
                f"🇪🇺 1 EUR ≈ *{1 / eur:,.2f} TL*",
                parse_mode="Markdown"
            )
        else:
            await query.message.reply_text(
                "❌ تعذر جلب السعر."
            )

    elif data == "stats":
        rows = get_category_stats(user_id)

        if not rows:
            await query.message.reply_text(
                "📊 لا توجد مصاريف."
            )
            return

        total = sum(
            row["total"]
            for row in rows
        )

        text = "📈 *الإحصائيات*\n\n"

        for row in rows:
            percentage = (
                row["total"] / total * 100
                if total
                else 0
            )

            text += (
                f"{row['category']}\n"
                f"💰 {format_money(row['total'])} TL "
                f"({percentage:.1f}%)\n\n"
            )

        await query.message.reply_text(
            text,
            parse_mode="Markdown"
        )

    elif data == "excel":
        await excel_command(update, context)

    elif data == "budget":
        await query.message.reply_text(
            "⚙️ لتحديد الميزانية اكتب:\n\n"
            "`ميزانية 10000`",
            parse_mode="Markdown"
        )

    elif data == "help":
        await query.message.reply_text(
            """
❓ *طريقة الاستخدام*

💸 `مصروف 250 مطعم`

💰 `دخل 15000 راتب`

🔎 `بحث بنزين`

⚙️ `ميزانية 10000`

📊 /report

📋 /recent

📈 /stats

📥 /excel

💵 /usd

💶 /eur
""",
            parse_mode="Markdown"
        )

    elif data == "add_expense":
        await query.message.reply_text(
            "💸 اكتب المصروف بهذا الشكل:\n\n"
            "`مصروف 250 مطعم`",
            parse_mode="Markdown"
        )

    elif data == "add_income":
        await query.message.reply_text(
            "💰 اكتب الدخل بهذا الشكل:\n\n"
            "`دخل 15000 راتب`",
            parse_mode="Markdown"
        )

    elif data.startswith("cash_"):
        transaction_id = data.split("_")[1]

        update_payment_method(
            transaction_id,
            user_id,
            "💵 كاش"
        )

        await query.message.reply_text(
            "✅ تم تسجيل طريقة الدفع: 💵 كاش"
        )

    elif data.startswith("card_"):
        transaction_id = data.split("_")[1]

        update_payment_method(
            transaction_id,
            user_id,
            "💳 بطاقة"
        )

        await query.message.reply_text(
            "✅ تم تسجيل طريقة الدفع: 💳 بطاقة"
        )


# =========================================================
# تحديث طريقة الدفع
# =========================================================

def update_payment_method(
    transaction_id,
    user_id,
    payment_method
):
    conn = get_db()

    conn.execute("""
        UPDATE transactions
        SET payment_method = ?
        WHERE id = ?
        AND user_id = ?
    """, (
        payment_method,
        transaction_id,
        user_id,
    ))

    conn.commit()
    conn.close()


# =========================================================
# أوامر إضافية
# =========================================================

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


# =========================================================
# تشغيل البوت
# =========================================================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN غير موجود. "
            "ضع توكن البوت في Environment Variables."
        )

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("report", report_command))
    application.add_handler(CommandHandler("recent", recent_command))
    application.add_handler(CommandHandler("usd", usd_command))
    application.add_handler(CommandHandler("eur", eur_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("budget", budget_command))
    application.add_handler(CommandHandler("excel", excel_command))
    application.add_handler(CommandHandler("menu", menu_command))

    # Buttons
    application.add_handler(CallbackQueryHandler(callback_handler))

    # Text
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler
        )
    )

    logger.info("Finance PRO Bot started.")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
