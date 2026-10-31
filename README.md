# Claude Instagram Telegram Bot

Telegram orqali topshiriq berasiz → Claude motion video yasaydi, caption yozadi → siz tasdiqlaysiz → Instagram'ga Reels bo'lib chiqadi.

```
Telegram → app/bot.py → app/agent.py (Claude Agent SDK)
                           ├─ ask_user             → tugmali so'rovnoma (g'oya, fon, ovoz)
                           ├─ generate_image       → app/images.py (OpenAI: qahramon, stiker, fon)
                           ├─ create_motion_video  → app/video.py (Pillow + ffmpeg, 1080x1920, emoji)
                           │                         + app/tts.py (VoiceLab, zaxira: bepul Edge TTS)
                           ├─ request_publish      → ✅/❌ tugmalar → app/instagram.py (Graph API)
                           └─ get_instagram_account
```

## 0. Claude token (API key shart emas)

Claude.ai **Pro/Max** obunangiz bo'lsa, terminalda quyidagini ishga tushiring:

```bash
claude setup-token
```

Brauzerda tasdiqlang. Chiqqan `sk-ant-oat01-...` tokenni `CLAUDE_CODE_OAUTH_TOKEN` ga qo'ying. Token ~1 yil amal qiladi.

> ⚠️ Bu token sizning shaxsiy obunangiz. Botni faqat o'zingiz ishlating (`ALLOWED_USER_IDS`) va uni boshqalarga xizmat sifatida taqdim etmang. Bot obunangiz limitlarini sarflaydi.
> Tokenni hech kimga bermang va GitHub'ga yuklamang.

## 1. Lokal sinov

```bash
python -m venv .venv && .venv\Scripts\activate     # Windows
pip install -r requirements.txt
copy .env.example .env                              # qiymatlarni to'ldiring
python -m app.bot
```

Kompyuterda `ffmpeg` o'rnatilgan bo'lishi kerak. `PUBLIC_URL` bo'sh bo'lsa, bot polling rejimida ishlaydi: video yasash ishlaydi, lekin Instagram'ga chiqarish ishlamaydi, chunki Instagram videoni ochiq URL'dan yuklab oladi.

Telegram ID'ingizni bilish uchun botga `/id` yozing va uni `ALLOWED_USER_IDS` ga qo'ying.

## 2. Render'ga deploy

1. Loyihani GitHub'ga yuklang (`.env` fayli `.gitignore` da, u yuklanmaydi).
2. Render → **New → Blueprint** → repo'ni tanlang. `render.yaml` avtomatik o'qiladi.
3. Environment bo'limida quyidagilarni kiriting: `TELEGRAM_BOT_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, `VOICELAB_API_KEY`, `OPENAI_API_KEY`, `ALLOWED_USER_IDS`, `IG_USER_ID`, `IG_ACCESS_TOKEN`.
4. Deploy. Webhook `RENDER_EXTERNAL_URL/telegram` ga avtomatik o'rnatiladi.

> Free plan: 15 daqiqa harakatsizlikdan keyin servis uxlaydi (birinchi xabarga javob ~1 daqiqa kechikadi) va 512 MB RAM video render uchun kam bo'lishi mumkin. Shu sabab `starter` plan tavsiya qilinadi.
> Render diski vaqtinchalik: deploy yoki qayta ishga tushishdan keyin `media/` dagi videolar o'chadi.

## 3. Instagram ulash

1. Instagram akkauntni **Business** yoki **Creator** ga o'tkazing va Facebook sahifasiga ulang.
2. [developers.facebook.com](https://developers.facebook.com) → yangi App yarating (turi: Business).
3. Instagram Graph API mahsulotini qo'shing. Ruxsatlar: `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`.
4. Graph API Explorer'da token oling va uni **long-lived** (60 kunlik) tokenga almashtiring.
5. `IG_USER_ID` ni olish: `GET /me/accounts` → sahifa ID → `GET /{page-id}?fields=instagram_business_account`.

Agar "Instagram Login" usuli bilan token olgan bo'lsangiz, `IG_GRAPH_HOST=graph.instagram.com` qiling.

## Video uslublari

| Uslub | Ko'rinish |
|---|---|
| `terminal` | qora fon, yashil mono shrift, kod oynalari, kartochkalar, pastda so'zma-so'z subtitr |
| `glass` | och fon, rangli xira dog'lar, oq kartochkalar, o'ng pastda qahramon |
| `slides` | katta qalin sarlavhalar, raqamli ro'yxatlar, `01/10` hisoblagich |
| `kinetic` | qora fon, katta matn kursor bilan yoziladi |
| `classic` | rangli gradient, to'lqin, setka va bokeh fonlar |

Elementlar: `cards`, `list`, `code`, `number`, `chips`. Shriftlar (Inter, JetBrains Mono, Anton) `app/fonts` papkasida, SIL Open Font License asosida.

## Pechat (watermark)

Bot yaratgan har bir videoning o'ng pastki burchagiga `husanbek-coder.png` logosi qo'yiladi. Logo orqasida yarim shaffof to'q plashka bor, shuning uchun och fonda ham o'qiladi. Sozlamalar: `WATERMARK_PATH`, `WATERMARK_WIDTH`, `WATERMARK_OPACITY`. Render'da ishlashi uchun `husanbek-coder.png` repo'ga commit qilingan bo'lishi kerak.

## Outro

Har bir video oxiriga `husanbek-coder.mp4` (5 s) qo'shiladi. Uning qora foni shaffof qilinadi va logo oxirgi sahnaning foni ustida chiqadi. Video och bo'lsa, logodagi oq yozuv to'q rangga o'tadi, rangli nurlar o'zgarmaydi. Sozlama: `OUTRO_PATH`.

## Shaxsiy avatar

`husanbek/` papkasidagi rasmlardan OpenAI (`gpt-image-2`) 3D qahramon-stikerlar yasaydi. Har bir poza `avatar_cache/` ga saqlanadi va qayta pul to'lanmaydi. `husanbek/` papkasi `.gitignore` da. Render'da yangi pozalar yaratilishi kerak bo'lsa, rasmlarni xususiy (private) repo orqali yuklang yoki `avatar_cache/` dagi tayyor pozalarni commit qiling.

## Buyruqlar

- `/start`: yo'riqnoma
- `/new`: suhbat tarixini tozalash
- `/id`: Telegram ID'ingiz
