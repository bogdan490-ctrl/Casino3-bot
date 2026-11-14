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
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан! Добавь в Environment Variables на Render.")

ADMIN_ID = 8491030557
WEB_APP_URL = "https://casino-bot-ojim.onrender.com"

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()

class SupportState(StatesGroup):
    waiting_for_msg = State()

# ===== БАЗА =====
DB_PATH = "casino.db"

# WAL mode — убивает лаги SQLite (параллельные чтения не блокируют запись)
def _init_wal():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.close()
_init_wal()

def init_db():
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (uid TEXT PRIMARY KEY, balance REAL, last_bonus INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS promos (code TEXT PRIMARY KEY, amount REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS inventory (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, item_name TEXT, item_price REAL, date TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS market (id INTEGER PRIMARY KEY AUTOINCREMENT, seller_id TEXT, item_name TEXT, item_price REAL, price REAL, status TEXT DEFAULT 'active', created_at INTEGER)''')
    # ДУЭЛИ ТЕПЕРЬ НА АЛМАЗЫ
    c.execute('''CREATE TABLE IF NOT EXISTS duels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        creator_id TEXT,
        stake REAL,
        opponent_id TEXT,
        status TEXT DEFAULT 'waiting',
        winner_id TEXT,
        created_at INTEGER
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS stats (uid TEXT PRIMARY KEY, total_bets INTEGER DEFAULT 0, total_won REAL DEFAULT 0, total_lost REAL DEFAULT 0, best_crash REAL DEFAULT 0, favorite_game TEXT DEFAULT 'none')''')
    c.execute('''CREATE TABLE IF NOT EXISTS feed (id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT, name TEXT, action TEXT, details TEXT, timestamp INTEGER)''')
    conn.commit(); conn.close()

init_db()

def get_user(uid):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT balance, last_bonus FROM users WHERE uid=?", (str(uid),))
    row = c.fetchone()
    if not row:
        c.execute("INSERT INTO users (uid, balance) VALUES (?, ?)", (str(uid), 0.00))
        conn.commit(); balance = 0.00; last_bonus = 0
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

def add_feed(uid, name, action, details):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("INSERT INTO feed (uid, name, action, details, timestamp) VALUES (?,?,?,?,?)",
              (str(uid), name, action, details, int(time.time())))
    c.execute("DELETE FROM feed WHERE id NOT IN (SELECT id FROM feed ORDER BY id DESC LIMIT 30)")
    conn.commit(); conn.close()

def get_stats(uid):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT total_bets, total_won, total_lost, best_crash, favorite_game FROM stats WHERE uid=?", (str(uid),))
    row = c.fetchone()
    if not row:
        c.execute("INSERT INTO stats (uid) VALUES (?)", (str(uid),))
        conn.commit(); conn.close()
        return {"total_bets": 0, "total_won": 0, "total_lost": 0, "best_crash": 0, "favorite_game": "none"}
    conn.close()
    return {"total_bets": row[0], "total_won": row[1], "total_lost": row[2], "best_crash": row[3], "favorite_game": row[4]}

def update_stat(uid, field, value, mode="set"):
    ALLOWED = {"total_bets", "total_won", "total_lost", "best_crash", "favorite_game"}
    if field not in ALLOWED: return
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO stats (uid) VALUES (?)", (str(uid),))
    if mode == "inc":
        c.execute(f"UPDATE stats SET {field} = {field} + ? WHERE uid=?", (value, str(uid)))
    elif mode == "max":
        c.execute(f"UPDATE stats SET {field} = MAX({field}, ?) WHERE uid=?", (value, str(uid)))
    else:
        c.execute(f"UPDATE stats SET {field} = ? WHERE uid=?", (value, str(uid)))
    conn.commit(); conn.close()

# ===== БАЗА ПОДАРКОВ =====
GIFTS = {
    "common": [
        {"name": "🌹 Роза", "price": 0.5}, {"name": "🧸 Мишка", "price": 0.8},
        {"name": "🎁 Подарок", "price": 1.0}, {"name": "🍰 Тортик", "price": 1.2},
        {"name": "🎈 Шарик", "price": 0.3}, {"name": "🍕 Пицца", "price": 1.5},
        {"name": "💌 Письмо", "price": 0.4}, {"name": "🎓 Диплом", "price": 1.8},
        {"name": "🍀 Клевер", "price": 1.0}, {"name": "🍄 Гриб", "price": 0.6},
        {"name": "🍉 Арбуз", "price": 0.9}, {"name": "🍔 Бургер", "price": 1.3},
        {"name": "🎂 Торт", "price": 2.0}, {"name": "🏀 Мяч", "price": 0.7},
        {"name": "🎧 Наушники", "price": 1.9}, {"name": "☕ Кофе", "price": 0.5}
    ],
    "uncommon": [
        {"name": "🌻 Подсолнух", "price": 3.0}, {"name": "🐱 Кот", "price": 4.0},
        {"name": "🐶 Собака", "price": 3.5}, {"name": "🦊 Лиса", "price": 4.5},
        {"name": "🐼 Панда", "price": 5.0}, {"name": "🏆 Кубок", "price": 4.0},
        {"name": "🚗 Машина", "price": 5.0}, {"name": "✈️ Самолет", "price": 4.8}
    ],
    "rare": [
        {"name": "💎 Кристалл", "price": 10.0}, {"name": "🔮 Шар", "price": 12.0},
        {"name": "🦄 Единорог", "price": 15.0}, {"name": "🦁 Лев", "price": 18.0},
        {"name": "🐉 Дракон", "price": 25.0}, {"name": "💍 Кольцо", "price": 20.0}
    ],
    "legendary": [
        {"name": "🚀 Ракета", "price": 50.0}, {"name": "👑 Корона", "price": 100.0},
        {"name": "💎 Эксклюзив", "price": 250.0}, {"name": "🐸 Пепе", "price": 500.0},
        {"name": "🏆 Кубок Мира", "price": 1000.0}, {"name": "🦍 Горилла", "price": 300.0}
    ]
}

def find_gift(name):
    for cat in GIFTS.values():
        for g in cat:
            if g["name"] == name: return g
    return None

def open_case_logic(case_id):
    if case_id == "bronze":
        roll = random.uniform(0, 100)
        if roll < 85: cat = "common"
        elif roll < 97: cat = "uncommon"
        elif roll < 99.9: cat = "rare"
        else: cat = "legendary"
    elif case_id == "silver":
        roll = random.uniform(0, 100)
        if roll < 60: cat = "common"
        elif roll < 90: cat = "uncommon"
        elif roll < 99: cat = "rare"
        else: cat = "legendary"
    elif case_id == "gold":
        roll = random.uniform(0, 100)
        if roll < 30: cat = "common"
        elif roll < 70: cat = "uncommon"
        elif roll < 95: cat = "rare"
        else: cat = "legendary"
    elif case_id == "diamond":
        roll = random.uniform(0, 100)
        if roll < 10: cat = "common"
        elif roll < 30: cat = "uncommon"
        elif roll < 80: cat = "rare"
        else: cat = "legendary"
    return random.choice(GIFTS[cat])

# ===== АКТИВНЫЕ СЕССИИ МИН =====
mine_sessions = {}

# ===== API =====
class UserAction(BaseModel):
    user_id: str
    action: str
    code: str = ""
    case_id: str = ""
    amount: float = 0.0
    item_id: int = -1
    cell: int = -1
    target_item: str = ""
    market_id: int = -1
    duel_id: int = -1

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
            if random.random() < 0.01: item = random.choice(GIFTS["legendary"])
            elif random.random() < 0.1: item = random.choice(GIFTS["rare"])
            else: item = random.choice(GIFTS["common"])
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
        if get_user(req.user_id)["balance"] < price:
            return {"success": False, "msg": "Недостаточно средств"}
        update_balance(req.user_id, -price)
        item = open_case_logic(req.case_id)
        add_inv(req.user_id, item["name"], item["price"])
        update_stat(req.user_id, "total_bets", 1, "inc")
        add_feed(req.user_id, "Игрок", "case", f"открыл кейс → {item['name']}")
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

    elif req.action == "get_shop":
        return {"success": True, "shop": GIFTS}

    elif req.action == "buy_shop":
        real_item = find_gift(req.code)
        if not real_item:
            return {"success": False, "msg": "❌ Товар не найден"}
        price = real_item["price"]
        if get_user(req.user_id)["balance"] < price:
            return {"success": False, "msg": f"❌ Недостаточно средств (нужно {price} 💎)"}
        update_balance(req.user_id, -price)
        add_inv(req.user_id, real_item["name"], real_item["price"])
        return {"success": True, "msg": f"✅ Куплено: {real_item['name']} за {price} 💎", "new_balance": get_user(req.user_id)["balance"]}

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
        return {"success": True, "top": [{"id": u, "bal": round(b, 2)} for u, b in top]}

    # ===== МИНЫ =====
    elif req.action == "mines_start":
        bet = req.amount
        if bet < 0.10:
            return {"success": False, "msg": "❌ Мин. ставка 0.10 💎"}
        if get_user(req.user_id)["balance"] < bet:
            return {"success": False, "msg": "❌ Недостаточно средств"}
        update_balance(req.user_id, -bet)
        all_cells = list(range(25))
        mines = set(random.sample(all_cells, 12))
        mine_sessions[req.user_id] = {"bet": bet, "mines": mines, "opened": set(), "mult": 1.0}
        return {"success": True, "msg": "💣 Игра началась! Открывай клетки."}

    elif req.action == "mines_open":
        sess = mine_sessions.get(req.user_id)
        if not sess:
            return {"success": False, "msg": "❌ Нет активной игры"}
        cell = req.cell
        if cell < 0 or cell > 24:
            return {"success": False, "msg": "❌ Неверная клетка"}
        if cell in sess["opened"]:
            return {"success": False, "msg": "❌ Уже открыта"}
        if cell in sess["mines"]:
            lost = sess["bet"]
            del mine_sessions[req.user_id]
            update_stat(req.user_id, "total_lost", lost, "inc")
            update_stat(req.user_id, "favorite_game", "mines")
            return {"success": True, "result": "boom", "cell": cell, "lost": lost,
                    "msg": f"💥 БУМ! Ты потерял {lost} 💎"}
        sess["opened"].add(cell)
        n = len(sess["opened"])
        sess["mult"] = round(1.0 + (n * 0.3) + (n * n * 0.05), 2)
        return {"success": True, "result": "safe", "cell": cell, "mult": sess["mult"],
                "opened": list(sess["opened"]), "msg": f"✅ Безопасно! x{sess['mult']}"}

    elif req.action == "mines_cashout":
        sess = mine_sessions.get(req.user_id)
        if not sess or len(sess["opened"]) == 0:
            return {"success": False, "msg": "❌ Нечего забирать"}
        win = round(sess["bet"] * sess["mult"], 2)
        update_balance(req.user_id, win)
        update_stat(req.user_id, "total_won", win, "inc")
        update_stat(req.user_id, "total_bets", 1, "inc")
        update_stat(req.user_id, "favorite_game", "mines")
        del mine_sessions[req.user_id]
        return {"success": True, "msg": f"💰 Забрал {win} 💎 (x{sess['mult']})", "win": win}

    # ===== АПГРЕЙД (новая математика) =====
elif req.action == "upgrade_start":
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("SELECT item_name, item_price FROM inventory WHERE id=? AND uid=?", (req.item_id, str(req.user_id)))
    row = c.fetchone()
    if not row:
        conn.close(); return {"success": False, "msg": "❌ Подарок не найден"}
    src_name, src_price = row
    target = find_gift(req.target_item)
    if not target:
        conn.close(); return {"success": False, "msg": "❌ Цель не найдена"}
    if target["price"] <= src_price:
        conn.close(); return {"success": False, "msg": "❌ Цель должна быть дороже"}
    
    # Базовая формула: чем дороже цель относительно источника, тем ниже шанс
    ratio = src_price / target["price"]  # 0.0 - 1.0
    
    # Штраф за дорогие подарки (защита экономики):
    # - Дешёвые (<5💎): коэффициент 0.9 (более щедро)
    # - Средние (5-50💎): 0.7
    # - Дорогие (50-250💎): 0.5
    # - Легендарные (>250💎): 0.3 (жёстко)
    if target["price"] < 5:
        multiplier = 0.90
    elif target["price"] < 50:
        multiplier = 0.70
    elif target["price"] < 250:
        multiplier = 0.50
    else:
        multiplier = 0.30
    
    real_chance = ratio * multiplier
    real_chance = max(0.03, min(0.80, real_chance))  # от 3% до 80% максимум
    
    # Красивый отображаемый шанс (чуть выше реального, чтоб игрок верил)
    display_chance = real_chance * 100
    # Добавляем ±3% для живости, но не даём выйти за 2-85
    display_chance = round(display_chance + random.uniform(-3, 3), 1)
    display_chance = max(2, min(85, display_chance))
    
    c.execute("DELETE FROM inventory WHERE id=?", (req.item_id,))
    conn.commit(); conn.close()
    
    if random.random() < real_chance:
        add_inv(req.user_id, target["name"], target["price"])
        add_feed(req.user_id, "Игрок", "upgrade", f"апгрейднул {src_name} → {target['name']}!")
        return {"success": True, "result": "win", "src": src_name, "target": target["name"], "chance": display_chance}
    else:
        return {"success": True, "result": "lose", "src": src_name, "target": target["name"], "chance": display_chance}
        
    # ===== БИРЖА (без минимума!) =====
    elif req.action == "market_list":
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT item_name, item_price FROM inventory WHERE id=? AND uid=?", (req.item_id, str(req.user_id)))
        row = c.fetchone()
        if not row:
            conn.close(); return {"success": False, "msg": "❌ Подарок не найден"}
        name, price = row
        if req.amount <= 0:
            conn.close(); return {"success": False, "msg": "❌ Цена должна быть больше 0"}
        c.execute("DELETE FROM inventory WHERE id=?", (req.item_id,))
        c.execute("INSERT INTO market (seller_id, item_name, item_price, price, status, created_at) VALUES (?,?,?,?, 'active', ?)",
                  (str(req.user_id), name, price, round(req.amount, 2), int(time.time())))
        conn.commit(); conn.close()
        return {"success": True, "msg": f"✅ {name} выставлен за {round(req.amount, 2)} 💎"}

    elif req.action == "market_buy":
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT seller_id, item_name, item_price, price FROM market WHERE id=? AND status='active'", (req.market_id,))
        row = c.fetchone()
        if not row:
            conn.close(); return {"success": False, "msg": "❌ Лот не найден"}
        seller_id, name, orig_price, list_price = row
        if seller_id == req.user_id:
            conn.close(); return {"success": False, "msg": "❌ Нельзя купить свой лот"}
        if get_user(req.user_id)["balance"] < list_price:
            conn.close(); return {"success": False, "msg": f"❌ Нужно {list_price} 💎"}
        # ЗАКРЫВАЕМ соединение ДО хелперов (фикс зависания!)
        c.execute("UPDATE market SET status='sold' WHERE id=?", (req.market_id,))
        conn.commit(); conn.close()
        update_balance(req.user_id, -list_price)
        update_balance(seller_id, round(list_price * 0.9, 2))
        add_inv(req.user_id, name, orig_price)
        add_feed(req.user_id, "Игрок", "market_buy", f"купил {name} за {list_price} 💎")
        return {"success": True, "msg": f"✅ Куплено {name} за {list_price} 💎"}

    elif req.action == "market_cancel":
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT item_name, item_price FROM market WHERE id=? AND seller_id=? AND status='active'", (req.market_id, str(req.user_id)))
        row = c.fetchone()
        if not row:
            conn.close(); return {"success": False, "msg": "❌ Лот не найден"}
        name, orig_price = row
        c.execute("UPDATE market SET status='cancelled' WHERE id=?", (req.market_id,))
        # ЗАКРЫВАЕМ соединение ДО add_inv (фикс зависания!)
        conn.commit(); conn.close()
        add_inv(req.user_id, name, orig_price)
        return {"success": True, "msg": "✅ Лот снят"}

    elif req.action == "market_get":
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT id, seller_id, item_name, price FROM market WHERE status='active' AND seller_id != ? ORDER BY price ASC LIMIT 50", (str(req.user_id),))
        rows = c.fetchall()
        c.execute("SELECT id, item_name, price FROM market WHERE status='active' AND seller_id = ? ORDER BY price ASC", (str(req.user_id),))
        own = c.fetchall()
        conn.close()
        return {"success": True,
                "lots": [{"id": r[0], "seller": r[1], "name": r[2], "price": r[3]} for r in rows],
                "own": [{"id": r[0], "name": r[1], "price": r[2]} for r in own]}

    # ===== ДУЭЛИ НА АЛМАЗЫ =====
    elif req.action == "duel_create":
        stake = req.amount
        if stake < 0.10:
            return {"success": False, "msg": "❌ Мин. ставка 0.10 💎"}
        if get_user(req.user_id)["balance"] < stake:
            return {"success": False, "msg": "❌ Недостаточно средств"}
        # Списываем сразу у создателя
        update_balance(req.user_id, -stake)
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("INSERT INTO duels (creator_id, stake, status, created_at) VALUES (?, ?, 'waiting', ?)",
                  (str(req.user_id), round(stake, 2), int(time.time())))
        conn.commit(); conn.close()
        return {"success": True, "msg": f"✅ Дуэль на {stake} 💎 создана! Ждём соперника."}

    elif req.action == "duel_join":
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT creator_id, stake FROM duels WHERE id=? AND status='waiting'", (req.duel_id,))
        row = c.fetchone()
        if not row:
            conn.close(); return {"success": False, "msg": "❌ Дуэль не найдена"}
        creator_id, stake = row
        if creator_id == req.user_id:
            conn.close(); return {"success": False, "msg": "❌ Нельзя играть с собой"}
        if get_user(req.user_id)["balance"] < stake:
            conn.close(); return {"success": False, "msg": f"❌ Нужно {stake} 💎"}
        # Списываем у соперника и закрываем
        update_balance(req.user_id, -stake)
        if random.random() < 0.5:
            winner = creator_id
        else:
            winner = str(req.user_id)
        # Победитель получает ВЕСЬ банк (2 ставки), минус 10% комиссия
        bank = stake * 2
        win_amount = round(bank * 0.9, 2)
        c.execute("UPDATE duels SET opponent_id=?, status='finished', winner_id=? WHERE id=?",
                  (str(req.user_id), winner, req.duel_id))
        conn.commit(); conn.close()
        update_balance(winner, win_amount)
        update_stat(winner, "total_won", win_amount, "inc")
        add_feed(winner, "Игрок", "duel_win", f"выиграл дуэль на {stake} 💎 (банк {win_amount} 💎)!")
        return {"success": True, "you_win": winner == req.user_id, "win_amount": win_amount,
                "msg": f"🎉 Ты выиграл {win_amount} 💎!" if winner == req.user_id else f"💥 Ты проиграл {stake} 💎"}

    elif req.action == "duel_list":
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT id, creator_id, stake FROM duels WHERE status='waiting' AND creator_id != ? ORDER BY id DESC LIMIT 20", (str(req.user_id),))
        rows = c.fetchall(); conn.close()
        return {"success": True, "duels": [{"id": r[0], "creator": r[1], "stake": r[2]} for r in rows]}

    # ===== ЛЕНТА =====
    elif req.action == "feed_get":
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("SELECT name, action, details, timestamp FROM feed ORDER BY id DESC LIMIT 15")
        rows = c.fetchall(); conn.close()
        return {"success": True, "feed": [{"name": r[0], "action": r[1], "details": r[2], "time": r[3]} for r in rows]}

    # ===== СТАТИСТИКА =====
    elif req.action == "get_stats":
        stats = get_stats(req.user_id)
        inv = get_inv(req.user_id)
        inv_value = sum(i[2] for i in inv)
        return {"success": True, "stats": stats, "inv_count": len(inv), "inv_value": round(inv_value, 2)}

    return {"success": False, "msg": "Unknown action"}

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
        roll = random.uniform(0, 100)
        if roll < 45: crash_target = random.uniform(1.00, 1.20)
        elif roll < 65: crash_target = random.uniform(1.20, 1.50)
        elif roll < 95: crash_target = random.uniform(1.50, 5.00)
        elif roll < 99.5: crash_target = random.uniform(5.00, 20.00)
        else: crash_target = random.uniform(20.00, 150.00)
        game_state = "flying"; crash_running = True
        await broadcast({"type": "start"})
        while crash_running:
            await asyncio.sleep(0.1)
            crash_mult += 0.015 * crash_mult
            if crash_mult >= crash_target: crash_running = False
            else: await broadcast({"type": "tick", "multiplier": round(crash_mult, 2)})
        game_state = "crashed"
        for uid_l, bet_l in active_bets.items():
            update_stat(uid_l, "total_lost", bet_l, "inc")
            update_stat(uid_l, "total_bets", 1, "inc")
            update_stat(uid_l, "favorite_game", "crash")
        await broadcast({"type": "crash", "multiplier": round(crash_target, 2)})
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
                if game_state != "waiting":
                    await websocket.send_json({"type": "error", "msg": "Ставки закрыты!"}); continue
                if amt < 0.10:
                    await websocket.send_json({"type": "error", "msg": "Мин. ставка 0.10"}); continue
                if get_user(user_id)["balance"] >= amt:
                    update_balance(user_id, -amt); active_bets[user_id] = amt
                    await broadcast({"type": "waiting", "countdown": "...", "players": active_bets, "names": player_names})
                else:
                    await websocket.send_json({"type": "error", "msg": "Нет денег"})
            elif data.get("action") == "cashout":
                if game_state == "flying" and user_id in active_bets:
                    bet = active_bets.pop(user_id); win = round(bet * crash_mult, 2)
                    update_balance(user_id, win)
                    update_stat(user_id, "total_won", win, "inc")
                    update_stat(user_id, "total_bets", 1, "inc")
                    update_stat(user_id, "best_crash", round(crash_mult, 2), "max")
                    update_stat(user_id, "favorite_game", "crash")
                    if crash_mult >= 5.0:
                        add_feed(user_id, "Игрок", "crash_win", f"выиграл {win} 💎 на x{round(crash_mult, 2)}!")
                    await websocket.send_json({"type": "cashed_out", "win": win})
    except WebSocketDisconnect:
        if websocket in clients: clients.remove(websocket)

# ===== БОТ =====
@dp.message(CommandStart())
async def cmd_start(message: Message):
    args = message.text.split()
    user_id = str(message.from_user.id)
    if len(args) > 1 and args[1].startswith("ref"):
        ref_id = args[1].replace("ref", "")
        if ref_id != user_id and ref_id.isdigit():
            conn = sqlite3.connect(DB_PATH); c = conn.cursor()
            c.execute("SELECT uid FROM users WHERE uid=?", (user_id,))
            already_exists = c.fetchone() is not None
            conn.close()
            if not already_exists:
                conn = sqlite3.connect(DB_PATH); c = conn.cursor()
                c.execute("INSERT OR IGNORE INTO users (uid, balance) VALUES (?, ?)", (user_id, 0.10))
                conn.commit(); conn.close()
                update_balance(ref_id, 0.005)
                try:
                    await bot.send_message(ref_id, "🎉 По твоей ссылке зашёл новый игрок! +0.005 💎")
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
    if cb.from_user.id != ADMIN_ID: return
    update_balance(ADMIN_ID, 100.0)
    await cb.message.answer(f"✅ Выдано 100 💎. Баланс: {get_user(ADMIN_ID)['balance']}")
    await cb.answer()

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(cb: CallbackQuery):
    if cb.from_user.id != ADMIN_ID: return
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
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
