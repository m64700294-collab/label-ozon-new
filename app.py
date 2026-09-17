import streamlit as st
import re
import os
from io import BytesIO
from pypdf import PdfReader, PdfWriter
import pdfplumber
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
import requests

# --- НАСТРОЙКИ СТРАНИЦЫ ---
st.set_page_config(page_title="Умная склейка этикеток Ozon", page_icon="🖨️", layout="wide")

st.title("🖨️ Склейка: Этикетки + Лист подбора")
st.write("Сервис автоматически подбирает размер текста, чтобы всё влезло на этикетку.")

# --- ЗАГРУЗКА ШРИФТА ---
@st.cache_resource
def load_font():
    font_path = "Roboto_Full_Final.ttf" 
    if not os.path.exists(font_path):
        url = "https://cdnjs.cloudflare.com/ajax/libs/pdfmake/0.1.66/fonts/Roboto/Roboto-Regular.ttf"
        r = requests.get(url)
        with open(font_path, 'wb') as f:
            f.write(r.content)
    pdfmetrics.registerFont(TTFont('OzonFont', font_path))
    return 'OzonFont'

font_name = load_font()

# --- ПАРСИНГ ЛИСТА ПОДБОРА ---
def parse_assembly_list(pdf_file):
    data = {}
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            # 1. Попытка умного извлечения таблицы (даже без видимых рамок)
            table = page.extract_table({
                "vertical_strategy": "text",
                "horizontal_strategy": "text"
            })
            
            rows_to_process = []
            
            if table:
                # Если таблица нашлась, преобразуем её в текстовые строки
                for row in table:
                    row_clean = [str(cell).strip().replace('\n', ' ') if cell else "" for cell in row]
                    rows_to_process.append(" ".join(row_clean))
            else:
                # 2. Надежный запасной вариант: читаем текст с сохранением отступов
                text = page.extract_text(layout=True)
                if text:
                    rows_to_process = text.split('\n')
            
            # Обрабатываем каждую строку
            for line in rows_to_process:
                # Нормализация (Ozon иногда подсовывает кириллическую 'і')
                line_norm = line.lower().replace('і', 'i').replace('І', 'i')
                
                # Ищем номер заказа
                match = re.search(r'(\d{8,15}-\d{4}-\d+|ii\d{9,15})', line_norm)
                if match:
                    order_num = match.group(1)
                    
                    # Очищаем строку от множественных пробелов для удобного поиска
                    clean_line = re.sub(r'\s+', ' ', line).strip()
                    
                    # Пытаемся вытащить Артикул и Количество с конца строки
                    # Формат Ozon обычно: ... [Артикул] [Кол-во] [4 цифры этикетки]
                    info_match = re.search(r'(.*?)\s+(\S+)\s+(\d+)\s+\d{4}$', clean_line)
                    
                    # Если в конце нет 4 цифр этикетки, пробуем без них
                    if not info_match:
                        info_match = re.search(r'(.*?)\s+(\S+)\s+(\d+)$', clean_line)
                        
                    if info_match:
                        raw_title = info_match.group(1)
                        # Убираем сам номер заказа из названия (чтобы не дублировался)
                        name_clean = re.split(order_num, raw_title, flags=re.IGNORECASE)
                        name = name_clean[-1].strip() if len(name_clean) > 1 else raw_title.strip()
                        
                        article = info_match.group(2)
                        qty = info_match.group(3)
                    else:
                        # Заглушка, если строка обрезалась
                        name = "Товар (название на другой строке)"
                        article = "?"
                        qty = "1"
                        
                    # Сохраняем в словарь
                    data[order_num] = {
                        "name": name if name else "Товар",
                        "article": article,
                        "qty": qty
                    }
    return data

def create_info_label(width, height, order_number, product_info):
    packet = BytesIO()
    c = canvas.Canvas(packet, pagesize=(width, height))
    
    x_margin = 10
    c.setFont(font_name, 10)
    c.drawString(x_margin, height - 18, f"Заказ: {order_number}")
    c.line(x_margin, height - 20, width - x_margin, height - 20)
    
    c.setFont(font_name, 12)
    article = product_info.get('article', '-')
    if len(article) > 25: article = article[:22] + "..."
    c.drawString(x_margin, height - 35, f"Арт: {article}")
    
    name = product_info.get('name', 'Товар не найден')
    top_limit = height - 52   
    bottom_limit = 50        
    available_h = top_limit - bottom_limit
    
    current_size = 10
    line_h = 12
    
    def get_lines(txt, chars):
        words = txt.split()
        res, cur = [], ""
        for w in words:
            if len(cur) + len(w) < chars: cur += w + " "
            else:
                res.append(cur.strip())
                cur = w + " "
        res.append(cur.strip())
        return res

    lines = get_lines(name, 32)
    while (len(lines) * line_h) > available_h and current_size > 6:
        current_size -= 0.5
        line_h -= 0.6
        lines = get_lines(name, int(32 * (10/current_size)))

    c.setFont(font_name, current_size)
    y_text = top_limit
    for line in lines:
        if y_text > bottom_limit:
            c.drawString(x_margin, y_text, line)
            y_text -= line_h
            
    c.setFont(font_name, 24)
    qty = product_info.get('qty', '?')
    c.drawString(x_margin, 15, f"КОЛ-ВО: {qty}")
    
    c.save()
    packet.seek(0)
    return PdfReader(packet).pages[0]

# --- ИНТЕРФЕЙС ---
col1, col2 = st.columns(2)
with col1:
    labels_file = st.file_uploader("1️⃣ Этикетки (PDF)", type="pdf")
with col2:
    assembly_file = st.file_uploader("2️⃣ Лист подбора отправлений (PDF)", type="pdf")

if labels_file and assembly_file:
    if st.button("🚀 Склеить файлы", type="primary", use_container_width=True):
        with st.status("Склеиваем...") as status:
            assembly_data = parse_assembly_list(assembly_file)
            reader = PdfReader(labels_file)
            writer = PdfWriter()
            
            for i in range(len(reader.pages)):
                page = reader.pages[i]
                writer.add_page(page)
                
                text = page.extract_text()
                text_no_underscores = re.sub(r'_\d+', '', text)
                clean_text = re.sub(r'\s+', '', text_no_underscores)
                
                # Нормализуем для поиска
                clean_text_norm = clean_text.lower().replace('і', 'i').replace('І', 'i')
                
                order_match = re.search(r'(\d{8,15}-\d{4}-\d+|ii\d{9,15})', clean_text_norm)
                
                w, h = float(page.mediabox.width), float(page.mediabox.height)
                
                if order_match:
                    order_num = order_match.group(1)
                    info = assembly_data.get(order_num, {"name": "Не найдено", "article": "-", "qty": "?"})
                    
                    # Оставляем оригинальный порядок символов, без перевода в заглавные
                    writer.add_page(create_info_label(w, h, order_num, info))
                else:
                    writer.add_page(create_info_label(w, h, "???", {"name": "Номер не распознан", "article": "-", "qty": "-"}))
            
            status.update(label="Готово!", state="complete")
            
        output = BytesIO()
        writer.write(output)
        output.seek(0)
        
        st.download_button("📥 Скачать результат", output, "Ready_Labels.pdf", "application/pdf")
