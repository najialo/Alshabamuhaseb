import logging
import os
import re
import sqlite3
import urllib.request
import json
from datetime import datetime, timedelta
from io import BytesIO

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# ============================================================
# ⚙️ الإعدادات
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DB_NAME = "ultimate_finance_pro.db"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# 🗄️ قاعدة البيانات
# ============================================================

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
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL,
            item TEXT NOT NULL,
            category TEXT NOT NULL,
            amount_try REAL NOT NULL,
            payment_method TEXT DEFAULT 'غير محدد',
            location TEXT DEFAULT 'غير محدد',
            note TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def ensure_user(update: Update):
    user = update.effective_user

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO users
        (user_id, first_name, username, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            first_name = excluded.first_name,
            username = excluded.username
    """, (
        user.id,
        user.first_name or "",
        user.username or "",
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ))

    conn.commit()
    conn.close()


# ============================================================
# 🏷️ التصنيف التلقائي
# ============================================================

def detect_category(text: str) -> str:
    text = text.lower()

    categories = {
        "🛒 سوبرماركت وأغذية": [
            "حليب", "اكل", "أكل", "بيم", "bim", "شوك", "şok",
            "a101", "جبنة", "جبن", "بيض", "لبن", "خبز",
            "ماركت", "خضار", "فواكه", "لحم", "دجاج", "مياه",
            "ماء", "زيت", "سكر", "رز", "أرز"
        ],

        "🍔 مطاعم وكافيهات": [
            "غداء", "عشاء", "فطور", "مطعم", "قهوة", "كافيه",
            "شاورما", "بيتزا", "برغر", "دونر", "كباب",
            "حلويات", "tavuk", "döner", "kahve"
        ],

        "🚗 مواصلات وسيارات": [
            "بنزين", "مازوت", "تاكسي", "تكسي", "مواقف",
            "مترو", "باص", "تصليح", "مغسلة", "دولموش",
            "سيارة", "سياره", "زيت سيارة"
        ],

        "🧾 فواتير واشتراكات": [
            "كهرباء", "ماء", "نت", "انترنت", "إنترنت",
            "اشتراك", "رصيد", "هاتف", "غاز", "إيجار",
            "ايجار", "فاتورة", "فاتوره", "wifi"
        ],

        "🛍️ تسوق وملابس": [
            "قميص", "بنطال", "شوز", "حذاء", "ملابس",
            "ساعة", "عطر", "ترينديول", "trendyol", "زارا",
            "zara", "lcw", "lc waikiki"
        ],

        "💊 صحة وعناية": [
            "صيدلية", "دواء", "دكتور", "طبيب", "مستشفى",
            "تحليل", "علاج", "حلاق", "شامبو"
        ],

        "📱 إلكترونيات": [
            "ايفون", "آيفون", "سامسونج", "هاتف", "جوال",
            "لابتوب", "كمبيوتر", "سماعة", "شاحن", "ابل",
            "apple", "iphone", "samsung"
        ]
    }

    for category, keywords in categories.items():
        if any(k in text for k in keywords):
            return category

    return "📦 مصاريف عامة"


# ============================================================
# 💱 أسعار العملات
# ============================================================

def get_live_rates():
    try:
        url = "https://open.er-api.com/v6/latest/TRY"

        with urllib.request.urlopen(url, timeout=8) as response:
            data = json.loads(response.read().decode())

        if data.get("result") != "success":
            return None, None

        rates = data.get("rates", {})

        usd = rates.get("USD")
        eur = rates.get("EUR")

        return usd, eur

    except Exception as e:
        logger.warning("Currency API error: %s", e)
        return None, None


# ============================================================
# 🧠 تحليل المصروف
# ============================================================

def parse_amount(text):
    match = re.search(
        r"(\d+(?:[.,]\d+)?)\s*(?:tl|try|ليرة|ل\.ت)?",
        text,
        re.IGNORECASE
    )

    if not match:
        return None

    value = match.group(1).replace(",", ".")

    try:
        return float(value), match.start(), match.end()
    except ValueError:
        return None


def clean_item(text, amount_start=None):
    if amount_start is not None:
        text = text[:amount_start]

    remove_words = [
        "اشتريت",
        "صرفت",
        "دفعت",
        "دفع",
        "اشتريت",
        "ليرة",
        "ل.ت",
        "tl",
        "try",
        "بـ",
        "ب",
        "على",
        "من",
        "في",
    ]

    for word in remove_words:
        text = re.sub(
            rf"\b{re.escape(word)}\b",
            "",
            text,
            flags=re.IGNORECASE
        )

    text = re.sub(r"\s+", " ", text).strip()

    return text or "مصاريف عامة"


def detect_location(text):
    places = [
        "BIM",
        "A101",
        "ŞOK",
        "شوك",
        "بيم",
        "a101",
        "ترينديول",
        "trendyol",
        "كارفور",
        "carrefour"
    ]

    for place in places:
        if place.lower() in text.lower():
            return place

    return "غير محدد"


# ============================================================
# ➕ إضافة معاملة
# ============================================================

async def add_transaction(
    update: Update,
    transaction_type: str,
    text: str
):
    ensure_user(update)

    parsed = parse_amount(text)

    if not parsed:
        await update.message.reply_text(
            "⚠️ لم أستطع العثور على المبلغ.\n\n"
            "مثال:\n"
            "بنزين 500\n"
            "مطعم 250\n"
            "راتب 15000"
        )
        return

    amount, start, end = parsed

    item = clean_item(text, start)
    location = detect_location(text)

    if transaction_type == "income":
        category = "💰 دخل"
    else:
        category = detect_category(text)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO transactions
        (
            user_id,
            type,
            item,
            category,
            amount_try,
            payment_method,
            location,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        update.effective_user.id,
        transaction_type,
        item,
        category,
        amount,
        "غير محدد",
        location,
        now
    ))

    transaction_id = cur.lastrowid

    conn.commit()
    conn.close()

    if transaction_type == "income":
        title = "💰 تم تسجيل الدخل!"
    else:
        title = "✅ تم تسجيل المصروف!"

    keyboard = [
        [
            InlineKeyboardButton(
                "💳 كارت بنك",
                callback_data=f"card_{transaction_id}"
            ),
            InlineKeyboardButton(
                "💵 نقدي",
                callback_data=f"cash_{transaction_id}"
            )
        ],
        [
            InlineKeyboardButton(
                "🗑️ حذف",
                callback_data=f"delete_{transaction_id}"
            )
        ]
    ]

    await update.message.reply_text(
        f"{title}\n"
        f"━━━━━━━━━━━━━━\n"
        f"🆔 الرقم: `{transaction_id}`\n"
        f"📝 البيان: {item}\n"
        f"🏷️ الفئة: {category}\n"
        f"💵 المبلغ: `{amount:,.2f} TL`\n"
        f"📍 المكان: {location}\n"
        f"━━━━━━━━━━━━━━\n"
        f"اختر طريقة الدفع:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


# ============================================================
# 📊 التقرير
# ============================================================

def current_month():
    return datetime.now().strftime("%Y-%m")


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)

    user_id = update.effective_user.id
    month = current_month()

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            SUM(CASE WHEN type='income' THEN amount_try ELSE 0 END),
            SUM(CASE WHEN type='expense' THEN amount_try ELSE 0 END)
        FROM transactions
        WHERE user_id=?
        AND substr(created_at,1,7)=?
    """, (user_id, month))

    income, expense = cur.fetchone()

    income = income or 0
    expense = expense or 0

    cur.execute("""
        SELECT category, SUM(amount_try), COUNT(*)
        FROM transactions
        WHERE user_id=?
        AND type='expense'
        AND substr(created_at,1,7)=?
        GROUP BY category
        ORDER BY SUM(amount_try) DESC
    """, (user_id, month))

    categories = cur.fetchall()

    conn.close()

    balance = income - expense

    msg = (
        f"📊 <b>التقرير المالي</b>\n"
        f"📅 الشهر: {month}\n"
        f"━━━━━━━━━━━━━━\n"
        f"💰 الدخل: <code>{income:,.2f} TL</code>\n"
        f"💸 المصاريف: <code>{expense:,.2f} TL</code>\n"
        f"💵 الرصيد: <code>{balance:,.2f} TL</code>\n"
        f"━━━━━━━━━━━━━━\n"
    )

    if categories:
        msg += "📋 <b>تفصيل المصاريف:</b>\n"

        for row in categories:
            msg += (
                f"• {row['category']}: "
                f"{row['SUM(amount_try)']:,.2f} TL "
                f"({row['COUNT(*)']} عملية)\n"
            )
    else:
        msg += "📭 لا توجد مصاريف هذا الشهر."

    await update.message.reply_text(
        msg,
        parse_mode="HTML"
    )


# ============================================================
# 📋 آخر العمليات
# ============================================================

async def recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM transactions
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 10
    """, (update.effective_user.id,))

    rows = cur.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(
            "📭 لا توجد عمليات مسجلة."
        )
        return

    await update.message.reply_text(
        "📋 <b>آخر 10 عمليات:</b>",
        parse_mode="HTML"
    )

    for row in rows:

        emoji = "💰" if row["type"] == "income" else "💸"

        keyboard = [
            [
                InlineKeyboardButton(
                    "🗑️ حذف",
                    callback_data=f"delete_{row['id']}"
                )
            ]
        ]

        await update.message.reply_text(
            f"{emoji} <b>{row['item']}</b>\n"
            f"🆔 {row['id']}\n"
            f"🏷️ {row['category']}\n"
            f"💵 {row['amount_try']:,.2f} TL\n"
            f"💳 {row['payment_method']}\n"
            f"🕒 {row['created_at']}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )


# ============================================================
# 🔍 البحث
# ============================================================

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)

    if not context.args:
        await update.message.reply_text(
            "🔍 اكتب كلمة البحث.\n\n"
            "مثال:\n"
            "/search بنزين\n"
            "/search مطعم"
        )
        return

    keyword = " ".join(context.args)

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM transactions
        WHERE user_id=?
        AND (
            item LIKE ?
            OR category LIKE ?
            OR location LIKE ?
            OR note LIKE ?
        )
        ORDER BY id DESC
        LIMIT 20
    """, (
        update.effective_user.id,
        f"%{keyword}%",
        f"%{keyword}%",
        f"%{keyword}%",
        f"%{keyword}%"
    ))

    rows = cur.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(
            f"🔍 لا توجد نتائج عن: {keyword}"
        )
        return

    msg = f"🔍 <b>نتائج البحث: {keyword}</b>\n\n"

    for row in rows:
        emoji = "💰" if row["type"] == "income" else "💸"

        msg += (
            f"{emoji} <b>{row['item']}</b>\n"
            f"💵 {row['amount_try']:,.2f} TL\n"
            f"🏷️ {row['category']}\n"
            f"🕒 {row['created_at']}\n"
            f"━━━━━━━━━━━━━━\n"
        )

    await update.message.reply_text(
        msg,
        parse_mode="HTML"
    )


# ============================================================
# 💱 تحويل العملات
# ============================================================

async def currency_report(update: Update, currency):
    ensure_user(update)

    usd, eur = get_live_rates()

    if usd is None or eur is None:
        await update.message.reply_text(
            "❌ تعذر الحصول على سعر الصرف حاليًا."
        )
        return

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            SUM(CASE WHEN type='income' THEN amount_try ELSE 0 END),
            SUM(CASE WHEN type='expense' THEN amount_try ELSE 0 END)
        FROM transactions
        WHERE user_id=?
        AND substr(created_at,1,7)=?
    """, (
        update.effective_user.id,
        current_month()
    ))

    income, expense = cur.fetchone()
    conn.close()

    income = income or 0
    expense = expense or 0

    balance = income - expense

    if currency == "usd":
        rate = usd
        symbol = "$"
        name = "الدولار"
    else:
        rate = eur
        symbol = "€"
        name = "اليورو"

    total = expense * rate
    balance_converted = balance * rate

    one_currency = 1 / rate

    await update.message.reply_text(
        f"💱 <b>التحويل إلى {name}</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"💸 المصاريف: <code>{expense:,.2f} TL</code>\n"
        f"💵 ما يعادل: <code>{total:,.2f} {symbol}</code>\n\n"
        f"💰 الرصيد: <code>{balance:,.2f} TL</code>\n"
        f"💰 ما يعادل: <code>{balance_converted:,.2f} {symbol}</code>\n\n"
        f"📈 1 {symbol} ≈ {one_currency:,.2f} TL",
        parse_mode="HTML"
    )


async def usd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await currency_report(update, "usd")


async def eur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await currency_report(update, "eur")


# ============================================================
# 🎯 الميزانية
# ============================================================

async def set_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)

    if not context.args:
        await update.message.reply_text(
            "🎯 مثال:\n"
            "/budget 10000\n\n"
            "لتحديد ميزانية شهرية قدرها 10,000 TL"
        )
        return

    try:
        budget = float(context.args[0].replace(",", "."))
    except ValueError:
        await update.message.reply_text(
            "❌ أدخل رقمًا صحيحًا."
        )
        return

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET monthly_budget=?
        WHERE user_id=?
    """, (budget, update.effective_user.id))

    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"🎯 تم تحديد ميزانيتك الشهرية:\n"
        f"<b>{budget:,.2f} TL</b>",
        parse_mode="HTML"
    )


async def budget_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        "SELECT monthly_budget FROM users WHERE user_id=?",
        (update.effective_user.id,)
    )

    budget = cur.fetchone()["monthly_budget"]

    cur.execute("""
        SELECT SUM(amount_try)
        FROM transactions
        WHERE user_id=?
        AND type='expense'
        AND substr(created_at,1,7)=?
    """, (
        update.effective_user.id,
        current_month()
    ))

    spent = cur.fetchone()[0] or 0

    conn.close()

    if budget <= 0:
        await update.message.reply_text(
            "🎯 لم تحدد ميزانية شهرية.\n"
            "استخدم:\n"
            "/budget 10000"
        )
        return

    remaining = budget - spent
    percentage = (spent / budget) * 100

    if percentage >= 100:
        status = "🚨 تجاوزت الميزانية!"
    elif percentage >= 80:
        status = "⚠️ اقتربت من الحد!"
    else:
        status = "✅ وضعك جيد."

    await update.message.reply_text(
        f"🎯 <b>الميزانية الشهرية</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"💰 الميزانية: {budget:,.2f} TL\n"
        f"💸 المصروف: {spent:,.2f} TL\n"
        f"💵 المتبقي: {remaining:,.2f} TL\n"
        f"📊 الاستخدام: {percentage:.1f}%\n\n"
        f"{status}",
        parse_mode="HTML"
    )


# ============================================================
# 📊 الإحصائيات
# ============================================================

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)

    conn = get_db()
    cur = conn.cursor()

    user_id = update.effective_user.id

    cur.execute(
        "SELECT COUNT(*) FROM transactions WHERE user_id=?",
        (user_id,)
    )
    total_transactions = cur.fetchone()[0]

    cur.execute("""
        SELECT SUM(amount_try)
        FROM transactions
        WHERE user_id=? AND type='income'
    """, (user_id,))
    total_income = cur.fetchone()[0] or 0

    cur.execute("""
        SELECT SUM(amount_try)
        FROM transactions
        WHERE user_id=? AND type='expense'
    """, (user_id,))
    total_expense = cur.fetchone()[0] or 0

    cur.execute("""
        SELECT category, SUM(amount_try)
        FROM transactions
        WHERE user_id=? AND type='expense'
        GROUP BY category
        ORDER BY SUM(amount_try) DESC
        LIMIT 1
    """, (user_id,))

    top = cur.fetchone()

    conn.close()

    top_text = (
        f"{top['category']} — {top['SUM(amount_try)']:,.2f} TL"
        if top else "لا يوجد"
    )

    await update.message.reply_text(
        f"📈 <b>إحصائيات حسابك</b>\n"
        f"━━━━━━━━━━━━━━\n"
        f"🔢 عدد العمليات: {total_transactions}\n"
        f"💰 إجمالي الدخل: {total_income:,.2f} TL\n"
        f"💸 إجمالي المصاريف: {total_expense:,.2f} TL\n"
        f"💵 الرصيد: {total_income-total_expense:,.2f} TL\n"
        f"🔥 أعلى فئة إنفاق:\n{top_text}",
        parse_mode="HTML"
    )


# ============================================================
# 📊 تصدير Excel
# ============================================================

async def export_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)

    try:
        from openpyxl import Workbook
    except ImportError:
        await update.message.reply_text(
            "❌ مكتبة openpyxl غير مثبتة.\n"
            "ثبتها باستخدام:\n"
            "pip install openpyxl"
        )
        return

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM transactions
        WHERE user_id=?
        ORDER BY id ASC
    """, (update.effective_user.id,))

    rows = cur.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text(
            "📭 لا توجد بيانات لتصديرها."
        )
        return

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "المعاملات"

    headers = [
        "ID",
        "النوع",
        "البيان",
        "الفئة",
        "المبلغ TL",
        "طريقة الدفع",
        "المكان",
        "ملاحظات",
        "التاريخ"
    ]

    sheet.append(headers)

    for row in rows:
        sheet.append([
            row["id"],
            "دخل" if row["type"] == "income" else "مصروف",
            row["item"],
            row["category"],
            row["amount_try"],
            row["payment_method"],
            row["location"],
            row["note"],
            row["created_at"]
        ])

    for column in sheet.columns:
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

        sheet.column_dimensions[column_letter].width = min(
            max_length + 2,
            40
        )

    file = BytesIO()
    workbook.save(file)
    file.seek(0)

    filename = (
        f"finance_{datetime.now().strftime('%Y_%m_%d')}.xlsx"
    )

    await update.message.reply_document(
        document=file,
        filename=filename,
        caption="📊 تم إنشاء ملف Excel لحسابك."
    )


# ============================================================
# 🗑️ حذف + طريقة الدفع
# ============================================================

async def handle_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    data = query.data
    user_id = query.from_user.id

    if data.startswith("delete_"):

        transaction_id = int(data.split("_")[1])

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            DELETE FROM transactions
            WHERE id=? AND user_id=?
        """, (
            transaction_id,
            user_id
        ))

        deleted = cur.rowcount

        conn.commit()
        conn.close()

        if deleted:
            await query.edit_message_text(
                "🗑️ تم حذف العملية بنجاح."
            )
        else:
            await query.edit_message_text(
                "❌ لا يمكن حذف هذه العملية."
            )

        return

    if data.startswith("card_") or data.startswith("cash_"):

        parts = data.split("_")

        method = (
            "💳 كارت بنك"
            if parts[0] == "card"
            else "💵 نقدي"
        )

        transaction_id = int(parts[1])

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            UPDATE transactions
            SET payment_method=?
            WHERE id=? AND user_id=?
        """, (
            method,
            transaction_id,
            user_id
        ))

        conn.commit()
        conn.close()

        keyboard = [[
            InlineKeyboardButton(
                "🗑️ حذف العملية",
                callback_data=f"delete_{transaction_id}"
            )
        ]]

        await query.edit_message_text(
            f"✅ تم اعتماد طريقة الدفع:\n\n"
            f"{method}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


# ============================================================
# 🏠 لوحة التحكم
# ============================================================

def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 التقرير", callback_data="menu_report"),
            InlineKeyboardButton("📋 العمليات", callback_data="menu_recent")
        ],
        [
            InlineKeyboardButton("📈 الإحصائيات", callback_data="menu_stats"),
            InlineKeyboardButton("🎯 الميزانية", callback_data="menu_budget")
        ],
        [
            InlineKeyboardButton("💵 الدولار", callback_data="menu_usd"),
            InlineKeyboardButton("💶 اليورو", callback_data="menu_eur")
        ],
        [
            InlineKeyboardButton("📊 Excel", callback_data="menu_excel")
        ]
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)

    await update.message.reply_text(
        "💎 <b>المساعد المالي PRO</b>\n\n"
        "سجل مصروفاتك أو دخلك مباشرة، وأنا أتولى الباقي.\n\n"
        "📝 أمثلة:\n"
        "<code>بنزين 500</code>\n"
        "<code>مطعم 250</code>\n"
        "<code>حليب 80</code>\n"
        "<code>راتب 15000</code>\n\n"
        "━━━━━━━━━━━━━━\n"
        "📊 /report — التقرير\n"
        "📋 /recent — آخر العمليات\n"
        "🔍 /search — البحث\n"
        "📈 /stats — الإحصائيات\n"
        "🎯 /budget — تحديد الميزانية\n"
        "🎯 /budget_status — حالة الميزانية\n"
        "💵 /usd — الدولار\n"
        "💶 /eur — اليورو\n"
        "📊 /excel — تصدير Excel\n\n"
        "أرسل أي مصروف الآن 👇",
        reply_markup=main_keyboard(),
        parse_mode="HTML"
    )


# ============================================================
# 🔘 أزرار القائمة
# ============================================================

async def menu_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    action = query.data

    if action == "menu_report":
        await report_callback(update)

    elif action == "menu_recent":
        await recent_callback(update)

    elif action == "menu_stats":
        await stats_callback(update)

    elif action == "menu_budget":
        await query.edit_message_text(
            "🎯 <b>الميزانية</b>\n\n"
            "لتحديد ميزانيتك الشهرية:\n"
            "<code>/budget 10000</code>\n\n"
            "لمعرفة وضع الميزانية:\n"
            "<code>/budget_status</code>",
            parse_mode="HTML"
        )

    elif action == "menu_usd":
        await currency_callback(update, "usd")

    elif action == "menu_eur":
        await currency_callback(update, "eur")

    elif action == "menu_excel":
        await excel_callback(update)


async def report_callback(update):
    query = update.callback_query
    user_id = query.from_user.id

    conn = get_db()
    cur = conn.cursor()

    month = current_month()

    cur.execute("""
        SELECT
            SUM(CASE WHEN type='income' THEN amount_try ELSE 0 END),
            SUM(CASE WHEN type='expense' THEN amount_try ELSE 0 END)
        FROM transactions
        WHERE user_id=? AND substr(created_at,1,7)=?
    """, (user_id, month))

    income, expense = cur.fetchone()

    conn.close()

    income = income or 0
    expense = expense or 0

    await query.edit_message_text(
        f"📊 <b>تقرير {month}</b>\n\n"
        f"💰 الدخل: {income:,.2f} TL\n"
        f"💸 المصاريف: {expense:,.2f} TL\n"
        f"💵 الرصيد: {income-expense:,.2f} TL",
        parse_mode="HTML"
    )


async def recent_callback(update):
    query = update.callback_query

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT item, amount_try, type
        FROM transactions
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT 5
    """, (query.from_user.id,))

    rows = cur.fetchall()
    conn.close()

    if not rows:
        await query.edit_message_text(
            "📭 لا توجد عمليات."
        )
        return

    msg = "📋 <b>آخر العمليات</b>\n\n"

    for row in rows:
        emoji = "💰" if row["type"] == "income" else "💸"

        msg += (
            f"{emoji} {row['item']} — "
            f"{row['amount_try']:,.2f} TL\n"
        )

    await query.edit_message_text(
        msg,
        parse_mode="HTML"
    )


async def stats_callback(update):
    query = update.callback_query
    user_id = query.from_user.id

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            SUM(CASE WHEN type='income' THEN amount_try ELSE 0 END),
            SUM(CASE WHEN type='expense' THEN amount_try ELSE 0 END),
            COUNT(*)
        FROM transactions
        WHERE user_id=?
    """, (user_id,))

    income, expense, count = cur.fetchone()
    conn.close()

    income = income or 0
    expense = expense or 0

    await query.edit_message_text(
        f"📈 <b>الإحصائيات</b>\n\n"
        f"🔢 العمليات: {count}\n"
        f"💰 الدخل: {income:,.2f} TL\n"
        f"💸 المصاريف: {expense:,.2f} TL\n"
        f"💵 الرصيد: {income-expense:,.2f} TL",
        parse_mode="HTML"
    )


async def currency_callback(update, currency):
    query = update.callback_query

    usd, eur = get_live_rates()

    if not usd or not eur:
        await query.edit_message_text(
            "❌ تعذر الحصول على أسعار العملات."
        )
        return

    rate = usd if currency == "usd" else eur
    name = "الدولار" if currency == "usd" else "اليورو"
    symbol = "$" if currency == "usd" else "€"

    await query.edit_message_text(
        f"💱 <b>{name}</b>\n\n"
        f"1 {symbol} ≈ {1/rate:,.2f} TL",
        parse_mode="HTML"
    )


async def excel_callback(update):
    await update.callback_query.edit_message_text(
        "📊 استخدم الأمر:\n\n"
        "/excel\n\n"
        "لإنشاء ملف Excel."
    )


# ============================================================
# 💰 أوامر الدخل
# ============================================================

async def income(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "💰 مثال:\n"
            "/income راتب 15000"
        )
        return

    text = " ".join(context.args)

    await add_transaction(
        update,
        "income",
        text
    )


# ============================================================
# 💸 أمر المصروف
# ============================================================

async def expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "💸 مثال:\n"
            "/expense بنزين 500"
        )
        return

    text = " ".join(context.args)

    await add_transaction(
        update,
        "expense",
        text
    )


# ============================================================
# 💬 استقبال الرسائل
# ============================================================

async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    text = update.message.text.strip()

    await add_transaction(
        update,
        "expense",
        text
    )


# ============================================================
# 🚀 تشغيل البوت
# ============================================================

def main():

    init_db()

    if not BOT_TOKEN:
        print(
            "❌ BOT_TOKEN غير موجود.\n"
            "ضع التوكن في Environment Variables."
        )
        return

    app = Application.builder().token(BOT_TOKEN).build()

    # الأوامر
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("recent", recent))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("usd", usd))
    app.add_handler(CommandHandler("eur", eur))
    app.add_handler(CommandHandler("budget", set_budget))
    app.add_handler(CommandHandler("budget_status", budget_status))
    app.add_handler(CommandHandler("search", search))
    app.add_handler(CommandHandler("excel", export_excel))
    app.add_handler(CommandHandler("income", income))
    app.add_handler(CommandHandler("expense", expense))

    # أزرار القائمة
    app.add_handler(
        CallbackQueryHandler(
            menu_callback,
            pattern=r"^menu_"
        )
    )

    # أزرار العمليات
    app.add_handler(
        CallbackQueryHandler(
            handle_callback,
            pattern=r"^(delete_|card_|cash_)"
        )
    )

    # الرسائل النصية
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler
        )
    )

    print("🚀 المساعد المالي PRO يعمل الآن...")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
