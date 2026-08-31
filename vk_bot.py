"""
VK-бот для «ДЕТИ ЕДЯТ!» — та же логика, что и Telegram-бот: принимает заявки,
присылает цены/меню, пишет всё в тот же Google Sheets-журнал.

Запуск: python vk_bot.py (Long Poll — не нужен вебхук/порт).
"""

import asyncio
import logging
import os
from pathlib import Path

import httpx
from vkbottle import (
    Callback,
    DocMessagesUploader,
    GroupEventType,
    Keyboard,
    KeyboardButtonColor,
)
from vkbottle.bot import Bot, Message, MessageEvent

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

VK_TOKEN = os.environ["VK_TOKEN"]
OWNER_VK_ID = int(os.environ["OWNER_VK_ID"])
PRIVACY_URL = "https://deti-edyat.ru/privacy"
COMPANY_PHONE = "+7 (911) 920-12-94"

SHEET_WEBHOOK_URL = os.environ.get("SHEET_WEBHOOK_URL")

FILES_DIR = Path(__file__).parent / "files"
FILES = {
    ("prices", "kindergarten"): (FILES_DIR / "Цены_детский_сад.pdf", "Цены — детский сад, сезон 2026"),
    ("prices", "school"): (FILES_DIR / "Цены_школа.pdf", "Цены — школа, сезон 2026"),
    ("menu", "kindergarten"): (FILES_DIR / "Меню_детский_сад.pdf", "Меню — пример одного дня — детский сад, 2026"),
    ("menu", "school"): (FILES_DIR / "Меню_школа.pdf", "Меню — пример одного дня — школа, 2026"),
}

INSTITUTION_LABELS = {"kindergarten": "Детский сад", "school": "Школа"}
MEAL_OPTIONS = ["Завтрак", "Второй завтрак", "Обед", "Полдник", "Ужин"]
PRICE_MENU_KEYWORDS = ("цен", "прайс", "стоимост", "меню")

bot = Bot(token=VK_TOKEN)
uploader = DocMessagesUploader(api=bot.api)

# Простое хранилище состояния диалога по user_id (в памяти процесса)
sessions: dict[int, dict] = {}

(
    STEP_KIDS_COUNT,
    STEP_MEALS,
    STEP_ADDRESS,
    STEP_NAME,
    STEP_PHONE,
    STEP_INSTITUTION_NAME,
    STEP_INSTITUTION_TYPE,
    STEP_CONSENT,
) = range(8)


def meals_keyboard(selected: set[str]) -> str:
    kb = Keyboard(inline=True)
    for i, meal in enumerate(MEAL_OPTIONS):
        if i:
            kb.row()
        mark = "✅ " if meal in selected else ""
        kb.add(Callback(f"{mark}{meal}", payload={"cmd": "meal", "value": meal}))
    kb.row()
    kb.add(Callback("Готово ▶️", payload={"cmd": "meal_done"}), color=KeyboardButtonColor.POSITIVE)
    return kb.get_json()


def institution_keyboard(prefix: str = "institution") -> str:
    kb = Keyboard(inline=True)
    kb.add(Callback("🧸 Детский сад", payload={"cmd": prefix, "value": "kindergarten"}))
    kb.row()
    kb.add(Callback("🎒 Школа", payload={"cmd": prefix, "value": "school"}))
    return kb.get_json()


def skip_keyboard() -> str:
    kb = Keyboard(inline=True)
    kb.add(Callback("Пропустить ➡️", payload={"cmd": "skip_institution_name"}))
    return kb.get_json()


def consent_keyboard() -> str:
    kb = Keyboard(inline=True)
    kb.add(Callback("✅ Согласен(а)", payload={"cmd": "consent", "value": "yes"}), color=KeyboardButtonColor.POSITIVE)
    kb.row()
    kb.add(Callback("Отмена", payload={"cmd": "consent", "value": "no"}), color=KeyboardButtonColor.NEGATIVE)
    return kb.get_json()


async def log_to_sheet(user_id: int, user_name: str, status: str, data: dict) -> None:
    if not SHEET_WEBHOOK_URL:
        return
    meals = data.get("meals") or set()
    payload = {
        "chat_id": f"vk{user_id}",
        "username": user_name,
        "status": status,
        "kids_count": data.get("kids_count", ""),
        "institution": data.get("institution", ""),
        "institution_name": data.get("institution_name", ""),
        "meals": ", ".join(sorted(meals, key=MEAL_OPTIONS.index)),
        "address": data.get("address", ""),
        "name": data.get("name", ""),
        "phone": data.get("phone", ""),
        "channel": "VK",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(SHEET_WEBHOOK_URL, json=payload)
    except Exception:
        logger.exception("Failed to log VK order to Google Sheet")


async def send_price_and_menu(peer_id: int, place: str) -> None:
    for action in ("prices", "menu"):
        file_path, caption = FILES[(action, place)]
        if not file_path.exists():
            logger.error("Missing file: %s", file_path)
            continue
        attachment = await uploader.upload(str(file_path), peer_id=peer_id)
        await bot.api.messages.send(
            peer_id=peer_id,
            message=caption,
            attachment=attachment,
            random_id=0,
        )


def detect_institution_from_text(text_lower: str) -> str | None:
    has_kindergarten = "сад" in text_lower
    has_school = "школ" in text_lower
    if has_kindergarten and not has_school:
        return "kindergarten"
    if has_school and not has_kindergarten:
        return "school"
    return None


async def get_user_name(user_id: int) -> str:
    try:
        users = await bot.api.users.get(user_ids=[user_id])
        if users:
            return f"{users[0].first_name} {users[0].last_name}".strip()
    except Exception:
        logger.exception("Failed to fetch VK user name")
    return f"id{user_id}"


async def start_order_flow(message: Message) -> None:
    sessions[message.from_id] = {"step": STEP_KIDS_COUNT}
    await message.answer(
        "Здравствуйте! Это бот «ДЕТИ ЕДЯТ!» 🍎\n"
        "Доставка питания в детские сады, школы и летние лагеря.\n\n"
        "Оформим заявку — в конце пришлём актуальные цены и меню.\n\n"
        "Сколько детей нужно покормить?"
    )


@bot.on.message()
async def on_message(message: Message):
    user_id = message.from_id
    text = (message.text or "").strip()
    session = sessions.get(user_id)

    if session is None:
        text_lower = text.lower()
        if any(word in text_lower for word in PRICE_MENU_KEYWORDS):
            place = detect_institution_from_text(text_lower)
            if place:
                await message.answer(f"Учреждение: {INSTITUTION_LABELS[place]}")
                await send_price_and_menu(message.peer_id, place)
            else:
                await message.answer(
                    "Для кого нужны цены и меню?",
                    keyboard=institution_keyboard(prefix="price_menu"),
                )
            return
        await start_order_flow(message)
        return

    step = session["step"]

    if step == STEP_KIDS_COUNT:
        if not text.isdigit() or int(text) <= 0:
            await message.answer("Пожалуйста, укажите количество детей числом, например: 10")
            return
        session["kids_count"] = text
        session["meals"] = set()
        user_name = await get_user_name(user_id)
        await log_to_sheet(user_id, user_name, "в процессе", session)
        session["step"] = STEP_MEALS
        await message.answer(
            "Какое питание нужно? Можно выбрать несколько вариантов.",
            keyboard=meals_keyboard(session["meals"]),
        )
        return

    if step == STEP_ADDRESS:
        session["address"] = text
        user_name = await get_user_name(user_id)
        await log_to_sheet(user_id, user_name, "в процессе", session)
        session["step"] = STEP_NAME
        await message.answer("Подскажите, пожалуйста, как я могу к вам обращаться?")
        return

    if step == STEP_NAME:
        session["name"] = text
        user_name = await get_user_name(user_id)
        await log_to_sheet(user_id, user_name, "в процессе", session)
        session["step"] = STEP_PHONE
        await message.answer("Укажите телефон для связи:")
        return

    if step == STEP_PHONE:
        session["phone"] = text
        user_name = await get_user_name(user_id)
        await log_to_sheet(user_id, user_name, "в процессе", session)
        session["step"] = STEP_INSTITUTION_NAME
        await message.answer(
            "Как называется ваш сад или школа? Можно написать название, а можно пропустить этот шаг.",
            keyboard=skip_keyboard(),
        )
        return

    if step == STEP_INSTITUTION_NAME:
        session["institution_name"] = text
        user_name = await get_user_name(user_id)
        await log_to_sheet(user_id, user_name, "в процессе", session)
        session["step"] = STEP_INSTITUTION_TYPE
        await message.answer(
            "И последнее: вам нужна цена и меню для сада или для школы?",
            keyboard=institution_keyboard(),
        )
        return

    # На остальных шагах (выбор питания/сада-школы/согласие) ждём нажатия кнопки,
    # а не текст — вежливо подсказываем.
    await message.answer("Пожалуйста, воспользуйтесь кнопками выше 🙂")


@bot.on.raw_event(GroupEventType.MESSAGE_EVENT, dataclass=MessageEvent)
async def on_callback(event: MessageEvent):
    user_id = event.user_id
    payload = event.payload or {}
    cmd = payload.get("cmd")
    session = sessions.get(user_id)

    if session is None:
        await event.send_message_event_answer(event_data={"type": "show_snackbar", "text": "Сессия истекла, напишите что-нибудь ещё раз."})
        return

    if cmd == "meal":
        meal = payload.get("value")
        selected = session.setdefault("meals", set())
        if meal in selected:
            selected.remove(meal)
        else:
            selected.add(meal)
        await event.edit_message(
            message="Какое питание нужно? Можно выбрать несколько вариантов.",
            keyboard=meals_keyboard(selected),
        )
        await event.send_empty_answer()
        return

    if cmd == "meal_done":
        selected = session.get("meals") or set()
        if not selected:
            await event.send_message_event_answer(event_data={"type": "show_snackbar", "text": "Выберите хотя бы один вариант"})
            return
        await event.edit_message(
            message="Питание: " + ", ".join(sorted(selected, key=MEAL_OPTIONS.index)),
            keyboard=Keyboard(inline=True).get_json(),
        )
        await event.send_empty_answer()
        user_name = await get_user_name(user_id)
        await log_to_sheet(user_id, user_name, "в процессе", session)
        session["step"] = STEP_ADDRESS
        await bot.api.messages.send(
            peer_id=event.peer_id,
            message="Укажите адрес детского сада/школы/лагеря (город, улица, дом):",
            random_id=0,
        )
        return

    if cmd == "skip_institution_name":
        session["institution_name"] = ""
        await event.edit_message(
            message="Как называется ваш сад или школа? (пропущено)",
            keyboard=Keyboard(inline=True).get_json(),
        )
        await event.send_empty_answer()
        session["step"] = STEP_INSTITUTION_TYPE
        await bot.api.messages.send(
            peer_id=event.peer_id,
            message="И последнее: вам нужна цена и меню для сада или для школы?",
            keyboard=institution_keyboard(),
            random_id=0,
        )
        return

    if cmd == "institution":
        place = payload.get("value")
        session["institution"] = INSTITUTION_LABELS[place]
        session["institution_place"] = place
        await event.edit_message(
            message=f"Учреждение: {session['institution']}",
            keyboard=Keyboard(inline=True).get_json(),
        )
        await event.send_empty_answer()
        user_name = await get_user_name(user_id)
        await log_to_sheet(user_id, user_name, "в процессе", session)
        session["step"] = STEP_CONSENT
        await bot.api.messages.send(
            peer_id=event.peer_id,
            message=(
                "Последний шаг — согласие на обработку персональных данных.\n"
                f"Политика конфиденциальности: {PRIVACY_URL}"
            ),
            random_id=0,
        )
        await bot.api.messages.send(
            peer_id=event.peer_id,
            message="Подтверждаете согласие на обработку персональных данных?",
            keyboard=consent_keyboard(),
            random_id=0,
        )
        return

    if cmd == "price_menu":
        place = payload.get("value")
        await event.edit_message(
            message=f"Учреждение: {INSTITUTION_LABELS[place]}",
            keyboard=Keyboard(inline=True).get_json(),
        )
        await event.send_empty_answer()
        await send_price_and_menu(event.peer_id, place)
        return

    if cmd == "consent":
        choice = payload.get("value")
        user_name = await get_user_name(user_id)

        if choice == "no":
            await event.edit_message(
                message=(
                    "Без согласия на обработку персональных данных мы не можем оформить заявку.\n"
                    f"Если передумаете — напишите нам ещё раз, либо позвоните: {COMPANY_PHONE}"
                ),
                keyboard=Keyboard(inline=True).get_json(),
            )
            await event.send_empty_answer()
            await log_to_sheet(user_id, user_name, "отклонил согласие", session)
            sessions.pop(user_id, None)
            return

        await event.edit_message(
            message=(
                "Готово! ✅ Ваша заявка принята.\n"
                "Мы свяжемся с вами в ближайшее время, чтобы уточнить детали и цену.\n\n"
                f"Если что-то срочное — звоните: {COMPANY_PHONE}"
            ),
            keyboard=Keyboard(inline=True).get_json(),
        )
        await event.send_empty_answer()

        place = session.get("institution_place")
        if place:
            await send_price_and_menu(event.peer_id, place)

        institution_full = session.get("institution", "-")
        if session.get("institution_name"):
            institution_full += f" ({session['institution_name']})"

        summary = (
            "🆕 Новая заявка из VK-бота\n\n"
            f"👶 Детей: {session.get('kids_count', '-')}\n"
            f"🍽 Питание: {', '.join(sorted(session.get('meals', []), key=MEAL_OPTIONS.index))}\n"
            f"📍 Адрес: {session.get('address', '-')}\n"
            f"🏫 Учреждение: {institution_full}\n"
            f"👤 Имя: {session.get('name', '-')}\n"
            f"📞 Телефон: {session.get('phone', '-')}\n"
            f"💬 VK: https://vk.com/id{user_id} ({user_name})\n"
            "✅ Согласие на обработку ПД получено"
        )
        await bot.api.messages.send(peer_id=OWNER_VK_ID, message=summary, random_id=0)
        await log_to_sheet(user_id, user_name, "заявка оформлена", session)
        sessions.pop(user_id, None)
        return


if __name__ == "__main__":
    logger.info("Starting VK bot (Long Poll)")
    bot.run_forever()
