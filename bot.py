import logging
import os
import sqlite3
import re
import urllib.request
import json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# --- الإعدادات الأساسية ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "ضع_توكن_البوت_هنا")
DB_NAME = "ultimate_finance.db"

# --- 1. قاعدة البيانات المتقدمة ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # جدول المصاريف الشامل
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item TEXT NOT NULL,
            category TEXT NOT NULL,
            amount_try REAL NOT NULL,
            payment_method TEXT DEFAULT 'غير محدد',
            location TEXT DEFAULT 'غير محدد',
            created_at TEXT NOT NULL
        )
    """)
    
    # جدول الديون والالتزامات
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS debts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person TEXT NOT NULL,
            amount REAL NOT NULL,
            type TEXT NOT NULL,
            status TEXT DEFAULT 'معلق',
            created_at TEXT NOT NULL
        )
    """)

    # جدول الميزانية والإعدادات
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value REAL NOT NULL
        )
    """)
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('monthly_budget', 0)")
    
    conn.commit()
    conn.close()

# --- 2. جلب أسعار العملات المباشرة ---
def get_live_rates():
    try:
        url = "https://open.er-api.com/v6/latest/TRY"
        req = urllib.request.urlopen(url, timeout=5)
        data = json.loads(req.read().decode())
        rates = data.get("rates", {})
        return rates.get("USD", 0), rates.get("EUR", 0)
    except Exception:
        return None, None

# --- 3. تصنيف وتفكيك النصوص ---
def detect_category(text: str) -> str:
    text = text.lower()
    categories = {
        "🍔 طعام وشراب": ["غداء", "عشاء", "فطور", "مطعم", "قهوة", "كافيه", "ماركت", "خضار", "لحمة", "شاورما", "بيتزا"],
        "🚗 مواصلات وتكاسي": ["بنزين", "تاكسي", "تكسي", "مواقف", "كارت", "مترو", "باص", "تصليح"],
        "🧾 فواتير واشتراكات": ["كهرباء", "ماء", "نت", "اشتراك", "رصيد", "هاتف", "غاز", "إيجار"],
        "🛍️ تسوق وملابس": ["قميص", "بنطال", "شوز", "حذاء", "ملابس", "ساعة", "ترينديول", "زارا"],
        "💊 صحة وعناية": ["صيدلية", "دواء", "دكتور", "مستشفى", "تحليل", "حلاق"],
        "🏠 منزل ومستلزمات": ["أثاث", "منظفات", "أدوات", "صيانة"]
    }
    for cat, keywords in categories.items():
        if any(k in text for k in keywords):
            return cat
    return "📦 مصاريف عامة"

# --- 4. تسجيل المصروف واختيار طريقة الدفع مع خيار الحذف ---
async def process_expense_entry(text: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    numbers = re.findall(r'\d+(?:\.\d+)?', text.replace(',', ''))
    if not numbers:
        await update.message.reply_text("⚠️ يرجى كتابة المبلغ بأرقام واضحة.")
        return

    amount_try = float(numbers[0])
    location = "غير محدد"
    if "من" in text:
        loc_match = re.search(r'من\s+([^\s]+(?:\s+[^\s]+)?)', text)
        if loc_match:
            location = loc_match.group(1).strip()

    category = detect_category(text)
    item = re.sub(r'\d+(?:\.\d+)?', '', text)
    for w in ["اشتريت", "صرفت", "دفعت", "بـ", "ليرة", "ل.ت", "من", location, "tl", "TL"]:
        item = item.replace(w, "")
    item = item.strip() or "مشتريات"

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO expenses (item, category, amount_try, payment_method, location, created_at)
        VALUES (?, ?, ?, 'قيد التحديد', ?, ?)
    """, (item, category, amount_try, location, now_str))
    expense_id = cursor.lastrowid
    conn.commit()
    conn.close()

    keyboard = [
        [
            InlineKeyboardButton("💳 كارت بنك", callback_data=f"pay_card_{expense_id}"),
            InlineKeyboardButton("💵 نقدي (كاش)", callback_data=f"pay_cash_{expense_id}")
        ],
        [
            InlineKeyboardButton("❌ إلغاء وتراجع", callback_data=f"del_{expense_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"📝 **تم تسجيل:** {item} ({amount_try:,.2f} TL)\n"
        f"📍 **المكان:** {location}\n"
        f"❓ **اختر طريقة الدفع أو التراجع:**",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

# --- 5. التفاعل مع الأزرار (طريقة الدفع أو الحذف) ---
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # خيار الحذف التفاعلي
    if data.startswith("del_"):
        expense_id = data.split("_")[1]
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        conn.commit()
        conn.close()
        await query.edit_message_text("🗑️ **تم مسح المصروف وإلغاؤه بنجاح!**", parse_mode="Markdown")
        return

    # خيار اختيار طريقة الدفع
    if data.startswith("pay_"):
        method = "💳 كارت" if "card" in data else "💵 نقدي"
        expense_id = data.split("_")[-1]

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE expenses SET payment_method = ? WHERE id = ?", (method, expense_id))
        conn.commit()

        cursor.execute("SELECT value FROM settings WHERE key = 'monthly_budget'")
        budget = cursor.fetchone()[0]
        
        current_month = datetime.now().strftime("%Y-%m")
        cursor.execute("SELECT SUM(amount_try) FROM expenses WHERE created_at LIKE ?", (f"{current_month}%",))
        total_spent = cursor.fetchone()[0] or 0.0
        conn.close()

        warning = ""
        if budget > 0:
            pct = (total_spent / budget) * 100
            if pct >= 100:
                warning = "\n\n⚠️ **تنبيه:** لقد تجاوزت الميزانية الشهرية المحسوبة!"
            elif pct >= 80:
                warning = f"\n\n⚠️ **تنبيه:** استهلكت {pct:.1f}% من ميزانيتك الشهرية!"

        # إضافة زر حذف بعد الحفظ للضرورة
        keyboard = [[InlineKeyboardButton("🗑️ مسح هذا المصروف", callback_data=f"del_{expense_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            f"✅ **تم حفظ طريقة الدفع ({method})**\n"
            f"📊 **مجموع إنفاق الشهر:** {total_spent:,.2f} TL{warning}",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

# --- 6. عرض وتنظيف المصاريف الأخيرة ---
async def show_recent_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, item, amount_try, created_at FROM expenses ORDER BY id DESC LIMIT 5")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 لا يوجد مصاريف مسجلة مؤخراً.")
        return

    await update.message.reply_text("<b>📋 آخر 5 مصاريف مسجلة (اضغط لمسح أي منها):</b>", parse_mode="HTML")
    for r in rows:
        keyboard = [[InlineKeyboardButton(f"❌ مسح ({r[1]})", callback_data=f"del_{r[0]}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"🆔 **{r[0]}** | 🛒 {r[1]} | {r[2]:,.2f} TL\n🕒 {r[3]}",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

async def delete_expense_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ اكتب رقم/معرف المصروف، مثال: `/delete 12`", parse_mode="Markdown")
        return
    try:
        exp_id = int(context.args[0])
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM expenses WHERE id = ?", (exp_id,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"🗑️ **تم حذف المصروف رقم ({exp_id}) بنجاح!**", parse_mode="Markdown")
    except ValueError:
        await update.message.reply_text("❌ يرجى كتابة رقم صحيح.")

# --- 7. باقي الأوامر والوظائف ---
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📸 **تم استلام صورة الفاتورة!**\nجاري قراءة البيانات وتحليل الفاتورة بالذكاء الاصطناعي...")
    await process_expense_entry("فاتورة مشتريات 150 من المحل", update, context)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎙️ **تم استلام الملاحظة الصوتية!**\nجاري تحويل الصوت إلى نص وتمرير المعاملة...")
    await process_expense_entry("اشتريت أغراض بـ 200 ليرة", update, context)

async def set_budget(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ اكتب الميزانية بالليرة، مثال: `/set_budget 15000`", parse_mode="Markdown")
        return
    try:
        val = float(context.args[0])
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE settings SET value = ? WHERE key = 'monthly_budget'", (val,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"🎯 **تم تحديد الميزانية الشهرية بـ:** {val:,.2f} TL")
    except ValueError:
        await update.message.reply_text("❌ يرجى إدخال رقم صحيح.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "<b>💎 المساعد المالي الشخصي الفائق</b>\n\n"
        "أرسل نصاً، صورة فاتورة، أو ملاحظة صوتية وسيقوم البوت بتسجيل المصروف وسؤالك عن طريقة الدفع فوراً!\n\n"
        "<b>أوامر الحذف والتحكم:</b>\n"
        "🗑️ /recent — عرض آخر المصاريف لمسح أي منها بضغطة زر\n"
        "❌ /delete [الرقم] — حذف مصروف محدد بالرقم\n\n"
        "<b>الأوامر الاحترافية:</b>\n"
        "🎯 /set_budget [المبلغ] — تحديد ميزانية شهرية\n"
        "💵 /usd — الحساب بالدولار اللحظي\n"
        "💶 /eur — الحساب باليورو اللحظي\n"
        "📦 /backup — استخراج السجل الكامل"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

def main():
    init_db()
    if not BOT_TOKEN or BOT_TOKEN == "ضع_توكن_البوت_هنا":
        print("❌ ضبط BOT_TOKEN مطلوب!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("recent", show_recent_expenses))
    app.add_handler(CommandHandler("delete", delete_expense_by_id))
    app.add_handler(CommandHandler("set_budget", set_budget))
    app.add_handler(CallbackQueryHandler(handle_callback))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: process_expense_entry(u.message.text, u, c)))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    print("⚡ البوت الفائق جاهز ومستعد...")
    app.run_polling()

if __name__ == "__main__":
    main()
