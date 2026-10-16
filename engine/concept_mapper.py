"""Multilingual concept dictionary and matcher.

Maps words in English, Roman Urdu, Urdu and Pashto onto a shared set of
concepts. Each concept knows which visual represents it and what kind of
concept it is (entity, action, trend, relation, emotion). The dictionary is
data: extend CONCEPTS to teach the engine new ideas.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# kind: entity | actor | action | trend | relation | emotion | quality
# visual: visual vocabulary name (see illustration_composer.VISUALS)
CONCEPTS: dict[str, dict] = {
    # ---- actors -------------------------------------------------------------
    "person": {"kind": "actor", "visual": "person", "kw": {
        "en": ["person", "man", "woman", "someone", "somebody", "individual", "guy", "girl", "boy", "friend", "he", "she", "i", "you", "investor", "owner", "human", "citizen"],
        "roman_urdu": ["shakhs", "aadmi", "admi", "aurat", "insaan", "insan", "banda", "bandi", "larka", "larki", "dost", "main", "mai", "aap", "tum", "woh", "wo", "investor", "maalik", "malik"],
        "ur": ["شخص", "آدمی", "عورت", "انسان", "بندہ", "لڑکا", "لڑکی", "دوست", "میں", "آپ", "تم", "وہ", "مالک"],
        "ps": ["سړی", "سړي", "ښځه", "انسان", "کس", "هلک", "نجلۍ", "ملګری", "زه", "ته", "تاسو", "هغه", "مالک"]}},
    "people": {"kind": "actor", "visual": "people_group", "kw": {
        "en": ["people", "everyone", "population", "public", "crowd", "community", "they", "we", "team", "society"],
        "roman_urdu": ["log", "logon", "awaam", "awam", "sab", "hum", "team", "muashra"],
        "ur": ["لوگ", "لوگوں", "عوام", "سب", "ہم", "ٹیم", "معاشرہ"],
        "ps": ["خلک", "خلکو", "ټول", "موږ", "ټیم", "ټولنه"]}},
    "customer": {"kind": "actor", "visual": "people_group", "kw": {
        "en": ["customer", "client", "buyer", "consumer", "shopper", "user"],
        "roman_urdu": ["customer", "gahak", "grahak", "kharidar", "khareedar", "user"],
        "ur": ["گاہک", "خریدار", "صارف", "کسٹمر"],
        "ps": ["پېرودونکی", "پیرودونکی", "مشتري", "کاروونکی"]}},
    "worker": {"kind": "actor", "visual": "worker", "kw": {
        "en": ["worker", "employee", "staff", "labour", "labor", "job", "jobs", "career", "farmer"],
        "roman_urdu": ["mazdoor", "mulazim", "mulazmat", "naukri", "staff", "kisan", "worker", "employee"],
        "ur": ["مزدور", "ملازم", "ملازمت", "نوکری", "کسان", "کارکن"],
        "ps": ["کارګر", "کارکوونکی", "دنده", "بزګر", "کارمند"]}},
    "employer": {"kind": "actor", "visual": "boss", "kw": {
        "en": ["employer", "boss", "manager", "company", "companies", "firm", "ceo", "entrepreneur", "founder"],
        "roman_urdu": ["boss", "manager", "company", "seth", "malik"],
        "ur": ["آجر", "باس", "مینیجر", "کمپنی", "سیٹھ"],
        "ps": ["مشر", "مدیر", "شرکت", "کمپنۍ"]}},
    "student": {"kind": "actor", "visual": "student", "kw": {
        "en": ["student", "pupil", "learner", "child", "children", "kid", "kids"],
        "roman_urdu": ["talib", "taalib", "student", "bacha", "bache", "bachay", "bachon"],
        "ur": ["طالب", "طلبہ", "بچہ", "بچے", "بچوں"],
        "ps": ["زده کوونکی", "شاګرد", "ماشوم", "ماشومان"]}},
    "teacher": {"kind": "actor", "visual": "teacher", "kw": {
        "en": ["teacher", "mentor", "coach", "trainer", "expert", "doctor"],
        "roman_urdu": ["ustad", "ustaad", "teacher", "doctor", "mahir"],
        "ur": ["استاد", "ٹیچر", "ڈاکٹر", "ماہر"],
        "ps": ["ښوونکی", "استاد", "ډاکټر", "ماهر"]}},
    # ---- money & economy ----------------------------------------------------
    "money": {"kind": "entity", "visual": "coin_stack", "kw": {
        "en": ["money", "cash", "dollar", "rupee", "fund", "funds", "capital", "wealth", "income", "earning", "earnings", "budget", "finance", "financial", "rich", "afford"],
        "roman_urdu": ["paisa", "paise", "paison", "raqam", "rupay", "rupaye", "dollar", "daulat", "amdani", "aamdani", "kamai", "budget", "ameer"],
        "ur": ["پیسہ", "پیسے", "پیسوں", "رقم", "روپے", "ڈالر", "دولت", "آمدنی", "کمائی", "بجٹ", "امیر"],
        "ps": ["پیسې", "پیسو", "روپۍ", "ډالر", "شتمني", "عاید", "ګټل", "بودیجه", "بډای"]}},
    "coin": {"kind": "entity", "visual": "coin", "kw": {
        "en": ["coin", "coins", "penny", "cent"], "roman_urdu": ["sikka", "sikkay"], "ur": ["سکہ", "سکے"], "ps": ["سکه", "سکې"]}},
    "wallet": {"kind": "entity", "visual": "wallet", "kw": {
        "en": ["wallet", "purse", "pocket"], "roman_urdu": ["batwa", "jeb", "wallet"], "ur": ["بٹوہ", "جیب", "پرس"], "ps": ["بټوه", "جیب"]}},
    "savings": {"kind": "entity", "visual": "piggy_bank", "kw": {
        "en": ["save", "saving", "savings", "saved", "emergency fund"], "roman_urdu": ["bachat", "bachana", "bachaen", "bachaye", "save"],
        "ur": ["بچت", "بچانا", "بچائیں", "بچائے"], "ps": ["سپما", "سپمول", "خوندي"]}},
    "bank": {"kind": "entity", "visual": "bank", "kw": {
        "en": ["bank", "banks", "account", "deposit", "atm"], "roman_urdu": ["bank", "account", "khata"], "ur": ["بینک", "اکاؤنٹ", "کھاتہ"], "ps": ["بانک", "حساب"]}},
    "investment": {"kind": "action", "visual": "money_growth", "kw": {
        "en": ["invest", "investing", "investment", "investor", "stock", "stocks", "shares", "portfolio", "compound", "interest", "returns", "return"],
        "roman_urdu": ["invest", "sarmaya", "sarmayakari", "lagaye", "lagaya", "lagana", "shares", "munafa", "sood"],
        "ur": ["سرمایہ", "سرمایہ کاری", "لگائے", "لگایا", "لگانا", "شیئرز", "سود", "انویسٹ"],
        "ps": ["پانګه", "پانګونه", "پانګه اچونه", "اچوي", "واچوله", "ونډې", "سود"]}},
    "debt": {"kind": "entity", "visual": "debt", "kw": {
        "en": ["debt", "loan", "loans", "borrow", "borrowed", "owe", "credit", "mortgage", "emi"],
        "roman_urdu": ["qarz", "qarza", "udhaar", "udhar", "loan"], "ur": ["قرض", "قرضہ", "ادھار"], "ps": ["پور", "قرض"]}},
    "salary": {"kind": "action", "visual": "salary", "kw": {
        "en": ["salary", "wage", "wages", "paycheck", "paid", "income"], "roman_urdu": ["tankhwah", "tankhwa", "salary", "ujrat"],
        "ur": ["تنخواہ", "اجرت", "سیلری"], "ps": ["معاش", "تنخوا", "مزد"]}},
    "payment": {"kind": "action", "visual": "payment", "kw": {
        "en": ["pay", "pays", "paying", "payment", "spend", "spends", "spent", "spending", "cost", "costs", "bill", "bills", "fee", "fees", "rent"],
        "roman_urdu": ["ada", "adaigi", "kharch", "kharcha", "kharchay", "bill", "fees", "kiraya"],
        "ur": ["ادا", "ادائیگی", "خرچ", "خرچہ", "بل", "فیس", "کرایہ"],
        "ps": ["ورکول", "ورکوي", "لګښت", "لګوي", "بل", "فیس", "کرایه"]}},
    "buy": {"kind": "action", "visual": "purchase", "kw": {
        "en": ["buy", "buys", "bought", "buying", "purchase", "purchased", "shopping", "order"],
        "roman_urdu": ["khareed", "kharid", "khareeda", "khareedna", "kharidna", "shopping"],
        "ur": ["خرید", "خریدا", "خریدنا", "خریداری"], "ps": ["اخیستل", "واخیست", "اخلي", "پېرل", "رانیول"]}},
    "sell": {"kind": "action", "visual": "purchase", "kw": {
        "en": ["sell", "sells", "sold", "selling", "sale", "sales", "revenue"], "roman_urdu": ["bech", "bechna", "becha", "farokht", "sales"],
        "ur": ["بیچ", "بیچنا", "بیچا", "فروخت", "سیلز"], "ps": ["پلورل", "پلوري", "وپلورل", "خرڅلاو"]}},
    "price": {"kind": "entity", "visual": "price_tag", "kw": {
        "en": ["price", "prices", "inflation", "expensive", "cheap", "afford", "value"], "roman_urdu": ["qeemat", "keemat", "mehngai", "mehnga", "sasta"],
        "ur": ["قیمت", "مہنگائی", "مہنگا", "سستا"], "ps": ["بیه", "بیې", "ګرانښت", "ګران", "ارزان"]}},
    "profit": {"kind": "entity", "visual": "money_bag", "kw": {
        "en": ["profit", "profits", "gain", "gains", "earn", "earns", "earned", "reward", "rewards", "bonus"],
        "roman_urdu": ["munafa", "fayda", "faida", "kamana", "kamaya", "inaam"], "ur": ["منافع", "فائدہ", "کمانا", "کمایا", "انعام"],
        "ps": ["ګټه", "ګټې", "ګټل", "انعام"]}},
    "loss": {"kind": "entity", "visual": "loss", "kw": {
        "en": ["loss", "losses", "lose", "loses", "lost", "waste", "wasted", "poor", "poverty", "broke"],
        "roman_urdu": ["nuqsan", "nuksan", "khasara", "ghareeb", "gharib", "zaya"], "ur": ["نقصان", "خسارہ", "غریب", "غربت", "ضائع"],
        "ps": ["زیان", "تاوان", "غریب", "بې وزلي"]}},
    "tax": {"kind": "entity", "visual": "document", "kw": {
        "en": ["tax", "taxes", "government", "law", "policy", "contract", "rule", "rules", "document", "paper", "report"],
        "roman_urdu": ["tax", "hukumat", "qanoon", "muahida", "kaghaz"], "ur": ["ٹیکس", "حکومت", "قانون", "معاہدہ", "کاغذ", "رپورٹ"],
        "ps": ["مالیه", "حکومت", "قانون", "تړون", "کاغذ", "راپور"]}},
    # ---- places & things ---------------------------------------------------
    "shop": {"kind": "entity", "visual": "shop", "kw": {
        "en": ["shop", "shops", "store", "market", "bazaar", "supermarket", "restaurant"], "roman_urdu": ["dukaan", "dukan", "bazaar", "bazar", "market", "store"],
        "ur": ["دکان", "بازار", "مارکیٹ", "اسٹور", "ہوٹل"], "ps": ["دوکان", "هټۍ", "بازار", "مارکېټ"]}},
    "business": {"kind": "entity", "visual": "building", "kw": {
        "en": ["business", "businesses", "startup", "office", "industry", "brand", "economy", "economic"], "roman_urdu": ["karobar", "karobaar", "business", "dafter", "daftar", "maeeshat", "maishat"],
        "ur": ["کاروبار", "بزنس", "دفتر", "صنعت", "معیشت"], "ps": ["سوداګري", "کاروبار", "دفتر", "صنعت", "اقتصاد"]}},
    "factory": {"kind": "entity", "visual": "factory", "kw": {
        "en": ["factory", "production", "produce", "manufacture", "manufacturing", "supply chain"], "roman_urdu": ["factory", "karkhana", "paidawar"],
        "ur": ["فیکٹری", "کارخانہ", "پیداوار"], "ps": ["فابریکه", "کارخانه", "تولید"]}},
    "house": {"kind": "entity", "visual": "house", "kw": {
        "en": ["house", "home", "homes", "property", "real estate", "apartment", "family"], "roman_urdu": ["ghar", "makaan", "makan", "property", "khandan", "ghar wale"],
        "ur": ["گھر", "مکان", "جائیداد", "خاندان"], "ps": ["کور", "کورونه", "ملکیت", "کورنۍ"]}},
    "school": {"kind": "entity", "visual": "school", "kw": {
        "en": ["school", "college", "university", "education", "class", "degree"], "roman_urdu": ["school", "college", "university", "taleem", "parhai", "parhna"],
        "ur": ["اسکول", "سکول", "کالج", "یونیورسٹی", "تعلیم", "پڑھائی"], "ps": ["ښوونځی", "پوهنتون", "زده کړه", "تعلیم"]}},
    "book": {"kind": "entity", "visual": "book", "kw": {
        "en": ["book", "books", "read", "reading", "learn", "learning", "knowledge", "study", "skill", "skills"],
        "roman_urdu": ["kitab", "kitaab", "parh", "seekh", "seekhna", "ilm", "hunar"], "ur": ["کتاب", "پڑھ", "سیکھ", "سیکھنا", "علم", "ہنر"],
        "ps": ["کتاب", "لوستل", "زده", "پوهه", "مهارت"]}},
    "phone": {"kind": "entity", "visual": "phone", "kw": {
        "en": ["phone", "mobile", "smartphone", "app", "apps", "call", "social media"], "roman_urdu": ["phone", "mobile", "app"],
        "ur": ["فون", "موبائل", "ایپ"], "ps": ["ټیلیفون", "مبایل", "موبایل", "اپلیکیشن"]}},
    "computer": {"kind": "entity", "visual": "laptop", "kw": {
        "en": ["computer", "laptop", "internet", "online", "website", "digital", "software", "technology", "ai", "data"],
        "roman_urdu": ["computer", "laptop", "internet", "online", "website", "digital", "technology"],
        "ur": ["کمپیوٹر", "لیپ ٹاپ", "انٹرنیٹ", "آن لائن", "ویب سائٹ", "ڈیجیٹل", "ٹیکنالوجی"],
        "ps": ["کمپیوټر", "لپ ټاپ", "انټرنېټ", "انټرنیټ", "آنلاین", "ټکنالوژي"]}},
    "car": {"kind": "entity", "visual": "car", "kw": {
        "en": ["car", "cars", "vehicle", "drive", "travel", "transport", "taxi"], "roman_urdu": ["gaari", "gari", "gaadi", "car", "safar"],
        "ur": ["گاڑی", "کار", "سفر", "ٹرانسپورٹ"], "ps": ["موټر", "سفر", "ټرانسپورټ"]}},
    "truck": {"kind": "entity", "visual": "truck", "kw": {
        "en": ["truck", "delivery", "shipping", "export", "import", "goods", "trade"], "roman_urdu": ["truck", "delivery", "tijarat", "maal"],
        "ur": ["ٹرک", "ڈیلیوری", "تجارت", "مال", "برآمد", "درآمد"], "ps": ["لارۍ", "سوداګر", "توکي", "صادرات", "واردات"]}},
    "time": {"kind": "entity", "visual": "clock", "kw": {
        "en": ["time", "hour", "hours", "minute", "minutes", "daily", "day", "days", "late", "early", "wait", "patience", "long term"],
        "roman_urdu": ["waqt", "time", "ghanta", "ghantay", "din", "roz", "rozana", "intezar", "sabr"],
        "ur": ["وقت", "گھنٹہ", "گھنٹے", "دن", "روز", "روزانہ", "انتظار", "صبر"], "ps": ["وخت", "ساعت", "ورځ", "ورځې", "هره ورځ", "صبر"]}},
    "year": {"kind": "entity", "visual": "calendar", "kw": {
        "en": ["year", "years", "month", "months", "week", "weeks", "annual", "decade", "future"], "roman_urdu": ["saal", "mahina", "maheena", "hafta", "mustaqbil"],
        "ur": ["سال", "مہینہ", "مہینے", "ہفتہ", "مستقبل"], "ps": ["کال", "کلونه", "میاشت", "اونۍ", "راتلونکی"]}},
    "idea": {"kind": "entity", "visual": "lightbulb", "kw": {
        "en": ["idea", "ideas", "think", "thinking", "plan", "strategy", "creative", "innovation", "smart", "secret", "tip", "tips"],
        "roman_urdu": ["khayal", "khyal", "soch", "sochna", "plan", "tareeqa", "tarika", "raaz"], "ur": ["خیال", "سوچ", "سوچنا", "منصوبہ", "طریقہ", "راز"],
        "ps": ["نظر", "فکر", "سوچ", "پلان", "لاره", "راز"]}},
    "goal": {"kind": "entity", "visual": "target", "kw": {
        "en": ["goal", "goals", "target", "aim", "dream", "dreams", "focus", "purpose"], "roman_urdu": ["maqsad", "hadaf", "target", "khwab", "manzil"],
        "ur": ["مقصد", "ہدف", "ٹارگٹ", "خواب", "منزل"], "ps": ["هدف", "موخه", "خوب", "ارمان"]}},
    "problem": {"kind": "entity", "visual": "warning", "kw": {
        "en": ["problem", "problems", "issue", "risk", "risks", "danger", "mistake", "mistakes", "crisis", "challenge", "trouble", "stress", "fail", "failure"],
        "roman_urdu": ["masla", "masail", "mushkil", "khatra", "ghalti", "galti", "pareshani", "nakami"], "ur": ["مسئلہ", "مسائل", "مشکل", "خطرہ", "غلطی", "پریشانی", "ناکامی"],
        "ps": ["ستونزه", "ستونزې", "خطر", "غلطي", "اندېښنه", "ناکامي"]}},
    "solution": {"kind": "entity", "visual": "check", "kw": {
        "en": ["solution", "solve", "solves", "fix", "answer", "solved", "way out"], "roman_urdu": ["hal", "solution", "jawab", "ilaj"],
        "ur": ["حل", "جواب", "علاج"], "ps": ["حل", "ځواب", "علاج", "د حل لاره"]}},
    "question": {"kind": "entity", "visual": "question", "kw": {
        "en": ["why", "how", "what", "question", "wonder", "confused", "unknown"], "roman_urdu": ["kyun", "kaise", "kya", "sawal", "sawaal"],
        "ur": ["کیوں", "کیسے", "سوال"], "ps": ["ولې", "څنګه", "پوښتنه"]}},
    "success": {"kind": "entity", "visual": "trophy", "kw": {
        "en": ["success", "successful", "win", "wins", "winner", "achieve", "achievement", "best", "champion", "freedom"],
        "roman_urdu": ["kamyabi", "kamiyabi", "kamyab", "jeet", "jeetna"], "ur": ["کامیابی", "کامیاب", "جیت", "آزادی"],
        "ps": ["بریالیتوب", "بریالی", "بریا", "ګټونکی", "آزادي"]}},
    "health": {"kind": "entity", "visual": "heart", "kw": {
        "en": ["health", "healthy", "heart", "love", "care", "happy life", "exercise", "sleep"], "roman_urdu": ["sehat", "dil", "mohabbat", "pyar", "khayal rakhna"],
        "ur": ["صحت", "دل", "محبت", "پیار"], "ps": ["روغتیا", "زړه", "مینه"]}},
    "hospital": {"kind": "entity", "visual": "hospital", "kw": {
        "en": ["hospital", "medicine", "medical", "disease", "sick", "illness", "treatment"], "roman_urdu": ["hospital", "dawai", "bimari", "beemari", "mareez"],
        "ur": ["ہسپتال", "دوائی", "دوا", "بیماری", "مریض"], "ps": ["روغتون", "درمل", "ناروغي", "ناروغ"]}},
    "food": {"kind": "entity", "visual": "food", "kw": {
        "en": ["food", "eat", "eating", "meal", "bread", "rice", "hungry", "cook", "kitchen"], "roman_urdu": ["khana", "roti", "chawal", "bhook", "pakana"],
        "ur": ["کھانا", "روٹی", "چاول", "بھوک"], "ps": ["خواړه", "ډوډۍ", "وريجې", "لوږه"]}},
    "water": {"kind": "entity", "visual": "water_drop", "kw": {
        "en": ["water", "rain", "drink", "river"], "roman_urdu": ["pani", "paani", "barish"], "ur": ["پانی", "بارش", "دریا"], "ps": ["اوبه", "باران", "سیند"]}},
    "nature": {"kind": "entity", "visual": "tree", "kw": {
        "en": ["tree", "trees", "nature", "environment", "forest", "climate", "green", "plant", "seed", "seeds"], "roman_urdu": ["darakht", "fitrat", "mahol", "jungle", "beej", "poda"],
        "ur": ["درخت", "فطرت", "ماحول", "جنگل", "بیج", "پودا"], "ps": ["ونه", "ونې", "طبیعت", "چاپیریال", "ځنګل", "تخم", "بوټی"]}},
    "energy": {"kind": "entity", "visual": "battery", "kw": {
        "en": ["energy", "power", "electricity", "battery", "fuel", "motivation"], "roman_urdu": ["bijli", "taqat", "energy", "himmat"],
        "ur": ["بجلی", "طاقت", "توانائی", "ہمت"], "ps": ["برېښنا", "بریښنا", "انرژي", "ځواک", "همت"]}},
    "world": {"kind": "entity", "visual": "globe", "kw": {
        "en": ["world", "global", "country", "countries", "international", "nation", "planet"], "roman_urdu": ["dunya", "duniya", "mulk", "mumalik", "qaum"],
        "ur": ["دنیا", "عالمی", "ملک", "ممالک", "قوم"], "ps": ["نړۍ", "نړیوال", "هېواد", "هیواد", "ملت"]}},
    "security": {"kind": "entity", "visual": "lock", "kw": {
        "en": ["safe", "safety", "secure", "security", "protect", "protection", "insurance", "privacy"], "roman_urdu": ["mehfooz", "hifazat", "tahaffuz", "insurance"],
        "ur": ["محفوظ", "حفاظت", "تحفظ", "انشورنس"], "ps": ["خوندي", "ساتنه", "امنیت", "بیمه"]}},
    "growth_rocket": {"kind": "entity", "visual": "rocket", "kw": {
        "en": ["launch", "boost", "fast", "quickly", "rapid", "explode", "skyrocket"], "roman_urdu": ["tezi", "tez", "jaldi"], "ur": ["تیزی", "تیز", "جلدی"], "ps": ["چټک", "ژر", "ګړندی"]}},
    "research": {"kind": "entity", "visual": "magnifier", "kw": {
        "en": ["research", "search", "find", "found", "discover", "analysis", "analyze", "check", "look"], "roman_urdu": ["tehqeeq", "talash", "dhoond", "jaiza"],
        "ur": ["تحقیق", "تلاش", "ڈھونڈ", "جائزہ"], "ps": ["څېړنه", "لټون", "پیدا", "کتنه"]}},
    "message": {"kind": "entity", "visual": "envelope", "kw": {
        "en": ["message", "email", "mail", "letter", "news", "notification"], "roman_urdu": ["paigham", "khat", "email", "khabar"],
        "ur": ["پیغام", "خط", "ای میل", "خبر"], "ps": ["پیغام", "لیک", "خبر"]}},
    "city": {"kind": "entity", "visual": "map_pin", "kw": {
        "en": ["city", "location", "place", "village", "town", "area", "local"], "roman_urdu": ["shehar", "sheher", "jagah", "gaon", "ilaqa"],
        "ur": ["شہر", "جگہ", "گاؤں", "علاقہ"], "ps": ["ښار", "ځای", "کلی", "سیمه"]}},
    "work": {"kind": "action", "visual": "desk_work", "kw": {
        "en": ["work", "works", "working", "worked", "effort", "hard work", "build", "building", "create", "project"],
        "roman_urdu": ["kaam", "kam karna", "mehnat", "banana", "project"], "ur": ["کام", "محنت", "بنانا", "پراجیکٹ"], "ps": ["کار", "هڅه", "زیار", "جوړول"]}},
    "partnership": {"kind": "relation", "visual": "handshake", "kw": {
        "en": ["deal", "agree", "agreement", "partner", "partnership", "together", "trust", "cooperate", "connect", "network"],
        "roman_urdu": ["deal", "muahida", "partner", "sath", "saath", "milkar", "bharosa", "aitbaar"], "ur": ["معاہدہ", "پارٹنر", "ساتھ", "مل کر", "بھروسہ", "اعتماد"],
        "ps": ["تړون", "شریک", "یوځای", "سره", "باور"]}},
    # ---- trends --------------------------------------------------------------
    "increase": {"kind": "trend", "visual": "trend_up", "kw": {
        "en": ["grow", "grows", "grew", "growing", "growth", "increase", "increases", "increased", "rise", "rises", "rose", "rising", "up", "more", "higher", "gain", "boost", "multiply", "doubles", "double", "doubled", "soar", "climb", "improve", "improves", "better", "step by step", "little by little", "builds wealth"],
        "roman_urdu": ["barh", "barhta", "barhti", "barhna", "barhe", "barha", "izafa", "izaafa", "zyada", "ziada", "upar", "behtar", "dugna", "taraqqi", "dheere dheere", "qadam ba qadam"],
        "ur": ["بڑھ", "بڑھتا", "بڑھتی", "بڑھنا", "بڑھا", "اضافہ", "زیادہ", "اوپر", "بہتر", "دگنا", "ترقی", "آہستہ آہستہ", "قدم بہ قدم"],
        "ps": ["زیات", "زیاتوالی", "زیاتېږي", "ډېر", "ډیر", "لوړ", "لوړېږي", "ښه", "وده", "پرمختګ", "دوه چنده", "ورو ورو", "ګام په ګام"]}},
    "decrease": {"kind": "trend", "visual": "trend_down", "kw": {
        "en": ["fall", "falls", "fell", "drop", "drops", "dropped", "decrease", "decreases", "decreased", "decline", "declines", "down", "less", "lower", "reduce", "reduces", "shrink", "crash", "worse", "lose value"],
        "roman_urdu": ["kam", "kami", "ghat", "ghatna", "girna", "gir", "neeche", "kamzor", "bad tar"],
        "ur": ["کم", "کمی", "گھٹ", "گر", "گرنا", "نیچے", "کمزور", "بدتر"],
        "ps": ["کم", "کموالی", "کمېږي", "ټیټ", "لږ", "لوېدل", "کمزوری"]}},
    # ---- relations -----------------------------------------------------------
    "cause": {"kind": "relation", "visual": "arrow", "kw": {
        "en": ["because", "so", "therefore", "thus", "leads to", "lead to", "results in", "causes", "cause", "due to", "as a result", "that is why", "which means", "makes"],
        "roman_urdu": ["kyunke", "kyunki", "isliye", "is liye", "wajah", "ki wajah se", "nateeja", "natija", "is se", "jis se"],
        "ur": ["کیونکہ", "اس لیے", "اسی لیے", "وجہ", "نتیجہ", "جس سے", "اس سے"],
        "ps": ["ځکه", "له امله", "نو", "پایله", "له دې امله", "د دې لپاره"]}},
    "comparison": {"kind": "relation", "visual": "vs", "kw": {
        "en": ["than", "versus", "vs", "compared", "compare", "comparison", "instead", "while", "difference", "whereas", "unlike"],
        "roman_urdu": ["mukable", "muqable", "muqabla", "farq", "bajaye", "jabke", "se zyada", "se kam"],
        "ur": ["مقابلے", "مقابلہ", "موازنہ", "فرق", "بجائے", "جبکہ"],
        "ps": ["پرتله", "په پرتله", "توپیر", "پرځای"]}},
    "sequence": {"kind": "relation", "visual": "steps", "kw": {
        "en": ["first", "second", "third", "then", "next", "finally", "after that", "step", "steps", "later", "afterwards", "lastly"],
        "roman_urdu": ["pehle", "pehla", "phir", "uske baad", "aakhir", "akhir mein", "qadam", "doosra", "teesra"],
        "ur": ["پہلے", "پہلا", "پھر", "اس کے بعد", "آخر میں", "قدم", "دوسرا", "تیسرا"],
        "ps": ["لومړی", "لومړۍ", "بیا", "وروسته", "په پای کې", "ګام", "دوهم", "درېیم"]}},
    "decision": {"kind": "relation", "visual": "decision_tree", "kw": {
        "en": ["choose", "choice", "decide", "decision", "option", "options", "either", "should"], "roman_urdu": ["faisla", "intikhab", "chunna", "option"],
        "ur": ["فیصلہ", "انتخاب", "چننا", "آپشن"], "ps": ["پرېکړه", "انتخاب", "غوراوی"]}},
    "risk_reward": {"kind": "relation", "visual": "scale", "kw": {
        "en": ["risk and reward", "balance", "trade-off", "tradeoff", "weigh", "pros and cons"], "roman_urdu": ["tawazun", "khatra aur fayda"],
        "ur": ["توازن", "خطرہ اور فائدہ"], "ps": ["توازن", "انډول"]}},
    "supply_demand": {"kind": "relation", "visual": "supply_demand", "kw": {
        "en": ["supply and demand", "demand", "supply"], "roman_urdu": ["talab", "rasad"], "ur": ["طلب", "رسد"], "ps": ["غوښتنه", "عرضه"]}},
    "cycle": {"kind": "relation", "visual": "cycle", "kw": {
        "en": ["cycle", "repeat", "again and again", "loop", "habit", "habits", "routine"], "roman_urdu": ["chakkar", "baar baar", "aadat", "adat", "mamool"],
        "ur": ["چکر", "بار بار", "عادت", "معمول"], "ps": ["دوره", "بیا بیا", "عادت", "ورځنی"]}},
    # ---- emotions ------------------------------------------------------------
    "happy": {"kind": "emotion", "visual": None, "kw": {
        "en": ["happy", "glad", "excited", "smile", "joy", "proud", "enjoy"], "roman_urdu": ["khush", "khushi", "maza"], "ur": ["خوش", "خوشی", "مزہ"], "ps": ["خوشحاله", "خوښ", "خوند"]}},
    "sad": {"kind": "emotion", "visual": None, "kw": {
        "en": ["sad", "worried", "worry", "afraid", "fear", "scared", "angry", "upset", "tired"], "roman_urdu": ["udaas", "pareshan", "dar", "khauf", "ghussa"],
        "ur": ["اداس", "پریشان", "ڈر", "خوف", "غصہ"], "ps": ["خفه", "اندېښمن", "ویره", "غوسه"]}},
}

STOPWORDS = {
    "en": set("a an the and or of to in on at by for with from is are was were be been being it its this that these those as but if then than so very just also can could will would should do does did has have had not no yes".split()),
    "roman_urdu": set("hai hain ka ki ke ko se mein me aur ya bhi to ye yeh woh wo tha thi thay ho hota hoti kar karo kiya ne ek aik "
                      "karte karta karti kare karein raha rahi rahe gaya gayi gaye jata jati jate hua hui hue apna apni apne us is un in jab tab "
                      "liye saath sath wala wali wale hota sakta sakte chahiye".split()),
    "ur": set("ہے ہیں کا کی کے کو سے میں اور یا بھی تو یہ وہ تھا تھی تھے ہو کر نے ایک کرتے کرتا کرتی کریں رہا رہی رہے گیا گئی گئے "
              "جاتا جاتی جاتے ہوا ہوئی ہوئے اپنا اپنی اپنے اس ان جب تب لیے ساتھ والا والی والے سکتا سکتے چاہیے شروع".split()),
    "ps": set("دی ده دې چې او په د ته کې دا هغه یې وو شو شوه نه دي یو کړه کړي کوي کېږي شوې شوي سره له لپاره وي دې پر څخه".split()),
}

# Words whose concept match is too ambiguous on their own (only count with other evidence).
WEAK_KEYWORDS = {"he", "she", "i", "you", "we", "they", "main", "mai", "aap", "tum", "woh", "wo", "hum", "sab", "so", "up", "more", "less", "kam", "zyada",
                 "میں", "آپ", "تم", "وہ", "ہم", "سب", "زه", "ته", "تاسو", "هغه", "موږ", "ټول", "نو", "کم", "ډېر", "لږ", "سره", "ساتھ", "sath", "saath", "better", "then", "should", "look", "check", "find", "what", "how", "why", "kya"}


@dataclass
class ConceptMatch:
    concept: str
    kind: str
    visual: str | None
    keyword: str
    start: int
    end: int
    surface: str
    weak: bool

    def to_dict(self) -> dict:
        return {"concept": self.concept, "kind": self.kind, "visual": self.visual, "keyword": self.keyword, "span": [self.start, self.end], "surface": self.surface, "weak": self.weak}


def _lang_key(lang: str) -> str:
    return {"ps_latn": "roman_urdu"}.get(lang, lang)


def _compile():
    table = {}
    for lang in ("en", "roman_urdu", "ur", "ps"):
        entries = []
        for cid, spec in CONCEPTS.items():
            for kw in spec["kw"].get(lang, []):
                entries.append((kw.lower(), cid))
        # Longest keywords first so "supply and demand" wins over "demand".
        entries.sort(key=lambda e: -len(e[0]))
        table[lang] = entries
    return table


_TABLE = _compile()
_LATIN_WORD = re.compile(r"[a-z0-9']+")


def match_concepts(text: str, lang: str) -> list[ConceptMatch]:
    lang = _lang_key(lang)
    lower = text.lower()
    taken = [False] * len(lower)
    matches: list[ConceptMatch] = []
    latin = lang in ("en", "roman_urdu")
    for kw, cid in _TABLE.get(lang, []):
        if latin:
            # Word-boundary match; allow simple inflection suffixes for longer keywords.
            suffix = r"(?:s|es|ed|ing|ment|ments|er|ers)?" if len(kw) >= 4 and " " not in kw else ""
            pattern = r"(?<![a-z0-9'])" + re.escape(kw) + suffix + r"(?![a-z0-9'])"
        else:
            # Arabic-script tokens: match at token start; allow short attached suffixes.
            pattern = r"(?<![؀-ۿ])" + re.escape(kw) + r"(?:[؀-ۿ]{0,2})(?![؀-ۿ])"
        for m in re.finditer(pattern, lower):
            s, e = m.start(), m.end()
            if any(taken[s:e]):
                continue
            for k in range(s, e):
                taken[k] = True
            spec = CONCEPTS[cid]
            matches.append(ConceptMatch(cid, spec["kind"], spec["visual"], kw, s, e, text[s:e], kw in WEAK_KEYWORDS))
    matches.sort(key=lambda m: m.start)
    return matches


def content_tokens(text: str, lang: str) -> list[tuple[str, int, int]]:
    lang = _lang_key(lang)
    stop = STOPWORDS.get(lang, set())
    out = []
    for m in re.finditer(r"[^\W_]+", text.lower(), flags=re.UNICODE):
        tok = m.group(0)
        if tok in stop or tok.isdigit() or len(tok) < 2:
            continue
        out.append((tok, m.start(), m.end()))
    return out
