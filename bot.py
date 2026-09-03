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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

ADMIN_ID = os.environ.get("ADMIN_ID", "ضع_معرف_حسابك_هنا") 
BOT_TOKEN = os.environ.get("BOT_TOKEN", "ضع_توكن_البوت_هنا")

DEFAULT_SALE_COMMISSION = 2.5  # عمولة البيع 2.5%

# المراحل الخاصة بالمحادثة
TYPE, RENT_DURATION, PRICE, OWNER_NAME, OWNER_PHONE = range(5)

def init_db():
    conn = sqlite3.connect("real_estate.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS properties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            price REAL NOT NULL,
            commission_rate REAL NOT NULL,
            commission_amount REAL NOT NULL,
            owner_name TEXT NOT NULL,
            owner_phone TEXT NOT NULL,
            status TEXT DEFAULT 'متاح',
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def is_admin(user_id: int) -> bool:
    if not ADMIN_ID or ADMIN_ID == "ضع_معرف_حسابك_هنا":
        return True
    return str(user_id) == str(ADMIN_ID)

def generate_excel_report():
    conn = sqlite3.connect("real_estate.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, type, price, commission_rate, commission_amount, owner_name, owner_phone, status, created_at FROM properties")
    rows = cursor.fetchall()
    
    cursor.execute("SELECT SUM(commission_amount) FROM properties WHERE status IN ('تم البيع', 'تم الإيجار')")
    earned = cursor.fetchone()[0] or 0.0
    cursor.execute("SELECT SUM(commission_amount) FROM properties WHERE status = 'متاح'")
    expected = cursor.fetchone()[0] or 0.0
    conn.close()

    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "سجل الصفقات والعمولات"
    ws1.append(["رقم العقار", "النوع", "السعر", "النسبة %", "قيمة العمولة", "اسم المالك", "رقم الهاتف", "الحالة", "تاريخ الإضافة"])
    for row in rows:
        ws1.append(list(row))

    ws2 = wb.create_sheet(title="الملخص المالي")
    ws2.append(["البيان", "المبلغ الكلي"])
    ws2.append(["إجمالي العمولات المحصلة", earned])
    ws2.append(["إجمالي العمولات المتوقعة", expected])
    ws2.append(["مجموع العمولات الكلي", earned + expected])

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🔒 هذا البوت خاص ومغلق بصلاحيات الآدمن فقط.")
        return

    welcome_text = (
        "<b>🧠 أهلاً بك في مساعدك العقاري السريع</b>\n\n"
        "<b>الأوامر المتاحة:</b>\n"
        "➕ /add - إضافة عقار (حساب العمولة آلياً حسب نوع وعقد العقار)\n"
        "📈 /stats - لوحة الأرباح والعمولات المالية\n"
        "📋 /list - عرض كافة العقارات\n"
        "🔍 /search [كلمة] - بحث باسم المالك أو رقمه\n"
        "📊 /auto_excel - استخراج تقرير Excel المالي\n"
        "📄 /pdf [ID] - إنشاء بروشور تسويقي PDF\n"
        "❌ /cancel - إلغاء العملية الحالية"
    )
    await update.message.reply_text(welcome_text, parse_mode="HTML")

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    keyboard = [
        [InlineKeyboardButton("🏠 للبيع", callback_data="للبيع"), InlineKeyboardButton("🔑 للإيجار", callback_data="للإيجار")]
    ]
    await update.message.reply_text("اختر نوع العرض:", reply_markup=InlineKeyboardMarkup(keyboard))
    return TYPE

async def type_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        return RENT_DURATION
    else:
        context.user_data["commission_rate"] = DEFAULT_SALE_COMMISSION
        await query.edit_message_text(f"النوع: <b>{choice}</b>\n\nأدخل **السعر الإجمالي للبيع** (أرقام فقط):", parse_mode="HTML")
        return PRICE

async def rent_duration_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    duration_key = query.data

    rates_map = {
        "rent_yearly": (8.33, "سنوي (أجار شهر كامل)"),
        "rent_monthly": (33.0, "شهري (+33%)"),
        "rent_two_weeks": (45.0, "أسبوعين (+45%)"),
        "rent_weekly": (85.0, "أسبوعي (+85%)"),
        "rent_daily": (45.0, "يومي (+45%)")
    }

    rate, label = rates_map.get(duration_key, (8.33, "إيجار"))
    context.user_data["commission_rate"] = rate
    context.user_data["rent_label"] = label
    
    await query.edit_message_text(
        f"مدة الإيجار المحددة: <b>{label}</b>\n\nأدخل **مبلغ الإيجار/حق المالك** (أرقام فقط):", 
        parse_mode="HTML"
    )
    return PRICE

async def set_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(",", "")
    try:
        price = float(text)
        comm_rate = context.user_data.get("commission_rate", DEFAULT_SALE_COMMISSION)
        comm_amount = (price * comm_rate) / 100

        context.user_data["price"] = price
        context.user_data["commission_amount"] = comm_amount

        prop_type = context.user_data.get("type")
        rent_label = context.user_data.get("rent_label", "")

        info_msg = f"💰 السعر/حق المالك: <b>{price:,.2f}</b>\n"
        if prop_type == "للإيجار":
            info_msg += f"📌 مدة العقد: <b>{rent_label}</b>\n"
        
        info_msg += f"⚡ صافي العمولة/الزيادة ({comm_rate}%): <b>{comm_amount:,.2f}</b>\n\nأدخل **اسم المالك**:"

        await update.message.reply_text(info_msg, parse_mode="HTML")
        return OWNER_NAME
    except ValueError:
        await update.message.reply_text("❌ يرجى إدخال أرقام فقط للسعر:")
        return PRICE

async def set_owner_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["owner_name"] = update.message.text.strip()
    await update.message.reply_text("أدخل **رقم هاتف المالك**:")
    return OWNER_PHONE

async def set_owner_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["owner_phone"] = update.message.text.strip()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    full_type = context.user_data["type"]
    if context.user_data.get("rent_label"):
        full_type += f" ({context.user_data['rent_label']})"

    conn = sqlite3.connect("real_estate.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO properties (type, price, commission_rate, commission_amount, owner_name, owner_phone, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        full_type, context.user_data["price"],
        context.user_data["commission_rate"], context.user_data["commission_amount"],
        context.user_data["owner_name"], context.user_data["owner_phone"], now_str
    ))
    prop_id = cursor.lastrowid
    conn.commit()
    conn.close()

    await update.message.reply_text(f"🎉 <b>تم حفظ العقار #{prop_id} بنجاح!</b>\nجاري إرسال تقرير Excel...", parse_mode="HTML")
    
    excel_file = generate_excel_report()
    await update.message.reply_document(
        document=excel_file,
        filename=f"تحديث_آلي_العمولات_{datetime.now().strftime('%Y%m%d')}.xlsx",
        caption="📊 **تحديث آلي:** تم تحديث سجل Excel بالبيانات الجديدة تلقائياً."
    )
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ تم إلغاء العملية.")
    return ConversationHandler.END

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    conn = sqlite3.connect("real_estate.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM properties")
    total_count = cursor.fetchone()[0]
    cursor.execute("SELECT SUM(commission_amount) FROM properties WHERE status IN ('تم البيع', 'تم الإيجار')")
    earned = cursor.fetchone()[0] or 0.0
    cursor.execute("SELECT SUM(commission_amount) FROM properties WHERE status = 'متاح'")
    expected = cursor.fetchone()[0] or 0.0
    conn.close()

    msg = (
        "<b>📈 لوحة التحليل المالي والعمولات:</b>\n\n"
        f"• إجمالي العقارات: <b>{total_count}</b>\n"
        f"• العمولات المحصلة: <b>{earned:,.2f}</b>\n"
        f"• العمولات المتوقعة: <b>{expected:,.2f}</b>\n"
        f"💰 <b>المجموع الكلي: {earned + expected:,.2f}</b>"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("⚠️ اكتب كلمة البحث بعد الأمر. مثال: <code>/search أحمد</code>", parse_mode="HTML")
        return
    
    keyword = f"%{context.args[0]}%"
    conn = sqlite3.connect("real_estate.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, type, price, commission_amount, owner_name, owner_phone, status FROM properties WHERE owner_name LIKE ? OR owner_phone LIKE ?", (keyword, keyword))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("🔍 لم يتم العثور على نتائج.")
        return

    msg = "<b>🔍 نتائج البحث:</b>\n\n"
    for r in rows:
        msg += f"<b>#{r[0]}</b> | {r[1]} | السعر: {r[2]:,.0f} | العمولة: <b>{r[3]:,.0f}</b> | المالك: {r[4]} ({r[5]}) - [{r[6]}]\n"
    await update.message.reply_text(msg, parse_mode="HTML")

async def auto_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    excel_file = generate_excel_report()
    await update.message.reply_document(
        document=excel_file,
        filename="التقرير_المالي_والعمولات.xlsx",
        caption="📊 التقرير المالي الشامل المحدث تلقائياً."
    )

async def export_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("⚠️ حدد رقم العقار. مثال: <code>/pdf 1</code>", parse_mode="HTML")
        return

    try:
        prop_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ رقم العقار غير صحيح.")
        return

    conn = sqlite3.connect("real_estate.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, type, price, status FROM properties WHERE id = ?", (prop_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        await update.message.reply_text("❌ العقار غير موجود.")
        return

    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    p.setFont("Helvetica-Bold", 18)
    p.drawString(180, 720, "REAL ESTATE PROPERTY BROCHURE")
    p.line(50, 700, 550, 700)
    p.setFont("Helvetica", 12)
    p.drawString(50, 650, f"Property ID: #{row[0]}")
    p.drawString(50, 620, f"Offer Type: {row[1]}")
    p.drawString(50, 590, f"Price: {row[2]:,.2f}")
    p.drawString(50, 560, f"Status: {row[3]}")
    p.line(50, 530, 550, 530)
    p.showPage()
    p.save()
    buffer.seek(0)

    await update.message.reply_document(
        document=buffer,
        filename=f"Brochure_Property_{prop_id}.pdf",
        caption=f"📄 بروشور العقار #{prop_id} جاهز بدون بيانات المالك."
    )

async def list_properties(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    conn = sqlite3.connect("real_estate.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, type, price, commission_amount, owner_name, status FROM properties")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 لا يوجد عقارات مسجلة.")
        return

    msg = "<b>📋 قائمة كافة العقارات:</b>\n\n"
    for r in rows:
        msg += f"<b>#{r[0]}</b> | {r[1]} | السعر: {r[2]:,.0f} | العمولة: <b>{r[3]:,.0f}</b> | المالك: {r[4]} | [{r[5]}]\n"
    await update.message.reply_text(msg, parse_mode="HTML")

def main():
    init_db()
    if not BOT_TOKEN or BOT_TOKEN == "ضع_توكن_البوت_هنا":
        print("❌ لم يتم إدخال BOT_TOKEN الصحيح!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            TYPE: [CallbackQueryHandler(type_choice)],
            RENT_DURATION: [CallbackQueryHandler(rent_duration_choice)],
            PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_price)],
            OWNER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_owner_name)],
            OWNER_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_owner_phone)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("search", search))
    app.add_handler(CommandHandler("list", list_properties))
    app.add_handler(CommandHandler("auto_excel", auto_excel))
    app.add_handler(CommandHandler("pdf", export_pdf))

    print("⚡ البوت يعمل الآن بالعمولات الجديدة...")
    app.run_polling()

if __name__ == "__main__":
    main()
