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
st.write("Сервис нарезает лист подбора по слоям для 100% захвата названий.")

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

# --- ПАРСИНГ ЛИСТА ПОДБОРА ПО ГОРИЗОНТАЛЬНЫМ ЛИНИЯМ ---
def parse_assembly_list(pdf_file):
    data = {}
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            # 1. Находим графические линии, чтобы нарезать страницу на товары
            lines = [line for line in page.lines if line['width'] > 30]
            lines.sort(key=lambda x: x['top'])
            
            y_coords = [0] + [line['top'] for line in lines] + [page.height]
            
            for i in range(len(y_coords) - 1):
                top = y_coords[i]
                bottom = y_coords[i+1]
                
                if bottom - top < 15: 
                    continue
                    
                # 2. Вырезаем горизонтальную полосу (один блок/товар)
                bbox = (0, top, page.width, bottom)
                try:
                    crop = page.within_bbox(bbox)
                except ValueError:
                    continue
                    
                # Читаем текст внутри полосы, сохраняя визуальные пробелы (layout=True)
                text = crop.extract_text(layout=True)
                if not text:
                    continue
                    
                # 3. Ищем ВСЕ заказы внутри этой полосы (универсальный паттерн для любых серий)
                # Ловит старые с дефисами и новые (любые буквы + от 10 до 15 цифр)
                order_pattern = r'(\d{8,15}-\d{4}-\d+|[a-zA-Z]{0,4}\d{10,15})'
                orders_in_slice = re.findall(order_pattern, text)
                
                if not orders_in_slice:
                    continue
                    
                # 4. Очищаем текст от номеров заказов
                text_clean = re.sub(order_pattern, ' ', text)
                
                # 5. УДАЛЯЕМ ПОРЯДКОВЫЕ НОМЕРА в начале строк (отделены | или пробелом)
                text_clean = re.sub(r'(?m)^\s*\d+\s*\|', ' ', text_clean)
                text_clean = re.sub(r'(?m)^\s*\d+\s{3,}', ' ', text_clean)
                text_clean = text_clean.replace('|', ' ')
                
                # 6. Удаляем шапку таблицы (если она попала в срез)
                headers = r'(Склад МСК ООО.*?|Склад:.*?|Служба доставки:.*?|Номер отправления|Номер с этикетки|Количество отправлений|Дата:|Фото|Товар|Артикул|Кол-во|Этикетка|Ozon|Проверьте список.*?отменять их\.|№)'
                text_clean = re.sub(headers, ' ', text_clean, flags=re.IGNORECASE)
                
                # 7. Ищем Артикул и Кол-во строго в конце строк
                art_qty_matches = list(re.finditer(r'\s{2,}([A-Za-z0-9\-_А-Яа-я/.]+)\s+(\d{1,3})(?:\s+\d{4})?\s*$', text_clean, re.MULTILINE))
                
                articles = []
                qtys = []
                name_text = text_clean
                
                # Вырезаем найденные Артикулы и Количество из текста
                for match in reversed(art_qty_matches):
                    articles.append(match.group(1))
                    qtys.append(match.group(2))
                    name_text = name_text[:match.start()] + " " + name_text[match.end():]
                    
                articles.reverse()
                qtys.reverse()
                
                # 8. Всё, что осталось после удаления номеров и артикулов — это чистое Название!
                name = re.sub(r'\s+', ' ', name_text).strip()
                if not name or len(name) < 2:
                    name = "Товар"
                    
                # 9. Раздаем извлеченные данные всем заказам в этой полосе
                for j, order in enumerate(orders_in_slice):
                    art = articles[min(j, len(articles)-1)] if articles else "-"
                    qty = qtys[min(j, len(qtys)-1)] if qtys else "1"
                    
                    item = {"name": name, "article": art, "qty": qty}
                    
                    order_norm = order.lower().replace('і', 'i').replace('І', 'i')
                    
                    # Универсальная привязка по 4 последним цифрам (не зависит от серии 500 или 501)
                    if '-' not in order_norm:
                        code = order_norm[-4:]
                    else:
                        code = order_norm.split('-')[0][-4:]
                        
                    data[code] = item
                    
                    # Резервная привязка по числовому коду
                    num_key = re.sub(r'\D', '', order_norm)
                    data[num_key] = item
                    if len(num_key) >= 10:
                        data[num_key[-10:]] = item
                        
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
                
                clean_text_norm = clean_text.lower().replace('і', 'i').replace('І', 'i')
                # Универсальный поиск: номера с дефисами ИЛИ буквы + от 10 цифр подряд
                order_match = re.search(r'(\d{8,15}-\d{4}-\d+|[a-zA-Z]{0,4}\d{10,15})', clean_text_norm)
                
                w, h = float(page.mediabox.width), float(page.mediabox.height)
                
                if order_match:
                    full_num = order_match.group(1)
                    
                    # Универсальное извлечение последних 4 цифр (без привязки к серии 500)
                    if '-' not in full_num:
                        short_code = full_num[-4:]
                    else:
                        short_code = full_num.split('-')[0][-4:]
                        
                    info = assembly_data.get(short_code)
                            
                    # Страховочный поиск по числовому ключу
                    if not info:
                        num_key = re.sub(r'\D', '', full_num)
                        info = assembly_data.get(num_key)
                        if not info and len(num_key) >= 10:
                            info = assembly_data.get(num_key[-10:])
                            
                    if not info:
                        info = {"name": "Товар не найден", "article": "-", "qty": "?"}
                        
                    display_num = full_num.upper()
                    if display_num.startswith('II'):
                        display_num = 'ii' + display_num[2:]
                        
                    writer.add_page(create_info_label(w, h, display_num, info))
                else:
                    writer.add_page(create_info_label(w, h, "???", {"name": "Номер не распознан", "article": "-", "qty": "-"}))
            
            status.update(label="Готово!", state="complete")
            
        output = BytesIO()
        writer.write(output)
        output.seek(0)
        
        st.download_button("📥 Скачать результат", output, "Ready_Labels.pdf", "application/pdf")
