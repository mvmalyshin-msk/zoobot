"""Точка входа: python bot.py"""
import asyncio
import logging

from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat, CallbackQuery, Message

import db
import keyboards as kb
import repo
import texts
from config import config
from handlers import admin, client, executor
from scheduler import setup_scheduler

log = logging.getLogger("zoobot")

CLIENT_COMMANDS = {"/start", "/menu", "/profile"}


class ConsentMiddleware(BaseMiddleware):
    """Пока клиент не нажал «Согласен на обработку ПДн» — дальше экрана согласия не пускаем."""

    async def __call__(self, handler, event, data):
        user = event.from_user
        if isinstance(event, Message):
            text = (event.text or "").split("@")[0]
            cmd = text.split(" ")[0]
            if cmd == "/start":
                return await handler(event, data)
            # админ-команды и /my исполнителя не требуют согласия клиента
            if cmd.startswith("/") and cmd not in CLIENT_COMMANDS:
                if user.id in config.admin_ids or cmd == "/my":
                    return await handler(event, data)
            answer_to = event
        else:  # CallbackQuery
            if event.data == "pdn:agree" or (event.data or "").startswith("ex:"):
                return await handler(event, data)
            answer_to = event.message

        u = await repo.get_or_create_user(user)
        if u["pdn_consent"]:
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            await event.answer()
        await answer_to.answer(texts.CONSENT_TEXT, reply_markup=kb.consent(config.policy_url))


async def set_commands(bot: Bot) -> None:
    """Клиентам показываем 3 команды, админам — ещё и /admin. Остальные админ-команды скрыты."""
    await bot.set_my_commands([
        BotCommand(command="menu", description="Главное меню"),
        BotCommand(command="profile", description="Профиль и питомцы"),
    ])
    for admin_id in config.admin_ids:
        try:
            await bot.set_my_commands(
                [BotCommand(command="menu", description="Главное меню"),
                 BotCommand(command="orders", description="Активные заказы"),
                 BotCommand(command="stats", description="Статистика за месяц"),
                 BotCommand(command="admin", description="Все команды админа")],
                scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception:  # админ ещё не писал боту — Telegram не даст задать меню
            log.warning("Не удалось задать меню для админа %s (он должен сначала нажать /start)", admin_id)


async def set_profile_texts(bot: Bot) -> None:
    """Текст на стартовой странице бота (до /start) и в шапке профиля. Задаём из кода, чтобы не зависеть от BotFather."""
    try:
        await bot.set_my_description(texts.BOT_DESCRIPTION)
        await bot.set_my_short_description(texts.BOT_SHORT_DESCRIPTION)
    except Exception as e:  # не критично: бот работает и без этого
        log.warning("Не удалось задать описание бота: %s", e)


async def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    await db.init_db()

    bot = Bot(config.bot_token)
    dp = Dispatcher(storage=MemoryStorage())  # состояния диалогов; при перезапуске клиент начинает с /menu
    dp.message.filter(F.chat.type == "private")

    consent = ConsentMiddleware()
    dp.message.outer_middleware(consent)
    dp.callback_query.outer_middleware(consent)

    # порядок важен: админ → исполнитель → клиент (у клиента внизу «запасные» обработчики)
    dp.include_router(admin.router)
    dp.include_router(executor.router)
    dp.include_router(client.router)

    scheduler = setup_scheduler(bot)
    scheduler.start()
    await set_commands(bot)
    await set_profile_texts(bot)
    log.info("Бот запущен")
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    if not config.admin_ids:
        logging.warning("ADMIN_IDS пуст — админ-команды будут недоступны")
    asyncio.run(main())
