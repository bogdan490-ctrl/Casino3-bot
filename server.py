import asyncio
import random
import os
import json
import sqlite3
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, PreCheckoutQuery, LabeledPrice, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ContentType
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

# ===== БАЗА =====
DB_PATH = "casino.db"
def init_db():
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (uid TEXT PRIMARY KEY, balance INTEGER, last_bonus INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS promos (code TEXT PRIMARY KEY, amount INTEGER)''')
    conn.commit(); conn.close()
init_db()

def get_balance(uid):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE uid=?", (str(uid),))
    row = c.fetchone()
    if not row:
        c.execute("INSERT INTO users (uid, balance) VALUES (?, ?)", (str(uid), 100))
        conn.commit(); balance = 100
    else: balance = row[0]
    conn.close(); return balance

def update_balance(uid, amount):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("UPDATE users SET balance = balance + ? WHERE uid=?", (amount, str(uid)))
    conn.commit(); conn.close()

def check_bonus(uid):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT last_bonus FROM users WHERE uid=?", (str(uid),))
    row = c.fetchone()
    now = int(time.time())
    if not row or (now - row[0]) > 86400:
        c.execute("UPDATE users SET last_bonus=? WHERE uid=?", (now, str(uid)))
        conn.commit(); conn.close()
        return True
    conn.close(); return False

# ===== API =====
class UserAction(BaseModel):
    user_id: str
    action: str
    code: str = ""
    case_id: str = ""

@app.post("/api/action")
async def api_action(req: UserAction):
    if req.action == "promo":
        code = req.code.upper()
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT amount FROM promos WHERE code=?", (code,))
        row = c.fetchone()
        if row:
            amount = row[0]
            update_balance(req.user_id, amount)
            c.execute("DELETE FROM promos WHERE code=?", (code,))
            conn.commit(); conn.close()
            return {"success": True, "msg": f"✅ Промокод активирован! +{amount} 💎"}
        else:
            conn.close()
            return {"success": False, "msg": "❌ Неверный или использованный промокод"}
            
    elif req.action == "bonus":
        if check_bonus(req.user_id):
            update_balance(req.user_id, 10)
            return {"success": True, "msg": "🎁 Ежедневный бонус: +10 💎"}
        return {"success": False, "msg": "❌ Бонус получен. Жди 24 часа."}

    elif req.action == "withdraw":
        balance = get_balance(req.user_id)
        if balance >= 1000:
            await bot.send_message(ADMIN_ID, f"💸 <b>ЗАЯВКА НА ВЫВОД</b>\nID: <code>{req.user_id}</code>\nСумма: 1000 💎\nВыдать: 100 ⭐")
            return {"success": True, "msg": f"✅ Заявка на вывод 1000 💎 создана. Ждите 100 ⭐"}
        return {"success": False, "msg": "❌ Минимум для вывода 1000 💎"}
        
    elif req.action == "open_case":
        prices = {"bronze": 50, "silver": 150, "gold": 500}
        items = {"bronze": ["🗑 Мусор", "🥉 Бронза", "🥈 Серебро"], "silver": ["🥈 Серебро", "🥇 Золото", "💎 Алмаз"], "gold": ["🥇 Золото", "💎 Алмаз", "👑 Корона"]}
        price = prices.get(req.case_id, 50)
        balance = get_balance(req.user_id)
        if balance < price: return {"success": False, "msg": "Недостаточно средств"}
        update_balance(req.user_id, -price)
        item = random.choice(items[req.case_id])
        return {"success": True, "item": item, "new_balance": balance - price}

    elif req.action == "spin":
        balance = get_balance(req.user_id)
        if balance < 50: return {"success": False, "msg": "Нужно 50 💎 для колеса"}
        update_balance(req.user_id, -50)
        win = random.choices([0, 10, 20, 50, 500], weights=[60, 20, 15, 4, 1], k=1)[0]
        update_balance(req.user_id, win)
        return {"success": True, "msg": f"🎡 Выпало: {win} 💎!", "new_balance": get_balance(req.user_id)}

@app.get("/api/balance/{user_id}")
def api_balance(user_id: str): return {"balance": get_balance(user_id)}

class BuyRequest(BaseModel):
    user_id: str
    amount: int

@app.post("/api/buy")
async def api_buy(req: BuyRequest):
    stars = req.amount // 5
    await bot.send_invoice(req.user_id, f"{req.amount} Кристаллов", "Покупка", f"buy_{req.amount}", "XTR", [LabeledPrice(label="Кристаллы", amount=stars)], provider_token="")
    return {"ok": True}

@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery): await query.answer(ok=True)

@dp.message(F.successful_payment)
async def payment_ok(message: Message):
    payload = message.successful_payment.invoice_payload
    if payload.startswith("buy_"):
        amount = int(payload.split("_")[1])
        update_balance(message.from_user.id, amount)
        await message.answer(f"✅ Оплата прошла! +{amount} Кристаллов.")

# ===== РАКЕТКА =====
active_bets = {}
player_names = {}
crash_mult = 1.0
crash_running = False
clients = set()
game_state = "waiting"

async def broadcast(msg):
    for c in clients.copy():
        try: await c.send_json(msg)
        except: pass

async def game_loop():
    global crash_mult, crash_running, active_bets, game_state
    while True:
        game_state = "waiting"; active_bets.clear(); crash_mult = 1.0
        for i in range(10, 0, -1):
            await broadcast({"type": "waiting", "countdown": i, "players": active_bets, "names": player_names})
            await asyncio.sleep(1)
        if not active_bets: continue
        game_state = "flying"; crash_running = True
        await broadcast({"type": "start"})
        while crash_running:
            await asyncio.sleep(0.1)
            crash_mult += 0.03 * crash_mult # Скорость
            if random.random() < 0.01 * crash_mult: crash_running = False
            await broadcast({"type": "tick", "multiplier": round(crash_mult, 2)})
        game_state = "crashed"
        await broadcast({"type": "crash", "multiplier": round(crash_mult, 2)})
        await asyncio.sleep(3)

@app.websocket("/ws/{user_id}")
async def ws_endpoint(websocket: WebSocket, user_id: str):
    await websocket.accept(); clients.add(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("action") == "bet":
                amt = data.get("amount", 0)
                name = data.get("name", "Игрок")
                player_names[user_id] = name
                if game_state != "waiting": await websocket.send_json({"type": "error", "msg": "Ставки закрыты"}); continue
                if get_balance(user_id) >= amt:
                    update_balance(user_id, -amt); active_bets[user_id] = amt
                    await broadcast({"type": "waiting", "countdown": "...", "players": active_bets, "names": player_names})
                else: await websocket.send_json({"type": "error", "msg": "Нет денег"})
            elif data.get("action") == "cashout":
                if game_state == "flying" and user_id in active_bets:
                    bet = active_bets.pop(user_id); win = int(bet * crash_mult)
                    update_balance(user_id, win); await websocket.send_json({"type": "cashed_out", "win": win})
    except WebSocketDisconnect: clients.remove(websocket)

# ===== БОТ =====
@dp.message(CommandStart())
async def cmd_start(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Играть", web_app=WebAppInfo(url=WEB_APP_URL))]])
    await message.answer("Добро пожаловать в Legendstars!", reply_markup=kb)

# ===== АДМИНКА =====
@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🎁 Выдать валюту", callback_data="admin_give")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")]
    ])
    await message.answer("👑 <b>Админ-панель</b>", reply_markup=kb)

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(cb: CallbackQuery):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT COUNT(*), SUM(balance) FROM users")
    row = c.fetchone(); conn.close()
    await cb.message.answer(f"👥 Игроков: {row[0]}\n💰 Всего алмазов: {row[1]}")
    await cb.answer()

@dp.callback_query(F.data == "admin_give")
async def admin_give(cb: CallbackQuery):
    await cb.message.answer("Напиши: <code>/give ID СУММА</code>")
    await cb.answer()

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(cb: CallbackQuery):
    await cb.message.answer("Напиши: <code>/broadcast ТЕКСТ</code>")
    await cb.answer()

@dp.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    if message.from_user.id != ADMIN_ID: return
    text = message.text.replace("/broadcast ", "")
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT uid FROM users"); uids = c.fetchall(); conn.close()
    count = 0
    for (uid,) in uids:
        try: await bot.send_message(uid, text); count += 1; await asyncio.sleep(0.05)
        except: pass
    await message.answer(f"✅ Отправлено {count} игрокам.")

@dp.message(Command("give"))
async def cmd_give(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        parts = message.text.split()
        update_balance(int(parts[1]), int(parts[2]))
        await message.answer(f"✅ Выдано {parts[2]} 💎 игроку {parts[1]}")
    except: await message.answer("❌ Формат: /give ID СУММА")

@dp.message(Command("setpromo"))
async def cmd_setpromo(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        parts = message.text.split()
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO promos (code, amount) VALUES (?, ?)", (parts[1].upper(), int(parts[2])))
        conn.commit(); conn.close()
        await message.answer(f"✅ Промокод {parts[1].upper()} на {parts[2]} 💎 создан!")
    except: await message.answer("❌ Формат: /setpromo КОД СУММА")

@app.get("/", response_class=HTMLResponse)
def read_index():
    with open("index.html", "r", encoding="utf-8") as f: return f.read()

@app.on_event("startup")
async def startup():
    asyncio.create_task(game_loop()); asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
