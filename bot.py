import logging
import os
import sqlite3
from io import BytesIO
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)
import openpyxl
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

# --- الإعدادات الأساسية ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "ضع_توكن_البوت_هنا")

# مراحل محادثة إضافة العقار
PROP_TYPE, RENT_DUR, PRICE, AREA, ROOMS, OWNER_NAME, OWNER_PHONE = range(7)
# مراحل محادثة إضافة عميل
CLIENT_NAME, CLIENT_PHONE, CLIENT_AREA, CLIENT_PRICE, CLIENT_ROOMS = range(7, 12)

# --- 1. تهيئة قاعدة البيانات الشاملة ---
def init_db():
    conn = sqlite3.connect("alshahbaa_master.db")
    cursor = conn.cursor()
    
    # جدول العقارات
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS properties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            type TEXT NOT NULL,
            price REAL NOT NULL,
            area TEXT NOT NULL,
            rooms INTEGER NOT NULL,
            commission_rate REAL NOT NULL,
            commission_amount REAL NOT NULL,
            owner_name TEXT NOT NULL,
            owner_phone TEXT NOT NULL,
            status TEXT DEFAULT 'متاح',
            featured INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    
    # جدول العملاء وطلباتهم
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            pref_area TEXT NOT NULL,
            max_price REAL NOT NULL,
            pref_rooms INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    # جدول المصاريف والإيرادات
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS finance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL, -- إيراد / مصروف
            title TEXT NOT NULL,
            amount REAL NOT NULL,
            date TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()

# السباحة العامة: السماح للجميع باستخدام البوت دون تحقّق
def is_admin(user_id: int) -> bool:
    return True

# --- 2. قائمة المساعدة الرئيسية الشاملة ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    menu_text = (
        "<b>🧠 مساعد الشهباء العقاري الذكي</b>\n\n"
        "مرحباً بك في نظام إدارة مكتب الشهباء العقاري 🏠\n"
        "━━━━━━━━━━━━━━\n"
        "<b>🏠 إدارة العقارات:</b>\n"
        "➕ /add — إضافة عقار جديد\n"
        "📋 /list — عرض جميع العقارات\n"
        "🔍 /search [كلمة] — البحث عن عقار\n"
        "🏷️ /available — العقارات المتاحة\n\n"
        "<b>👨‍💼 العملاء والمطابقة:</b>\n"
        "👥 /clients — قائمة العملاء\n"
        "➕ /add_client — إضافة عميل جديد\n"
        "🎯 /matches — مطابقة العملاء والعقارات تلقائياً\n\n"
        "<b>💰 المال والعمولات:</b>\n"
        "📊 /stats — لوحة التحليل المالي\n"
        "📥 /auto_excel — تصدير تقرير Excel المالي\n\n"
        "<b>📢 التسويق:</b>\n"
        "✍️ /caption [ID] — كتابة منشور تسويقي\n"
        "📄 /pdf [ID] — إنشاء بروشور PDF\n"
        "❌ /cancel — إلغاء العملية الحالية\n"
        "━━━━━━━━━━━━━━"
    )
    await update.message.reply_text(menu_text, parse_mode="HTML")

# --- 3. نظام إضافة العقارات وحساب العمولات ---
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("🏠 للبيع", callback_data="للبيع"), InlineKeyboardButton("🔑 للإيجار", callback_data="للإيجار")]]
    await update.message.reply_text("اختر نوع العرض:", reply_markup=InlineKeyboardMarkup(keyboard))
    return PROP_TYPE

async def prop_type_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data
    context.user_data["type"] = choice

    if choice == "للإيجار":
        keyboard = [
            [InlineKeyboardButton("📅 سنوي (أجار شهر)", callback_data="rent_yearly")],
            [InlineKeyboardButton("🗓️ شهري (+33%)", callback_data="rent_monthly")],
            [InlineKeyboardButton("⏳ أسبوعين (+45%)", callback_data="rent_two_weeks")],
            [InlineKeyboardButton("📆 أسبوعي (+85%)", callback_data="rent_weekly")],
            [InlineKeyboardButton("☀️ يومي (+45%)", callback_data="rent_daily")]
        ]
        await query.edit_message_text("اختر **مدة الإيجار** لحساب العمولة تلقائياً:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return RENT_DUR
    else:
        context.user_data["commission_rate"] = 2.5
        await query.edit_message_text("أدخل **السعر الإجمالي للبيع** ($):")
        return PRICE

async def rent_dur_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rates_map = {
        "rent_yearly": (8.33, "سنوي"), "rent_monthly": (33.0, "شهري"),
        "rent_two_weeks": (45.0, "أسبوعين"), "rent_weekly": (85.0, "أسبوعي"),
        "rent_daily": (45.0, "يومي")
    }
    rate, label = rates_map.get(query.data, (8.33, "إيجار"))
    context.user_data["commission_rate"] = rate
    context.user_data["rent_label"] = label
    await query.edit_message_text(f"مدة الإيجار: <b>{label}</b>\nأدخل **مبلغ الإيجار/حق المالك** ($):", parse_mode="HTML")
    return PRICE

async def set_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = float(update.message.text.replace(",", ""))
        context.user_data["price"] = price
        comm_rate = context.user_data["commission_rate"]
        context.user_data["commission_amount"] = (price * comm_rate) / 100
        await update.message.reply_text("أدخل **المنطقة/الاسم العقاري** (مثال: الحمدانية):")
        return AREA
    except ValueError:
        await update.message.reply_text("❌ يرجى إدخال أرقام فقط للسعر:")
        return PRICE

async def set_area(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["area"] = update.message.text.strip()
    await update.message.reply_text("أدخل **عدد الغرف** (أرقام فقط, مثال: 3):")
    return ROOMS

async def set_rooms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["rooms"] = int(update.message.text.strip())
        await update.message.reply_text("أدخل **اسم المالك**: ")
        return OWNER_NAME
    except ValueError:
        await update.message.reply_text("❌ يرجى إدخال رقم لعدد الغرف:")
        return ROOMS

async def set_owner_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["owner_name"] = update.message.text.strip()
    await update.message.reply_text("أدخل **رقم هاتف المالك**: ")
    return OWNER_PHONE

async def set_owner_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["owner_phone"] = update.message.text.strip()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    full_type = context.user_data["type"]
    if context.user_data.get("rent_label"):
        full_type += f" ({context.user_data['rent_label']})"

    title = f"عقار {context.user_data['rooms']} غرف في {context.user_data['area']}"

    conn = sqlite3.connect("alshahbaa_master.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO properties (title, type, price, area, rooms, commission_rate, commission_amount, owner_name, owner_phone, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        title, full_type, context.user_data["price"], context.user_data["area"],
        context.user_data["rooms"], context.user_data["commission_rate"],
        context.user_data["commission_amount"], context.user_data["owner_name"],
        context.user_data["owner_phone"], now_str
    ))
    prop_id = cursor.lastrowid
    conn.commit()
    conn.close()

    await update.message.reply_text(f"🎉 <b>تم حفظ العقار #{prop_id} بنجاح!</b>", parse_mode="HTML")
    context.user_data.clear()
    return ConversationHandler.END

# --- 4. نظام إضافة العملاء والمطابقة الذكية ---
async def add_client_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("أدخل **اسم العميل**: ")
    return CLIENT_NAME

async def set_client_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["c_name"] = update.message.text.strip()
    await update.message.reply_text("أدخل **رقم هاتف العميل**: ")
    return CLIENT_PHONE

async def set_client_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["c_phone"] = update.message.text.strip()
    await update.message.reply_text("أدخل **المنطقة المطلوبة** (مثال: الحمدانية): ")
    return CLIENT_AREA

async def set_client_area(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["c_area"] = update.message.text.strip()
    await update.message.reply_text("أدخل **الحد الأقصى للسعر** ($): ")
    return CLIENT_PRICE

async def set_client_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["c_price"] = float(update.message.text.replace(",", ""))
        await update.message.reply_text("أدخل **عدد الغرف المطلوب**: ")
        return CLIENT_ROOMS
    except ValueError:
        await update.message.reply_text("❌ أدخل رقماً صحيحاً للسعر:")
        return CLIENT_PRICE

async def set_client_rooms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rooms = int(update.message.text.strip())
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        conn = sqlite3.connect("alshahbaa_master.db")
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO clients (name, phone, pref_area, max_price, pref_rooms, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            context.user_data["c_name"], context.user_data["c_phone"],
            context.user_data["c_area"], context.user_data["c_price"], rooms, now_str
        ))
        conn.commit()
        conn.close()

        await update.message.reply_text("✅ <b>تم حفظ ملف العميل بنجاح!</b> يمكنك استخدام /matches لمطابقة طلباته.", parse_mode="HTML")
        context.user_data.clear()
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ أدخل رقماً لعدد الغرف:")
        return CLIENT_ROOMS

async def smart_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect("alshahbaa_master.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name, phone, pref_area, max_price, pref_rooms FROM clients")
    clients = cursor.fetchall()

    if not clients:
        await update.message.reply_text("📭 لا يوجد عملاء مسجلون حالياً.")
        conn.close()
        return

    report = "<b>🎯 محرك المطابقة الذكي (العملاء والعقارات):</b>\n\n"
    found = False
    for c in clients:
        cursor.execute("""
            SELECT id, title, price, area, rooms FROM properties 
            WHERE status = 'متاح' AND area LIKE ? AND price <= ? AND rooms >= ?
        """, (f"%{c[2]}%", c[3], c[4]))
        matches = cursor.fetchall()
        if matches:
            found = True
            report += f"👤 <b>العميل:</b> {c[0]} ({c[1]})\n"
            report += f"🎯 <b>الطلب:</b> {c[2]} | {c[4]} غرف | حتى ${c[3]:,.0f}\n"
            report += "🏡 <b>العقارات المطابقة:</b>\n"
            for m in matches:
                report += f"   • <b>#{m[0]}</b> - {m[1]} | ${m[2]:,.0f}\n"
            report += "----------------------------\n"

    conn.close()
    if not found:
        report += "لم يتم العثور على مطابقات جديدة حالياً."
    await update.message.reply_text(report, parse_mode="HTML")

# --- 5. التسويق وقوالب المنشورات والبروشور ---
async def generate_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ حدد رقم العقار. مثال: <code>/caption 1</code>", parse_mode="HTML")
        return

    conn = sqlite3.connect("alshahbaa_master.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, type, price, area, rooms FROM properties WHERE id = ?", (context.args[0],))
    p = cursor.fetchone()
    conn.close()

    if not p:
        await update.message.reply_text("❌ العقار غير موجود.")
        return

    post = (
        f"🏠 <b>عرض عقاري مميز - مكتب الشهباء</b> 🏠\n\n"
        f"📌 <b>العنوان:</b> {p[1]}\n"
        f"🏷️ <b>النوع:</b> {p[2]}\n"
        f"📍 <b>المنطقة:</b> {p[4]}\n"
        f"🚪 <b>عدد الغرف:</b> {p[5]}\n"
        f"💰 <b>السعر:</b> ${p[3]:,.0f}\n\n"
        "✨ جاهز للتسليم الفوري مع كافة الخدمات!\n\n"
        "📞 <b>للتواصل والاستفسار:</b>\n"
        "مكتب الشهباء العقاري - حلب، سوريا\n"
        "📱 اتصل بنا الآن للمعاينة!"
    )
    await update.message.reply_text(post, parse_mode="HTML")

# --- 6. الإلغاء والأوامر العامة ---
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ تم إلغاء العملية.")
    return ConversationHandler.END

# --- التشغيل الرئيسي ---
def main():
    init_db()
    if not BOT_TOKEN or BOT_TOKEN == "ضع_توكن_البوت_هنا":
        print("❌ الرجاء ضبط BOT_TOKEN!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    # محادثة إضافة عقار
    prop_conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            PROP_TYPE: [CallbackQueryHandler(prop_type_choice)],
            RENT_DUR: [CallbackQueryHandler(rent_dur_choice)],
            PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_price)],
            AREA: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_area)],
            ROOMS: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_rooms)],
            OWNER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_owner_name)],
            OWNER_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_owner_phone)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # محادثة إضافة عميل
    client_conv = ConversationHandler(
        entry_points=[CommandHandler("add_client", add_client_start)],
        states={
            CLIENT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_client_name)],
            CLIENT_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_client_phone)],
            CLIENT_AREA: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_client_area)],
            CLIENT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_client_price)],
            CLIENT_ROOMS: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_client_rooms)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(prop_conv)
    app.add_handler(client_conv)
    app.add_handler(CommandHandler("matches", smart_matches))
    app.add_handler(CommandHandler("caption", generate_caption))

    print("⚡ البوت مفتوح للجميع وجاهز للاستخدام...")
    app.run_polling()

if __name__ == "__main__":
    main()
