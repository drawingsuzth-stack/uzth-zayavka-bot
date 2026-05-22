# 🤖 Trello → Telegram Bot v3

## Yangi funksiyalar (v2 dan farqi)

| Funksiya | Tavsif |
|----------|--------|
| 🌅 Ertalabki digest | Har kuni 09:00 da bugun tugaydigan + kechikkan zayavkalar |
| 📈 Haftalik hisobot | Har dushanba — hafta xulosasi, top xodimlar |
| 🕸️ Qotib qolgan zayavka | 7+ kun o'zgarishsiz turgan kartalar uchun ogohlantirish |
| 📭 Muddatsiz eslatma | 3 kundan ortiq muddatsiz tursa eslatma |
| 👤 Mas'ulsiz ogohlantirish | Hech kim biriktirilmagan karta qo'shilsa xabar |
| 🔗 Telegram @mention | Bildirishnomada mas'ul @username bilan mention qilinadi |
| /kechikkanlar | Muddati o'tgan barcha zayavkalar ro'yxati |
| /bugun | Bugun muddati tugaydigan zayavkalar |
| /qidir [so'z] | Zayavka nomi bo'yicha qidiruv |
| /mening_zayavkalarim | Shaxsiy chatda — faqat o'z zayavkalarim |

---

## O'rnatish

### 1. Environment variables (.env yoki Render dashboard)

```
BOT_TOKEN=your_telegram_bot_token
CHAT_ID=-100your_group_id
TRELLO_KEY=your_trello_api_key
TRELLO_TOKEN=your_trello_token
BOARD_ID=your_board_id
```

### 2. Trello ↔ Telegram @mention sozlash

`main.py` faylida `MEMBER_TELEGRAM_MAP` ni to'ldiring:

```python
MEMBER_TELEGRAM_MAP = {
    "Tashkenbayev B.":    "@tashkenbayev",
    "Umaraliyev B.":      "@umaraliyev",
    "Abduraxmonova V.":   "@abduraxmonova",
    "Usmonov B.":         "@usmonov",
    "Vahobov X.":         "@vahobov",
    "Zohidjonov J.":      "@zohidjonov",
}
```

> Trellodagi ismni **aynan** `member_map` dagi kabi yozing.
> Trello ismlari: Render loglarida `✅ Boshlang'ich holat saqlandi` xabaridan keyin
> yoki `/hisobot` buyrug'i chiqadigan ismlarni ko'rib oling.

### 3. Ishga tushirish

```bash
pip install -r requirements.txt
python main.py
```

Yoki Render.com da — `render.yaml` ni repo ildiziga qo'ying va deploy qiling.

---

## Buyruqlar

### Guruhda:
| Buyruq | Tavsif |
|--------|--------|
| `/hisobot` | To'liq statistika — xodimlar, ustunlar, jami |
| `/kechikkanlar` | Muddati o'tgan zayavkalar ro'yxati |
| `/bugun` | Bugun muddati tugaydigan zayavkalar |
| `/qidir PPU` | "PPU" so'zi bo'lgan zayavkalar |
| `/help` | Barcha buyruqlar |

### Bot bilan shaxsiy chatda:
| Buyruq | Tavsif |
|--------|--------|
| `/mening_zayavkalarim` | Faqat o'z zayavkalarim (MEMBER_TELEGRAM_MAP kerak) |

---

## Sozlamalar (main.py yuqorisida)

```python
DONE_LIST_NAME   = "YOPILGAN"  # Yopilgan ustun nomi
DUE_WARN_DAYS    = 5           # Necha kun qolsa ogohlantirish
STUCK_DAYS       = 7           # Necha kun qotib qolsa ogohlantirish
NO_DUE_WARN_DAYS = 3           # Muddatsiz karta necha kundan keyin eslatilsin
DAILY_DIGEST_HOUR = 9          # Ertalabki digest soati
WEEKLY_REPORT_DAY = 0          # 0=Dushanba, 4=Juma
```
