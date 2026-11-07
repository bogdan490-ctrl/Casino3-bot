import asyncio
import random
import os
import json
import sqlite3
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, PreCheckoutQuery, LabeledPrice, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ContentType
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import uvicorn

# ===== НАСТРОЙКИ =====
BOT_TOKEN = "9280034478:AAHgmjytU79yLOsnh7HnksSOrlzBZ2rXNgA"
ADMIN_ID = 8491030557
WEB_APP_URL = "https://casino-bot-ojim.onrender.com"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()

# ===== БАЗА ДАННЫХ =====
DB_PATH = "casino.db"
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (uid TEXT PRIMARY KEY, balance INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS promos (code TEXT PRIMARY KEY, amount INTEGER)''')
    conn.commit()
    conn.close()

init_db()

def get_user(uid):
    uid = str(uid)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE uid=?", (uid,))
    row = c.fetchone()
    if not row:
        c.execute("INSERT INTO users (uid, balance) VALUES (?, ?)", (uid, 100))
        conn.commit()
        balance = 100
    else:
        balance = row[0]
    conn.close()
    return {"balance": balance}

def update_balance(uid, amount):
    uid = str(uid)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET balance = balance + ? WHERE uid=?", (amount, uid))
    conn.commit()
    conn.close()

class AdminStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_amount = State()

# ===== API =====
@app.get("/api/balance/{user_id}")
def api_balance(user_id: str): return get_user(user_id)

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
    
    update_balance(req.user_id, -price)
    item = random.choice(items[req.case_id])
    return {"item": item, "balance": user["balance"] - price}

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
        
        if not active_bets: continue
        
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
                    update_balance(user_id, -amount)
                    active_bets[user_id] = amount
                else: await websocket.send_json({"type": "error", "msg": "Недостаточно средств"})
            elif data.get("action") == "cashout":
                if game_state == "flying" and user_id in active_bets:
                    bet = active_bets.pop(user_id)
                    win = int(bet * crash_multiplier)
                    update_balance(user_id, win)
                    await websocket.send_json({"type": "cashed_out", "win": win})
    except WebSocketDisconnect:
        connected_clients.remove(websocket)

# ===== БОТ =====
@dp.message(CommandStart())
async def cmd_start(message: Message):
    args = message.text.split()
    user_id = str(message.from_user.id)
    if len(args) > 1 and args[1].startswith("ref"):
        referrer_id = args[1].replace("ref", "")
        if referrer_id != user_id:
            update_balance(referrer_id, 5)
            try: await bot.send_message(referrer_id, "🎉 По твоей ссылке зашел друг! +5 💎")
            except: pass

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Открыть казино", web_app=WebAppInfo(url=WEB_APP_URL))],
        [InlineKeyboardButton(text="🆘 Поддержка", url=f"tg://user?id={ADMIN_ID}")]
    ])
    await message.answer(f"Привет! Добро пожаловать в Legendstars.", reply_markup=kb)

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Выдать себе", callback_data="admin_give_self")],
        [InlineKeyboardButton(text="🎁 Выдать игроку", callback_data="admin_give_user")],
    ])
    await message.answer("👑 <b>Админ-панель</b>", reply_markup=kb)

@dp.message(Command("setpromo"))
async def cmd_setpromo(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        parts = message.text.split()
        code = parts[1].upper()
        amount = int(parts[2])
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO promos (code, amount) VALUES (?, ?)", (code, amount))
        conn.commit()
        conn.close()
        await message.answer(f"✅ Промокод <code>{code}</code> на <b>{amount} 💎</b> создан!")
    except:
        await message.answer("❌ Формат: /setpromo КОД СУММА")

@dp.callback_query(F.data == "admin_give_self")
async def admin_give_self(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.update_data(target_id=ADMIN_ID)
    await state.set_state(AdminStates.waiting_for_amount)
    await callback.message.edit_text("Введи сумму для себя:")

@dp.callback_query(F.data == "admin_give_user")
async def admin_give_user(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.set_state(AdminStates.waiting_for_user_id)
    await callback.message.edit_text("Введи ID игрока:")

@dp.message(AdminStates.waiting_for_user_id)
async def process_user_id(message: Message, state: FSMContext):
    try: uid = int(message.text.strip())
    except: return await message.answer("Неверный ID")
    await state.update_data(target_id=uid)
    await state.set_state(AdminStates.waiting_for_amount)
    await message.answer("Введи сумму:")

@dp.message(AdminStates.waiting_for_amount)
async def process_amount(message: Message, state: FSMContext):
    try: amount = int(message.text.strip())
    except: return await message.answer("Неверная сумма")
    data = await state.get_data()
    target_id = data.get("target_id")
    update_balance(target_id, amount)
    await message.answer(f"✅ Выдано {amount} 💎 игроку {target_id}.")
    await state.clear()

@dp.message(F.content_type == ContentType.WEB_APP_DATA)
async def web_app_data(message: Message):
    data = json.loads(message.web_app_data.data)
    
    if data["action"] == "buy_crystals":
        await bot.send_invoice(
            chat_id=message.from_user.id, title="500 Кристаллов", description="Покупка валюты", 
            payload="buy_500", currency="XTR", prices=[LabeledPrice(label="Кристаллы", amount=250)], provider_token="" 
        )
    
    elif data["action"] == "promo":
        code = data["code"].upper()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT amount FROM promos WHERE code=?", (code,))
        row = c.fetchone()
        if row:
            amount = row[0]
            update_balance(message.from_user.id, amount)
            c.execute("DELETE FROM promos WHERE code=?", (code,))
            conn.commit()
            conn.close()
            await message.answer(f"✅ Промокод активирован! +{amount} 💎")
        else:
            conn.close()
            await message.answer("❌ Неверный или использованный промокод")
            
    elif data["action"] == "withdraw":
        amount = data["amount"]
        if amount >= 1000:
            stars_to_give = int(amount / 100) # Твой курс 1000 = 10 звезд
            await bot.send_message(ADMIN_ID, f"💸 <b>ЗАЯВКА НА ВЫВОД</b>\nЮзер: {message.from_user.id}\nСумма: {amount} 💎\n\n<b>Переведи ему {stars_to_give} ⭐</b>")
            await message.answer(f"✅ Заявка на вывод {amount} 💎 создана. Ожидайте перевода {stars_to_give} ⭐.")
        else:
            await message.answer("❌ Минимальная сумма вывода: 1000 💎")

@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery): await query.answer(ok=True)

@dp.message(F.successful_payment)
async def payment_ok(message: Message):
    update_balance(message.from_user.id, 500)
    await message.answer("✅ Оплата прошла! +500 Кристаллов.")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(game_loop())
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
