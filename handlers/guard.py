"""Защита ответов клиента в диалоге: пока бот собирает данные (адрес, имя, питомец, комментарий), мат не принимаем."""
from aiogram import BaseMiddleware
from aiogram.types import Message

import profanity


class ProfanityGuard(BaseMiddleware):
    async def __call__(self, handler, event: Message, data: dict):
        text = getattr(event, "text", None) or ""
        state = data.get("state")
        if text and not text.startswith("/") and state is not None and profanity.has_profanity(text):
            if await state.get_state():  # идёт сбор данных; обычное сообщение администратору (без шага) не трогаем
                await event.answer(profanity.MESSAGE + " Напишите ещё раз.")
                return None
        return await handler(event, data)
