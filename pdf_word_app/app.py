from flask import Flask, render_template, request, redirect, url_for, flash
import os
from werkzeug.utils import secure_filename
import sqlite3
import pdfplumber
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextContainer, LTTextLineHorizontal, LTChar
from docx import Document
from docx.shared import Pt, Inches
import io
from PIL import Image

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Change this to a random secret key
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'pdf', 'docx'}
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


# Function to check if the uploaded file is valid
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']


# Database setup
def create_db():
    conn = sqlite3.connect('document_data.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS TextContent (
        id INTEGER PRIMARY KEY,
        content TEXT
    )''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS TableContent (
        id INTEGER PRIMARY KEY,
        table_data TEXT
    )''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS ImageContent (
        id INTEGER PRIMARY KEY,
        image BLOB
    )''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS FormattingContent (
        id INTEGER PRIMARY KEY,
        text TEXT,
        font_size TEXT,
        font_style TEXT
    )''')
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS VersionControl (
        id INTEGER PRIMARY KEY,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        document TEXT
    )''')
    conn.commit()
    return conn


# Function to store content in the database
def store_pdf_content_in_db(conn, content):
    cursor = conn.cursor()
    cursor.execute("INSERT INTO TextContent (content) VALUES (?)", (content["text"],))
    for table in content["tables"]:
        cursor.execute("INSERT INTO TableContent (table_data) VALUES (?)", (str(table),))
    for img in content["images"]:
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='PNG')
        img_data = img_byte_arr.getvalue()
        cursor.execute("INSERT INTO ImageContent (image) VALUES (?)", (img_data,))
    for formatting in content["formatting"]:
        cursor.execute("INSERT INTO FormattingContent (text, font_size, font_style) VALUES (?, ?, ?)",
                       (formatting["text"], str(formatting["font_sizes"]), str(formatting["font_styles"])))
    conn.commit()


# Function to extract content from the PDF
def extract_pdf_content_and_formatting(pdf_file_path):
    content = {
        "text": "",
        "tables": [],
        "images": [],
        "formatting": []
    }
    with pdfplumber.open(pdf_file_path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            if tables:
                content["tables"].extend(tables)
            table_words = []
            for table in tables:
                for row in table:
                    for cell in row:
                        if isinstance(cell, str):
                            table_words.append(cell.strip())
            page_text = ""
            words = page.extract_words()
            for word in words:
                if word["text"] not in table_words:
                    page_text += word["text"] + " "
            content["text"] += page_text + "\n"
            for img in page.images:
                x0, top, x1, bottom = img['x0'], img['top'], img['x1'], img['bottom']
                image_data = page.to_image()
                pil_image = image_data.original
                cropped_img = pil_image.crop((x0, top, x1, bottom))
                content["images"].append(cropped_img)
    for page_layout in extract_pages(pdf_file_path):
        for element in page_layout:
            if isinstance(element, LTTextContainer):
                for text_line in element:
                    if isinstance(text_line, LTTextLineHorizontal):
                        line_text = "".join([char.get_text() for char in text_line if isinstance(char, LTChar)])
                        font_sizes = [char.size for char in text_line if isinstance(char, LTChar)]
                        font_styles = [char.fontname for char in text_line if isinstance(char, LTChar)]
                        content["formatting"].append({
                            "text": line_text,
                            "font_sizes": font_sizes,
                            "font_styles": font_styles
                        })
    return content


# Function to generate Word document from the extracted content
def generate_word_document(pdf_content, output_file):
    doc = Document()
    for formatted_line in pdf_content["formatting"]:
        text = formatted_line["text"]
        font_sizes = formatted_line["font_sizes"]
        font_styles = formatted_line["font_styles"]
        p = doc.add_paragraph()
        for idx, char in enumerate(text):
            run = p.add_run(char)
            if len(font_sizes) > idx and font_sizes[idx]:
                run.font.size = Pt(font_sizes[idx])
            if len(font_styles) > idx and "Bold" in font_styles[idx]:
                run.bold = True
            if len(font_styles) > idx and "Italic" in font_styles[idx]:
                run.italic = True
    for table in pdf_content["tables"]:
        word_table = doc.add_table(rows=len(table), cols=len(table[0]))
        for row_idx, row in enumerate(table):
            for col_idx, cell_text in enumerate(row):
                word_table.cell(row_idx, col_idx).text = cell_text if cell_text else ""
    for img in pdf_content["images"]:
        img_path = "temp_image.png"
        img.save(img_path)
        doc.add_picture(img_path, width=Inches(3))
    doc.save(output_file)
    print(f"Word document saved to {output_file}")


# Initialize the database
conn = create_db()


@app.route('/', methods=['GET', 'POST'])
def index():
    uploaded_file = None
    message = None
    versions = []

    if request.method == 'POST':
        if 'pdfFile' in request.files:
            file = request.files['pdfFile']
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(file_path)
                content = extract_pdf_content_and_formatting(file_path)
                store_pdf_content_in_db(conn, content)
                uploaded_file = filename
                flash('PDF uploaded and scraped successfully.')
                return redirect(url_for('index'))

        if 'docxFile' in request.files:
            file = request.files['docxFile']
            if file and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(file_path)
                message = f'{filename} uploaded and saved successfully.'
                # Here you can implement saving the document in version control
                cursor = conn.cursor()
                cursor.execute("INSERT INTO VersionControl (document) VALUES (?)", (filename,))
                conn.commit()
                versions = cursor.execute("SELECT * FROM VersionControl").fetchall()
                return redirect(url_for('index'))

    # Fetch versions from the database
    cursor = conn.cursor()
    versions = cursor.execute("SELECT * FROM VersionControl").fetchall()

    return render_template('upload.html', uploaded_file=uploaded_file, message=message, versions=versions)


from flask import send_file


@app.route('/generate_docx', methods=['POST'])
def generate_docx():
    cursor = conn.cursor()
    last_pdf_content = cursor.execute("SELECT content FROM TextContent ORDER BY id DESC LIMIT 1").fetchone()

    if last_pdf_content:
        pdf_content = {
            "text": last_pdf_content[0],
            "tables": [],
            "images": [],
            "formatting": []  # Populate this from the database as needed
        }

        if not pdf_content["text"] and not pdf_content["tables"] and not pdf_content["images"]:
            flash('No content extracted from the PDF.')
            return redirect(url_for('index'))

        output_file = "output.docx"
        generate_word_document(pdf_content, output_file)

        # Check if the output file exists and is not empty
        if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
            return send_file(output_file, as_attachment=True)
        else:
            flash('Failed to create the Word document. Please check the PDF content extraction.')
            return redirect(url_for('index'))

    flash('No PDF content found to generate the Word document.')
    return redirect(url_for('index'))



@app.route('/restore_version', methods=['POST'])
def restore_version():
    version_id = request.form['version_id']
    cursor = conn.cursor()
    version_data = cursor.execute("SELECT document FROM VersionControl WHERE id = ?", (version_id,)).fetchone()
    if version_data:
        # Logic to restore the document version can go here
        flash('Version restored successfully.')
    return redirect(url_for('index'))


@app.route('/delete_version', methods=['POST'])
def delete_version():
    version_id = request.form['version_id']
    cursor = conn.cursor()
    cursor.execute("DELETE FROM VersionControl WHERE id = ?", (version_id,))
    conn.commit()
    flash('Version deleted successfully.')
    return redirect(url_for('index'))


if __name__ == '__main__':
    app.run(debug=True)
