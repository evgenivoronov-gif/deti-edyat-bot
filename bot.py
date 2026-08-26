"""
Telegram-бот для «ДЕТИ ЕДЯТ!» — принимает заявки на доставку питания прямо в чате,
присылает цены/меню и пересылает оформленную заявку владельцу бизнеса.

Запуск локально (polling, для теста):
    python bot.py

Продакшен (Render, webhook) — см. README.md.
"""

import logging
import os
from html import escape
from pathlib import Path

import httpx
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_CHAT_ID = int(os.environ["OWNER_CHAT_ID"])
PRIVACY_URL = "https://deti-edyat.ru/privacy"
COMPANY_PHONE = "+7 (911) 920-12-94"

# Порт/URL для вебхука на Render (см. README.md)
PORT = int(os.environ.get("PORT", "8080"))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # напр. https://your-app.onrender.com

# URL Google Apps Script Web App для журнала заявок (необязательно, см. README.md)
SHEET_WEBHOOK_URL = os.environ.get("SHEET_WEBHOOK_URL")

FILES_DIR = Path(__file__).parent / "files"
FILES = {
    ("prices", "kindergarten"): (FILES_DIR / "prices_kindergarten.pdf", "Цены — детский сад, сезон 2026"),
    ("prices", "school"): (FILES_DIR / "prices_school.pdf", "Цены — школа, сезон 2026"),
    ("menu", "kindergarten"): (FILES_DIR / "menu_kindergarten.xlsx", "Меню (осень) — детский сад, 2026"),
    ("menu", "school"): (FILES_DIR / "menu_school.xlsx", "Меню (осень) — школа, 2026"),
}

INSTITUTION_LABELS = {"kindergarten": "Детский сад", "school": "Школа"}

(
    MAIN_MENU,
    PLACE_CHOICE,
    ASK_KIDS_COUNT,
    ASK_MEALS,
    ASK_ADDRESS,
    ASK_NAME,
    ASK_PHONE,
    ASK_INSTITUTION_NAME,
    ASK_INSTITUTION_TYPE,
    ASK_CONSENT,
) = range(10)

MEAL_OPTIONS = ["Завтрак", "Второй завтрак", "Обед", "Полдник", "Ужин"]


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📋 Оставить заявку", callback_data="menu:order")],
            [InlineKeyboardButton("💰 Цены", callback_data="menu:prices")],
            [InlineKeyboardButton("🍽 Меню", callback_data="menu:menu")],
        ]
    )


def place_keyboard(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🧸 Детский сад", callback_data=f"place:{action}:kindergarten")],
            [InlineKeyboardButton("🎒 Школа", callback_data=f"place:{action}:school")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="menu:back")],
        ]
    )


def institution_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🧸 Детский сад", callback_data="institution:kindergarten")],
            [InlineKeyboardButton("🎒 Школа", callback_data="institution:school")],
        ]
    )


def skip_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Пропустить ➡️", callback_data="skip_institution_name")]])


def meals_keyboard(selected: set[str]) -> InlineKeyboardMarkup:
    rows = []
    for meal in MEAL_OPTIONS:
        mark = "✅ " if meal in selected else ""
        rows.append([InlineKeyboardButton(f"{mark}{meal}", callback_data=f"meal:{meal}")])
    rows.append([InlineKeyboardButton("Готово ▶️", callback_data="meal:done")])
    return InlineKeyboardMarkup(rows)


def consent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Согласен(а)", callback_data="consent:yes")],
            [InlineKeyboardButton("Отмена", callback_data="consent:no")],
        ]
    )


async def log_to_sheet(user, status: str, data: dict) -> None:
    if not SHEET_WEBHOOK_URL:
        return
    meals = data.get("meals") or set()
    payload = {
        "chat_id": user.id,
        "username": user.username or "",
        "status": status,
        "kids_count": data.get("kids_count", ""),
        "institution": data.get("institution", ""),
        "institution_name": data.get("institution_name", ""),
        "meals": ", ".join(sorted(meals, key=MEAL_OPTIONS.index)),
        "address": data.get("address", ""),
        "name": data.get("name", ""),
        "phone": data.get("phone", ""),
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(SHEET_WEBHOOK_URL, json=payload)
    except Exception:
        logger.exception("Failed to log order to Google Sheet")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "Здравствуйте! Это бот «ДЕТИ ЕДЯТ!» 🍎\n"
        "Доставка питания в детские сады, школы и летние лагеря.\n\n"
        "Что вас интересует?",
        reply_markup=main_menu_keyboard(),
    )
    return MAIN_MENU


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Что вас интересует?", reply_markup=main_menu_keyboard())
    return MAIN_MENU


async def main_menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":", 1)[1]

    if choice == "order":
        context.user_data.clear()
        await query.edit_message_text("Оформляем заявку.\n\nСколько детей нужно покормить?")
        return ASK_KIDS_COUNT

    action = "prices" if choice == "prices" else "menu"
    label = "цены" if action == "prices" else "меню"
    await query.edit_message_text(
        f"Для кого нужны {label}?", reply_markup=place_keyboard(action)
    )
    return PLACE_CHOICE


async def send_requested_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    _, action, place = query.data.split(":", 2)

    file_path, caption = FILES[(action, place)]
    if file_path.exists():
        with file_path.open("rb") as f:
            await context.bot.send_document(chat_id=query.message.chat_id, document=f, caption=caption)
    else:
        logger.error("Missing file: %s", file_path)
        await query.message.reply_text(
            f"Не получилось найти файл. Позвоните нам: {COMPANY_PHONE}"
        )

    await query.message.reply_text("Что ещё интересует?", reply_markup=main_menu_keyboard())
    return MAIN_MENU


async def ask_kids_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("Пожалуйста, укажите количество детей числом, например: 10")
        return ASK_KIDS_COUNT

    context.user_data["kids_count"] = text
    context.user_data["meals"] = set()
    await log_to_sheet(update.effective_user, "в процессе", context.user_data)
    await update.message.reply_text(
        "Какое питание нужно? Можно выбрать несколько вариантов.",
        reply_markup=meals_keyboard(context.user_data["meals"]),
    )
    return ASK_MEALS


async def toggle_meal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":", 1)[1]

    if choice == "done":
        selected = context.user_data.get("meals") or set()
        if not selected:
            await query.answer("Выберите хотя бы один вариант", show_alert=True)
            return ASK_MEALS
        await query.edit_message_text(
            "Питание: " + ", ".join(sorted(selected, key=MEAL_OPTIONS.index))
        )
        await log_to_sheet(update.effective_user, "в процессе", context.user_data)
        await query.message.reply_text("Укажите адрес детского сада/школы/лагеря (город, улица, дом):")
        return ASK_ADDRESS

    selected = context.user_data.setdefault("meals", set())
    if choice in selected:
        selected.remove(choice)
    else:
        selected.add(choice)
    await query.edit_message_reply_markup(reply_markup=meals_keyboard(selected))
    return ASK_MEALS


async def ask_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["address"] = update.message.text.strip()
    await log_to_sheet(update.effective_user, "в процессе", context.user_data)
    await update.message.reply_text("Как к вам обращаться? (имя)")
    return ASK_NAME


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["name"] = update.message.text.strip()
    await log_to_sheet(update.effective_user, "в процессе", context.user_data)
    phone_button = KeyboardButton("📱 Отправить мой номер", request_contact=True)
    await update.message.reply_text(
        "Укажите телефон для связи (или отправьте номер кнопкой ниже):",
        reply_markup=ReplyKeyboardMarkup([[phone_button]], one_time_keyboard=True, resize_keyboard=True),
    )
    return ASK_PHONE


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.contact:
        phone = update.message.contact.phone_number
    else:
        phone = update.message.text.strip()

    context.user_data["phone"] = phone
    await log_to_sheet(update.effective_user, "в процессе", context.user_data)
    await update.message.reply_text(
        "Как называется ваш сад или школа? Можно написать название, а можно пропустить этот шаг.",
        reply_markup=skip_keyboard(),
    )
    return ASK_INSTITUTION_NAME


async def ask_institution_type_prompt(target_message) -> int:
    await target_message.reply_text(
        "И последнее: вам нужна цена и меню для сада или для школы?",
        reply_markup=institution_keyboard(),
    )
    return ASK_INSTITUTION_TYPE


async def institution_name_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["institution_name"] = update.message.text.strip()
    await log_to_sheet(update.effective_user, "в процессе", context.user_data)
    return await ask_institution_type_prompt(update.message)


async def institution_name_skip(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["institution_name"] = ""
    await query.edit_message_reply_markup(reply_markup=None)
    return await ask_institution_type_prompt(query.message)


async def ask_institution_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    place = query.data.split(":", 1)[1]

    context.user_data["institution"] = INSTITUTION_LABELS[place]
    await log_to_sheet(update.effective_user, "в процессе", context.user_data)
    await query.edit_message_text(f"Учреждение: {context.user_data['institution']}")

    for action in ("prices", "menu"):
        file_path, caption = FILES[(action, place)]
        if file_path.exists():
            with file_path.open("rb") as f:
                await context.bot.send_document(chat_id=query.message.chat_id, document=f, caption=caption)

    await query.message.reply_text(
        "Последний шаг — согласие на обработку персональных данных.\n"
        f"Политика конфиденциальности: {PRIVACY_URL}",
    )
    await query.message.reply_text(
        "Подтверждаете согласие на обработку персональных данных?",
        reply_markup=consent_keyboard(),
    )
    return ASK_CONSENT


async def handle_consent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":", 1)[1]

    if choice == "no":
        await query.edit_message_text(
            "Без согласия на обработку персональных данных мы не можем оформить заявку.\n"
            f"Если передумаете — напишите /start, либо позвоните нам: {COMPANY_PHONE}"
        )
        await log_to_sheet(update.effective_user, "отклонил согласие", context.user_data)
        context.user_data.clear()
        return ConversationHandler.END

    data = context.user_data
    user = update.effective_user

    await query.edit_message_text(
        "Готово! ✅ Ваша заявка принята.\n"
        "Мы свяжемся с вами в ближайшее время, чтобы уточнить детали и цену.\n\n"
        f"Если что-то срочное — звоните: {COMPANY_PHONE}"
    )

    institution_full = data.get("institution", "-")
    if data.get("institution_name"):
        institution_full += f" ({data['institution_name']})"

    summary = (
        "🆕 <b>Новая заявка из Telegram-бота</b>\n\n"
        f"👶 Детей: {escape(data.get('kids_count', '-'))}\n"
        f"🍽 Питание: {escape(', '.join(sorted(data.get('meals', []), key=MEAL_OPTIONS.index)))}\n"
        f"📍 Адрес: {escape(data.get('address', '-'))}\n"
        f"🏫 Учреждение: {escape(institution_full)}\n"
        f"👤 Имя: {escape(data.get('name', '-'))}\n"
        f"📞 Телефон: {escape(data.get('phone', '-'))}\n"
        f"💬 Telegram: @{escape(user.username) if user.username else '(без username)'} (id {user.id})\n"
        "✅ Согласие на обработку ПД получено"
    )
    await context.bot.send_message(chat_id=OWNER_CHAT_ID, text=summary, parse_mode="HTML")
    await log_to_sheet(user, "заявка оформлена", data)

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if context.user_data:
        await log_to_sheet(update.effective_user, "отменил", context.user_data)
    context.user_data.clear()
    await update.message.reply_text(
        "Заявка отменена. Если захотите оформить снова — напишите /start.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


def build_application() -> Application:
    application = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [CallbackQueryHandler(main_menu_router, pattern=r"^menu:(order|prices|menu)$")],
            PLACE_CHOICE: [
                CallbackQueryHandler(send_requested_file, pattern=r"^place:"),
                CallbackQueryHandler(show_main_menu, pattern=r"^menu:back$"),
            ],
            ASK_KIDS_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_kids_count)],
            ASK_MEALS: [CallbackQueryHandler(toggle_meal, pattern=r"^meal:")],
            ASK_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_address)],
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            ASK_PHONE: [
                MessageHandler(filters.CONTACT | (filters.TEXT & ~filters.COMMAND), ask_phone)
            ],
            ASK_INSTITUTION_NAME: [
                CallbackQueryHandler(institution_name_skip, pattern=r"^skip_institution_name$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, institution_name_text),
            ],
            ASK_INSTITUTION_TYPE: [CallbackQueryHandler(ask_institution_type, pattern=r"^institution:")],
            ASK_CONSENT: [CallbackQueryHandler(handle_consent, pattern=r"^consent:")],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
    )
    application.add_handler(conv_handler)
    return application


def main() -> None:
    application = build_application()

    if WEBHOOK_URL:
        logger.info("Starting in webhook mode on port %s", PORT)
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL.rstrip('/')}/{BOT_TOKEN}",
        )
    else:
        logger.info("Starting in polling mode (local dev)")
        application.run_polling()


if __name__ == "__main__":
    main()
