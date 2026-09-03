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
    filters,
)

# --- الإعدادات الأساسية ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "ضع_توكن_البوت_هنا")
DB_NAME = "try_expenses.db"

# --- 1. قاعدة بيانات دائمة وآمنة ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # جدول المصاريف الأساسي بالليرة التركية
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item TEXT NOT NULL,
            category TEXT NOT NULL,
            amount_try REAL NOT NULL,
            location TEXT DEFAULT 'غير محدد',
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

# --- 2. جلب سعر الصرف اللحظي لليرة التركية ---
def get_live_rates():
    """جلب سعر صرف الليرة التركية مقابل الدولار واليورو لحظياً"""
    try:
        url = "https://open.er-api.com/v6/latest/TRY"
        req = urllib.request.urlopen(url, timeout=5)
        data = json.loads(req.read().decode())
        rates = data.get("rates", {})
        usd_rate = rates.get("USD", 0) # كم دولار تساوي الليرة الواحدة
        eur_rate = rates.get("EUR", 0) # كم يورو تساوي الليرة الواحدة
        return usd_rate, eur_rate
    except Exception as e:
        logging.error(f"خطأ في جلب أسعار العملات: {e}")
        return None, None

# --- 3. محرك التصنيف الذكي ---
def detect_category(text: str) -> str:
    text = text.lower()
    categories = {
        "🍔 طعام وشراب": ["غداء", "عشاء", "فطور", "مطعم", "قهوة", "كافيه", "أكل", "ماركت", "خضار", "لحمة", "شاورما", "بيتزا", "مخبز", "تنتوني"],
        "🚗 مواصلات وتكاسي": ["بنزين", "تاكسي", "تكسي", "مواقف", "كارت", "كرت", "مترو", "باص", "تصليح", "مغسلة"],
        "🧾 فواتير واشتراكات": ["كهرباء", "ماء", "نت", "أنترنت", "اشتراك", "رصيد", "هاتف", "غاز", "إيجار", "ايجار"],
        "🛍️ تسوق وملابس": ["قميص", "بنطال", "شوز", "حذاء", "ملابس", "ساعة", "عطر", "ترينديول", "زارا"],
        "💊 صحة وعناية": ["صيدلية", "دواء", "دكتور", "مستشفى", "تحليل", "علاج", "حلاق"],
        "🏠 منزل ومستلزمات": ["أثاث", "منظفات", "أدوات", "صيانة", "شوكلاتة"]
    }
    for cat, keywords in categories.items():
        if any(keyword in text for keyword in keywords):
            return cat
    return "📦 مصاريف عامة"

# --- 4. تسجيل المصاريف بالليرة التركية ---
async def handle_expense(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    # استخراج المبلغ
    numbers = re.findall(r'\d+(?:\.\d+)?', text.replace(',', ''))
    if not numbers:
        await update.message.reply_text("⚠️ يرجى كتابة المبلغ بالليرة التركية بشكل واضح بالأرقام.")
        return

    amount_try = float(numbers[0])

    # استخراج المكان إذا احتوت الرسالة على كلمة "من"
    location = "غير محدد"
    if "من" in text:
        loc_match = re.search(r'من\s+([^\s]+(?:\s+[^\s]+)?)', text)
        if loc_match:
            location = loc_match.group(1).strip()

    # تصنيف المصروف
    category = detect_category(text)

    # تنظيف اسم المنتج
    item = re.sub(r'\d+(?:\.\d+)?', '', text)
    for w in ["اشتريت", "صرفت", "دفعت", "بـ", "ليرة", "ل.ت", "من", location, "tl", "TL"]:
        item = item.replace(w, "")
    item = item.strip() or "مشتريات"

    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M")

    # حفظ دائم بالليرة التركية
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO expenses (item, category, amount_try, location, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (item, category, amount_try, location, now_str))
    conn.commit()
    conn.close()

    reply = (
        f"✅ **تم تسجيل المصروف بالليرة التركية!**\n"
        f"━━━━━━━━━━━━━━\n"
        f"🏷️ **المنتج:** {item}\n"
        f"📂 **الفئة:** {category}\n"
        f"💵 **المبلغ:** {amount_try:,.2f} TL\n"
        f"📍 **المكان:** {location}\n"
        f"🕒 **الوقت:** {now.strftime('%I:%M %p')} | {now.strftime('%Y-%m-%d')}\n"
        f"━━━━━━━━━━━━━━"
    )
    await update.message.reply_text(reply, parse_mode="Markdown")

# --- 5. أوامر التحويل والتقارير الحية ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "<b>🧠 نظام إدارة المصاريف الشخصية بالليرة التركية</b>\n\n"
        "أرسل لي مصاريفك بالليرة التركية فور حدوثها، وسأقوم بتسجيلها وتصنيفها تلقائياً.\n\n"
        "<b>أمثلة للإرسال:</b>\n"
        "• <code>غداء 250 من مطعم السلطان</code>\n"
        "• <code>كرت باص 100</code>\n"
        "• <code>ماركت 850 من شوك</code>\n\n"
        "<b>التقارير وسعر الصرف اللحظي:</b>\n"
        "📊 /report — تقرير المصاريف بالليرة التركية\n"
        "💵 /usd — تحويل وحساب المجموع بالدولار ($)\n"
        "💶 /eur — تحويل وحساب المجموع باليورو (€)\n"
        "📦 /backup — نسخة احتياطية لسجلاتك"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

async def get_total_try_this_month():
    current_month = datetime.now().strftime("%Y-%m")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT SUM(amount_try) FROM expenses WHERE created_at LIKE ?", (f"{current_month}%",))
    total_try = cursor.fetchone()[0] or 0.0
    conn.close()
    return total_try, current_month

async def usd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_try, month = await get_total_try_this_month()
    if total_try == 0:
        await update.message.reply_text("📭 لا يوجد مصاريف مسجلة لهذا الشهر.")
        return

    usd_rate, _ = get_live_rates()
    if usd_rate:
        total_usd = total_try * usd_rate
        # سعر صرف 1 دولار بالليرة
        try_per_usd = 1 / usd_rate
        msg = (
            f"<b>💵 مجموع مصاريف شهر ({month}) بالدولار:</b>\n\n"
            f"🏛️ **الإجمالي بالليرة:** {total_try:,.2f} TL\n"
            f"🌐 **الإجمالي بالدولار:** <code>${total_usd:,.2f}</code>\n"
            f"📈 **سعر الصرف اللحظي:** 1$ = {try_per_usd:,.2f} TL"
        )
    else:
        msg = "❌ تعذر جلب سعر صرف الدولار المباشر، يرجى المحاولة لاحقاً."
    
    await update.message.reply_text(msg, parse_mode="HTML")

async def eur_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total_try, month = await get_total_try_this_month()
    if total_try == 0:
        await update.message.reply_text("📭 لا يوجد مصاريف مسجلة لهذا الشهر.")
        return

    _, eur_rate = get_live_rates()
    if eur_rate:
        total_eur = total_try * eur_rate
        try_per_eur = 1 / eur_rate
        msg = (
            f"<b>💶 مجموع مصاريف شهر ({month}) باليورو:</b>\n\n"
            f"🏛️ **الإجمالي بالليرة:** {total_try:,.2f} TL\n"
            f"🌐 **الإجمالي باليورو:** <code>€{total_eur:,.2f}</code>\n"
            f"📈 **سعر الصرف اللحظي:** 1€ = {try_per_eur:,.2f} TL"
        )
    else:
        msg = "❌ تعذر جلب سعر صرف اليورو المباشر، يرجى المحاولة لاحقاً."
    
    await update.message.reply_text(msg, parse_mode="HTML")

async def detailed_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    current_month = datetime.now().strftime("%Y-%m")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT category, SUM(amount_try), COUNT(*) 
        FROM expenses 
        WHERE created_at LIKE ? 
        GROUP BY category
    """, (f"{current_month}%",))
    cats = cursor.fetchall()
    
    cursor.execute("SELECT SUM(amount_try) FROM expenses WHERE created_at LIKE ?", (f"{current_month}%",))
    total_try = cursor.fetchone()[0] or 0.0
    conn.close()

    if not cats:
        await update.message.reply_text("📭 لا يوجد مصاريف مسجلة لهذا الشهر.")
        return

    msg = f"<b>📊 تقرير مصاريف شهر ({current_month}):</b>\n\n"
    for c in cats:
        msg += f"<b>{c[0]}:</b> {c[1]:,.2f} TL <i>({c[2]} عمليات)</i>\n"
    
    msg += f"\n━━━━━━━━━━━━━━\n"
    msg += f"💰 **الإجمالي الكلي:** {total_try:,.2f} TL\n\n"
    msg += "💡 <i>للحساب بالدولار أرسل /usd أو باليورو أرسل /eur</i>"
    
    await update.message.reply_text(msg, parse_mode="HTML")

async def backup_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if os.path.exists(DB_NAME):
        await update.message.reply_document(
            document=open(DB_NAME, "rb"),
            caption=f"📦 **سجل المصاريف الكامل بالليرة التركية**\nالتاريخ: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            parse_mode="Markdown"
        )

# --- التشغيل الرئيسي ---
def main():
    init_db()
    if not BOT_TOKEN or BOT_TOKEN == "ضع_توكن_البوت_هنا":
        print("❌ ضبط التوكن مطلوب!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("report", detailed_report))
    app.add_handler(CommandHandler("usd", usd_summary))
    app.add_handler(CommandHandler("eur", eur_summary))
    app.add_handler(CommandHandler("backup", backup_db))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_expense))

    print("⚡ البوت المالي بالليرة التركية جاهز...")
    app.run_polling()

if __name__ == "__main__":
    main()
