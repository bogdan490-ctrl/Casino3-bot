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

# ===== НАСТРОЙКИ =====
BOT_TOKEN = "9280034478:AAHgmjytU79yLOsnh7HnksSOrlzBZ2rXNgA" # <--- ВСТАВЬ ТОКЕН
ADMIN_ID = 8491030557 # <--- ВСТАВЬ СВОЙ ID
WEB_APP_URL = "https://casino-bot-ojim.onrender.com" # <--- ТВОЯ ССЫЛКА

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

# ===== СОСТОЯНИЯ =====
class AdminStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_amount = State()

# ===== API ДЛЯ КЕЙСОВ =====
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
    items = {"bronze": ["🗑 Мусор", "🥉 Бронза", "🥈 Серебро"], "silver": ["🥈 Серебро", "🥇 Золото", "💎 Алмаз"], "gold": ["🥇 Золото", "💎 Алмаз", "👑 Корона"]}
    
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

# ===== WEBSOCKET ДЛЯ РАКЕТКИ =====
active_bets = {} # user_id: bet_amount
crash_multiplier = 1.0
crash_running = False
connected_clients = set()

async def game_loop():
    global crash_multiplier, crash_running, active_bets
    
    while True:
        # Ожидание ставок (10 секунд)
        crash_multiplier = 1.0
        crash_running = False
        active_bets.clear()
            
        await asyncio.sleep(10) 
        
        if not active_bets: continue # Если никто не поставил, пропускаем раунд

        # Старт ракеты
        crash_running = True
        crash_multiplier = 1.0
        for client in connected_clients.copy():
            try: await client.send_json({"type": "start"})
            except: pass
            
        # Летим вверх
        while crash_running:
            await asyncio.sleep(0.1)
            crash_multiplier += random.uniform(0.01, 0.08)
            
            # Шанс упасть (чем выше, тем больше шанс)
            if random.random() < (0.01 * crash_multiplier):
                crash_running = False
                
            for client in connected_clients.copy():
                try: await client.send_json({"type": "tick", "multiplier": crash_multiplier})
                except: pass

        # КРАШ
        for client in connected_clients.copy():
            try: await client.send_json({"type": "crash", "multiplier": crash_multiplier})
            except: pass
        
        await asyncio.sleep(3) # Пауза после краша

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
                
                if crash_running: 
                    await websocket.send_json({"type": "error", "msg": "Ракета уже летит!"})
                    continue
                    
                if user["balance"] >= amount:
                    user["balance"] -= amount
                    active_bets[user_id] = amount
                    await websocket.send_json({"type": "bet_ok"})
                else:
                    await websocket.send_json({"type": "error", "msg": "Недостаточно средств"})
            
            elif data.get("action") == "cashout":
                if crash_running and user_id in active_bets:
                    bet = active_bets.pop(user_id)
                    win = int(bet * crash_multiplier)
                    get_user(user_id)["balance"] += win
                    await websocket.send_json({"type": "cashed_out", "win": win})
                    
    except WebSocketDisconnect:
        connected_clients.remove(websocket)

# ===== БОТ =====
@dp.message(CommandStart())
async def cmd_start(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎰 Открыть Казино", web_app=WebAppInfo(url=WEB_APP_URL))]
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
    user = get_user(target_id)
    user["balance"] += amount
    await message.answer(f"✅ Выдано {amount} 💎 игроку {target_id}.")
    await state.clear()

@dp.message(F.web_app_data)
async def web_app_data(message: Message):
    if "buy_crystals" in message.web_app_data.data:
        await bot.send_invoice(message.from_user.id, "500 Кристаллов", "Покупка", "buy_500", "XTR", [LabeledPrice(label="Кристаллы", amount=250)])

@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery): await query.answer(ok=True)

@dp.message(F.successful_payment)
async def payment_ok(message: Message):
    user = get_user(message.from_user.id)
    user["balance"] += 500
    await message.answer("✅ Оплата прошла! +500 Кристаллов.")

# ===== ЗАПУСК =====
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(game_loop())
    asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
