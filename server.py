import asyncio
import random
import os
import sqlite3
import time
import json
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
BOT_TOKEN = "9280034478:AAHgmjytU79yLOsnh7HnksSOrlzBZ2rXNgA"
ADMIN_ID = 8491030557
WEB_APP_URL = "https://casino-bot-ojim.onrender.com"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()

class SupportState(StatesGroup):
    waiting_for_msg = State()

# ===== БАЗА =====
DB_PATH = "casino.db"
def init_db():
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (uid TEXT PRIMARY KEY, balance REAL, last_bonus INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS promos (code TEXT PRIMARY KEY, amount REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, item_name TEXT, item_price REAL, date TEXT)''')
    conn.commit(); conn.close()
init_db()

def get_user(uid):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT balance, last_bonus FROM users WHERE uid=?", (str(uid),))
    row = c.fetchone()
    if not row:
        c.execute("INSERT INTO users (uid, balance) VALUES (?, ?)", (str(uid), 0.10))
        conn.commit(); balance = 0.10; last_bonus = 0
    else: balance, last_bonus = row
    conn.close(); return {"balance": balance, "last_bonus": last_bonus}

def update_balance(uid, amount):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("UPDATE users SET balance = balance + ? WHERE uid=?", (round(amount, 2), str(uid)))
    conn.commit(); conn.close()

def add_inv(uid, name, price):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("INSERT INTO inventory (uid, item_name, item_price, date) VALUES (?, ?, ?, ?)", (str(uid), name, price, str(time.time())))
    conn.commit(); conn.close()

def get_inv(uid):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT id, item_name, item_price FROM inventory WHERE uid=?", (str(uid),))
    rows = c.fetchall(); conn.close(); return rows

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
        conn.close()
        return {"success": False, "msg": "❌ Неверный промокод"}

    elif req.action == "daily_case":
        user = get_user(req.user_id)
        now = int(time.time())
        if now - user["last_bonus"] > 86400:
            conn = sqlite3.connect(DB_PATH); c = conn.cursor()
            c.execute("UPDATE users SET last_bonus=? WHERE uid=?", (now, str(req.user_id)))
            conn.commit(); conn.close()
            
            if random.random() < 0.01:
                item = {"name": "🚀 Ракета", "price": 50.0}
            else:
                item = {"name": "🧸 Мишка", "price": 1.0}
            add_inv(req.user_id, item["name"], item["price"])
            return {"success": True, "item": item["name"], "price": item["price"], "msg": f"🎁 Выпало: {item['name']}"}
        return {"success": False, "msg": "❌ Приходи завтра!"}

    elif req.action == "withdraw":
        balance = get_user(req.user_id)["balance"]
        if balance >= req.amount >= 10.0:
            update_balance(req.user_id, -req.amount)
            stars = int(req.amount * 10) 
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"appr_{req.user_id}_{req.amount}_{stars}"),
                 InlineKeyboardButton(text="❌ Отклонить", callback_data=f"decl_{req.user_id}_{req.amount}")]
            ])
            await bot.send_message(ADMIN_ID, f"💸 <b>ЗАЯВКА НА ВЫВОД</b>\nID: <code>{req.user_id}</code>\nСумма: {req.amount} 💎\nВыдать: {stars} ⭐", reply_markup=kb)
            return {"success": True, "msg": f"✅ Заявка на {req.amount} 💎 создана. Ожидайте."}
        return {"success": False, "msg": "❌ Минимум 10 💎"}

    elif req.action == "open_case":
        prices = {"bronze": 2.0, "silver": 5.0, "gold": 10.0, "diamond": 20.0}
        price = prices.get(req.case_id, 2.0)
        balance = get_user(req.user_id)["balance"]
        if balance < price: return {"success": False, "msg": "Недостаточно средств"}
        
        update_balance(req.user_id, -price)
        
        # Сбалансированные шансы
        choices = [
            {"name": "🧸 Мишка", "price": 1.0, "weight": 55},
            {"name": "🌹 Роза", "price": 3.0, "weight": 25},
            {"name": "🍰 Торт", "price": 5.0, "weight": 12},
            {"name": "💎 Кристалл", "price": 15.0, "weight": 6},
            {"name": "🚀 Ракета", "price": 50.0, "weight": 1.5},
            {"name": "👑 Корона", "price": 100.0, "weight": 0.5} 
        ]
        
        total_weight = sum(c["weight"] for c in choices)
        r = random.uniform(0, total_weight)
        upto = 0
        item = choices[0]
        for c in choices:
            if upto + c["weight"] >= r:
                item = c; break
            upto += c["weight"]
            
        add_inv(req.user_id, item["name"], item["price"])
        return {"success": True, "item": item["name"], "price": item["price"], "new_balance": get_user(req.user_id)["balance"]}

    elif req.action == "sell_item":
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT item_name, item_price FROM inventory WHERE id=? AND uid=?", (req.item_id, str(req.user_id)))
        row = c.fetchone()
        if row:
            name, price = row
            sell_price = round(price * 0.8, 2)
            c.execute("DELETE FROM inventory WHERE id=?", (req.item_id,))
            conn.commit(); conn.close()
            update_balance(req.user_id, sell_price)
            return {"success": True, "msg": f"✅ Продано: {name} за {sell_price} 💎", "new_balance": get_user(req.user_id)["balance"]}
        conn.close()
        return {"success": False, "msg": "Ошибка"}

    elif req.action == "sell_all":
        inv = get_inv(req.user_id)
        total_sell = 0
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        for i in inv:
            total_sell += i[2] * 0.8
            c.execute("DELETE FROM inventory WHERE id=?", (i[0],))
        conn.commit(); conn.close()
        update_balance(req.user_id, total_sell)
        return {"success": True, "msg": f"✅ Продано всё за {round(total_sell, 2)} 💎", "new_balance": get_user(req.user_id)["balance"]}

    elif req.action == "get_inv":
        inv = get_inv(req.user_id)
        return {"success": True, "inv": [{"id": i[0], "name": i[1], "price": i[2]} for i in inv]}

    elif req.action == "buy":
        stars = int(req.amount * 100) 
        try:
            await bot.send_invoice(
                chat_id=req.user_id, title=f"{req.amount} Алмазов", description="Покупка валюты", 
                payload=f"buy_{req.amount}", currency="XTR", prices=[LabeledPrice(label="Алмазы", amount=stars)]
            )
            return {"success": True}
        except Exception as e:
            return {"success": False, "msg": str(e)}

    elif req.action == "top":
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT uid, balance FROM users ORDER BY balance DESC LIMIT 10")
        top = c.fetchall(); conn.close()
        return {"success": True, "top": [{"id": u, "bal": b} for u, b in top]}

@app.get("/api/balance/{user_id}")
def api_balance(user_id: str): return {"balance": get_user(user_id)["balance"]}

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
                if get_user(user_id)["balance"] >= amt:
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
    args = message.text.split()
    user_id = str(message.from_user.id)
    if len(args) > 1 and args[1].startswith("ref"):
        ref_id = args[1].replace("ref", "")
        if ref_id != user_id:
            update_balance(ref_id, 1.0)
            try: await bot.send_message(ref_id, f"🎉 По твоей ссылке зашел друг! +1.00 💎")
            except: pass

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Играть", web_app=WebAppInfo(url=WEB_APP_URL))],
        [InlineKeyboardButton(text="🆘 Анонимная поддержка", callback_data="support")]
    ])
    await message.answer("Добро пожаловать в Legendstars!", reply_markup=kb)

@dp.callback_query(F.data == "support")
async def support_start(cb: CallbackQuery, state: FSMContext):
    await state.set_state(SupportState.waiting_for_msg)
    await cb.message.answer("Напиши свой вопрос. Я передам его админу анонимно.")

@dp.message(SupportState.waiting_for_msg)
async def support_send(message: Message, state: FSMContext):
    await state.clear()
    try:
        await bot.send_message(ADMIN_ID, f"📩 <b>Анонимное сообщение:</b>\n\n{message.text}\n\nОтвечать ему: /reply {message.from_user.id} текст")
        await message.answer("✅ Сообщение отправлено!")
    except: await message.answer("❌ Ошибка")

@dp.message(Command("reply"))
async def cmd_reply(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        parts = message.text.split(maxsplit=2)
        uid = int(parts[1]); text = parts[2]
        await bot.send_message(uid, f"💬 <b>Ответ поддержки:</b>\n\n{text}")
        await message.answer("✅ Отправлено")
    except: await message.answer("❌ Формат: /reply ID ТЕКСТ")

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
    await cb.message.answer(f"✅ Выдано 100 💎. Баланс: {get_user(ADMIN_ID)['balance']}")
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
        await message.answer(f"✅ Выдано {amt} 💎 игроку {uid}. Баланс: {get_user(uid)['balance']}")
    except: await message.answer("❌ Формат: /give ID СУММА")

@dp.message(Command("setpromo"))
async def cmd_setpromo(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        parts = message.text.split()
        code = parts[1].upper(); amount = float(parts[2])
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO promos (code, amount) VALUES (?, ?)", (code, amount))
        conn.commit(); conn.close()
        await message.answer(f"✅ Промокод {code} на {amount} 💎 создан!")
    except Exception as e: await message.answer(f"❌ Ошибка: {e}")

@dp.callback_query(F.data.startswith("appr_"))
async def admin_approve(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    try:
        _, uid, amount, stars = cb.data.split("_")
        await bot.send_message(uid, f"✅ Вывод подтвержден! Вам начислено {stars} ⭐")
        await cb.message.edit_text(f"✅ Выплата {amount} 💎 (выдано {stars}⭐) подтверждена.")
    except: await cb.answer("Ошибка")

@dp.callback_query(F.data.startswith("decl_"))
async def admin_decline(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    try:
        _, uid, amount = cb.data.split("_")
        update_balance(uid, float(amount))
        await bot.send_message(uid, f"❌ Вывод отклонен. Алмазы возвращены на баланс.")
        await cb.message.edit_text(f"❌ Выплата {amount} 💎 отклонена. Алмазы возвращены.")
    except: await cb.answer("Ошибка")

@app.get("/", response_class=HTMLResponse)
def read_index():
    with open("index.html", "r", encoding="utf-8") as f: return f.read()

@app.on_event("startup")
async def startup():
    asyncio.create_task(game_loop()); asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000))).
