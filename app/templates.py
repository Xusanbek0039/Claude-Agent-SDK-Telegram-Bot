"""/shablon: namunalar papkasidagi 18 ta video asosida tayyor video shablonlari.

Har bir shablon: qaysi uslub (theme), musiqa, video qanday ko'rinishi (foydalanuvchi uchun) va
agentga ko'rsatma (sahnalar tuzilishi, qaysi elementlar ishlatiladi).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Template:
    id: str
    sample: str        # namunalar papkasidagi fayl raqami(lari)
    name: str          # tugmadagi nom
    theme: str
    music: str
    description: str   # foydalanuvchiga: video qanday chiqadi
    guide: str         # agentga: sahnalarni qanday qurish
    example: str       # foydalanuvchiga: buyruq namunasi


COMMON = (
    "Shablon tanlangan: uslub va ovoz haqida SAVOL BERMA, darhol video yasa. Ovoz: voice_id=\"men\" "
    "(foydalanuvchining o'z ovozi). Har sahnada narration bo'lsin (jonli o'zbek tili, 1-2 jumla). "
    "Har sahnaga mos emoji qo'y. 1-sahna kuchli hook, oxirgi sahna CTA."
)

TEMPLATES: list[Template] = [
    Template(
        "chat-ekran", "1", "💬 Claude chat ekrani", "chatui", "tech",
        "Qorong'i Claude ilovasi ekrani: tepada «Chat | Code», o'ngda /buyruq yorlig'i, «✳ Editing with…» "
        "yozuvi, pastda «Reply to Claude…» maydoni. O'rtada kartochkalar, kod va teglar chiqadi. "
        "Vosita, effekt yoki buyruqlarni namoyish qilish uchun.",
        "theme=chatui, label = asosiy buyruq yoki vosita nomi (masalan 'match-cut'). 5-8 sahna: har sahnada "
        "bitta effekt yoki imkoniyat. heading qisqa, elements: cards (icon+title+text), chips yoki code.",
        "Claude Code'ning 6 ta foydali buyrug'i haqida video. Buyruq: /effects"),
    Template(
        "terminal", "2", "🖥 Terminal (xakerlik)", "terminal", "tech",
        "Qora fon, yashil mono shrift, «root@…» sarlavhasi. Kartochkalar, sanab o'suvchi raqamlar, yozilib "
        "chiqadigan kod oynalari. Pastda so'zma-so'z subtitr, aytilayotgan so'z yashil yonadi.",
        "theme=terminal. 6-9 sahna. elements: code (buyruqlar, $ bilan), number (statistika), cards, list. "
        "heading'da *urg'u* so'z bo'lsin.",
        "Saytlardagi 5 ta xavfli xato haqida, xakerlar qanday hujum qiladi"),
    Template(
        "glass-qahramon", "3", "🧑‍🏫 Glass + mening qahramonim", "glass", "bright",
        "Och shisha uslubi: rangli xira dog'lar, oq kartochkalar, kalit so'z tagiga chiziq. O'ng pastda sizning "
        "3D qahramoningiz turli pozalarda (o'ylayapti, ko'rsatyapti, bosh barmoq). Tepada so'zma-so'z sarlavha.",
        "theme=glass. Avval avatar_sticker bilan 2-4 xil poza yarat va sahnalarda character qilib almashtirib "
        "ishlat. elements: cards, code, chips. 6-9 sahna.",
        "Claude Code bilan 5 daqiqada video yasash haqida qo'llanma"),
    Template(
        "karusel", "4", "📑 Karusel slaydlar", "slides", "upbeat",
        "Qora setkali fon, katta qalin bosh harflar (sarlavhaning bir qismi to'q-sariq), raqamli ro'yxatlar, "
        "tepada 01/10 hisoblagich, pastda bo'laklangan progress. Instagram karuselining video ko'rinishi.",
        "theme=slides. 7-10 sahna (har biri slayd). heading katta (3-6 so'z), elements: list, cards, code. "
        "1-2 sahnada variant='light' (och slayd).",
        "Prompt yozish formulasi: 8 slaydli qo'llanma"),
    Template(
        "kinetik", "5", "💔 Kinetik his-tuyg'u", "kinetic", "emotional",
        "Qora fon, iliq yorug'lik. Matn katta mono shriftda qiya holda kursor bilan yozilib boradi, urg'u "
        "so'zlar sariq. Orasida katta 3D emoji va qalin sarlavhali sahnalar.",
        "theme=kinetic, music=emotional. 6-10 sahna, narration she'riy va his-tuyg'uli, *urg'u* so'zlar. "
        "Ba'zi sahnalarda emoji (💔, 🌙, 💡) va qisqa heading; variant='#ff6a00' kabi yorug'lik ranglarini almashtir.",
        "Yolg'izlik va o'zini topish haqida his-tuyg'uli matn"),
    Template(
        "kod-sheriyat", "6", "⌨️ Kod she'riyati", "kinetic", "emotional",
        "Qorong'i fon, kod qatorlari ko'rinishidagi she'r: while(true), if, drive() kabi so'zlar yozilib "
        "boradi, qizil kursor. Hayotiy fikrlar dasturchi tilida.",
        "theme=kinetic. narration = kod kabi yozilgan fikrlar (o'zbekcha izoh bilan), har sahnada elements: "
        "code (2-4 qator, masalan 'while (hayot) { o'rgan(); }'). label = mavzu.",
        "Dasturchi hayoti haqida kod ko'rinishidagi she'r"),
    Template(
        "top-royxat", "7", "🔟 Top ro'yxat kartochkalari", "warm", "bright",
        "Iliq och fon, har sahnada bitta katta oq kartochka: illyustratsiya (emoji/diagramma), qalin nom va "
        "tagiga chizilgan izoh. Tepada «03 / 10» hisoblagich va progress. Oxirida «Kommentga yozing» chaqirig'i.",
        "theme=warm. 1-sahna: intro (heading 'Claude uchun *10 ta* kuchli repo', emoji). Keyin har sahna = "
        "bitta element: heading = nom, subheading = qisqa izoh, emoji yoki diagram/chips. Oxirgi sahna CTA: "
        "elements profile (name=@handle, button='Obuna bo'lish').",
        "Dasturchilar uchun 7 ta foydali AI vosita"),
    Template(
        "lime-biznes", "8", "⚡ Lime biznes", "lime", "upbeat",
        "Krem va yorqin lime fonlar almashadi, tunda ko'k osmon sahnasi. Qora qalin matn, urg'u so'zlar "
        "lime/qora plashkada. Robot maskot 🤖, katta raqamlar, ⚡ va 🚀 belgilar.",
        "theme=lime. Sahnalarda variant ni almashtir: '' (krem), 'lime', 'night'. heading'da *urg'u* so'z "
        "(plashkada chiqadi). emoji: 🤖 ⏰ 💰 ⚡ 🚀. elements: number (masalan '291' soat), chips, list.",
        "Biznesingizni AI bilan avtomatlashtirish: vaqt va pulni tejash"),
    Template(
        "minimal-qora", "9", "🌑 Minimal qorong'i", "minimal", "tech",
        "Qop-qora minimal fon. Tepada so'zma-so'z subtitr, aytilayotgan so'z ko'k tanlov ramkasida. "
        "Qorong'i kod oynalari, katta foiz raqamlari, oxirida profil kartochkasi.",
        "theme=minimal. elements: code, number (masalan '-45', suffix '%'), cards, profile (oxirida). "
        "Bitta sahnada variant='light' (oq qog'oz). heading shart emas (subtitr asosiy).",
        "Ponytail skill: AI agentni senior dasturchiga aylantirish"),
    Template(
        "neon-tushuntirish", "10", "🔵 Neon tushuntirish", "neon", "tech",
        "To'q ko'k fon, markazda nurli shar. Katta oq sarlavhalar, «Coordinator → Frontend/Backend/Review» "
        "kabi nurli diagrammalar, shisha kartochkalar. Pastda subtitr, urg'u so'zlar moviy.",
        "theme=neon, label = mavzu. emoji ishlatma (o'rniga nurli shar avtomatik). elements: diagram "
        "(center + 3-4 nodes), code, cards. 7-10 sahna, murakkab mavzuni sodda tushuntir.",
        "Harness engineering nima: AI agentlar qanday ishlaydi"),
    Template(
        "personaj-dialog", "11", "🧱 Personajlar dialogi", "dialog", "calm",
        "Kod naqshli to'q ko'k fon. Pastda katta personaj: siz va AI yordamchi navbatma-navbat gapirasiz. "
        "Tepada oq qalin subtitr. Qisqa, kulgili suhbat ko'rinishi.",
        "theme=dialog. Har sahna = bitta replika. Sahnalarni almashtir: foydalanuvchi (character = "
        "avatar_sticker image_id, speaker='Men') va AI (emoji='🤖', speaker='Claude'). narration = replika "
        "matni, heading kerak emas. 8-12 sahna.",
        "Men va Claude o'rtasida ovoz klonlash haqida kulgili suhbat"),
    Template(
        "biznes-avtomatlash", "12", "🚀 Biznes avtomatlashtirish", "space", "upbeat",
        "Kosmik to'q fon, yulduzlar. Telefon maketida chat (mijoz yozadi, bot javob beradi), bosqichli "
        "jarayon (1→2→3), soat, nurli AI belgisi. Pastda qora plashkada subtitr.",
        "theme=space, label = mavzu. elements: phone (title + messages [{from:'user'|'bot', text}]), steps "
        "(items + active), cards. Muammo → yechim → natija tuzilishi. 7-10 sahna.",
        "Telegram bot va AI agent biznesga qanday mijoz olib keladi"),
    Template(
        "neon-lab-hikoya", "13", "🎮 Neon laboratoriya: hikoya", "neonlab", "tech",
        "Neon laboratoriya: ko'k chiroqli devorlar, to'q-sariq nur chiziqlari. Pastda personajlar, tepada "
        "bo'lim yorlig'i («1 · VAZIFA BERAMIZ»), subtitr ustida kim gapirayotgani («NODIR», «CLAUDE»).",
        "theme=neonlab, handle va label bering. heading = bo'lim yorlig'i ('1 · Vazifa beramiz'). Sahnalar "
        "dialog: speaker='Men' (character = avatar) va speaker='Claude' (emoji '🤖'). elements: code, cards. 8-12 sahna.",
        "Claude'ga dasturni tahlil qildirish: 3 qismli hikoya"),
    Template(
        "neon-lab-qadam", "14", "🧪 Neon laboratoriya: qadamlar", "neonlab", "tech",
        "Xuddi shu neon laboratoriya, lekin qadamma-qadam qo'llanma: «01 · O'RNATISH», «02 · NIMA QILADI?», "
        "«03 · QAYTA YARATISH». Kod va interfeys panellari chiqadi.",
        "theme=neonlab. heading = '01 · O'rnatish' kabi raqamli bo'lim. speaker almashib turadi (Men / Claude). "
        "elements: code, cards, chips. 8-12 sahna.",
        "GitHub'dagi repo'ni Claude bilan o'rnatib, ishlatish bosqichlari"),
    Template(
        "retro", "15", "🌀 Retro tipografiya", "retro", "upbeat",
        "Krem retro qog'oz, perspektiv setka va doiralar. Ekranda katta 3D harflarda (to'q ko'k va "
        "to'q-sariq) aytilayotgan so'zlar navbat bilan sakrab chiqadi. Musiqiy, dinamik.",
        "theme=retro, music=upbeat. narration qisqa, ta'sirli iboralar (har jumla 3-6 so'z), *urg'u* so'zlar "
        "to'q-sariq chiqadi. heading kerak emas, elements kam (chips bo'lishi mumkin). 8-12 sahna.",
        "Motivatsiya: bilish emas, harakat qilish kerak"),
    Template(
        "ember", "16", "🔥 To'q-sariq tushuntirish", "ember", "tech",
        "Qora fon, markazda to'q-sariq nur. Tepada butun jumla, kalit so'zlar to'q-sariq. Katta to'q-sariq "
        "raqamlar, oq kartochkalar, bitta-ikkita och sahna. Oxirida profil va obuna tugmasi.",
        "theme=ember, handle va label. elements: number (masalan '2:04:37' yoki '5' format), cards, chips, "
        "profile (oxirgi sahna). 1-2 sahnada variant='light'. 8-12 sahna.",
        "Google NotebookLM: 2 soatlik videoni 5 daqiqada o'rganish"),
    Template(
        "monitor", "17", "🖥 Monitor oldida monolog", "monitor", "calm",
        "Yulduzli qora fon, stol ustida monitor. Ekranda matn kursor bilan yozilib boradi (eslatmalar, kod). "
        "Monitor oldida personaj gapiradi, pastda qora plashkada sariq subtitr.",
        "theme=monitor. Har sahnada character (avatar_sticker yoki emoji '🤖'), heading = ekrandagi sarlavha, "
        "elements: code (ekranda yoziladigan 2-6 qator: '> ...', '# ...'). narration = monolog. 6-10 sahna.",
        "AI xotirasi: Claude sizni qanday eslab qoladi"),
    Template(
        "pastel-demo", "18", "🍑 Pastel mahsulot demo", "peach", "bright",
        "Och shaftoli fon. Tepada so'zma-so'z subtitr, aytilayotgan so'z ko'k ramkada. Telefon maketlari, "
        "«Boshlash» tugmasi, 1-2-3 raqamli bosqichlar, oxirida profil va obuna.",
        "theme=peach. elements: phone (ilova chati), steps, number (katta raqam), chips, profile (oxirida). "
        "Mahsulot yoki funksiyani namoyish qil. 7-10 sahna.",
        "Raqobatchining ilovasidagi funksiyani Claude bilan qayta yaratish"),
]

BY_ID = {t.id: t for t in TEMPLATES}


def agent_prompt(t: Template, user_text: str) -> str:
    return (f"[SHABLON: {t.name} — {t.sample}-namuna uslubida]\n{COMMON}\n"
            f"create_motion_video: theme=\"{t.theme}\", music=\"{t.music}\".\n{t.guide}\n\n"
            f"Foydalanuvchi topshirig'i: {user_text}")


# Har bir shablon uchun 3 ta tayyor prompt (botda nusxa olish yoki tugma bilan darhol ishga tushirish)
EXAMPLES: dict[str, tuple[str, ...]] = {
    "chat-ekran": (
        "Claude Code'ning eng foydali 6 ta buyrug'i: /init, /review, /compact va boshqalar. Har biri nima qilishini qisqa ko'rsat. Buyruq: /buyruqlar. Nikim @husanbek_coder",
        "Claude'da video montaj effektlari: match-cut, screen-push, liquid-glass. Har bir effektni alohida sahnada tanishtir. Buyruq: /effektlar",
        "Claude Code bilan 1 daqiqada sayt yasash: so'rov yozish, kod yaratish, ishga tushirish bosqichlari. Buyruq: /sayt",
    ),
    "terminal": (
        "Saytlardagi 5 ta eng xavfli xato (OWASP): Broken Access Control, SQL injection, zaif parollar. Xakerlar qanday hujum qilishini kod bilan ko'rsat. Nikim @husanbek_coder",
        "Parolingiz 1 soniyada buzilishi mumkin: brute-force qanday ishlaydi, kuchli parol qoidalari va 2FA. Statistikalar bilan",
        "Telegram akkauntingizni o'g'irlashning 4 ta usuli va har biridan qanday himoyalanish kerak",
    ),
    "glass-qahramon": (
        "Claude Code bilan 5 daqiqada motion video yasash bo'yicha qo'llanma. Har sahnada mening qahramonim boshqa pozada bo'lsin. Nikim @husanbek_coder",
        "Dasturlashni noldan boshlash uchun 5 ta maslahat: qaysi tildan boshlash, qayerdan o'rganish, qanday mashq qilish",
        "Sun'iy intellekt bilan ishlashda 4 ta eng ko'p uchraydigan xato va ularni qanday tuzatish kerak",
    ),
    "karusel": (
        "Prompt yozish formulasi: subyekt + harakat + joy + kamera + uslub. 8 slaydli qo'llanma, misollar bilan. Nikim @husanbek_coder",
        "Freelance'da birinchi buyurtmani olish: 7 slayd — portfolio, platforma tanlash, narx belgilash, mijoz bilan muloqot",
        "ChatGPT va Claude farqi: 6 slaydda qaysi biri qaysi ish uchun yaxshi",
    ),
    "kinetik": (
        "Yolg'izlik va o'zini topish haqida his-tuyg'uli matn: kechalari uxlay olmaslik, o'ylar, va oxirida umid. Ayol ovozi emas, mening ovozim",
        "Ota-onaning qadri haqida qisqa va ta'sirli matn: ular borida qadrlang",
        "Muvaffaqiyatsizlikdan keyin qayta turish haqida motivatsion his-tuyg'uli matn",
    ),
    "kod-sheriyat": (
        "Dasturchi hayoti haqida kod ko'rinishidagi she'r: while (hayot) { o'rgan(); xato_qil(); qayta_urin(); }",
        "Sevgi haqida kod tilida yozilgan she'r: if (sen.yonimda) { kun = yorug'; }",
        "Yoz va xotiralar haqida kod ko'rinishidagi nostalgik she'r",
    ),
    "top-royxat": (
        "Dasturchilar uchun 7 ta foydali AI vosita: har birining nomi va nima qilishi bitta kartochkada. Oxirida kommentga yozing chaqirig'i. Nikim @husanbek_coder",
        "Claude uchun 10 ta kuchli GitHub repo: nomi va qisqa vazifasi bilan",
        "Talabalar uchun 5 ta bepul o'quv platformasi: nomi, nima o'rgatadi, narxi",
    ),
    "lime-biznes": (
        "Biznesingizni AI bilan avtomatlashtirish: mijozlarga kechikib javob berish, unutilgan lidlar, yiliga 291 soat yo'qotish va AI yechimi. Nikim @husanbek_coder",
        "Kichik do'kon egasi uchun Telegram bot: buyurtmalar, to'lov va hisobotlar avtomatik",
        "Nega sizning Instagram sahifangiz mijoz olib kelmayapti va buni AI qanday tuzatadi",
    ),
    "minimal-qora": (
        "Ponytail skill: AI agentni senior dasturchiga aylantirish. Chiqish tokenlari 45% kamayishi, kod sifati, va lite/full rejimlari. Oxirida profil. Nikim @husanbek_coder",
        "Claude Code'da CLAUDE.md fayli nima uchun kerak va uni qanday to'g'ri yozish kerak",
        "Kodni review qilishda AI'dan foydalanish: 3 ta amaliy misol",
    ),
    "neon-tushuntirish": (
        "Harness engineering nima: model, infratuzilma va bir nechta agentlar (frontend, backend, review) qanday birga ishlaydi. Diagrammalar bilan. Mavzu: Harness",
        "MCP (Model Context Protocol) nima va u AI'ga qanday qilib vositalar beradi — oddiy tilda tushuntir",
        "AI agent qanday ishlaydi: rejalashtirish, vositalar, xotira va tekshirish — diagramma bilan",
    ),
    "personaj-dialog": (
        "Men va Claude o'rtasida kulgili suhbat: mening ovozim yo'qolib qoldi, Reels esa bugun kerak, Claude ovozimni klonlab yordam beradi",
        "Men Claude'dan dars qilishni so'rayman, u esa meni o'zim o'rganishga undaydi — qisqa kulgili dialog",
        "Claude bilan bahs: kim tezroq kod yozadi? Oxirida birga ishlash eng yaxshisi degan xulosa",
    ),
    "biznes-avtomatlash": (
        "Telegram bot va AI agent biznesga qanday mijoz olib keladi: mijoz yozadi, siz javob bermaysiz, raqobatchi sotadi — bot esa darhol javob beradi. Nikim @husanbek_coder",
        "Restoran uchun buyurtma boti: menyu, buyurtma, yetkazib berish — telefon chatida ko'rsat",
        "Onlayn kurs sotish uchun avtomat voronka: reklama, bot, to'lov, kursga kirish",
    ),
    "neon-lab-hikoya": (
        "Claude'ga dasturni tahlil qildirish 3 qismda: 1 · vazifa beramiz, 2 · dalillarni topadi, 3 · o'xshash funksiyani yaratadi. Men va Claude dialogi. Nikim @husanbek_coder",
        "Claude bilan buzilgan saytni tuzatish hikoyasi: xatoni topish, sababini tushunish, tuzatish",
        "Claude'ga eski loyihani yangilatish: 3 bosqichli hikoya, dialog ko'rinishida",
    ),
    "neon-lab-qadam": (
        "GitHub'dagi repo'ni Claude bilan o'rnatish va ishlatish: 01 · o'rnatish, 02 · nima qiladi, 03 · qayta yaratish. Nikim @husanbek_coder",
        "Claude Code'ni kompyuterga o'rnatish bosqichlari: 01 · Node.js, 02 · o'rnatish buyrug'i, 03 · birinchi so'rov",
        "Telegram bot yaratish: 01 · BotFather, 02 · token, 03 · kod, 04 · ishga tushirish",
    ),
    "retro": (
        "Motivatsiya: nima qilish kerakligini bilish yetarli emas, harakat qilish kerak. Qisqa va kuchli iboralar bilan",
        "Mashg'ulot mavsumi boshlandi: intizom, har kungi kichik qadamlar, natija",
        "Orzu qilish emas, boshlash kerak: bugun birinchi qadamni qo'y",
    ),
    "ember": (
        "Google NotebookLM: 2 soatlik videoni 5 daqiqada o'rganish — manbalarni yuklash, savol berish, studiyada audiopodkast va testlar. Oxirida profil. Nikim @husanbek_coder",
        "Kitobni 10 daqiqada tushunish uchun AI usuli: xulosa, asosiy g'oyalar, savollar",
        "YouTube videolaridan konspekt tayyorlashning eng tez usuli",
    ),
    "monitor": (
        "AI xotirasi: Claude keyingi suhbatda sizni qanday eslab qoladi — eslatmalar fayli, odatlaringiz, va 'Salom! Men Klodman' bilan yakun",
        "Dasturchining kechasi: monitor oldida xatoni qidirish va nihoyat topish haqida monolog",
        "AI yordamchining birinchi ish kuni haqida kulgili monolog",
    ),
    "pastel-demo": (
        "Raqobatchining ilovasidagi funksiyani Claude bilan qayta yaratish: 1 · tahlil, 2 · oddiy so'zlar bilan tushuntirish, 3 · loyihaga qo'shish. Oxirida profil. Nikim @husanbek_coder",
        "Fitnes ilovasi demo: kunlik qadamlar, mashqni boshlash tugmasi va natijalar — telefon maketida",
        "Yangi to'lov funksiyasini foydalanuvchilarga tanishtirish: 3 qadamda qanday ishlaydi",
    ),
}


def examples(t: Template) -> tuple[str, ...]:
    return EXAMPLES.get(t.id, (t.example,))
