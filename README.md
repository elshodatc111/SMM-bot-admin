# AdminBot — litsenziya boti va admin panel

Telegram Group Post dasturi litsenziyalarini sotadigan bitta bot + veb admin panel + dastur uchun litsenziya API.
Stek: Python 3.10+, FastAPI, aiogram 3 (webhook), SQLite (WAL), Payme Merchant API.

## Nima qiladi
- **Bot:** majburiy ro'yxatdan o'tish (ism-familiya, telefon kontakt tugmasi bilan, viloyat) → tarif tanlash → Payme to'lov → bir martalik token. «Litsenziyalarim», «To'lovlarim», «Qurilmani almashtirish» (faqat yillik), muddat tugashi eslatmalari (sozlanadi, standart 7/3/1 kun va tugaganda).
- **Admin panel** (`/admin/`, faqat siz): foydalanuvchilar, litsenziyalar (muddatdan oldin tugatish, muddat o'zgartirish, qurilmani bo'shatish, qo'lda berish), qurilma so'rovlari, to'lovlar, kunlik statistika, tariflar va narxlar, ommaviy xabar (auditoriya tanlab), sozlamalar, Excel eksport (foydalanuvchilar, to'lovlar, litsenziyalar, statistika).
- **Litsenziya API:** `POST /api/v1/license/activate` va `/check`. Javob Ed25519 bilan imzolanadi.
- **Payme:** `POST /payme` (Merchant API).

## Litsenziya qoidalari
1. To'lov bajarilganda token yaratiladi (`TGP-XXXX-XXXX-XXXX-XXXX`), muddat **hali boshlanmaydi**.
2. Dasturga birinchi kiritilganda muddat boshlanadi (1 oy = 30, 3 oy = 90, 1 yil = 365 kun) va token shu kompyuterning barmoq iziga bog'lanadi.
3. **Dastur o'chib ketdi / Windows yangilandi:** muddat serverda saqlanadi. Tokenni shu kompyuterda qayta kiritsa, qolgan muddat bilan davom etadi (yangi 30 kun berilmaydi). Kompyuter `MachineGuid` bilan yoki anakart/disk/protsessor/MAC dan kamida 2 tasi bilan taniladi, shuning uchun Windows'ni qayta o'rnatganda ham ko'pincha taniydi.
4. **Boshqa kompyuter:** rad etiladi. Faqat **yillik** tarif egasi botdagi «Qurilmani almashtirish» tugmasi bilan so'rov yuboradi (har litsenziyaga 2 marta, Sozlamalarda o'zgaradi). Admin ruxsat bersa (Telegramdagi tugma yoki panel), bog'lanish o'chadi va token yangi kompyuterda kiritiladi. Muddat davom etadi.
5. Admin istalgan litsenziyani muddatdan oldin tugatadi (pul qaytarish qo'lda). Dastur navbatdagi tekshiruvda to'xtaydi.
6. Payme to'lovni bekor qilsa: litsenziya hali aktivlashtirilmagan bo'lsa o'chadi; aktivlashtirilgan bo'lsa Payme'ga «qaytarib bo'lmaydi» (-31007) javob beriladi, pulni qo'lda kelishasiz.

7. **Umrbod litsenziya:** sotuvda yo'q. Faqat admin panelda: Foydalanuvchi sahifasi → «Qo'lda litsenziya berish» → «Umrbod (faqat admin)». Token bir martalik, kompyuterga bog'lanadi, muddat tugamaydi (ichkarida `days=0`, tugash sanasi 2100-01-01), muddat o'zgartirish tugmasi yo'q. Qurilma almashtirish yillik kabi (limit bor, admin ruxsati bilan). Dastur javobida `lifetime: true` maydoni keladi.

8. **To'lanmagan buyurtma:** to'lov havolasi berilgandan 1 soat o'tib to'lanmasa, bot eslatma yuboradi (to'lov tugmasi bilan, necha soat qolgani va aks holda bekor bo'lishi aytiladi). 12 soat ichida to'lanmasa, buyurtma avtomatik bekor bo'ladi (ochiq Payme tranzaksiyasi ham), foydalanuvchiga bekor xabari boradi va eski havola bilan to'lov qabul qilinmaydi. Ikkala vaqt Sozlamalarda o'zgaradi (12 soat, 60 daqiqa). Tekshiruv har daqiqa.

## Papkalar
```
app/            main.py (ilova), payme.py, licenses.py, api.py, jobs.py, db.py, config.py, signing.py, ...
app/bot/        handlers.py, keyboards.py, regions.py
app/admin/      __init__.py (sahifalar), auth.py, stats.py, exports.py
app/templates/  admin panel shablonlari      app/static/admin.css
tools/          make_admin_hash.py, gen_secrets.py, set_webhook.py
client_reference/license_client.py   dastur tomoni uchun namuna (keyingi bosqichda ulanadi)
tests/          pytest (39 ta test)
data/           SQLite baza va imzo kaliti (ed25519.key) — ZAXIRALANG, o'chirmang!
```

## 1. Telegram bot yaratish
@BotFather → `/newbot` → token oling. Admin Telegram ID ingizni @userinfobot dan biling.

## 2. Mahalliy (kompyuterda) sinash
1. `run_local.bat` ni ochsangiz `.env` yaratiladi. To'ldiring: `BOT_TOKEN`, `ADMIN_TG_ID` (`BOT_MODE=polling` turaversin; `BASE_URL` va `WEBHOOK_SECRET` shart emas).
2. Parol xeshi: `.venv\Scripts\python tools\make_admin_hash.py` → `ADMIN_PASSWORD_HASH=...` ni `.env` ga qo'ying. `tools\gen_secrets.py` → `WEBHOOK_SECRET`, `SESSION_SECRET`.
3. `run_local.bat` → http://127.0.0.1:8100/admin/ . Botga `/start` yozing.
(Payme mahalliy kompyuterda ishlamaydi: unga ochiq HTTPS manzil kerak.)

## 3. alwaysdata.com ga joylash
1. Akkaunt oching. **Remote access → SSH** ni yoqing.
2. Fayllarni `~/adminbot/` ga yuklang (SFTP yoki `scp`). `.venv`, `data`, `.env` ni yuklamang.
3. SSH orqali:
   ```
   cd ~/adminbot
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   cp .env.example .env && nano .env
   ```
   `.env`: `BOT_TOKEN`, `BASE_URL=https://AKKAUNT.alwaysdata.net`, `WEBHOOK_SECRET`, `SESSION_SECRET`, `ADMIN_PASSWORD_HASH`, `ADMIN_TG_ID`, `PAYME_*` va **`BOT_MODE=webhook`** (`.env.example` da standart `polling`: u faqat kompyuterda sinash uchun).
   (Xeshni kompyuteringizda `make_admin_hash.py` bilan yaratib, `.env` ga ko'chirsangiz ham bo'ladi.)
4. Panel: **Web → Sites → Add a site**: Addresses `AKKAUNT.alwaysdata.net`, Type **User program**, Command:
   ```
   /home/AKKAUNT/adminbot/.venv/bin/uvicorn app.main:app --host $IP --port $PORT --proxy-headers --forwarded-allow-ips "*"
   ```
   Working directory: `/home/AKKAUNT/adminbot`. Saqlang va saytni **Restart** qiling.
5. Tekshiring: `https://AKKAUNT.alwaysdata.net/healthz` → `{"ok":true}`; `/admin/` ga kiring.
6. Webhook ilova ishga tushganda o'zi o'rnatiladi. Tekshirish: `.venv/bin/python tools/set_webhook.py info`.
7. Zaxira: `data/adminbot.sqlite3` va `data/ed25519.key` ni vaqti-vaqti bilan yuklab oling.

> Eslatma: alwaysdata bepul tarifi tavsifida «shaxsiy ehtiyojlar uchun» deyilgan; savdo maqsadida ishlatish shartlarini o'zingiz tekshiring. Kod boshqa xostga (VPS) o'zgarishsiz ko'chadi.

## 4. Payme Business sozlash
Kassa sozlamalarida:
- **Billing URL (Endpoint):** `https://AKKAUNT.alwaysdata.net/payme`
- **Account maydoni:** nomi `order_id` (turi: raqam, majburiy).
- **Kalit (Password):** `.env` dagi `PAYME_KEY` ga yozing. Login har doim `Paycom`.
- **Sinov:** avval `PAYME_TEST=1` (test kalit va test checkout), Payme sandbox'da hamma metodlarni tekshirib chiqing. Keyin `PAYME_TEST=0` va ishchi kalit.
Bot to'lov havolasini `m=MERCHANT_ID;ac.order_id=...;a=TIYIN` ko'rinishida yaratadi.

## 5. Admin panelda birinchi qadamlar
1. **Tariflar:** 1 oylik / 3 oylik / 1 yillik narxlarini kiriting va «Sotuvda» belgisini qo'ying (narxi 0 bo'lsa sotuvga chiqmaydi).
2. **Sozlamalar:** salomlashuv, yordam matni, eslatma kunlari, almashtirish limiti. U yerda **imzo ochiq kaliti** ham ko'rinadi (dasturga joylanadi).
3. Botga o'zingiz `/start` bering va to'lov oqimini sinab ko'ring.

## Dastur (Telegram Group Post) bilan ulash — keyingi bosqich
`client_reference/license_client.py` namuna: barmoq izi yig'ish, `/activate`, imzoni tekshirish. Dastur: token kiritiladi → `activate` → javob imzosi ochiq kalit bilan tekshiriladi → `expires_at` va `fp_hash` shifrlab saqlanadi → har ishga tushganda `check` (internet shart).

## Keyinchalik o'zgartirish kiritish (xavfsiz yangilash)
Serverga hozir yuklab, keyin yangilayverish mumkin. Qoidalar:
1. Yangilashda faqat kod fayllarini almashtiring (`app/`, `tools/`, `requirements.txt`). **`data/` va `.env` ga tegmang** — baza, tokenlar, imzo kaliti shu yerda.
2. Baza sxemasi o'zgarsa, `app/db.py` dagi `MIGRATIONS` ro'yxatiga yangi SQL qo'shiladi; ilova ishga tushganda mavjud ma'lumotlarni saqlagan holda o'zi qo'llaydi.
3. `data/ed25519.key` ni hech qachon almashtirmang va yo'qotmang: aks holda dasturga joylangan ochiq kalit mos kelmay qoladi.
4. Litsenziya API javob formati (`ok`, `code`, `payload`, `sig`) va kodlarini o'zgartirmasdan faqat qo'shimcha maydon qo'shing: eski o'rnatilgan dasturlar ishlashda davom etadi.
5. Yangilashdan oldin `data/` papkasini nusxalab oling.

## Sinov
```
pip install -r requirements.txt pytest httpx
python -m pytest tests -q
```
