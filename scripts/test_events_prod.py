import asyncio
from unittest.mock import AsyncMock
from vkbottle.bot import Message
from bots.vk.bot import vk_bot

async def main():
    print("--- 1. Testing message 'Мероприятия' dispatch ---")
    msg = AsyncMock(spec=Message)
    msg.from_id = 999222
    msg.peer_id = 999222
    msg.state_peer = None
    msg.text = "Мероприятия"

    view = vk_bot.router.views["message"]
    for idx, h in enumerate(view.handlers):
        passed = True
        for rule in h.rules:
            res = await rule.check(msg)
            if res is False:
                passed = False
                break
        if passed:
            print(f"Handler matched: {h.handler.__name__}")
            await h.handler(msg)
            print("Bot Answer:\n" + str(msg.answer.call_args[0][0]))
            print("Keyboard attached:\n", msg.answer.call_args[1].get("keyboard"))
            break

    print("\n--- 2. Testing registration with 'Записаться #1' ---")
    msg2 = AsyncMock(spec=Message)
    msg2.from_id = 999222
    msg2.peer_id = 999222
    msg2.state_peer = None
    msg2.text = "Записаться #1"

    for idx, h in enumerate(view.handlers):
        passed = True
        for rule in h.rules:
            res = await rule.check(msg2)
            if res is False:
                passed = False
                break
        if passed:
            print(f"Handler matched: {h.handler.__name__}")
            await h.handler(msg2)
            print("Bot Answer:\n" + str(msg2.answer.call_args[0][0]))
            break

    print("\n--- 3. Testing repeated registration with 'Записаться 1' ---")
    msg3 = AsyncMock(spec=Message)
    msg3.from_id = 999222
    msg3.peer_id = 999222
    msg3.state_peer = None
    msg3.text = "Записаться 1"

    for idx, h in enumerate(view.handlers):
        passed = True
        for rule in h.rules:
            res = await rule.check(msg3)
            if res is False:
                passed = False
                break
        if passed:
            print(f"Handler matched: {h.handler.__name__}")
            await h.handler(msg3)
            print("Bot Answer:\n" + str(msg3.answer.call_args[0][0]))
            break

if __name__ == "__main__":
    asyncio.run(main())
