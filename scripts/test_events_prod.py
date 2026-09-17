import asyncio
from unittest.mock import AsyncMock

from vkbottle.bot import Message

from bots.vk.bot import vk_bot

TEST_VK_ID = 999222


async def _dispatch(view, text: str) -> Message:
    """Смоделировать входящее сообщение и вызвать первый подходящий хендлер."""
    msg = AsyncMock(spec=Message)
    msg.from_id = TEST_VK_ID
    msg.peer_id = TEST_VK_ID
    msg.state_peer = None
    msg.text = text

    for handler in view.handlers:
        if any(await rule.check(msg) is False for rule in handler.rules):
            continue
        print(f"Handler matched: {handler.handler.__name__}")
        await handler.handler(msg)
        return msg

    print("Совпавших хендлеров нет")
    return msg


def _print_answer(msg: Message, *, with_keyboard: bool = False) -> None:
    """Напечатать ответ бота (и клавиатуру, если она была приложена)."""
    if not msg.answer.called:
        print("Бот не ответил")
        return
    print("Bot Answer:\n" + str(msg.answer.call_args[0][0]))
    if with_keyboard:
        print("Keyboard attached:\n", msg.answer.call_args[1].get("keyboard"))


async def main():
    view = vk_bot.router.views["message"]

    print("--- 1. Testing message 'Мероприятия' dispatch ---")
    msg = await _dispatch(view, "Мероприятия")
    _print_answer(msg, with_keyboard=True)

    print("\n--- 2. Testing registration with 'Записаться #1' ---")
    msg2 = await _dispatch(view, "Записаться #1")
    _print_answer(msg2)

    print("\n--- 3. Testing repeated registration with 'Записаться 1' ---")
    msg3 = await _dispatch(view, "Записаться 1")
    _print_answer(msg3)


if __name__ == "__main__":
    asyncio.run(main())
