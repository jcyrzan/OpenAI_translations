from flask import Flask, render_template, request, jsonify, send_file, Response
from datetime import datetime
import csv
import openai
import os
import oracledb
import time
import SQL
import configparser
import threading
import requests
import logging

app = Flask(__name__)

UPLOAD_FOLDER = 'uploads'
TRANSLATED_FOLDER = 'translated'


translation_progress = 0
download_path = None
lock = threading.Lock()
db_thread = None
db_online = False
ping_thread = None
api_error = None
selected_key_label = 'api_key_1'
config_lock = threading.Lock()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def used():
    try:
        logger.info(f'selected key label global{selected_key_label}')
        config = read_config()
        times_used = config.get('USE', 'times_used')
        times_used_increase = int(times_used) + 1
        config.set('USE', 'times_used', str(times_used_increase))
        with open('config.ini', 'w') as configfile:
            config.write(configfile)
    except Exception as e:
        logger.error(f"Error in used function: {str(e)}")


def read_config():
    try:
        current_directory = (os.path.dirname(os.path.abspath(__file__)))
        local_file_path = os.path.join(current_directory, "config.ini")
        config = configparser.ConfigParser()
        config_path = local_file_path
        with config_lock:
            config.read(config_path)
        return config
    except Exception as e:
        logger.error(f"Error reading config: {str(e)}")
        return None


config = read_config()
username = config.get('SQL', 'db_username')
password = config.get('SQL', 'db_password')
ip = config.get('SQL', 'db_ip')
sid = config.get('SQL', 'db_sid')
selected_key_final = config.get('API', 'api_key_1')

# zmiany do wywalenia jak nie dziala

connection = None

try:
    connection = oracledb.connect(
        user=username,
        password=password,
        dsn=ip + "/" + sid)
    logger.info("Database connection established")
except oracledb.DatabaseError as e:
    logger.error(f"Database connection error: {str(e)}")


def update_config(selected_key2):
    try:
        global selected_key_label, selected_key_final
        selected_key_label = selected_key2
        logger.info(f'selected key label global{selected_key_label}')
        config = read_config()
        config.set('API', 'selected_key', selected_key_label)
        with open('config.ini', 'w') as configfile:
            config.write(configfile)
        update_selected_key_final()
        openai.api_key = selected_key_final
    except Exception as e:
        logger.error(f"Error updating config: {str(e)}")


def update_selected_key_final():
    try:
        global selected_key_final
        config = read_config()
        selected_key_final2 = config.get('API', 'selected_key')
        selected_key_final3 = config.get('API', f'{selected_key_final2}')
        selected_key_final = selected_key_final3
    except Exception as e:
        logger.error(f"Error updating selected key final: {str(e)}")


def execute_sql(connection, querry, par1, par2=None):
    execute_sql_result = None
    try:
        if connection is None:
            raise ConnectionError("Database connection is not established")
        cur = connection.cursor()
        if par2 is not None:
            cur.execute(querry, str=par1, num=par2)
        else:
            cur.execute(querry, str=par1)
        connection.commit()
        result = cur.fetchone()
        execute_sql_result = result[0] if result else None
    except oracledb.Error as error:
        logger.error(f"SQL execution error: {error}")
    except ConnectionError as ce:
        logger.error(f"Connection error: {ce}")
        raise
    finally:
        time.sleep(1)
    return execute_sql_result


def translate_text(text):
    try:
        response = openai.completions.create(
            model="gpt-3.5-turbo-instruct", # tested on gpt-3.5-turbo-instruct
            prompt=f"Translate to English: '{text}'",
            max_tokens=150,
            temperature=0.5,
            n=1
        )
        return response.choices[0].text.strip()
    except Exception as e:
        logger.error(f"Error translating text: {str(e)}")
        return None


def generate_progress():
    global translation_progress
    while True:
        yield f"data: {translation_progress}\n\n"
        if translation_progress == 100:
            yield "data: 100\n\n"
            while translation_progress >= 0:
                yield f"data: {translation_progress}\n\n"
                time.sleep(1)
        time.sleep(1)  # Introduce a sleep interval to slow down progress


def translate_csv(input_file_path, output_folder, translate_column_index):
    global translation_progress, download_path, api_error, selected_key_fetched
    config = read_config()
    selected_key_fetched = config.get('API', 'selected_key')
    translation_progress = 0
    api_error = None
    logger.info("Starting translation process...")
    try:
        with open(input_file_path, 'r', newline='', encoding='utf-8') as csvfile:
            reader = csv.reader(csvfile)
            rows = list(reader)
            total_rows = len(rows) - 1  # Total rows excluding header

        translated_rows = [rows[0]]  # Initialize with the header row
        for i, row in enumerate(rows[1:], start=2):  # Start iterating from the second row
            translated_row = []
            for j, column in enumerate(row):
                if j == translate_column_index:
                    logger.info(f"Translating row {i}, column {j}...")
                    if column.strip():
                        try:
                            exception = str(execute_sql(connection, SQL.translation_en, column))
                        except ConnectionError as ce:
                            logger.error(f"Database connection error during translation: {ce}")
                            translated_row.extend([column, "DB connection failed"])
                            continue

                        exception_check = "".join(c if c.isalnum() or c == '_' or c.isspace() else '' for c in exception)
                        logger.info(exception_check)
                        if exception_check == "None":
                            translated_text = translate_text(column)
                            logger.info(f"Translated text: {translated_text}")
                            translated_row.append(column)
                            translated_text_quotation = translated_text.replace('"', '').replace("'", '')
                            translated_row.append(translated_text_quotation)
                        else:
                            logger.info(f"Exception found: {exception_check}")
                            translated_row.append(column)
                            translated_row.append(exception_check)
                    else:
                        translated_row.extend(["", ""])
                else:
                    translated_row.append(column)
            translated_rows.append(translated_row)
            translation_progress = ((i - 1) / total_rows) * 100  # Update progress based on total rows
            logger.info(translation_progress)
            time.sleep(0.5)  # Introduce a sleep interval to slow down progress

        translation_progress = 100

        output_file = os.path.join(output_folder, os.path.basename(input_file_path))
        with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerows(translated_rows)
        logger.info(f"Translated CSV saved to {output_file}")
        download_path = os.path.basename(input_file_path)
        return output_file
    except openai.RateLimitError as e:
        logger.error(f"OpenAI Rate Limit Error:{e}")
        api_error = e
        return api_error


@app.route('/', methods=['GET'])
def home():
    return render_template('upload_form.html')


@app.route('/translate', methods=['POST'])
def translate():
    global api_error
    api_error = None
    if request.form.get('reset'):
        global translation_progress, download_path
        translation_progress = 0
        download_path = None
        return Response(status=200)
    input_file = request.files.get('input_file')
    translate_column_index = request.form.get('translate_column_index')
    if input_file is None:
        logger.error({'error': 'No file uploaded'})
        return Response(status=400)
    if translate_column_index is None:
        logger.error({'error': 'Translate Column Index is missing'})
        return Response(status=400)
    # Store the uploaded file temporarily
    file_path = os.path.join(UPLOAD_FOLDER, input_file.filename)
    input_file.save(file_path)
    # Start translation in a separate thread
    translation_thread = threading.Thread(target=translate_csv, args=(file_path, TRANSLATED_FOLDER, int(translate_column_index)))
    translation_thread.start()
    translation_thread.join()
    # ... wait for the translation thread to finish ...
    if api_error is not None:
        error_message = str(api_error)
        error_message_stripped = error_message.split('Visit', 1)
        api_error_stripped = error_message_stripped[0].strip(), "Consider change of APIkey"
        return jsonify({'error': str(api_error_stripped)}), 500
    else:
        return Response(status=200)
    # Download the translated file
    # return send_file(result, as_attachment=True)


@app.route('/progress', methods=['GET'])
def progress():
    return Response(generate_progress(), content_type='text/event-stream')


@app.route('/download/<file_path>', methods=['GET'])
def download_file(file_path):
    file_path2 = os.path.join(TRANSLATED_FOLDER, file_path)
    archiving('translated/')
    archiving('uploads/')
    used()
    return send_file(file_path2, as_attachment=True)


@app.route('/get_download_path', methods=['GET'])
def get_download_path():
    global download_path
    return jsonify({'download_path': download_path})


@app.route('/openai-usage', methods=['GET'])
def get_openai_usage():
    date = datetime.today().strftime('%Y-%m-%d')
    update_selected_key_final()
    try:
        headers = {
            'Authorization': f'Bearer {selected_key_final}'
        }
        params = {
            'date': date
        }
        response = requests.get('https://api.openai.com/v1/usage', headers=headers, params=params)
        if response.status_code == 200:
            data = response.json()
            total_requests = sum(item['n_requests'] for item in data['data'])
            logger.info(f"Total requests:{total_requests}")
            return jsonify({'total_requests': total_requests})
        else:
            return jsonify({'error': 'Failed to fetch OpenAI usage', 'status_code': response.status_code})
    except Exception as e:
        logger.error(f"Error fetching OpenAI usage:{str(e)}")
        return jsonify({'error': 'Failed to fetch OpenAI usage'})


@app.route('/select_api_key', methods=['POST'])
def select_api_key():
    selected_key2 = request.form.get('selected_key')
    logger.info(f'selected key2{selected_key2}')
    api_selection_thread = threading.Thread(target=update_config, args=(selected_key2,))
    api_selection_thread.start()
    return Response(status=200)


def ping_db():
    global db_online
    try:
        global connection
        if connection:
            connection.ping()
            db_online = True
        else:
            db_online = False
    except oracledb.DatabaseError:
        db_online = False  # Ping failed, database is offline


def start_ping_thread():
    global ping_thread
    ping_thread = threading.Thread(target=ping_db)
    ping_thread.start()


def archiving(directory):
    global translation_progress, download_path
    file_to_exclude = download_path
    if translation_progress == 100:
        try:
            for filename in os.listdir(directory):
                if filename != file_to_exclude:
                    os.remove(os.path.join(directory, filename))
        except Exception as e:
            logger.error(f"Error during archiving:{str(e)}")


@app.route('/db_status', methods=['GET'])
def get_db_status():
    global db_online # Reference the global db_thread variable
    return jsonify({'db_online': db_online})


@app.errorhandler(Exception)
def handle_exception(e):
    logger.error(f"Unhandled exception: {str(e)}")
    return jsonify({'error': 'An internal error occurred, please try again later.'}), 500


if __name__ == '__main__':
    start_ping_thread()
    app.run(host='0.0.0.0', debug=True, threaded=True)