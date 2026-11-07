import asyncio
import random
import os
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, PreCheckoutQuery, LabeledPrice, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import uvicorn

# ===== НАСТРОЙКИ =====
BOT_TOKEN = "9280034478:AAHgmjytU79yLOsnh7HnksSOrlzBZ2rXNgA"
ADMIN_ID = 8491030557  # Твой ID

# ВСТАВЬ СЮДА ССЫЛКУ ОТ RENDER
WEB_APP_URL = "https://твой-сервис.onrender.com"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()

# ===== БАЗА =====
users = {}

def get_user(uid):
    uid = str(uid)
    if uid not in users:
        users[uid] = {"balance": 100}
    return users[uid]

# ===== СОСТОЯНИЯ ДЛЯ FSM (ВЫДАЧА ВАЛЮТЫ) =====
class AdminStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_amount = State()

# ===== API ДЛЯ MINI APP =====
@app.get("/api/balance/{user_id}")
def api_balance(user_id: str):
    return {"balance": get_user(user_id)["balance"]}

class OpenCaseRequest(BaseModel):
    user_id: str
    case_id: str

@app.post("/api/open_case")
def api_open_case(req: OpenCaseRequest):
    user = get_user(req.user_id)
    prices = {"bronze": 50, "silver": 150, "gold": 500}
    items = {"bronze": ["🗑", "🥉", "🥈"], "silver": ["🥈", "🥇", "💎"], "gold": ["🥇", "💎", "👑"]}
    
    price = prices.get(req.case_id, 50)
    if user["balance"] < price:
        return {"error": "Недостаточно средств"}
    
    user["balance"] -= price
    item = random.choice(items[req.case_id])
    return {"item": item, "balance": user["balance"]}

@app.get("/", response_class=HTMLResponse)
def read_index():
    file_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>index.html не найден</h1>"

# ===== БОТ: СТАРТ =====
@dp.message(CommandStart())
async def cmd_start(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎰 Открыть Казино", web_app=WebAppInfo(url=WEB_APP_URL))]
    ])
    await message.answer(
        f"Привет, {message.from_user.first_name}! 👋\n"
        f"Добро пожаловать в Lucky Cases.\n\n"
        f"Нажми на кнопку ниже, чтобы начать играть!",
        reply_markup=kb
    )

# ===== БОТ: АДМИНКА =====
@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    # Проверка, что это владелец
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У тебя нет доступа к админке.")
        return

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Выдать себе", callback_data="admin_give_self")],
        [InlineKeyboardButton(text="🎁 Выдать игроку", callback_data="admin_give_user")],
    ])
    await message.answer("👑 <b>Админ-панель</b>\nВыбери действие:", reply_markup=kb)

@dp.callback_query(F.data == "admin_give_self")
async def admin_give_self(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await state.update_data(target_id=ADMIN_ID)
    await state.set_state(AdminStates.waiting_for_amount)
    await callback.message.edit_text("Введи сумму, которую хочешь выдать себе:")

@dp.callback_query(F.data == "admin_give_user")
async def admin_give_user(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return
    await state.set_state(AdminStates.waiting_for_user_id)
    await callback.message.edit_text("Введи ID игрока (его можно узнать в базе или попросить у него):")

@dp.message(AdminStates.waiting_for_user_id)
async def process_user_id(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        user_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Неверный ID. Введи число.")
        return
    await state.update_data(target_id=user_id)
    await state.set_state(AdminStates.waiting_for_amount)
    await message.answer(f"Введи сумму для игрока {user_id}:")

@dp.message(AdminStates.waiting_for_amount)
async def process_amount(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        amount = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Неверная сумма. Введи число.")
        return
    
    data = await state.get_data()
    target_id = data.get("target_id")
    
    user = get_user(target_id)
    user["balance"] += amount
    
    await message.answer(
        f"✅ Успешно!\nПользователю <code>{target_id}</code> выдано <b>{amount} 💎</b>.\n"
        f"Его текущий баланс: {user['balance']} 💎",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎰 Открыть Казино", web_app=WebAppInfo(url=WEB_APP_URL))]
        ])
    )
    await state.clear()

# ===== БОТ: ОПЛАТА =====
@dp.message(F.web_app_data)
async def web_app_data(message: Message):
    if "buy_crystals" in message.web_app_data.data:
        await bot.send_invoice(
            chat_id=message.from_user.id,
            title="500 Кристаллов",
            description="Покупка валюты",
            payload="buy_500",
            currency="XTR",
            prices=[LabeledPrice(label="Кристаллы", amount=250)]
        )

@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery):
    await query.answer(ok=True)

@dp.message(F.successful_payment)
async def payment_ok(message: Message):
    user = get_user(message.from_user.id)
    user["balance"] += 500
    await message.answer("✅ Оплата прошла! +500 Кристаллов.")

# ===== ЗАПУСК =====
async def main():
    asyncio.create_task(dp.start_polling(bot))
    port = int(os.environ.get("PORT", 8000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
