import logging
import os
import sqlite3
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

# --- الإعدادات الأساسية ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "ضع_توكن_البوت_هنا")
DB_NAME = "alshahbaa_master.db"

# --- 1. تهيئة قاعدة البيانات الآمنة (غير القابلة لللمسح) ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # جدول العقارات بحالة أمان (is_deleted = 0)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS properties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            type TEXT NOT NULL,
            price REAL NOT NULL,
            area TEXT NOT NULL,
            rooms INTEGER NOT NULL,
            owner_name TEXT NOT NULL,
            owner_phone TEXT NOT NULL,
            status TEXT DEFAULT 'متاح',
            is_deleted INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    
    # جدول العملاء بحالة أمان (is_deleted = 0)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            pref_area TEXT NOT NULL,
            max_price REAL NOT NULL,
            pref_rooms INTEGER NOT NULL,
            is_deleted INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

# --- 2. القائمة الرئيسية التفاعلية الأنيقة ---
def get_main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ إضافة عقار سريع", callback_data="menu_add_prop"), InlineKeyboardButton("👥 إضافة عميل سريع", callback_data="menu_add_client")],
        [InlineKeyboardButton("📋 العقارات المتاحة", callback_data="menu_list_props"), InlineKeyboardButton("👨‍💼 قائمة العملاء", callback_data="menu_list_clients")],
        [InlineKeyboardButton("🎯 المطابقة الذكية", callback_data="menu_matches"), InlineKeyboardButton("📦 نسخة احتياطية للبيانات", callback_data="menu_backup")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "<b>🏠 مرحباً بك في نظام الشهباء العقاري الذكي</b>\n\n"
        "النظام يعمل بتقنية **الأرشيف الدائم** لحماية كافة بياناتك من المسح نهائياً.\n"
        "اختر العملية من الأزرار أدناه للبدء السريع:"
    )
    if update.message:
        await update.message.reply_text(msg, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")
    elif update.callback_query:
        await update.callback_query.message.reply_text(msg, reply_markup=get_main_menu_keyboard(), parse_mode="HTML")

# --- 3. الإدخال السريع الذكي برسالة واحدة ---
async def handle_quick_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    
    # طريقة الإدخال السريع للعقار: عقار | بيع/إيجار | السعر | المنطقة | الغرف | اسم المالك | رقم المالك
    if text.startswith("عقار"):
        try:
            parts = [p.strip() for p in text.split("|")]
            if len(parts) < 7:
                await update.message.reply_text(
                    "⚠️ **صيغة إضافة عقار غير مكتملة!**\n"
                    "استخدم الصيغة السريعة التالية:\n"
                    "<code>عقار | للبيع | 50000 | الحمدانية | 3 | أحمد | 0912345678</code>",
                    parse_mode="HTML"
                )
                return

            _, p_type, price, area, rooms, owner, phone = parts
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            title = f"عقار {rooms} غرف في {area}"

            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO properties (title, type, price, area, rooms, owner_name, owner_phone, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (title, p_type, float(price), area, int(rooms), owner, phone, now_str))
            prop_id = cursor.lastrowid
            conn.commit()
            conn.close()

            await update.message.reply_text(f"✅ **تم حفظ العقار #{prop_id} بنجاح وللأبد!**", parse_mode="HTML")
            
            # فحص المطابقة التلقائي والإشعار
            await check_matches_for_prop(update, area, float(price), int(rooms))
        except Exception as e:
            await update.message.reply_text("❌ حدث خطأ في البيانات المكتوبة. يرجى التأكد من كتابة الأرقام بشكل صحيح.")

    # طريقة الإدخال السريع للعميل: عميل | الاسم | الهاتف | المنطقة | السعر الأقصى | الغرف
    elif text.startswith("عميل"):
        try:
            parts = [p.strip() for p in text.split("|")]
            if len(parts) < 6:
                await update.message.reply_text(
                    "⚠️ **صيغة إضافة عميل غير مكتملة!**\n"
                    "استخدم الصيغة السريعة التالية:\n"
                    "<code>عميل | محمد | 0987654321 | الحمدانية | 55000 | 3</code>",
                    parse_mode="HTML"
                )
                return

            _, name, phone, area, max_price, rooms = parts
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO clients (name, phone, pref_area, max_price, pref_rooms, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (name, phone, area, float(max_price), int(rooms), now_str))
            client_id = cursor.lastrowid
            conn.commit()
            conn.close()

            await update.message.reply_text(f"👤 **تم تسجيل ملف العميل #{client_id} بنجاح!**", parse_mode="HTML")
        except Exception as e:
            await update.message.reply_text("❌ حدث خطأ في إدخال بيانات العميل.")

# --- 4. المحرك التلقائي للمطابقة والإشعارات ---
async def check_matches_for_prop(update: Update, area: str, price: float, rooms: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT name, phone FROM clients 
        WHERE is_deleted = 0 AND pref_area LIKE ? AND max_price >= ? AND pref_rooms <= ?
    """, (f"%{area}%", price, rooms))
    matched_clients = cursor.fetchall()
    conn.close()

    if matched_clients:
        alert = "🎯 <b>إشعار مطابقة فورية!</b>\nهذا العقار الجديد يناسب العملاء التاليين:\n\n"
        for c in matched_clients:
            alert += f"• <b>{c[0]}</b> - هاتف: <code>{c[1]}</code>\n"
        await update.message.reply_text(alert, parse_mode="HTML")

# --- 5. استعراض البيانات والنسخ الاحتياطي ---
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "menu_add_prop":
        msg = (
            "<b>➕ إضافة عقار برسالة واحدة:</b>\n\n"
            "انسخ الرسالة التالية وعدل عليها ثم أرسلها للبوت مباشرة:\n\n"
            "<code>عقار | للبيع | 50000 | الحمدانية | 3 | المالك أحمد | 0912345678</code>"
        )
        await query.message.reply_text(msg, parse_mode="HTML")

    elif data == "menu_add_client":
        msg = (
            "<b>👥 إضافة عميل برسالة واحدة:</b>\n\n"
            "انسخ الرسالة التالية وعدل عليها ثم أرسلها للبوت مباشرة:\n\n"
            "<code>عميل | محمود | 0987654321 | الحمدانية | 55000 | 3</code>"
        )
        await query.message.reply_text(msg, parse_mode="HTML")

    elif data == "menu_list_props":
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, title, price, type, owner_phone FROM properties WHERE is_deleted = 0 ORDER BY id DESC")
        props = cursor.fetchall()
        conn.close()

        if not props:
            await query.message.reply_text("📭 لا يوجد عقارات مضافة في السجل.")
            return

        msg = "<b>📋 العقارات المسجلة المحفوظة:</b>\n\n"
        for p in props:
            msg += f"<b>#{p[0]}</b> - {p[1]} ({p[3]})\n💰 ${p[2]:,.0f} | 📱 {p[4]}\n------------------\n"
        await query.message.reply_text(msg, parse_mode="HTML")

    elif data == "menu_list_clients":
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, phone, pref_area, max_price FROM clients WHERE is_deleted = 0 ORDER BY id DESC")
        clients = cursor.fetchall()
        conn.close()

        if not clients:
            await query.message.reply_text("👤 لا يوجد عملاء في السجل حالياً.")
            return

        msg = "<b>👥 قائمة العملاء:</b>\n\n"
        for c in clients:
            msg += f"<b>#{c[0]}</b> {c[1]} (<code>{c[2]}</code>)\n📍 {c[3]} | حتى ${c[4]:,.0f}\n------------------\n"
        await query.message.reply_text(msg, parse_mode="HTML")

    elif data == "menu_backup":
        if os.path.exists(DB_NAME):
            await query.message.reply_document(
                document=open(DB_NAME, "rb"),
                caption=f"📦 **نسخة احتياطية شاملة للبيانات**\nتاريخ الصدور: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                parse_mode="Markdown"
            )
        else:
            await query.message.reply_text("❌ لم يتم العثور على ملف قاعدة البيانات.")

# أمر النسخ الاحتياطي اليدوي عبر /backup
async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if os.path.exists(DB_NAME):
        await update.message.reply_document(
            document=open(DB_NAME, "rb"),
            caption=f"📦 **نسخة احتياطية شاملة للبيانات**\nتاريخ: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            parse_mode="Markdown"
        )

# --- التشغيل الرئيسي ---
def main():
    init_db()
    if not BOT_TOKEN or BOT_TOKEN == "ضع_توكن_البوت_هنا":
        print("❌ يرجى كتابة التوكن الصحيح!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_quick_input))

    print("⚡ البوت السريع الذكي يعمل بأمان تام...")
    app.run_polling()

if __name__ == "__main__":
    main()
