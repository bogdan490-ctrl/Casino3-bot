import asyncio
import random
import os
import sqlite3
import time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, PreCheckoutQuery, LabeledPrice, WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import uvicorn

BOT_TOKEN = "9280034478:AAHgmjytU79yLOsnh7HnksSOrlzBZ2rXNgA"
ADMIN_ID = 8491030557
WEB_APP_URL = "https://casino-bot-ojim.onrender.com"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
app = FastAPI()

# ===== БАЗА =====
DB_PATH = "casino.db"
def init_db():
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (uid TEXT PRIMARY KEY, balance REAL, last_bonus INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS promos (code TEXT PRIMARY KEY, amount REAL)''')
    # Таблица инвентаря (для NFT-подарков)
    c.execute('''CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, item_name TEXT, item_price REAL, date TEXT)''')
    conn.commit(); conn.close()
init_db()

def get_balance(uid):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE uid=?", (str(uid),))
    row = c.fetchone()
    if not row:
        c.execute("INSERT INTO users (uid, balance) VALUES (?, ?)", (str(uid), 0.10))
        conn.commit(); balance = 0.10
    else: balance = row[0]
    conn.close(); return balance

def update_balance(uid, amount):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("UPDATE users SET balance = balance + ? WHERE uid=?", (round(amount, 2), str(uid)))
    conn.commit(); conn.close()

def add_inventory(uid, name, price):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("INSERT INTO inventory (uid, item_name, item_price, date) VALUES (?, ?, ?, ?)", (str(uid), name, price, str(time.time())))
    conn.commit(); conn.close()

def get_inventory(uid):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT id, item_name, item_price FROM inventory WHERE uid=?", (str(uid),))
    rows = c.fetchall(); conn.close()
    return rows

# ===== API =====
class UserAction(BaseModel):
    user_id: str
    action: str
    code: str = ""
    case_id: str = ""
    amount: float = 0.0
    item_id: int = -1

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
            return {"success": False, "msg": "❌ Неверный промокод"}

    elif req.action == "withdraw":
        balance = get_balance(req.user_id)
        if balance >= req.amount >= 10.0: # Порог вывода снижен до 10 алмазов
            update_balance(req.user_id, -req.amount)
            stars = int(req.amount * 10) # 1 алмаз = 10 звезд
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"appr_{req.user_id}_{req.amount}_{stars}"),
                 InlineKeyboardButton(text="❌ Отклонить", callback_data=f"decl_{req.user_id}_{req.amount}")]
            ])
            await bot.send_message(ADMIN_ID, f"💸 <b>ЗАЯВКА НА ВЫВОД</b>\nID: <code>{req.user_id}</code>\nСумма: {req.amount} 💎\nВыдать: {stars} ⭐", reply_markup=kb)
            return {"success": True, "msg": f"✅ Заявка на {req.amount} 💎 создана. Ожидайте."}
        return {"success": False, "msg": "❌ Минимум 10 💎"}

    elif req.action == "open_case":
        # ЦЕНЫ КЕЙСОВ
        prices = {"bronze": 2.0, "silver": 5.0, "gold": 10.0, "diamond": 20.0}
        price = prices.get(req.case_id, 2.0)
        balance = get_balance(req.user_id)
        if balance < price: return {"success": False, "msg": "Недостаточно средств"}
        
        update_balance(req.user_id, -price)
        
        # СПИСОК NFT-ПОДАРКОВ И ИХ ШАНСЫ
        # Чем выше цена кейса, тем больше шанс на дорогой подарок, но базовая логика одинакова
        choices = [
            {"name": "🧸 Мишка", "price": 1.0, "weight": 40},  # Обычный
            {"name": "🌹 Роза", "price": 3.0, "weight": 25},   # Редкий
            {"name": "🍰 Торт", "price": 5.0, "weight": 15},   # Эпический
            {"name": "💎 Кристалл", "price": 15.0, "weight": 10}, # Легендарный
            {"name": "🚀 Ракета", "price": 50.0, "weight": 5}, # Мифический
            {"name": "👑 Корона", "price": 100.0, "weight": 2} # Эксклюзив
        ]
        
        # Если открывают дорогой кейс, повышаем шанс на дорогие предметы
        if req.case_id == "gold":
            for c in choices: 
                if c["price"] > 5.0: c["weight"] *= 2
        elif req.case_id == "diamond":
            for c in choices: 
                if c["price"] > 5.0: c["weight"] *= 4

        total_weight = sum(c["weight"] for c in choices)
        r = random.uniform(0, total_weight)
        upto = 0
        item = choices[0]
        for c in choices:
            if upto + c["weight"] >= r:
                item = c
                break
            upto += c["weight"]
            
        add_inventory(req.user_id, item["name"], item["price"])
        return {"success": True, "item": item["name"], "price": item["price"], "new_balance": get_balance(req.user_id)}

    elif req.action == "sell_item":
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT item_name, item_price FROM inventory WHERE id=? AND uid=?", (req.item_id, str(req.user_id)))
        row = c.fetchone()
        if row:
            name, price = row
            sell_price = round(price * 0.8, 2) # Продажа за 80%
            c.execute("DELETE FROM inventory WHERE id=?", (req.item_id,))
            conn.commit(); conn.close()
            update_balance(req.user_id, sell_price)
            return {"success": True, "msg": f"✅ Продано: {name} за {sell_price} 💎", "new_balance": get_balance(req.user_id)}
        conn.close()
        return {"success": False, "msg": "Ошибка инвентаря"}

    elif req.action == "get_inv":
        inv = get_inventory(req.user_id)
        return {"success": True, "inv": [{"id": i[0], "name": i[1], "price": i[2]} for i in inv]}

    elif req.action == "buy":
        stars = int(req.amount * 100) # 1.00 алмаз = 100 звезд
        try:
            await bot.send_invoice(
                chat_id=req.user_id, title=f"{req.amount} Алмазов", description="Покупка валюты", 
                payload=f"buy_{req.amount}", currency="XTR", prices=[LabeledPrice(label="Алмазы", amount=stars)]
            )
            return {"success": True}
        except Exception as e:
            return {"success": False, "msg": str(e)}

@app.get("/api/balance/{user_id}")
def api_balance(user_id: str): return {"balance": get_balance(user_id)}

@dp.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery): await query.answer(ok=True)

@dp.message(F.successful_payment)
async def payment_ok(message: Message):
    payload = message.successful_payment.invoice_payload
    if payload.startswith("buy_"):
        amount = float(payload.split("_")[1])
        update_balance(message.from_user.id, amount)
        await message.answer(f"✅ Оплата прошла! +{amount} Алмазов.")

# ===== РАКЕТКА =====
active_bets, player_names = {}, {}
crash_mult, crash_running = 1.0, False
clients, game_state = set(), "waiting"

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
            crash_mult += 0.015 * crash_mult
            if crash_mult > 1.5:
                crash_chance = 0.01 * (crash_mult ** 0.5) 
                if random.random() < crash_chance: crash_running = False
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
                amt = float(data.get("amount", 0))
                player_names[user_id] = data.get("name", "Игрок")
                if game_state != "waiting": await websocket.send_json({"type": "error", "msg": "Ставки закрыты!"}); continue
                if amt < 0.10: await websocket.send_json({"type": "error", "msg": "Мин. ставка 0.10"}); continue
                if get_balance(user_id) >= amt:
                    update_balance(user_id, -amt); active_bets[user_id] = amt
                    await broadcast({"type": "waiting", "countdown": "...", "players": active_bets, "names": player_names})
                else: await websocket.send_json({"type": "error", "msg": "Нет денег"})
            elif data.get("action") == "cashout":
                if game_state == "flying" and user_id in active_bets:
                    bet = active_bets.pop(user_id); win = round(bet * crash_mult, 2)
                    update_balance(user_id, win); await websocket.send_json({"type": "cashed_out", "win": win})
    except WebSocketDisconnect: clients.remove(websocket)

# ===== БОТ =====
@dp.message(CommandStart())
async def cmd_start(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Играть", web_app=WebAppInfo(url=WEB_APP_URL))],
        [InlineKeyboardButton(text="🆘 Поддержка", url=f"tg://user?id={ADMIN_ID}")]
    ])
    await message.answer("Добро пожаловать в Legendstars!", reply_markup=kb)

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Выдать себе 100", callback_data="admin_give_self")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")]
    ])
    await message.answer("👑 <b>Админ-панель</b>", reply_markup=kb)

@dp.callback_query(F.data == "admin_give_self")
async def admin_give_self(cb: CallbackQuery):
    update_balance(ADMIN_ID, 100.0)
    await cb.message.answer(f"✅ Выдано 100 💎. Баланс: {get_balance(ADMIN_ID)}")
    await cb.answer()

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(cb: CallbackQuery):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT COUNT(*), SUM(balance) FROM users")
    row = c.fetchone(); conn.close()
    await cb.message.answer(f"👥 Игроков: {row[0]}\n💰 Всего: {round(row[1] or 0, 2)} 💎")
    await cb.answer()

@dp.message(Command("give"))
async def cmd_give(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        parts = message.text.split()
        uid = int(parts[1]); amt = float(parts[2])
        update_balance(uid, amt)
        await message.answer(f"✅ Выдано {amt} 💎 игроку {uid}. Баланс: {get_balance(uid)}")
    except: await message.answer("❌ Формат: /give ID СУММА")

@app.get("/", response_class=HTMLResponse)
def read_index():
    with open("index.html", "r", encoding="utf-8") as f: return f.read()

@app.on_event("startup")
async def startup():
    asyncio.create_task(game_loop()); asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
