"""
Telegram-бот для «ДЕТИ ЕДЯТ!» — принимает заявки на доставку питания прямо в чате
и пересылает оформленную заявку владельцу бизнеса.

Запуск локально (polling, для теста):
    python bot.py

Продакшен (Render, webhook) — см. README.md.
"""

import logging
import os
from html import escape

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

(
    ASK_KIDS_COUNT,
    ASK_MEALS,
    ASK_ADDRESS,
    ASK_NAME,
    ASK_PHONE,
    ASK_CONSENT,
) = range(6)

MEAL_OPTIONS = ["Завтрак", "Второй завтрак", "Обед", "Полдник", "Ужин"]


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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "Здравствуйте! Это бот «ДЕТИ ЕДЯТ!» 🍎\n"
        "Оформим заявку на доставку питания в детский сад, школу или лагерь.\n\n"
        "Сколько детей нужно покормить?",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ASK_KIDS_COUNT


async def ask_kids_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("Пожалуйста, укажите количество детей числом, например: 10")
        return ASK_KIDS_COUNT

    context.user_data["kids_count"] = text
    context.user_data["meals"] = set()
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
    await update.message.reply_text("Как к вам обращаться? (имя)")
    return ASK_NAME


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["name"] = update.message.text.strip()
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
    await update.message.reply_text(
        "Последний шаг — согласие на обработку персональных данных.\n"
        f"Политика конфиденциальности: {PRIVACY_URL}",
        reply_markup=ReplyKeyboardRemove(),
    )
    await update.message.reply_text(
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
        context.user_data.clear()
        return ConversationHandler.END

    data = context.user_data
    user = update.effective_user

    await query.edit_message_text(
        "Готово! ✅ Ваша заявка принята.\n"
        "Мы свяжемся с вами в ближайшее время, чтобы уточнить детали и цену.\n\n"
        f"Если что-то срочное — звоните: {COMPANY_PHONE}"
    )

    summary = (
        "🆕 <b>Новая заявка из Telegram-бота</b>\n\n"
        f"👶 Детей: {escape(data.get('kids_count', '-'))}\n"
        f"🍽 Питание: {escape(', '.join(sorted(data.get('meals', []), key=MEAL_OPTIONS.index)))}\n"
        f"📍 Адрес: {escape(data.get('address', '-'))}\n"
        f"👤 Имя: {escape(data.get('name', '-'))}\n"
        f"📞 Телефон: {escape(data.get('phone', '-'))}\n"
        f"💬 Telegram: @{escape(user.username) if user.username else '(без username)'} (id {user.id})\n"
        "✅ Согласие на обработку ПД получено"
    )
    await context.bot.send_message(chat_id=OWNER_CHAT_ID, text=summary, parse_mode="HTML")

    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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
            ASK_KIDS_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_kids_count)],
            ASK_MEALS: [CallbackQueryHandler(toggle_meal, pattern=r"^meal:")],
            ASK_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_address)],
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            ASK_PHONE: [
                MessageHandler(filters.CONTACT | (filters.TEXT & ~filters.COMMAND), ask_phone)
            ],
            ASK_CONSENT: [CallbackQueryHandler(handle_consent, pattern=r"^consent:")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
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
