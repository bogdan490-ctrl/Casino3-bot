import asyncio
import random
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
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

BOT_TOKEN = "9280034478:AAHgmjytU79yLOsnh7HnksSOrlzBZ2rXNgA"
ADMIN_ID = 8491030557
WEB_APP_URL = "https://casino-bot-ojim.onrender.com"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()

users = {}
def get_user(uid):
    uid = str(uid)
    if uid not in users: users[uid] = {"balance": 100}
    return users[uid]

class AdminStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_amount = State()

@app.get("/api/balance/{user_id}")
def api_balance(user_id: str): return {"balance": get_user(user_id)["balance"]}

class OpenCaseRequest(BaseModel):
    user_id: str
    case_id: str

@app.post("/api/open_case")
def api_open_case(req: OpenCaseRequest):
    user = get_user(req.user_id)
    prices = {"bronze": 50, "silver": 150, "gold": 500}
    items = {"bronze": ["🗑 Мусор", "🥉 Бронза", "🥈 Серебро"], "silver": ["🥈 Серебро", "🥇 Золото", "💎 Алмаз"], "gold": ["🥇 Золото", "💎 Алмаз", "👑 Корона"]}
    price = prices.get(req.case_id, 50)
    if user["balance"] < price: return {"error": "Недостаточно средств"}
    user["balance"] -= price
    return {"item": random.choice(items[req.case_id]), "balance": user["balance"]}

@app.get("/", response_class=HTMLResponse)
def read_index():
    file_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f: return f.read()
    return "<h1>index.html не найден</h1>"

# ===== РАКЕТКА =====
active_bets = {}
crash_multiplier = 1.0
crash_running = False
connected_clients = set()
game_state = "waiting"

async def broadcast(message):
    for client in connected_clients.copy():
        try: await client.send_json(message)
        except: pass

async def game_loop():
    global crash_multiplier, crash_running, active_bets, game_state
    while True:
        game_state = "waiting"; active_bets.clear(); crash_multiplier = 1.0
        for i in range(10, 0, -1):
            await broadcast({"type": "waiting", "count": len(active_bets), "countdown": i})
            await asyncio.sleep(1)
        game_state = "flying"; crash_running = True
        await broadcast({"type": "start"})
        while crash_running:
            await asyncio.sleep(0.1)
            crash_multiplier += 0.03 * crash_multiplier
            if random.random() < 0.015 * crash_multiplier: crash_running = False
            await broadcast({"type": "tick", "multiplier": round(crash_multiplier, 2)})
        game_state = "crashed"
        await broadcast({"type": "crash", "multiplier": round(crash_multiplier, 2)})
        await asyncio.sleep(4)

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await websocket.accept()
    connected_clients.add(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("action") == "bet":
                amount = data.get("amount", 0)
                user = get_user(user_id)
                if game_state != "waiting": 
                    await websocket.send_json({"type": "error", "msg": "Ставки закрыты!"}); continue
                if user["balance"] >= amount:
                    user["balance"] -= amount
                    active_bets[user_id] = amount
                else: await websocket.send_json({"type": "error", "msg": "Недостаточно средств"})
            elif data.get("action") == "cashout":
                if game_state == "flying" and user_id in active_bets:
                    bet = active_bets.pop(user_id)
                    win = int(bet * crash_multiplier)
                    get_user(user_id)["balance"] += win
                    await websocket.send_json({"type": "cashed_out", "win": win})
    except WebSocketDisconnect:
        connected_clients.remove(websocket)

# ===== БОТ =====
@dp.message(CommandStart())
async def cmd_start(message: Message):
    args = message.text.split()
    user_id = str(message.from_user.id)
    
    # Проверяем реферальную ссылку
    if len(args) > 1 and args[1].startswith("ref"):
        referrer_id = args[1].replace("ref", "")
        if referrer_id != user_id:
            ref_user = get_user(referrer_id)
            ref_user["balance"] += 5 # +5 алмазов рефереру
            try: await bot.send_message(referrer_id, "🎉 По твоей ссылке зашел друг! Тебе начислено +5 💎")
            except: pass

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Открыть казино", web_app=WebAppInfo(url=WEB_APP_URL))]])
    await message.answer(f"Привет! Добро пожаловать в Legendstars.", reply_markup=kb)

@dp.message(F.web_app_data)
async def web_app_data(message: Message):
    import json
    data = json.loads(message.web_app_data.data)
    
    # ПОПОЛНЕНИЕ
    if data["action"] == "buy_crystals":
        await bot.send_invoice(message.from_user.id, "500 Кристаллов", "Покупка валюты", "buy_500", "XTR", [LabeledPrice(label="Кристаллы", amount=250)])
    
    # ПРОМОКОД
    elif data["action"] == "promo":
        if data["code"] == "START":
            user = get_user(message.from_user.id)
            user["balance"] += 50
            await message.answer("✅ Промокод активирован! +50 💎")
        else:
            await message.answer("❌ Неверный промокод")
            
    # ВЫВОД
    elif data["action"] == "withdraw":
        amount = data["amount"]
        if amount >= 1000:
            await bot.send_message(ADMIN_ID, f"💸 <b>ЗАЯВКА НА ВЫВОД</b>\nЮзер: {message.from_user.id}\nСумма: {amount} 💎\n\nПереведи ему {amount // 100} ⭐")
            await message.answer(f"✅ Заявка на вывод {amount} 💎 создана. Ожидайте перевода Stars (до 24ч).")

@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery): await query.answer(ok=True)

@dp.message(F.successful_payment)
async def payment_ok(message: Message):
    user = get_user(message.from_user.id)
    user["balance"] += 500
    await message.answer("✅ Оплата прошла! +500 Кристаллов.")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(game_loop())
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
