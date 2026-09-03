import logging
import os
import sqlite3
import re
import urllib.request
import json
from datetime import datetime, timedelta
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

# --- 1. قاعدة البيانات ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value REAL NOT NULL
        )
    """)
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('monthly_budget', 0)")
    conn.commit()
    conn.close()

# --- 2. محرك تصنيف ذكي وشامل ---
def detect_category(text: str) -> str:
    text = text.lower()
    categories = {
        "🛒 سوبرماركت وأغذية": ["حليب", "أكلات", "بيم", "bim", "شوك", "şok", "A101", "a101", "جبنة", "بيض", "لبن", "خبز", "ماركت", "خضار", "فواكه", "لحم", "دجاج", "مياه", "زيت"],
        "🍔 مطاعم وكافيهات": ["غداء", "عشاء", "فطور", "مطعم", "قهوة", "كافيه", "شاورما", "بيتزا", "برغر", "دونر", "تنتوني", "كباب", "حلويات"],
        "🚗 مواصلات وتكاسي": ["بنزين", "تاكسي", "تكسي", "مواقف", "كارت", "كرت", "مترو", "باص", "تصليح", "مغسلة", "دولموش"],
        "🧾 فواتير واشتراكات": ["كهرباء", "ماء", "نت", "أنترنت", "اشتراك", "رصيد", "هاتف", "غاز", "إيجار", "ايجار", "فاتورة"],
        "🛍️ تسوق وملابس": ["قميص", "بنطال", "شوز", "حذاء", "ملابس", "ساعة", "عطر", "ترينديول", "زارا", "lcw", "ل سي"],
        "💊 صحة وعناية": ["صيدلية", "دواء", "دكتور", "مستشفى", "تحليل", "علاج", "حلاق", "شامبو"]
    }
    for cat, keywords in categories.items():
        if any(k in text for k in keywords):
            return cat
    return "📦 مصاريف عامة"

# --- 3. تسجيل المصروف واختيار طريقة الدفع ---
async def process_expense_entry(text: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    numbers = re.findall(r'\d+(?:\.\d+)?', text.replace(',', ''))
    if not numbers:
        await update.message.reply_text("⚠️ يرجى كتابة المبلغ بالأرقام بشكل واضح.")
        return

    amount_try = float(numbers[0])
    
    # تحديد المكان
    location = "غير محدد"
    if "من" in text:
        loc_match = re.search(r'من\s+([^\s]+(?:\s+[^\s]+)?)', text)
        if loc_match:
            location = loc_match.group(1).strip()
    else:
        # التعرّف على المحلات المشهورة كأمكنة تلقائية
        for place in ["البيم", "بيم", "شوك", "a101", "A101", "ترينديول"]:
            if place in text.lower():
                location = place
                break

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
            InlineKeyboardButton("🗑️ مسح المصروف", callback_data=f"del_{expense_id}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"✅ **تم تسجيل المصروف!**\n"
        f"━━━━━━━━━━━━━━\n"
        f"🏷️ **المنتج:** {item}\n"
        f"📂 **الفئة:** {category}\n"
        f"💵 **المبلغ:** {amount_try:,.2f} TL\n"
        f"📍 **المكان:** {location}\n"
        f"🕒 **الوقت:** {now_str}\n"
        f"━━━━━━━━━━━━━━\n"
        f"❓ **حدد طريقة الدفع:**",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

# --- 4. معالجة الأزرار التفاعلية ---
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("del_"):
        expense_id = data.split("_")[1]
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        conn.commit()
        conn.close()
        await query.edit_message_text("🗑️ **تم مسح المصروف بنجاح!**", parse_mode="Markdown")
        return

    if data.startswith("pay_"):
        method = "💳 كارت" if "card" in data else "💵 نقدي"
        expense_id = data.split("_")[-1]

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE expenses SET payment_method = ? WHERE id = ?", (method, expense_id))
        conn.commit()
        conn.close()

        await query.edit_message_text(f"✅ **تم اعتماد طريقة الدفع:** {method}", parse_mode="Markdown")

# --- 5. التقرير المالي الشامل المطور ---
async def detailed_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    current_month = now.strftime("%Y-%m")
    
    # تحديد تواريخ الأسبوع الحالي والأسبوع الماضي
    start_this_week = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    start_last_week = (now - timedelta(days=now.weekday() + 7)).strftime("%Y-%m-%d")
    end_last_week = (now - timedelta(days=now.weekday() + 1)).strftime("%Y-%m-%d")

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # 1. تفصيل حسب الفئات
    cursor.execute("""
        SELECT category, SUM(amount_try), COUNT(*) 
        FROM expenses 
        WHERE created_at LIKE ? 
        GROUP BY category 
        ORDER BY SUM(amount_try) DESC
    """, (f"{current_month}%",))
    category_summary = cursor.fetchall()
    
    # 2. الإجمالي الكلي للشهر
    cursor.execute("SELECT SUM(amount_try) FROM expenses WHERE created_at LIKE ?", (f"{current_month}%",))
    total_month = cursor.fetchone()[0] or 0.0

    # 3. حساب الأسبوع الحالي
    cursor.execute("SELECT SUM(amount_try) FROM expenses WHERE date(created_at) >= ?", (start_this_week,))
    total_this_week = cursor.fetchone()[0] or 0.0

    # 4. حساب الأسبوع الماضي
    cursor.execute("SELECT SUM(amount_try) FROM expenses WHERE date(created_at) BETWEEN ? AND ?", (start_last_week, end_last_week))
    total_last_week = cursor.fetchone()[0] or 0.0

    conn.close()

    if not category_summary:
        await update.message.reply_text("📭 لا يوجد مصاريف مسجلة لهذا الشهر حتى الآن.")
        return

    # صياغة التقرير والتحليلات
    top_category = category_summary[0][0]
    top_amount = category_summary[0][1]
    top_pct = (top_amount / total_month) * 100 if total_month > 0 else 0

    # حساب فرق الأسابيع
    week_diff = total_this_week - total_last_week
    if week_diff > 0:
        week_analysis = f"📈 **ارتفع إنفاقك هذا الأسبوع** بمقدار `{week_diff:,.2f} TL` عن الأسبوع الماضي."
    elif week_diff < 0:
        week_analysis = f"📉 **ممتاز! انخفض إنفاقك هذا الأسبوع** بمقدار `{abs(week_diff):,.2f} TL` مقارنة بالأسبوع الماضي."
    else:
        week_analysis = "⚖️ **إنفاقك هذا الأسبوع مساوٍ تماماً** للإنفاق في الأسبوع الماضي."

    # توليد نصيحة للترشيد بناءً على أعلى فئة
    if "سوبرماركت" in top_category or "مطاعم" in top_category:
        tip = "💡 **نصيحة مالية:** أعلى إنفاق لديك على الطعام والمواد الغذائية. يفضل تحضير قائمة قبل الشراء لتقليل الشراء العشوائي."
    elif "مواصلات" in top_category:
        tip = "💡 **نصيحة مالية:** قطاع المواصلات يستهلك معظم ميزانيتك. شحن الكرت بمبالغ كبيرة بداية الشهر يحميك من المصاريف الفردية المفاجئة."
    else:
        tip = "💡 **نصيحة مالية:** حاول تخصيص 10% من الميزانية للادخار في بداية كل شهر قبل البدء بالإنفاق."

    msg = (
        f"📊 **تقرير وتحليل المصاريف الشامل ({current_month})**\n"
        f"━━━━━━━━━━━━━━\n"
        f"💰 **إجمالي الإنفاق الشهري:** `{total_month:,.2f} TL`\n\n"
        f"🔥 **أعلى فئة إنفاق:** {top_category}\n"
        f"📊 **نسبتها من الميزانية:** `{top_pct:.1f}%` ({top_amount:,.2f} TL)\n"
        f"━━━━━━━━━━━━━━\n"
        f"<b>📋 تفصيل الإنفاق حسب الفئات:</b>\n"
    )

    for cat in category_summary:
        msg += f"• <b>{cat[0]}:</b> {cat[1]:,.2f} TL <i>({cat[2]} عمليات)</i>\n"

    msg += (
        f"\n━━━━━━━━━━━━━━\n"
        f"<b>🗓️ تحليل ومقارنة الأسابيع:</b>\n"
        f"• **إنفاق الأسبوع الحالي:** `{total_this_week:,.2f} TL`\n"
        f"• **إنفاق الأسبوع الماضي:** `{total_last_week:,.2f} TL`\n"
        f"{week_analysis}\n\n"
        f"{tip}\n"
        f"━━━━━━━━━━━━━━\n"
        f"💡 <i>للحساب بالدولار أرسل /usd أو باليورو أرسل /eur</i>"
    )

    await update.message.reply_text(msg, parse_mode="HTML")

# --- 6. باقي الأوامر والتشغيل ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "<b>💎 المساعد المالي الشخصي الفائق</b>\n\n"
        "أرسل مشترياتك مباشرة وسيتم تصنيفها وتحديد مكانها وتحديث تحليلاتك فوراً.\n\n"
        "<b>أوامر التقرير والتحليل:</b>\n"
        "📊 /report — تقرير مفصل، مقارنة أسابيع، ونصائح ترشيد\n"
        "💵 /usd — تحويل المجموع للدولار\n"
        "💶 /eur — تحويل المجموع لليورو\n"
        "🗑️ /recent — عرض المصاريف الأخيرة لمسح أي منها"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

def main():
    init_db()
    if not BOT_TOKEN or BOT_TOKEN == "ضع_توكن_البوت_هنا":
        print("❌ ضبط BOT_TOKEN مطلوب!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("report", detailed_report))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: process_expense_entry(u.message.text, u, c)))

    print("⚡ البوت المالي المطور جاهز...")
    app.run_polling()

if __name__ == "__main__":
    main()
