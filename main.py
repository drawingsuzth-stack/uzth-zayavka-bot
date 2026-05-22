import os
import re
import json
import time
import threading
import requests
from flask import Flask
from datetime import datetime, timezone, timedelta

# ═══════════════════════════════════════════════════════
#  SOZLAMALAR
# ═══════════════════════════════════════════════════════
BOT_TOKEN    = os.getenv("BOT_TOKEN")
CHAT_ID      = str(os.getenv("CHAT_ID"))
TRELLO_KEY   = os.getenv("TRELLO_KEY")
TRELLO_TOKEN = os.getenv("TRELLO_TOKEN")
BOARD_ID     = os.getenv("BOARD_ID")

DONE_LIST_NAME     = "YOPILGAN"   # Trellodagi yopilgan ustun nomi (katta harf)
CHECK_INTERVAL     = 30           # Soniyada bir tekshirish
DUE_WARN_DAYS      = 5            # Necha kun qolsa ogohlantirish
STUCK_DAYS         = 7            # Necha kun qotib qolsa ogohlantirish
NO_DUE_WARN_DAYS   = 3            # Muddatsiz karta necha kundan keyin eslatilsin

# Toshkent UTC+5
TZ = timezone(timedelta(hours=5))

# ── Trello ismi → Telegram @username xaritasi ──────────
# "Trellodagi to'liq ism": "@telegram_username"
# Agar Telegram usernameni bilmasangiz — bo'sh qoldiring
MEMBER_TELEGRAM_MAP = {
    # "Tashkenbayev B.":    "@tashkenbayev",
    # "Umaraliyev B.":      "@umaraliyev",
    # "Abduraxmonova V.":   "@abduraxmonova",
    # "Usmonov B.":         "@usmonov",
    # "Vahobov X.":         "@vahobov",
    # "Zohidjonov J.":      "@zohidjonov",
    # "Abdusattarov A.":    "@abdusattarov",
}

# ── Xotira fayllari ─────────────────────────────────────
import tempfile, pathlib
_TMP = pathlib.Path(tempfile.gettempdir())
STATE_FILE         = str(_TMP / "known_cards.json")
WARNED_5DAYS_FILE  = str(_TMP / "warned_5days.json")
WARNED_OVERDUE_FILE= str(_TMP / "warned_overdue.json")
WARNED_STUCK_FILE  = str(_TMP / "warned_stuck.json")
WARNED_NODUE_FILE  = str(_TMP / "warned_nodue.json")
WARNED_NOUSER_FILE = str(_TMP / "warned_nouser.json")
CARD_SEEN_DATE_FILE= str(_TMP / "card_seen_date.json")

# ═══════════════════════════════════════════════════════
#  FLASK
# ═══════════════════════════════════════════════════════
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot ishlayapti ✅"

# ═══════════════════════════════════════════════════════
#  FAYL BILAN ISHLASH
# ═══════════════════════════════════════════════════════
def load_json(filepath, default):
    try:
        if os.path.exists(filepath):
            with open(filepath, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return default

def save_json(filepath, data):
    try:
        with open(filepath, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Fayl saqlashda xato ({filepath}):", e)

# ═══════════════════════════════════════════════════════
#  HOLAT O'ZGARUVCHILARI
# ═══════════════════════════════════════════════════════
known_cards      = load_json(STATE_FILE, {})
warned_5days     = set(load_json(WARNED_5DAYS_FILE, []))
warned_overdue   = set(load_json(WARNED_OVERDUE_FILE, []))
warned_stuck     = set(load_json(WARNED_STUCK_FILE, []))
warned_nodue     = set(load_json(WARNED_NODUE_FILE, []))
warned_nouser    = set(load_json(WARNED_NOUSER_FILE, []))
card_seen_date   = load_json(CARD_SEEN_DATE_FILE, {})
last_update_id   = None

# ═══════════════════════════════════════════════════════
#  TELEGRAM
# ═══════════════════════════════════════════════════════
def send_message(text, chat_id=None):
    cid = chat_id or CHAT_ID
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    while len(text) > 3900:
        cut = text.rfind("\n", 0, 3900)
        if cut == -1:
            cut = 3900
        requests.post(url, data={
            "chat_id": cid,
            "text": text[:cut],
            "parse_mode": "HTML"
        }, timeout=30)
        text = text[cut:].strip()
        time.sleep(0.5)
    if text:
        requests.post(url, data={
            "chat_id": cid,
            "text": text,
            "parse_mode": "HTML"
        }, timeout=30)

# ═══════════════════════════════════════════════════════
#  TRELLO API
# ═══════════════════════════════════════════════════════
def trello_get(path, extra=None):
    params = {"key": TRELLO_KEY, "token": TRELLO_TOKEN}
    if extra:
        params.update(extra)
    try:
        r = requests.get(
            f"https://api.trello.com/1/{path}",
            params=params, timeout=30
        )
        if r.status_code != 200:
            print(f"Trello xato {r.status_code}:", r.text[:300])
            return []
        return r.json()
    except Exception as e:
        print("Trello so'rovida xato:", e)
        return []

def get_cards():
    return trello_get(f"boards/{BOARD_ID}/cards", {
        "members": "true",
        "labels": "all",
        "customFieldItems": "true",
    })

def get_custom_fields():
    """Doskanin custom field larini oladi."""
    return trello_get(f"boards/{BOARD_ID}/customFields")

# Custom field ID larini cache qilish
_custom_field_cache = {}

def get_zayavka_turi_field_id():
    """Zayavka turi custom field ID sini topadi."""
    global _custom_field_cache
    if "zayavka_turi_id" in _custom_field_cache:
        return _custom_field_cache["zayavka_turi_id"]
    
    fields = get_custom_fields()
    for field in fields:
        if "Zayavka turi" in field.get("name", "") or "zayavka" in field.get("name", "").lower():
            _custom_field_cache["zayavka_turi_id"] = field["id"]
            # Options ni ham cache qilish
            options = {}
            for opt in field.get("options", []):
                options[opt["id"]] = opt.get("value", {}).get("text", "")
            _custom_field_cache["zayavka_turi_options"] = options
            return field["id"]
    return None

def get_lists():
    return trello_get(f"boards/{BOARD_ID}/lists")

def get_members():
    return trello_get(f"boards/{BOARD_ID}/members")

def build_list_map():
    return {x["id"]: x["name"] for x in get_lists()}

def build_member_map():
    return {
        m["id"]: m.get("fullName") or m.get("username") or m["id"]
        for m in get_members()
    }

# ═══════════════════════════════════════════════════════
#  YORDAMCHI FUNKSIYALAR
# ═══════════════════════════════════════════════════════
def now_tashkent():
    return datetime.now(TZ)

def today_tashkent():
    return now_tashkent().date()

def current_year():
    return str(now_tashkent().year)

def is_done(list_name):
    return list_name.strip().upper() == DONE_LIST_NAME.upper()

def is_current_year_card(card):
    year = current_year()
    name = card.get("name", "")
    desc = card.get("desc", "")
    years_in_name = re.findall(r"20\d{2}", name)
    if years_in_name:
        return year in years_in_name
    years_in_desc = re.findall(r"20\d{2}", desc)
    if years_in_desc:
        return year in years_in_desc
    if card.get("due"):
        return card["due"][:4] == year
    return False

# ═══════════════════════════════════════════════════════
#  LABEL (ZAYAVKA TURI) FUNKSIYALARI
# ═══════════════════════════════════════════════════════
LABEL_MAHALLIY = "Mahalliy"
LABEL_IMPORT   = "Import"
LABEL_ARALASH  = "Aralash"

LABEL_EMOJI = {
    LABEL_MAHALLIY: "🟡",
    LABEL_IMPORT:   "🔵",
    LABEL_ARALASH:  "🟠",
}

def get_card_label(card):
    """Kartaning zayavka turini karta nomidan topadi.
    Nom ichida Mahalliy / Import / Aralash so'zi bo'lsa - o'sha tur qaytariladi.
    """
    name = card.get("name", "")
    desc = card.get("desc", "")
    text = (name + " " + desc).lower()

    if "mahalliy" in text:
        return LABEL_MAHALLIY
    if "import" in text:
        return LABEL_IMPORT
    if "aralash" in text:
        return LABEL_ARALASH
    return None

def get_label_emoji(label_name):
    return LABEL_EMOJI.get(label_name, "⚪")

def filter_cards_by_label(cards, label_name):
    """Kartalarni label bo'yicha filtrlaydi."""
    return [c for c in cards if get_card_label(c) == label_name]

def due_date(card):
    if not card.get("due"):
        return None
    try:
        return datetime.strptime(card["due"][:10], "%Y-%m-%d").date()
    except Exception:
        return None

def due_text(card):
    d = due_date(card)
    if not d:
        return "Muddat qo'yilmagan"
    today = today_tashkent()
    days_left = (d - today).days
    date_str = d.strftime("%d.%m.%Y")
    if days_left < 0:
        return f"{date_str} ⚠️ {abs(days_left)} kun kechikkan"
    elif days_left == 0:
        return f"{date_str} 🔴 Bugun tugaydi!"
    elif days_left <= DUE_WARN_DAYS:
        return f"{date_str} 🟡 {days_left} kun qoldi"
    return f"{date_str} ({days_left} kun qoldi)"

def is_overdue(card, list_name):
    if is_done(list_name):
        return False
    d = due_date(card)
    if not d:
        return False
    return d < today_tashkent()

def is_due_within_n_days(card, list_name, n=DUE_WARN_DAYS):
    if is_done(list_name):
        return False, None
    d = due_date(card)
    if not d:
        return False, None
    days_left = (d - today_tashkent()).days
    return 0 <= days_left <= n, days_left

def card_desc_short(card, limit=500):
    desc = card.get("desc", "").strip()
    if not desc:
        return ""
    return (desc[:limit] + "...") if len(desc) > limit else desc

def short_url(card):
    return card.get("shortUrl") or card.get("url") or ""

def member_names_plain(card, member_map):
    names = [member_map.get(mid, mid) for mid in card.get("idMembers", [])]
    return ", ".join(names) if names else "Biriktirilmagan"

def member_names_mention(card, member_map):
    """Trello ismi Telegram @mention ga o'girilsa — mention, bo'lmasa — ism."""
    parts = []
    ids = card.get("idMembers", [])
    if not ids:
        return "Biriktirilmagan"
    for mid in ids:
        name = member_map.get(mid, mid)
        mention = MEMBER_TELEGRAM_MAP.get(name)
        parts.append(mention if mention else name)
    return ", ".join(parts)

def days_since_seen(card_id):
    """Karta nechi kun avval birinchi ko'rilgan."""
    seen = card_seen_date.get(card_id)
    if not seen:
        return 0
    try:
        seen_date = datetime.strptime(seen, "%Y-%m-%d").date()
        return (today_tashkent() - seen_date).days
    except Exception:
        return 0

# ═══════════════════════════════════════════════════════
#  TRELLO O'ZGARISHLARINI TEKSHIRISH
# ═══════════════════════════════════════════════════════
def initialize_known_cards():
    global known_cards, card_seen_date
    if known_cards:
        print(f"✅ Saqlangan holat yuklandi: {len(known_cards)} ta karta")
        return
    cards = get_cards()
    list_map = build_list_map()
    today_str = today_tashkent().isoformat()
    for card in cards:
        known_cards[card["id"]] = list_map.get(card["idList"], "")
        if card["id"] not in card_seen_date:
            card_seen_date[card["id"]] = today_str
    save_json(STATE_FILE, known_cards)
    save_json(CARD_SEEN_DATE_FILE, card_seen_date)
    print(f"✅ Boshlang'ich holat saqlandi: {len(known_cards)} ta karta")


# Muddat ogohlantirishlari faqat soat 08:30 da yuboriladi
DUE_NOTIFY_HOUR   = 8
DUE_NOTIFY_MINUTE = 30
# Oxirgi muddat bildirishnomasi yuborilgan kun
_last_due_notify_date = None

def check_trello_changes():
    global known_cards, warned_5days, warned_overdue
    global warned_stuck, warned_nodue, warned_nouser, card_seen_date
    global _last_due_notify_date

    now = now_tashkent()
    # Muddat ogohlantirishlarini faqat 08:30 da yuborish
    is_due_notify_time = (
        now.hour == DUE_NOTIFY_HOUR and
        now.minute >= DUE_NOTIFY_MINUTE and
        _last_due_notify_date != now.date()
    )
    if is_due_notify_time:
        _last_due_notify_date = now.date()

    cards = get_cards()
    if not cards:
        return

    list_map   = build_list_map()
    member_map = build_member_map()
    new_known  = {}
    today_str  = today_tashkent().isoformat()

    dirty = {
        "state": False, "5days": False, "overdue": False,
        "stuck": False, "nodue": False, "nouser": False, "seen": False
    }

    for card in cards:
        card_id   = card["id"]
        name      = card.get("name", "Nomsiz")
        list_name = list_map.get(card.get("idList"), "Noma'lum ustun")
        old_list  = known_cards.get(card_id)
        url       = short_url(card)
        members   = member_names_mention(card, member_map)
        desc      = card_desc_short(card)
        desc_line = f"\n📝 <b>Tavsif:</b> {desc}" if desc else ""

        new_known[card_id] = list_name

        # Birinchi ko'rish sanasini yozib qo'yish
        if card_id not in card_seen_date:
            card_seen_date[card_id] = today_str
            dirty["seen"] = True

        # ── 1. Yangi karta ────────────────────────────────
        if card_id not in known_cards:
            if not is_current_year_card(card):
                continue
            card_label = get_card_label(card)
            label_line = f"\n{get_label_emoji(card_label)} <b>Turi:</b> {card_label}" if card_label else ""
            send_message(
                f"🆕 <b>Yangi zayavka qo'shildi!</b>\n\n"
                f"📌 <b>Zayavka:</b> {name}\n"
                f"📂 <b>Ustun:</b> {list_name}\n"
                f"👥 <b>Mas'ullar:</b> {members}\n"
                f"📅 <b>Muddat:</b> {due_text(card)}"
                f"{label_line}"
                f"{desc_line}\n\n"
                f"🔗 {url}"
            )
            dirty["state"] = True

        # ── 2. Yopildi ────────────────────────────────────
        elif old_list and not is_done(old_list) and is_done(list_name):
            send_message(
                f"✅ <b>Zayavka yopildi!</b>\n\n"
                f"📌 <b>Zayavka:</b> {name}\n"
                f"📂 <b>Avvalgi ustun:</b> {old_list}\n"
                f"📂 <b>Hozirgi ustun:</b> {list_name}\n"
                f"👥 <b>Mas'ullar:</b> {members}\n"
                f"📅 <b>Muddat:</b> {due_text(card)}"
                f"{desc_line}\n\n"
                f"🔗 {url}"
            )
            dirty["state"] = True

        # ── 3. Ustun o'zgardi (YOPILGAN emas) ────────────
        elif old_list and old_list != list_name and not is_done(list_name):
            if is_current_year_card(card):
                send_message(
                    f"🔄 <b>Zayavka ko'chirildi!</b>\n\n"
                    f"📌 <b>Zayavka:</b> {name}\n"
                    f"📂 <b>Avvalgi ustun:</b> {old_list}\n"
                    f"📂 <b>Yangi ustun:</b> {list_name}\n"
                    f"👥 <b>Mas'ullar:</b> {members}\n"
                    f"📅 <b>Muddat:</b> {due_text(card)}\n\n"
                    f"🔗 {url}"
                )
                dirty["state"] = True

        if is_done(list_name):
            continue

        if not is_current_year_card(card):
            continue

        # ── 4. Muddatga 5 kun qoldi ───────────────────────
        within, days_left = is_due_within_n_days(card, list_name)
        warn_key_5 = f"{card_id}_5days_{due_date(card)}"
        if within and warn_key_5 not in warned_5days and is_due_notify_time:
            d = due_date(card)
            send_message(
                f"⏰ <b>Muddat yaqinlashmoqda!</b>\n\n"
                f"📌 <b>Zayavka:</b> {name}\n"
                f"📂 <b>Ustun:</b> {list_name}\n"
                f"👥 <b>Mas'ullar:</b> {members}\n"
                f"📅 <b>Muddat:</b> {d.strftime('%d.%m.%Y')}\n"
                f"⏳ <b>Qolgan:</b> {days_left} kun\n\n"
                f"🔗 {url}"
            )
            warned_5days.add(warn_key_5)
            dirty["5days"] = True

        # ── 5. Muddati o'tib ketdi ────────────────────────
        warn_key_od = f"{card_id}_overdue_{due_date(card)}"
        if is_overdue(card, list_name) and warn_key_od not in warned_overdue and is_due_notify_time:
            d = due_date(card)
            days_passed = (today_tashkent() - d).days
            send_message(
                f"❌ <b>Zayavka muddati o'tib ketdi!</b>\n\n"
                f"📌 <b>Zayavka:</b> {name}\n"
                f"📂 <b>Ustun:</b> {list_name}\n"
                f"👥 <b>Mas'ullar:</b> {members}\n"
                f"📅 <b>Muddat edi:</b> {d.strftime('%d.%m.%Y')}\n"
                f"🕒 <b>Kechikish:</b> {days_passed} kun\n\n"
                f"🔗 {url}"
            )
            warned_overdue.add(warn_key_od)
            dirty["overdue"] = True

        # ── 6. Qotib qolgan zayavka (STUCK) ──────────────
        days_on_board = days_since_seen(card_id)
        warn_key_stuck = f"{card_id}_stuck_{list_name}"
        if days_on_board >= STUCK_DAYS and warn_key_stuck not in warned_stuck:
            send_message(
                f"🕸️ <b>Zayavka qotib qolgan!</b>\n\n"
                f"📌 <b>Zayavka:</b> {name}\n"
                f"📂 <b>Ustun:</b> {list_name}\n"
                f"👥 <b>Mas'ullar:</b> {members}\n"
                f"📅 <b>Muddat:</b> {due_text(card)}\n"
                f"🕒 <b>O'zgarishsiz:</b> {days_on_board}+ kun\n\n"
                f"🔗 {url}"
            )
            warned_stuck.add(warn_key_stuck)
            dirty["stuck"] = True

        # ── 7. Muddatsiz karta eslatmasi ──────────────────
        if not card.get("due"):
            days_on_board = days_since_seen(card_id)
            warn_key_nd = f"{card_id}_nodue"
            if days_on_board >= NO_DUE_WARN_DAYS and warn_key_nd not in warned_nodue:
                send_message(
                    f"📭 <b>Muddatsiz zayavka!</b>\n\n"
                    f"📌 <b>Zayavka:</b> {name}\n"
                    f"📂 <b>Ustun:</b> {list_name}\n"
                    f"👥 <b>Mas'ullar:</b> {members}\n"
                    f"⚠️ <b>{days_on_board} kun davomida muddat qo'yilmagan</b>\n\n"
                    f"🔗 {url}"
                )
                warned_nodue.add(warn_key_nd)
                dirty["nodue"] = True

        # ── 8. Mas'ulsiz karta ────────────────────────────
        if not card.get("idMembers"):
            warn_key_nu = f"{card_id}_nouser"
            if warn_key_nu not in warned_nouser:
                send_message(
                    f"👤 <b>Mas'ulsiz zayavka!</b>\n\n"
                    f"📌 <b>Zayavka:</b> {name}\n"
                    f"📂 <b>Ustun:</b> {list_name}\n"
                    f"📅 <b>Muddat:</b> {due_text(card)}\n"
                    f"⚠️ <b>Hech kim biriktirilmagan!</b>\n\n"
                    f"🔗 {url}"
                )
                warned_nouser.add(warn_key_nu)
                dirty["nouser"] = True

    # ── Holat saqlash ─────────────────────────────────────
    known_cards.update(new_known)
    if dirty["state"]:    save_json(STATE_FILE, known_cards)
    if dirty["5days"]:    save_json(WARNED_5DAYS_FILE, list(warned_5days))
    if dirty["overdue"]:  save_json(WARNED_OVERDUE_FILE, list(warned_overdue))
    if dirty["stuck"]:    save_json(WARNED_STUCK_FILE, list(warned_stuck))
    if dirty["nodue"]:    save_json(WARNED_NODUE_FILE, list(warned_nodue))
    if dirty["nouser"]:   save_json(WARNED_NOUSER_FILE, list(warned_nouser))
    if dirty["seen"]:     save_json(CARD_SEEN_DATE_FILE, card_seen_date)


# ═══════════════════════════════════════════════════════
#  STATISTIKA HISOBLASH (umumiy)
# ═══════════════════════════════════════════════════════
def calculate_stats(cards=None, list_map=None, member_map=None):
    if cards is None:     cards = get_cards()
    if list_map is None:  list_map = build_list_map()
    if member_map is None: member_map = build_member_map()

    year_cards = [c for c in cards if is_current_year_card(c)]

    total = active = done = overdue = no_due = 0
    column_stats   = {}
    employee_stats = {}

    for card in year_cards:
        list_name   = list_map.get(card.get("idList"), "Noma'lum")
        done_status = is_done(list_name)
        over_status = is_overdue(card, list_name)
        no_due_stat = not card.get("due") and not done_status

        total += 1
        if done_status:
            done += 1
        else:
            active += 1
            column_stats[list_name] = column_stats.get(list_name, 0) + 1

        if over_status: overdue += 1
        if no_due_stat: no_due  += 1

        members_on_card = card.get("idMembers", [])
        keys = members_on_card if members_on_card else ["__unassigned__"]
        if not members_on_card:
            member_map["__unassigned__"] = "— Biriktirilmagan —"

        for mid in keys:
            emp = member_map.get(mid, mid)
            if emp not in employee_stats:
                employee_stats[emp] = {
                    "total": 0, "active": 0,
                    "done": 0, "overdue": 0, "no_due": 0
                }
            s = employee_stats[emp]
            s["total"]   += 1
            s["done"]    += 1 if done_status else 0
            s["active"]  += 0 if done_status else 1
            s["overdue"] += 1 if over_status else 0
            s["no_due"]  += 1 if no_due_stat else 0

    return {
        "total": total, "active": active, "done": done,
        "overdue": overdue, "no_due": no_due,
        "column_stats": column_stats,
        "employee_stats": employee_stats,
        "year_cards": year_cards,
        "list_map": list_map,
        "member_map": member_map,
    }


# ═══════════════════════════════════════════════════════
#  /HISOBOT — TO'LIQ STATISTIKA
# ═══════════════════════════════════════════════════════
def generate_report():
    s = calculate_stats()
    today = now_tashkent().strftime("%d.%m.%Y %H:%M")

    text  = f"📊 <b>Xarid bo'limi — {current_year()} yil hisoboti</b>\n"
    text += f"🗓 <i>{today} (Toshkent)</i>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"

    text += "📦 <b>UMUMIY</b>\n"
    text += f"  Jami zayavka: {s['total']} ta\n"
    text += f"  🔄 Jarayonda: {s['active']} ta\n"
    text += f"  ✅ Yopilgan:  {s['done']} ta\n"
    if s['overdue']:
        text += f"  ❌ Kechikkan: {s['overdue']} ta\n"
    if s['no_due']:
        text += f"  📭 Muddatsiz: {s['no_due']} ta\n"

    text += "\n━━━━━━━━━━━━━━━━━━━━━━\n"
    text += "📂 <b>USTUNLAR BO'YICHA (jarayonda)</b>\n"
    for col, cnt in sorted(s["column_stats"].items(), key=lambda x: -x[1]):
        text += f"  {col}: {cnt} ta\n"

    text += "\n━━━━━━━━━━━━━━━━━━━━━━\n"
    text += "👨‍💼 <b>XODIMLAR BO'YICHA</b>\n"
    text += "<i>(bitta zayavkada bir nechta mas'ul → alohida hisoblanadi)</i>\n\n"

    for emp, st in sorted(s["employee_stats"].items(), key=lambda x: -x[1]["total"]):
        text += f"👤 <b>{emp}</b>\n"
        text += f"   📌 Jami ishtirok: {st['total']} ta\n"
        text += f"   🔄 Jarayonda: {st['active']} ta  |  ✅ Yopilgan: {st['done']} ta\n"
        if st['overdue']: text += f"   ❌ Kechikkan: {st['overdue']} ta\n"
        if st['no_due']:  text += f"   📭 Muddatsiz: {st['no_due']} ta\n"
        text += "\n"

    return text


# ═══════════════════════════════════════════════════════
#  /KECHIKKANLAR — MUDDATI O'TGAN KARTALAR
# ═══════════════════════════════════════════════════════
def generate_overdue_report():
    cards      = get_cards()
    list_map   = build_list_map()
    member_map = build_member_map()

    overdue_cards = [
        c for c in cards
        if is_current_year_card(c)
        and is_overdue(c, list_map.get(c.get("idList"), ""))
    ]

    if not overdue_cards:
        return "✅ <b>Kechikkan zayavkalar yo'q!</b>"

    overdue_cards.sort(key=lambda c: due_date(c) or today_tashkent())

    text = f"❌ <b>Muddati o'tgan zayavkalar</b> — {len(overdue_cards)} ta\n"
    text += f"🗓 <i>{now_tashkent().strftime('%d.%m.%Y %H:%M')}</i>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"

    for card in overdue_cards:
        d = due_date(card)
        days_passed = (today_tashkent() - d).days if d else 0
        list_name = list_map.get(card.get("idList"), "—")
        members   = member_names_plain(card, member_map)
        text += (
            f"📌 <b>{card.get('name','Nomsiz')}</b>\n"
            f"   📂 {list_name}  |  👥 {members}\n"
            f"   📅 Muddat edi: {d.strftime('%d.%m.%Y') if d else '—'} "
            f"(<b>{days_passed} kun kechikkan</b>)\n"
            f"   🔗 {short_url(card)}\n\n"
        )

    return text


# ═══════════════════════════════════════════════════════
#  /BUGUN — BUGUN TUGAYDIGAN KARTALAR
# ═══════════════════════════════════════════════════════
def generate_today_report():
    cards      = get_cards()
    list_map   = build_list_map()
    member_map = build_member_map()
    today      = today_tashkent()

    today_cards = [
        c for c in cards
        if is_current_year_card(c)
        and due_date(c) == today
        and not is_done(list_map.get(c.get("idList"), ""))
    ]

    if not today_cards:
        return "✅ <b>Bugun muddati tugaydigan zayavkalar yo'q.</b>"

    text = f"🔴 <b>Bugun muddati tugaydi</b> — {len(today_cards)} ta\n"
    text += f"📅 <i>{today.strftime('%d.%m.%Y')}</i>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"

    for card in today_cards:
        list_name = list_map.get(card.get("idList"), "—")
        members   = member_names_plain(card, member_map)
        text += (
            f"📌 <b>{card.get('name','Nomsiz')}</b>\n"
            f"   📂 {list_name}  |  👥 {members}\n"
            f"   🔗 {short_url(card)}\n\n"
        )

    return text


# ═══════════════════════════════════════════════════════
#  /QIDIR — ZAYAVKA NOMI BO'YICHA QIDIRUV
# ═══════════════════════════════════════════════════════
def search_cards(query):
    if not query or len(query) < 2:
        return "❓ Qidiruv so'zini kiriting. Masalan: /qidir PPU"

    cards      = get_cards()
    list_map   = build_list_map()
    member_map = build_member_map()
    q          = query.lower()

    found = [
        c for c in cards
        if q in c.get("name", "").lower()
        or q in c.get("desc", "").lower()
    ]

    if not found:
        return f"🔍 <b>«{query}»</b> bo'yicha hech narsa topilmadi."

    text = f"🔍 <b>«{query}»</b> bo'yicha topildi: {len(found)} ta\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"

    for card in found[:15]:  # Eng ko'pi 15 ta
        list_name = list_map.get(card.get("idList"), "—")
        members   = member_names_plain(card, member_map)
        done_mark = "✅ " if is_done(list_name) else ""
        text += (
            f"{done_mark}📌 <b>{card.get('name','Nomsiz')}</b>\n"
            f"   📂 {list_name}  |  👥 {members}\n"
            f"   📅 {due_text(card)}\n"
            f"   🔗 {short_url(card)}\n\n"
        )

    if len(found) > 15:
        text += f"<i>... va yana {len(found)-15} ta. Aniqroq so'z kiriting.</i>"

    return text


# ═══════════════════════════════════════════════════════
#  /MENING_ZAYAVKALARIM — SHAXSIY BUYRUQ (shaxsiy chat)
# ═══════════════════════════════════════════════════════
def generate_personal_report(telegram_username):
    """Telegram @username bo'yicha o'sha xodimning zayavkalarini topadi."""
    cards      = get_cards()
    list_map   = build_list_map()
    member_map = build_member_map()

    # Trello ismi → Telegram username teskari xarita
    tg_to_trello = {v.lstrip("@").lower(): k for k, v in MEMBER_TELEGRAM_MAP.items()}
    trello_name  = tg_to_trello.get(telegram_username.lstrip("@").lower())

    # Trello member_map'dan mos id ni topamiz
    target_ids = set()
    for mid, mname in member_map.items():
        if trello_name and mname == trello_name:
            target_ids.add(mid)
        elif telegram_username.lstrip("@").lower() in mname.lower():
            target_ids.add(mid)

    if not target_ids:
        return (
            f"⚠️ Siz (<b>@{telegram_username}</b>) Trello bilan bog'lanmagan.\n\n"
            f"Bot adminiga murojaat qiling — u sizning Trello ismingizni "
            f"<code>MEMBER_TELEGRAM_MAP</code> ga qo'shsin."
        )

    my_cards = [
        c for c in cards
        if any(mid in target_ids for mid in c.get("idMembers", []))
        and is_current_year_card(c)
    ]

    active_cards  = [c for c in my_cards if not is_done(list_map.get(c.get("idList",""),""))]
    done_cards    = [c for c in my_cards if is_done(list_map.get(c.get("idList",""),""))]
    overdue_cards = [c for c in active_cards if is_overdue(c, list_map.get(c.get("idList",""),""))]

    name_display = trello_name or telegram_username
    text  = f"👤 <b>{name_display} — shaxsiy hisobot</b>\n"
    text += f"🗓 <i>{now_tashkent().strftime('%d.%m.%Y %H:%M')}</i>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    text += f"📌 Jami: {len(my_cards)} ta  |  🔄 Jarayonda: {len(active_cards)} ta  |  ✅ Yopilgan: {len(done_cards)} ta\n"
    if overdue_cards:
        text += f"❌ Kechikkan: {len(overdue_cards)} ta\n"
    text += "\n"

    if active_cards:
        text += "🔄 <b>JARAYONDA:</b>\n"
        active_sorted = sorted(active_cards, key=lambda c: due_date(c) or today_tashkent())
        for card in active_sorted:
            list_name = list_map.get(card.get("idList"), "—")
            overdue_mark = " ❌" if is_overdue(card, list_name) else ""
            text += (
                f"  • <b>{card.get('name','Nomsiz')}</b>{overdue_mark}\n"
                f"    📂 {list_name}  |  📅 {due_text(card)}\n"
                f"    🔗 {short_url(card)}\n"
            )
        text += "\n"

    return text


# ═══════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════
#  LABEL BO'YICHA HISOBOT FUNKSIYASI
# ═══════════════════════════════════════════════════════
def generate_label_report(label_name):
    cards      = get_cards()
    list_map   = build_list_map()
    member_map = build_member_map()

    emoji = get_label_emoji(label_name)
    label_short = label_name.replace("Zayavka turi: ", "")

    year_cards    = [c for c in cards if is_current_year_card(c)]
    label_cards   = filter_cards_by_label(year_cards, label_name)
    active_cards  = [c for c in label_cards if not is_done(list_map.get(c.get("idList",""),""))]
    done_cards    = [c for c in label_cards if is_done(list_map.get(c.get("idList",""),""))]
    overdue_cards = [c for c in active_cards if is_overdue(c, list_map.get(c.get("idList",""),""))]

    today = now_tashkent().strftime("%d.%m.%Y")
    text  = f"{emoji} <b>{label_short} zayavkalar hisoboti</b>\n"
    text += f"🗓 <i>{today}</i>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    text += f"📌 Jami: {len(label_cards)} ta\n"
    text += f"🔄 Jarayonda: {len(active_cards)} ta\n"
    text += f"✅ Yopilgan: {len(done_cards)} ta\n"
    if overdue_cards:
        text += f"❌ Kechikkan: {len(overdue_cards)} ta\n"

    if active_cards:
        text += "\n━━━━━━━━━━━━━━━━━━━━━━\n"
        text += "🔄 <b>JARAYONDA:</b>\n\n"
        active_sorted = sorted(active_cards, key=lambda c: due_date(c) or today_tashkent())
        for card in active_sorted[:20]:
            list_name = list_map.get(card.get("idList"), "—")
            members   = member_names_plain(card, member_map)
            overdue_mark = " ❌" if is_overdue(card, list_name) else ""
            text += (
                f"📌 <b>{card.get('name','Nomsiz')}</b>{overdue_mark}\n"
                f"   👥 {members}  |  📅 {due_text(card)}\n"
                f"   🔗 {short_url(card)}\n\n"
            )
        if len(active_cards) > 20:
            text += f"<i>... va yana {len(active_cards)-20} ta</i>\n"

    return text


# ═══════════════════════════════════════════════════════
#  TELEGRAM BUYRUQLARI
# ═══════════════════════════════════════════════════════
def get_latest_update_id():
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
            timeout=20
        ).json()
        updates = r.get("result", [])
        return updates[-1]["update_id"] if updates else None
    except Exception:
        return None


def handle_telegram_commands():
    global last_update_id

    params = {"timeout": 20}
    if last_update_id is not None:
        params["offset"] = last_update_id + 1

    try:
        result = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
            params=params, timeout=30
        ).json()
    except Exception as e:
        print("Telegram getUpdates xato:", e)
        return

    for update in result.get("result", []):
        last_update_id = update["update_id"]

        msg     = update.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text    = msg.get("text", "").strip()
        username = msg.get("from", {}).get("username", "")

        if not text:
            continue

        parts = text.split()
        cmd   = parts[0].lower().split("@")[0]

        # Guruh buyruqlari
        if chat_id == CHAT_ID:
            if cmd in ["/hisobot", "hisobot"]:
                send_message("⏳ Hisobot tayyorlanmoqda...")
                send_message(generate_report())

            elif cmd == "/kechikkanlar":
                send_message(generate_overdue_report())

            elif cmd == "/mahalliy":
                send_message("⏳ Mahalliy zayavkalar tayyorlanmoqda...")
                send_message(generate_label_report(LABEL_MAHALLIY))

            elif cmd == "/import":
                send_message("⏳ Import zayavkalar tayyorlanmoqda...")
                send_message(generate_label_report(LABEL_IMPORT))

            elif cmd == "/aralash":
                send_message("⏳ Aralash zayavkalar tayyorlanmoqda...")
                send_message(generate_label_report(LABEL_ARALASH))

            elif cmd == "/bugun":
                send_message(generate_today_report())

            elif cmd == "/qidir":
                query = " ".join(parts[1:]).strip()
                send_message(search_cards(query))

            elif cmd in ["/help", "/start"]:
                send_message(
                    "🤖 <b>Trello-Telegram Bot — Buyruqlar</b>\n\n"
                    "<b>Guruh buyruqlari:</b>\n"
                    "/hisobot — To'liq statistika\n"
                    "/kechikkanlar — Muddati o'tgan zayavkalar\n"
                    "/bugun — Bugun muddati tugaydigan zayavkalar\n"
                    "/qidir [so'z] — Zayavka qidirish\n"
                    "/help — Yordam\n\n"
                    "<b>Shaxsiy buyruq (bot bilan shaxsiy chat):</b>\n"
                    "/mening_zayavkalarim — Faqat o'z zayavkalarim\n\n"
                    "<b>Avtomatik bildirishnomalar:</b>\n"
                    "🆕 Yangi zayavka qo'shilganda\n"
                    "✅ Zayavka yopilganda\n"
                    "🔄 Ustun o'zgarganda\n"
                    f"⏰ Muddatga {DUE_WARN_DAYS} kun qolganda\n"
                    "❌ Muddat o'tib ketganda\n"
                    "🕸️ Zayavka 7+ kun qotib qolsa\n"
                    "📭 Muddat qo'yilmasa (3 kundan keyin)\n"
                    "👤 Mas'ul biriktirilmasa\n"
                )

        # Shaxsiy chat buyruqlari (bot bilan to'g'ridan-to'g'ri)
        else:
            if cmd == "/mening_zayavkalarim":
                if not username:
                    send_message(
                        "⚠️ Telegram username'ingiz yo'q. "
                        "Telegram sozlamalaridan username qo'ying.",
                        chat_id=chat_id
                    )
                else:
                    send_message("⏳ Sizning zayavkalaringiz qidirilmoqda...", chat_id=chat_id)
                    send_message(generate_personal_report(username), chat_id=chat_id)

            elif cmd in ["/start", "/help"]:
                send_message(
                    "🤖 <b>Trello-Telegram Bot</b>\n\n"
                    "Shaxsiy buyruq:\n"
                    "/mening_zayavkalarim — Trellodagi o'z zayavkalaringiz\n\n"
                    "<i>Guruh buyruqlari uchun botni guruhga qo'shing.</i>",
                    chat_id=chat_id
                )


# ═══════════════════════════════════════════════════════
#  ASOSIY SIKL
# ═══════════════════════════════════════════════════════
def bot_loop():
    global last_update_id

    print("🤖 Bot ishga tushmoqda (v3)...")
    last_update_id = get_latest_update_id()
    initialize_known_cards()
    print(f"✅ Bot tayyor. Har {CHECK_INTERVAL} soniyada tekshiriladi.")

    while True:
        try:
            handle_telegram_commands()
            check_trello_changes()
        except Exception as e:
            ts = now_tashkent().strftime("%H:%M:%S")
            print(f"[{ts}] Xato: {e}")
        time.sleep(CHECK_INTERVAL)


# Gunicorn va to'g'ridan-to'g'ri ishganda ham thread boshlanadi
def start_bot():
    t = threading.Thread(target=bot_loop, daemon=True)
    t.start()

start_bot()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
