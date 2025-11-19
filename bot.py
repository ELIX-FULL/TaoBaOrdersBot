import json
import os
import sqlite3
from datetime import datetime

import gspread
import telebot
from dotenv import load_dotenv
from oauth2client.service_account import ServiceAccountCredentials
from telebot import types
from telebot.types import ReplyKeyboardRemove

# ---------------- ENV ----------------
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS")
GROUP_ID = int(os.getenv("GROUP_ID"))

ADMIN_IDS = ['без кавычек с запятыми список ']

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

# ---------------- Translations ----------------
with open('translations.json', 'r', encoding='utf-8') as f:
    LANG_TEXT = json.load(f)


def get_text(key, lang_code='ru'):
    """Получает текст из JSON по ключу и языку."""
    return LANG_TEXT.get(key, {}).get(lang_code, f"NO_TRANSLATION_FOR_{key}")


# ---------------- DB ----------------
conn = sqlite3.connect("new_orders.db", check_same_thread=False)
cursor = conn.cursor()

# Таблица пользователей: добавлено поле языка
cursor.execute("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id INTEGER UNIQUE,
    agreed INTEGER DEFAULT 0,
    language_code TEXT DEFAULT 'ru'
)
""")

# Таблица заказов: без изменений
cursor.execute("""
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    full_name TEXT,
    phone TEXT,
    order_number TEXT,
    order_date TEXT,
    latitude REAL,
    longitude REAL,
    applicant_order_number TEXT UNIQUE,
    location TEXT,
    FOREIGN KEY (user_id) REFERENCES users (id)
)
""")
conn.commit()

# ---------------- Google Sheets ----------------
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, scope)
client = gspread.authorize(creds)
sheet = client.open_by_key(SHEET_ID).sheet1

if len(sheet.get_all_values()) == 0:
    sheet.append_row([
        "Telegram ID", "ФИО", "Телефон",
        "Номер заказа (Taobao)", "Дата заказа",
        "Локация", "Номер заказа заявителя"
    ])

# ---------------- Временные данные ----------------
user_data = {}


def get_or_create_user(tg_id):
    """Возвращает ID, статус согласия и язык. Если пользователя нет, создает его."""
    cursor.execute("SELECT id, agreed, language_code FROM users WHERE tg_id = ?", (tg_id,))
    row = cursor.fetchone()
    if row:
        return row[0], row[1], row[2]  # user_id, agreed_status, lang_code
    else:
        cursor.execute("INSERT INTO users (tg_id, agreed) VALUES (?, ?)", (tg_id, 0))
        conn.commit()
        return cursor.lastrowid, 0, None


def get_location_link(latitude, longitude, lang):
    if not latitude or not longitude:
        return get_text("location_not_specified", lang)
    map_link = f"<a href='https://www.google.com/maps/search/?api=1&query={latitude},{longitude}'>Google</a>"
    map_link2 = f"<a href='https://yandex.com/maps/?ll={longitude},{latitude}&z=15'>Yandex</a>"
    return f"\n{map_link} | {map_link2}"


def main_menu(chat_id, lang):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(get_text("main_menu_order_btn", lang))
    kb.add(get_text("main_menu_my_orders_btn", lang), get_text("main_menu_help_btn", lang))
    kb.add(get_text("main_menu_settings_btn", lang))
    if chat_id in ADMIN_IDS:
        kb.add(get_text("admin_order_info_btn", lang), get_text("admin_stats_btn", lang))
    bot.send_message(chat_id, get_text("main_menu_title", lang), reply_markup=kb)


@bot.message_handler(commands=['start'])
def start(message):
    user_id, agreed, lang = get_or_create_user(message.from_user.id)

    if not lang:
        kb = types.InlineKeyboardMarkup(row_width=1)
        # <<< ИЗМЕНЕНИЕ 1: Меняем callback_data для ПЕРВОНАЧАЛЬНОЙ установки
        kb.add(
            types.InlineKeyboardButton("🇷🇺 Русский", callback_data="initial_lang_ru"),
            types.InlineKeyboardButton("🇬🇧 English", callback_data="initial_lang_en"),
            types.InlineKeyboardButton("🇺🇿 O'zbekcha", callback_data="initial_lang_uz")
        )
        bot.send_message(message.chat.id, get_text('choose_language', 'ru'),
                         reply_markup=kb)
        return

    if agreed:
        main_menu(message.chat.id, lang)
    else:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(get_text("agree_yes_btn", lang), callback_data="agree_yes"))
        markup.add(types.InlineKeyboardButton(get_text("agree_no_btn", lang), callback_data="agree_no"))
        bot.send_message(
            message.chat.id,
            get_text("agreement_prompt", lang),
            reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True
        )


@bot.callback_query_handler(func=lambda call: call.data.startswith("initial_lang_"))
def initial_set_language(call):
    lang_code = call.data.split("_")[-1]
    cursor.execute("UPDATE users SET language_code = ? WHERE tg_id = ?", (lang_code, call.from_user.id))
    conn.commit()
    bot.answer_callback_query(call.id, get_text("language_selected", lang_code))
    bot.delete_message(chat_id=call.message.chat.id, message_id=call.message.message_id)
    # Вызываем start, чтобы продолжить регистрацию (показать соглашение)
    start(call.message)


@bot.callback_query_handler(func=lambda call: call.data.startswith("change_lang_"))
def change_language_from_settings(call):
    lang_code = call.data.split("_")[-1]
    cursor.execute("UPDATE users SET language_code = ? WHERE tg_id = ?", (lang_code, call.from_user.id))
    conn.commit()
    bot.answer_callback_query(call.id, get_text("language_selected", lang_code))
    bot.delete_message(chat_id=call.message.chat.id, message_id=call.message.message_id)
    main_menu(call.message.chat.id, lang_code)


@bot.callback_query_handler(func=lambda call: call.data in ["agree_yes", "agree_no"])
def handle_agreement(call):
    _, _, lang = get_or_create_user(call.from_user.id)
    bot.answer_callback_query(call.id)
    if call.data == "agree_no":
        bot.edit_message_text(
            get_text("agree_no_reply", lang),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id
        )
    else:
        cursor.execute("UPDATE users SET agreed = 1 WHERE tg_id = ?", (call.from_user.id,))
        conn.commit()
        bot.edit_message_text(
            get_text("agree_thanks", lang),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id
        )
        main_menu(call.message.chat.id, lang)


# ---------------- Главное меню ----------------
@bot.message_handler(func=lambda m: True)
def handle_text(message):
    _, _, lang = get_or_create_user(message.from_user.id)

    if message.text == get_text("main_menu_order_btn", lang):
        order_start(message, lang)
    elif message.text == get_text("main_menu_my_orders_btn", lang):
        my_orders(message, lang)
    elif message.text == get_text("admin_stats_btn", lang) and message.chat.id in ADMIN_IDS:
        get_stats(message, lang)
    elif message.text == get_text("admin_order_info_btn", lang) and message.chat.id in ADMIN_IDS:
        get_order_info_start(message, lang)
    elif message.text == get_text("main_menu_help_btn", lang):
        help_message(message, lang)
    elif message.text == get_text("main_menu_settings_btn", lang):
        show_settings(message, lang)


def show_settings(message, lang):
    kb = types.InlineKeyboardMarkup(row_width=1)
    # <<< ИЗМЕНЕНИЕ 4: Меняем callback_data для смены языка
    kb.add(
        types.InlineKeyboardButton("🇷🇺 Русский", callback_data="change_lang_ru"),
        types.InlineKeyboardButton("🇬🇧 English", callback_data="change_lang_en"),
        types.InlineKeyboardButton("🇺🇿 O'zbekcha", callback_data="change_lang_uz"),
        types.InlineKeyboardButton(get_text("back_btn", lang), callback_data="back_to_main")
    )
    bot.send_message(message.chat.id, get_text('settings_menu_title', lang), reply_markup=kb)


@bot.callback_query_handler(func=lambda call: call.data == "back_to_main")
def back_to_main_handler(call):
    bot.answer_callback_query(call.id)
    bot.delete_message(chat_id=call.message.chat.id, message_id=call.message.message_id)


def order_start(message, lang):
    user_data[message.chat.id] = {}
    bot.send_message(message.chat.id, get_text("get_name_prompt", lang))
    bot.register_next_step_handler(message, get_name, lang)


def my_orders(message, lang):
    tg_id = message.from_user.id
    cursor.execute(
        """
        SELECT o.applicant_order_number, o.full_name, o.phone, o.order_number, o.order_date, o.latitude, o.longitude
        FROM orders o JOIN users u ON o.user_id = u.id
        WHERE u.tg_id = ? ORDER BY o.id
        """, (tg_id,)
    )
    orders = cursor.fetchall()
    if not orders:
        bot.send_message(message.chat.id, get_text("no_orders", lang))
        return
    user_data[tg_id] = {'orders': orders, 'current_index': 0}
    send_order_message(message.chat.id, tg_id, lang, new_message=True)


def send_order_message(chat_id, tg_id, lang, new_message=False):
    orders_info = user_data.get(tg_id)
    if not orders_info:
        bot.send_message(chat_id, get_text("order_info_error", lang))
        return

    index = orders_info['current_index']
    order = orders_info['orders'][index]
    total_orders = len(orders_info['orders'])

    location_links = get_location_link(order[5], order[6], lang)
    text = (
        f"📋 {get_text('order_x_of_y', lang).format(index=index + 1, total=total_orders)}\n\n"
        f"{get_text('your_order_number', lang)}: {order[0]}\n"
        f"👤 {get_text('full_name_label', lang)}: {order[1]}\n"
        f"📞 {get_text('phone_label', lang)}: {order[2]}\n"
        f"📦 {get_text('taobao_order_num_label', lang)}: {order[3]}\n"
        f"📅 {get_text('date_label', lang)}: {order[4]}\n"
        f"📍 {get_text('location_label', lang)}: {location_links}"
    )
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(get_text("prev_btn", lang), callback_data="prev"),
        types.InlineKeyboardButton(get_text("next_btn", lang), callback_data="next")
    )
    try:
        if new_message:
            sent_message = bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML",
                                            disable_web_page_preview=True)
            user_data[tg_id]['message_id'] = sent_message.message_id
        else:
            bot.edit_message_text(text, chat_id=chat_id, message_id=orders_info['message_id'],
                                  parse_mode="HTML", disable_web_page_preview=True, reply_markup=kb)
    except Exception as e:
        print(f"Error sending order message: {e}")
        sent_message = bot.send_message(chat_id, text, reply_markup=kb, parse_mode="HTML",
                                        disable_web_page_preview=True)
        user_data[tg_id]['message_id'] = sent_message.message_id


@bot.callback_query_handler(func=lambda call: call.data in ["prev", "next"])
def switch_order(call):
    _, _, lang = get_or_create_user(call.from_user.id)
    orders_info = user_data.get(call.from_user.id)

    if not orders_info:
        bot.answer_callback_query(call.id, get_text("order_info_error", lang), show_alert=True)
        return

    current_index = orders_info['current_index']
    total_orders = len(orders_info['orders'])
    if call.data == "prev":
        orders_info['current_index'] = (current_index - 1 + total_orders) % total_orders
    elif call.data == "next":
        orders_info['current_index'] = (current_index + 1) % total_orders

    bot.answer_callback_query(call.id)
    send_order_message(call.message.chat.id, call.from_user.id, lang)


# ---------------- Admin Functions ----------------
def get_stats(message, lang):
    cursor.execute("SELECT COUNT(id) FROM users")
    count = cursor.fetchone()[0]
    bot.send_message(message.chat.id, get_text("stats_message", lang).format(count=count))


def get_order_info_start(message, lang):
    bot.send_message(message.chat.id, get_text("admin_find_order_prompt", lang))
    bot.register_next_step_handler(message, find_order_by_applicant_number, lang)


def find_order_by_applicant_number(message, lang):
    applicant_order_number = message.text.strip()
    cursor.execute("""
        SELECT o.applicant_order_number, o.full_name, o.phone, o.order_number, o.order_date, o.location, u.tg_id
        FROM orders o JOIN users u ON o.user_id = u.id
        WHERE o.applicant_order_number = ?
    """, (applicant_order_number,))
    order = cursor.fetchone()

    if not order:
        bot.send_message(message.chat.id, get_text("admin_order_not_found", lang).format(number=applicant_order_number))
        return

    text = (
        f"{get_text('admin_order_found_title', lang).format(number=order[0])}\n\n"
        f"{get_text('admin_user_data_label', lang)}\n"
        f"👤 {get_text('full_name_label', lang)}: {order[1]}\n"
        f"📞 {get_text('phone_label', lang)}: {order[2]}\n"
        f"💬 Telegram ID: <code>{order[6]}</code> (<a href='tg://user?id={order[6]}'>{get_text('admin_open_chat_link', lang)}</a>)\n\n"
        f"{get_text('admin_order_data_label', lang)}\n"
        f"📦 {get_text('taobao_order_num_label', lang)}: {order[3]}\n"
        f"📅 {get_text('date_label', lang)}: {order[4]}\n"
        f"📍 {get_text('location_label', lang)}: {order[5]}"
    )
    bot.send_message(message.chat.id, text, disable_web_page_preview=True)


def help_message(message, lang):
    bot.send_message(message.chat.id, get_text("help_text", lang))


# ---------------- ORDERING PROCESS ----------------
def get_name(message, lang):
    full_name = message.text.strip()
    if len(full_name.split()) < 2:
        bot.send_message(message.chat.id, get_text("name_error", lang))
        bot.register_next_step_handler(message, get_name, lang)
        return
    user_data[message.chat.id]['full_name'] = full_name
    bot.send_message(message.chat.id, get_text("get_phone_prompt", lang))
    bot.register_next_step_handler(message, get_phone, lang)


def get_phone(message, lang):
    phone = message.text.strip()
    if not phone.startswith("+"):
        bot.send_message(message.chat.id, get_text("phone_error", lang))
        bot.register_next_step_handler(message, get_phone, lang)
        return
    user_data[message.chat.id]['phone'] = phone
    user_data[message.chat.id]['tg_id'] = message.from_user.id

    text = (f"{get_text('confirm_data_prompt', lang)}\n\n"
            f"👤 {get_text('full_name_label', lang)}: {user_data[message.chat.id]['full_name']}\n"
            f"📞 {get_text('phone_label', lang)}: {user_data[message.chat.id]['phone']}")
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(get_text("confirm_yes_btn", lang), callback_data="confirm_yes"))
    markup.add(types.InlineKeyboardButton(get_text("confirm_no_btn", lang), callback_data="confirm_no"))
    bot.send_message(message.chat.id, text, reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data in ["confirm_yes", "confirm_no"])
def confirm_data(call):
    _, _, lang = get_or_create_user(call.from_user.id)
    bot.answer_callback_query(call.id)
    if call.data == "confirm_no":
        bot.edit_message_text(get_text("restart_prompt", lang),
                              chat_id=call.message.chat.id, message_id=call.message.message_id)
        user_data[call.message.chat.id] = {}
        bot.register_next_step_handler(call.message, get_name, lang)
    else:
        user_id, _, _ = get_or_create_user(call.from_user.id)
        address_text = (
            f"📍 Филиал в Китае: \n\n"
            f"Warehouse 55055, No. B7, No. 101 Zhanqian Road, Liwan District, Guangzhou, Niuyun Hengtong Logistics 13178855505\n\n"
            f"广州市荔湾区站前路101号B7号5505库房牛运亨通物流13178855505\n\n"
            f"{get_text('address_prompt', lang).format(user_id=user_id)}"
        )
        bot.edit_message_text(address_text, chat_id=call.message.chat.id, message_id=call.message.message_id)
        bot.register_next_step_handler(call.message, get_order_number, lang)


def get_order_number(message, lang):
    if message.text is None:
        bot.send_message(message.chat.id, get_text("get_order_number_error", lang))
        bot.register_next_step_handler(message, get_order_number, lang)
        return
    user_data[message.chat.id]['order_number'] = message.text.strip()
    user_data[message.chat.id]['order_date'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(types.KeyboardButton(get_text("location_share_btn", lang), request_location=True))
    bot.send_message(message.chat.id, get_text("get_location_prompt", lang), reply_markup=markup)


@bot.message_handler(content_types=['location'])
def get_location(message):
    _, _, lang = get_or_create_user(message.from_user.id)
    chat_id = message.chat.id
    bot.send_message(chat_id, get_text("location_received", lang), reply_markup=ReplyKeyboardRemove())

    user_data[chat_id]['latitude'] = message.location.latitude
    user_data[chat_id]['longitude'] = message.location.longitude

    user_id, _, _ = get_or_create_user(message.from_user.id)
    location_links = get_location_link(message.location.latitude, message.location.longitude, lang)
    summary = (
        f"{get_text('final_check_prompt', lang)}\n\n"
        f"👤 {get_text('full_name_label', lang)}: {user_data[chat_id]['full_name']}\n"
        f"📞 {get_text('phone_label', lang)}: {user_data[chat_id]['phone']}\n"
        f"♾️ {get_text('taobao_id_label', lang)}: <code>Gv{user_id}</code>\n"
        f"📦 {get_text('taobao_order_num_label', lang)}: {user_data[chat_id]['order_number']}\n"
        f"📅 {get_text('date_label', lang)}: {user_data[chat_id]['order_date']}\n"
        f"📍 {get_text('location_label', lang)}: {location_links}"
    )
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton(get_text("final_confirm_btn", lang), callback_data="save_yes"))
    kb.add(types.InlineKeyboardButton(get_text("final_reject_btn", lang), callback_data="save_no"))
    bot.send_message(chat_id, summary, parse_mode="HTML", reply_markup=kb, disable_web_page_preview=True)


@bot.callback_query_handler(func=lambda call: call.data in ["save_yes", "save_no"])
def final_save(call):
    chat_id = call.message.chat.id
    _, _, lang = get_or_create_user(call.from_user.id)
    bot.answer_callback_query(call.id)

    if call.data == "save_no":
        bot.edit_message_text(get_text("final_restart_prompt", lang),
                              chat_id=chat_id, message_id=call.message.message_id)
        user_data[chat_id] = {}
        bot.register_next_step_handler(call.message, get_name, lang)
        return

    data = user_data[chat_id]
    user_id, _, _ = get_or_create_user(data['tg_id'])

    cursor.execute("SELECT COUNT(id) FROM orders")
    count = cursor.fetchone()[0]
    applicant_code = f"Gv{1000 + count + 1}"

    latitude = data.get('latitude')
    longitude = data.get('longitude')

    location_link_sheets = f"https://maps.google.com/?q={latitude},{longitude}" if latitude else ""
    location_link_html = get_location_link(latitude, longitude, lang)

    cursor.execute("""
        INSERT INTO orders (user_id, full_name, phone, order_number, order_date, latitude, longitude, location, applicant_order_number)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id, data['full_name'], data['phone'], data['order_number'], data['order_date'],
        latitude, longitude, location_link_sheets, applicant_code
    ))
    conn.commit()
    sheet.append_row([
        data['tg_id'], data['full_name'], data['phone'], data['order_number'],
        data['order_date'], location_link_sheets, applicant_code
    ])

    order_text_for_group = (
        f"🆕 <b>Новый заказ!</b>\n\n"
        f"Номер заказа заявителя: <code>{applicant_code}</code>\n"
        f"Уникальный ID: <code>{user_id}</code>\n\n"
        f"<b>Клиент:</b>\n"
        f"👤 {data['full_name']}\n"
        f"📞 {data['phone']}\n"
        f"🆔 <a href='tg://user?id={data['tg_id']}'>{data['tg_id']}</a>\n\n"
        f"<b>Заказ:</b>\n"
        f"📦 Taobao №: {data['order_number']}\n"
        f"📅 Дата: {data['order_date']}\n"
        f"📍 Локация: {location_link_html}"
    )
    bot.send_message(GROUP_ID, order_text_for_group, parse_mode="HTML", disable_web_page_preview=True)

    order_text_for_user = (
        f"{get_text('order_success_title', lang)}\n\n"
        f"{get_text('your_order_number', lang)}: <code>{applicant_code}</code>\n\n"
        f"<b>{get_text('details', lang)}:</b>\n"
        f"👤 {data['full_name']}\n"
        f"📞 {data['phone']}\n"
        f"📦 Taobao №: {data['order_number']}\n"
        f"📅 {data['order_date']}\n"
        f"📍 {get_text('location_label', lang)}: {location_link_html}"
    )
    bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id,
                          text=order_text_for_user, parse_mode="HTML", disable_web_page_preview=True)
    main_menu(chat_id, lang)
    user_data[chat_id] = {}


if __name__ == '__main__':
    bot.infinity_polling()
