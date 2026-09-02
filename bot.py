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

# إعداد تسجل الأخطاء
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# معرف الآدمن (حسابك الشخصي) لمنع أي شخص آخر من استخدام البوت
ADMIN_ID = os.environ.get("ADMIN_ID")  # يتم ضبطه في المتغيرات البيئية

# حالات محادثة إضافة العقار
TYPE, PRICE, COMMISSION_RATE, OWNER_NAME, OWNER_PHONE = range(5)

# --- إعداد قاعدة البيانات ---
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            remind_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

# --- دالة التحقق من الأمان ---
def is_admin(user_id: int) -> bool:
    if not ADMIN_ID:
        return True # في حال عدم ضبط الحساب يتيح الوصول للجميع للتجربة
    return str(user_id) == str(ADMIN_ID)

# --- توليد ملف Excel آلياً ---
def generate_excel_report():
    conn = sqlite3.connect("real_estate.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, type, price, commission_rate, commission_amount, owner_name, owner_phone, status, created_at FROM properties")
    rows = cursor.fetchall()
    
    # حساب الإحصائيات التلقائية
    cursor.execute("SELECT SUM(commission_amount) FROM properties WHERE status IN ('تم البيع', 'تم الإيجار')")
    earned = cursor.fetchone()[0] or 0.0
    cursor.execute("SELECT SUM(commission_amount) FROM properties WHERE status = 'متاح'")
    expected = cursor.fetchone()[0] or 0.0
    conn.close()

    wb = openpyxl.Workbook()
    
    # ورقة الصفقات الشاملة
    ws1 = wb.active
    ws1.title = "سجل الصفقات والعمولات"
    headers = ["رقم العقار", "النوع", "السعر", "النسبة %", "قيمة العمولة", "اسم المالك", "رقم الهاتف", "الحالة", "تاريخ الإضافة"]
    ws1.append(headers)
    for row in rows:
        ws1.append(list(row))

    # ورقة الملخص المالي الآلي
    ws2 = wb.create_sheet(title="الملخص المالي")
    ws2.append(["البيان", "المبلغ الكلي"])
    ws2.append(["إجمالي العمولات المحصلة (تم البيع/الإيجار)", earned])
    ws2.append(["إجمالي العمولات المتوقعة (العقارات المتاحة)", expected])
    ws2.append(["مجموع العمولات الكلي", earned + expected])

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output

# --- الأوامر الأساسية ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🔒 هذا البوت خاص ومغلق بصلاحيات الآدمن فقط.")
        return

    welcome_text = (
        "<b>🧠 أهلاً بك في مساعدك العقاري الذكي والخاص</b>\n\n"
        "<b>قائمة الأوامر المتاحة:</b>\n"
        "➕ /add - إضافة عقار وحساب عمولته وحفظه\n"
        "📈 /stats - لوحة الأرباح والعمولات المالية\n"
        "📋 /list - عرض كافة العقارات المسجلة\n"
        "🔍 /search [كلمة] - بحث باسم المالك أو رقمه\n"
        "🔄 /status [ID] - تغيير حالة العقار (متاح/مباع...)\n"
        "📊 /auto_excel - استخراج التقرير المالي تلقائياً (Excel)\n"
        "📄 /pdf [ID] - إنشاء بروشور وتسويق عقار (PDF للعميل)\n"
        "⏰ /remind [التكست] - إضافة تذكير لمتابعة\n"
        "❌ /cancel - إلغاء العملية الحالية"
    )
    await update.message.reply_text(welcome_text, parse_mode="HTML")

# --- إضافة عقار جديد ---
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    keyboard = [[InlineKeyboardButton("🏠 للبيع", callback_data="للبيع"), InlineKeyboardButton("🔑 للإيجار", callback_data="للإيجار")]]
    await update.message.reply_text("اختر نوع العرض:", reply_markup=InlineKeyboardMarkup(keyboard))
    return TYPE

async def type_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["type"] = query.data
    await query.edit_message_text(f"النوع: <b>{query.data}</b>\n\nأدخل **السعر الكلي** (أرقام فقط):", parse_mode="HTML")
    return PRICE

async def set_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["price"] = float(update.message.text.strip())
        await update.message.reply_text("أدخل **نسبة العمولة %** (مثال: 2.5):")
        return COMMISSION_RATE
    except ValueError:
        await update.message.reply_text("❌ يرجى إدخال أرقام فقط للسعر:")
        return PRICE

async def set_commission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        rate = float(update.message.text.strip())
        context.user_data["commission_rate"] = rate
        price = context.user_data["price"]
        comm_amount = (price * rate) / 100
        context.user_data["commission_amount"] = comm_amount

        await update.message.reply_text(f"✅ صافي العمولة: <b>{comm_amount:,.2f}</b>\n\nأدخل **اسم المالك** (سرّي):", parse_mode="HTML")
        return OWNER_NAME
    except ValueError:
        await update.message.reply_text("❌ يرجى إدخال نسبة صحيحة:")
        return COMMISSION_RATE

async def set_owner_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["owner_name"] = update.message.text.strip()
    await update.message.reply_text("أدخل **رقم هاتف المالك** (سرّي):")
    return OWNER_PHONE

async def set_owner_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["owner_phone"] = update.message.text.strip()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    conn = sqlite3.connect("real_estate.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO properties (type, price, commission_rate, commission_amount, owner_name, owner_phone, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        context.user_data["type"], context.user_data["price"],
        context.user_data["commission_rate"], context.user_data["commission_amount"],
        context.user_data["owner_name"], context.user_data["owner_phone"], now_str
    ))
    prop_id = cursor.lastrowid
    conn.commit()
    conn.close()

    await update.message.reply_text(f"🎉 <b>تم حفظ العقار #{prop_id} بنجاح!</b>\nجاري توليد ملف Excel وتحديث التقرير المالي...", parse_mode="HTML")
    
    # إرسال أوتوماتيكي لملف Excel فور الحفظ!
    excel_file = generate_excel_report()
    await update.message.reply_document(
        document=excel_file,
        filename=f"تحديث_آلي_العمولات_{datetime.now().strftime('%Y%m%d')}.xlsx",
        caption="📊 **تحديث آلي:** تم تحديث سجل Excel بالبيانات والعمولة الجديدة تلقائياً."
    )
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ تم إلغاء العملية.")
    return ConversationHandler.END

# --- لوحة الإحصائيات والأرباح المالية ---
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
        f"• إجمالي العقارات المسجلة: <b>{total_count}</b>\n"
        f"• العمولات المحصلة الفعلية: <b>{earned:,.2f}</b>\n"
        f"• العمولات المتوقعة (المتاحة): <b>{expected:,.2f}</b>\n"
        f"💰 <b>مجموع الأرباح الكلي المتوقع: {earned + expected:,.2f}</b>"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

# --- البحث الفوري ---
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
        await update.message.reply_text("🔍 لم يتم العثور على أي نتائج مطابقة.")
        return

    msg = "<b>🔍 نتائج البحث:</b>\n\n"
    for r in rows:
        msg += f"<b>#{r[0]}</b> | {r[1]} | السعر: {r[2]:,.0f} | العمولة: <b>{r[3]:,.0f}</b> | المالك: {r[4]} ({r[5]}) - [{r[6]}]\n"
    await update.message.reply_text(msg, parse_mode="HTML")

# --- طلب ملف Excel المالي يدوياً ---
async def auto_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    excel_file = generate_excel_report()
    await update.message.reply_document(
        document=excel_file,
        filename="التقرير_المالي_والعمولات.xlsx",
        caption="📊 تفضل، التقرير المالي الشامل المحدث تلقائياً."
    )

# --- تصدير بروشور PDF للعملاء ---
async def export_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("⚠️ حدد رقم العقار. مثال: <code>/pdf 1</code>", parse_mode="HTML")
        return

    try:
        prop_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ رقم العقار يجب أن يكون رقماً.")
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
    p.setFont("Helvetica-Oblique", 10)
    p.drawString(50, 500, "For inquiries, viewing, and bookings, please contact your trusted agent.")
    p.showPage()
    p.save()
    buffer.seek(0)

    await update.message.reply_document(
        document=buffer,
        filename=f"Brochure_Property_{prop_id}.pdf",
        caption=f"📄 بروشور العقار #{prop_id} جاهز بدون إظهار بيانات المالك أو عمولتك."
    )

# --- عرض كافة العقارات ---
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

# --- التشغيل ---
def main():
    init_db()
    bot_token = os.environ.get("BOT_TOKEN")
    if not bot_token:
        print("❌ لم يتم العثور على BOT_TOKEN!")
        return

    app = Application.builder().token(bot_token).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            TYPE: [CallbackQueryHandler(type_choice)],
            PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_price)],
            COMMISSION_RATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_commission)],
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

    print("🚀 المساعد العقاري الذكي يعمل الآن...")
    app.run_polling()

if __name__ == "__main__":
    main()
