import os
import re
import sqlite3
import logging
import pdfplumber
import http.server
import threading
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# إعدادات مراقبة الأخطاء
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- كود وهمي لفتح منفذ ويب لكي يقبله موقع Render مجاناً 100% ---
def run_dummy_server():
    try:
        port = int(os.getenv("PORT", 10000))
        server_address = ('', port)
        httpd = http.server.HTTPServer(server_address, http.server.SimpleHTTPRequestHandler)
        logger.info(f"Dummy web server started on port {port}")
        httpd.serve_forever()
    except Exception as e:
        logger.error(f"Error in dummy server: {e}")

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- ضع معرفاتك هنا لتشغيل البوت ---
TOKEN = 8871114751:AAGXJr2BY0kQ1JjO1baTSqhdJnt6Eh3_cIM
CHANNEL_ID = -1006633422540

# --- إنشاء وإعداد قاعدة البيانات الذكية ---
def init_db():
    conn = sqlite3.connect('grades.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS students_grades (
            student_id TEXT,
            student_name TEXT,
            subject_name TEXT,
            practical_grade TEXT DEFAULT 'غير متوفر',
            theoretical_grade TEXT DEFAULT 'غير متوفر',
            final_grade TEXT DEFAULT 'غير متوفر',
            PRIMARY KEY (student_id, subject_name)
        )
    ''')
    conn.commit()
    conn.close()

# --- فحص محتوى الـ PDF واستخراج البيانات ---
def process_pdf(pdf_path):
    students_data = []
    is_theoretical = False
    subject_name = "مادة عامة"
    
    try:
        with pdfplumber.open(pdf_path) as pdf:
            first_page_text = ""
            if pdf.pages:
                first_page_text = pdf.pages[0].extract_text() or ""
            
            if "نظري" in first_page_text or "نهائي" in first_page_text or "مجموع" in first_page_text:
                is_theoretical = True
                
            for line in first_page_text.split('\n'):
                if "مادة" in line or "المادة" in line:
                    subject_name = line.strip()
                    break

            for page in pdf.pages:
                table = page.extract_table()
                if table:
                    for row in table:
                        if not row or len(row) < 3:
                            continue
                        row = [str(cell).strip() for cell in row if cell is not None]
                        if row and re.search(r'\d+', row[0]):
                            stu_id = row[0]
                            stu_name = row[1]
                            if is_theoretical and len(row) >= 4:
                                theoretical = row[2]
                                final = row[3]
                                students_data.append((stu_id, stu_name, subject_name, "theoretical", theoretical, final))
                            else:
                                grade = row[2]
                                students_data.append((stu_id, stu_name, subject_name, "practical", grade, None))
    except Exception as e:
        logger.error(f"خطأ أثناء قراءة الـ PDF: {e}")
    return students_data, is_theoretical

# --- حفظ وتحديث البيانات ذكياً في قاعدة البيانات ---
def save_grades(students_data, is_theoretical):
    if not students_data:
        return 0
    conn = sqlite3.connect('grades.db')
    cursor = conn.cursor()
    count = 0
    for stu_id, stu_name, subject, data_type, val1, val2 in students_data:
        if data_type == "theoretical":
            cursor.execute('''
                INSERT INTO students_grades (student_id, student_name, subject_name, theoretical_grade, final_grade)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(student_id, subject_name) DO UPDATE SET
                    student_name = excluded.student_name,
                    theoretical_grade = excluded.theoretical_grade,
                    final_grade = excluded.final_grade
            ''', (stu_id, stu_name, subject, val1, val2))
        else:
            cursor.execute('''
                INSERT INTO students_grades (student_id, student_name, subject_name, practical_grade)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(student_id, subject_name) DO UPDATE SET
                    student_name = excluded.student_name,
                    practical_grade = excluded.practical_grade
            ''', (stu_id, stu_name, subject, val1))
        count += 1
    conn.commit()
    conn.close()
    return count

# --- استقبال الملفات من القناة ---
async def handle_channel_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.channel_post.chat_id != CHANNEL_ID:
        return
    if update.channel_post.document and update.channel_post.document.mime_type == 'application/pdf':
        doc = update.channel_post.document
        file_name = doc.file_name.lower()
        ignored = ["قاعة", "قاعات", "امتحان", "امتحانات", "جدول", "اعتراض", "اعتراضات", "مدرج"]
        if any(x in file_name for x in ignored):
            return
        tg_file = await context.bot.get_file(doc.file_id)
        pdf_path = f"temp_{doc.file_name}"
        await tg_file.download_to_drive(pdf_path)
        data, is_theoretical = process_pdf(pdf_path)
        inserted = save_grades(data, is_theoretical)
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
        logger.info(f"✅ تمت معالجة الملف بنجاح وتحديث علامات {inserted} طالب.")

# --- أوامر الطلاب واستعلام الخاص ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلاً بك في نظام استعلام العلامات المطور! 📊\n\n"
        "الرجاء إرسال **رقمك الجامعي** أو **اسمك الثلاثي** لمعرفة علاماتك (العملي والنظري) فوراً."
    )

async def handle_student_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    conn = sqlite3.connect('grades.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT subject_name, student_name, practical_grade, theoretical_grade, final_grade 
        FROM students_grades 
        WHERE student_id = ? OR student_name LIKE ?
    ''', (user_input, f"%{user_input}%"))
    results = cursor.fetchall()
    conn.close()
    if results:
        response = f"📊 **كشف علامات الطالب:**\n"
        response += "───────────────────\n"
        for row in results:
            subject, name, practical, theoretical, final = row
            response += (
                f"📚 **المادة:** {subject}\n"
                f"🛠️ **علامة العملي:** {practical}\n"
                f"📝 **علامة النظري:** {theoretical}\n"
                f"🎯 **المجموع النهائي:** {final}\n"
                f"───────────────────\n"
            )
    else:
        response = "❌ لم يتم العثور على أي نتائج. تأكد من كتابة الرقم الجامعي أو الاسم بشكل صحيح."
    await update.message.reply_text(response, parse_mode="Markdown")

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.Chat(chat_id=CHANNEL_ID) & filters.Document.PDF, handle_channel_pdf))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT, handle_student_query))
    logger.info("البوت يعمل الآن ومستعد لتلقي البيانات...")
    app.run_polling()

if __name__ == '__main__':
    main()
