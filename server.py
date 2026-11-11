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
        c.execute("INSERT INTO users (uid, balance) VALUES (?, ?)", (str(uid), 1))
        conn.commit(); balance = 1
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
    amount: int = 0

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
            
    elif req.action == "bonus":
        if check_bonus(req.user_id):
            update_balance(req.user_id, 10)
            return {"success": True, "msg": "🎁 Ежедневный бонус: +10 💎"}
        return {"success": False, "msg": "❌ Бонус получен. Жди 24 часа."}

    elif req.action == "withdraw":
        balance = get_balance(req.user_id)
        if balance >= req.amount >= 1000:
            update_balance(req.user_id, -req.amount)
            stars = req.amount // 10
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"appr_{req.user_id}_{req.amount}_{stars}"),
                 InlineKeyboardButton(text="❌ Отклонить", callback_data=f"decl_{req.user_id}_{req.amount}")]
            ])
            await bot.send_message(ADMIN_ID, f"💸 <b>ЗАЯВКА НА ВЫВОД</b>\nID: <code>{req.user_id}</code>\nСумма: {req.amount} 💎\nВыдать: {stars} ⭐", reply_markup=kb)
            return {"success": True, "msg": f"✅ Заявка на {req.amount} 💎 создана. Ожидайте."}
        return {"success": False, "msg": "❌ Минимум 1000 💎"}
        
    elif req.action == "open_case":
        prices = {"bronze": 50, "silver": 150, "gold": 500}
        price = prices.get(req.case_id, 50)
        balance = get_balance(req.user_id)
        if balance < price: return {"success": False, "msg": "Недостаточно средств"}
        
        update_balance(req.user_id, -price)
        choices = ["🗑 Мусор", "🥉 Бронза", "🥈 Серебро", "🥇 Золото", "💎 Алмаз", "👑 Корона", "💰 +10 💎", "💰 +50 💎", "💰 +100 💎"]
        weights = [40, 25, 15, 8, 5, 2, 3, 1.5, 0.5]
        item = random.choices(choices, weights=weights, k=1)[0]
        
        if "+10" in item: update_balance(req.user_id, 10)
        elif "+50" in item: update_balance(req.user_id, 50)
        elif "+100" in item: update_balance(req.user_id, 100)
        
        return {"success": True, "item": item, "new_balance": get_balance(req.user_id)}

    elif req.action == "spin":
        balance = get_balance(req.user_id)
        if balance < 50: return {"success": False, "msg": "Нужно 50 💎"}
        update_balance(req.user_id, -50)
        win = random.choices([0, 10, 20, 50, 500], weights=[60, 20, 15, 4, 1], k=1)[0]
        update_balance(req.user_id, win)
        return {"success": True, "win": win, "new_balance": get_balance(req.user_id)}

    elif req.action == "buy":
        stars = req.amount // 5
        try:
            # Убрали provider_token, чтобы Telegram понял, что это Stars
            await bot.send_invoice(
                chat_id=req.user_id, 
                title=f"{req.amount} Кристаллов", 
                description="Покупка валюты", 
                payload=f"buy_{req.amount}", 
                currency="XTR", 
                prices=[LabeledPrice(label="Кристаллы", amount=stars)]
            )
            return {"success": True}
        except Exception as e:
            print(f"ОШИБКА ОПЛАТЫ: {e}")
            return {"success": False, "msg": str(e)}
        
    elif req.action == "top":
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT uid, balance FROM users ORDER BY balance DESC LIMIT 10")
        top = c.fetchall(); conn.close()
        return {"success": True, "top": [{"id": u, "bal": b} for u, b in top]}

@dp.callback_query(F.data.startswith("appr_"))
async def admin_approve(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    _, uid, amount, stars = cb.data.split("_")
    await bot.send_message(uid, f"✅ Вывод подтвержден! Вам начислено {stars} ⭐")
    await cb.message.edit_text(f"✅ Выплата {amount} 💎 (выдано {stars}⭐) подтверждена.")

@dp.callback_query(F.data.startswith("decl_"))
async def admin_decline(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
    _, uid, amount = cb.data.split("_")
    update_balance(uid, int(amount))
    await bot.send_message(uid, f"❌ Вывод отклонен. Алмазы возвращены на баланс.")
    await cb.message.edit_text(f"❌ Выплата {amount} 💎 отклонена. Алмазы возвращены.")

@app.get("/api/balance/{user_id}")
def api_balance(user_id: str): return {"balance": get_balance(user_id)}

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
active_bets, player_names = {}, {}
crash_mult, crash_running = 1.0, False
clients, game_state = set(), "waiting"
rig_next_round = False

async def broadcast(msg):
    for c in clients.copy():
        try: await c.send_json(msg)
        except: pass

async def game_loop():
    global crash_mult, crash_running, active_bets, game_state, rig_next_round
    while True:
        game_state = "waiting"; active_bets.clear(); crash_mult = 1.0
        for i in range(10, 0, -1):
            await broadcast({"type": "waiting", "countdown": i, "players": active_bets, "names": player_names})
            await asyncio.sleep(1)
        if not active_bets: continue
        
        game_state = "flying"; crash_running = True
        await broadcast({"type": "start"})
        is_rigged = rig_next_round
        if is_rigged: rig_next_round = False

        while crash_running:
            await asyncio.sleep(0.1)
            crash_mult += 0.015 * crash_mult # Скорость
            
            if is_rigged:
                if crash_mult >= 100.0: crash_running = False
            else:
                if crash_mult > 10.0:
                    if random.random() < 0.15: crash_running = False # Шанс на высоких
                else:
                    if random.random() < 0.02 * crash_mult: crash_running = False # Шанс на низких
                    
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
                player_names[user_id] = data.get("name", "Игрок")
                if game_state != "waiting": 
                    await websocket.send_json({"type": "error", "msg": "Ставки закрыты!"})
                    continue
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
    args = message.text.split()
    user_id = str(message.from_user.id)
    if len(args) > 1 and args[1].startswith("ref"):
        referrer_id = args[1].replace("ref", "")
        if referrer_id != user_id:
            update_balance(referrer_id, 5)
            try: await bot.send_message(referrer_id, "🎉 По твоей ссылке зашел друг! +5 💎")
            except: pass

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚀 Играть", web_app=WebAppInfo(url=WEB_APP_URL))], [InlineKeyboardButton(text="🆘 Поддержка", url=f"tg://user?id={ADMIN_ID}")]])
    await message.answer("Добро пожаловать в Legendstars!", reply_markup=kb)

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💰 Выдать себе 10к", callback_data="admin_give_self")],
        [InlineKeyboardButton(text="🎰 Подкрутить ракетку (x100)", callback_data="admin_rig")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")]
    ])
    await message.answer("👑 <b>Админ-панель</b>", reply_markup=kb)

@dp.callback_query(F.data == "admin_give_self")
async def admin_give_self(cb: CallbackQuery):
    update_balance(ADMIN_ID, 10000)
    await cb.message.answer(f"✅ Выдано 10 000 💎. Твой баланс: {get_balance(ADMIN_ID)}")
    await cb.answer()

@dp.callback_query(F.data == "admin_rig")
async def admin_rig(cb: CallbackQuery):
    global rig_next_round
    rig_next_round = True
    await cb.message.answer("🎰 <b>Ракетка подкручена!</b>\nСледующий раунд долетит до x100.")
    await cb.answer()

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(cb: CallbackQuery):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT COUNT(*), SUM(balance) FROM users")
    row = c.fetchone(); conn.close()
    await cb.message.answer(f"👥 Игроков: {row[0]}\n💰 Всего: {row[1]} 💎")
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

@dp.message(Command("setpromo"))
async def cmd_setpromo(message: Message):
    if message.from_user.id != ADMIN_ID: return
    try:
        parts = message.text.split()
        if len(parts) < 3:
            await message.answer("❌ Формат: /setpromo КОД СУММА")
            return
        code = parts[1].upper(); amount = int(parts[2])
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO promos (code, amount) VALUES (?, ?)", (code, amount))
        conn.commit(); conn.close()
        await message.answer(f"✅ Промокод <code>{code}</code> на <b>{amount} 💎</b> создан!")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")

@app.get("/", response_class=HTMLResponse)
def read_index():
    with open("index.html", "r", encoding="utf-8") as f: return f.read()

@app.on_event("startup")
async def startup():
    asyncio.create_task(game_loop()); asyncio.create_task(dp.start_polling(bot))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
