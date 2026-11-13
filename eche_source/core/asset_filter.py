# core/asset_filter.py
# Media-only prompt gate for ?asset. Stricter than the chat word list.
# Size, age, family, stage and stylization cues in ~20 languages, plus
# living or anthropomorphic objects near a person, romance or body word.
# Does not change core/blocklist.py.

from __future__ import annotations

import re

# Size / age / stylization cues that only block next to a person or character.
_SIZE_CUES = (
    "little", "tiny", "small", "petite", "smol", "chibi", "moe",
    "young-looking", "young looking", "youthful", "youthful-looking",
    "babyface", "baby-faced", "baby faced", "childlike", "child-like",
    "loli", "shota", "boyish", "girlish", "doll-sized", "doll sized",
    "pigtails", "pig tails", "knee socks", "kneesocks",
    "school uniform", "sailor uniform", "sailor-fuku", "sailor fuku",
    "pequeñ[oa]", "pequeño", "pequeña", "petit", "petite", "klein(?:e[rsn]?)?",
    "piccolo", "piccola", "pequeno", "pequena", "klein", "liten", "litet",
    "lilla", "küçük", "nho", "nhỏ", "kecil", "เล็ก", "маленьк(?:ий|ая|ое)",
    "маленький", "маленькая", "ちび", "ちびっ?こ", "小さな", "小さい",
    "幼い", "Q版", "萌", "귀여운",
)

# Person / character nouns the size cues must sit next to.
_PERSON = (
    "character", "person", "people", "human", "humanoid", "anthropomorphic",
    "figure", "someone", "somebody", "girl", "boy", "kid", "kids", "child",
    "children", "teen", "teenager", "man", "woman", "lady", "gentleman",
    "student", "pupil", "schoolgirl", "schoolboy", "elf", "creature",
    "lad", "lass", "guy", "gal", "fellow",
    "fairy", "faerie", "faery", "gymnast", "dancer", "sprite", "cherub",
    "pixie", "angel", "elf", "nymph",
    "fille", "garçon", "garcon", "enfant", "niña", "nina", "niño", "nino",
    "mädchen", "madchen", "junge", "kind", "menina", "menino", "bambina",
    "bambino", "meisje", "jongen", "dziewczynka", "chłopiec", "chlopiec",
    "девочка", "мальчик", "ребёнок", "ребенок", "少女", "少年", "子供",
    "子ども", "아이", "어린이", "소녀", "소년", "kız", "kiz", "çocuk",
    "cocuk", "बच्ची", "बच्चा", "बालक", "طفلة", "طفل", "gadis", "cô bé",
    "cau be", "cậu bé", "เด็กหญิง", "เด็กชาย", "flicka", "pojke",
    "дівчинка", "хлопчик", "дитина",
)

# Living or stylized objects that, near a person, romance or body word,
# are the incident-class without needing the word "character".
_OBJECT = (
    "twig", "stick", "match", "matchstick", "matchsticks", "match-stick",
    "sapling", "acorn", "object",
)
_ANTHRO_NEAR = (
    "man", "woman", "guy", "lady", "gal", "fellow", "boyfriend", "girlfriend",
    "couple", "kiss", "kisses", "kissing", "love", "face", "eyes", "living",
    "anthropomorphic", "person", "people", "creature", "character",
)

# Always blocked in an asset prompt (family, stage, stylized incident cues).
_ALWAYS = (
    # EN family / stage / synonyms
    r"newborn", r"new-born", r"baby", r"babies", r"infant", r"toddler",
    r"kindergarten", r"kindergartner", r"nursery", r"pre-?school",
    r"cub(?:s)?(?:\s+scout)?", r"brownie\s+scout", r"scout",
    r"\bson\b", r"\bdaughter\b", r"\bsister\b", r"\bbrother\b",
    r"\bnephew\b", r"\bniece\b", r"bathtime", r"bath\s*time",
    r"pigtails", r"pig\s*tails", r"knee\s*socks", r"lollipop",
    r"school\s+uniform", r"sailor\s+uniform", r"sailor\s*fuku",
    r"pleated\s+skirt", r"gym\s+class", r"playground", r"\bprom\b",
    r"\blad\b", r"\blass\b", r"\bbairn\b", r"\btween\b", r"juvenile",
    r"adolescent", r"youngster", r"schoolkid", r"school\s*kid",
    r"elementary\s+(?:school|student)", r"first-?year",
    r"junior[\s\-]?high", r"jr[\s\-]?high", r"middle-?school(?:\s+student|\s+dance)?", r"middle\s+school",
    r"munchkin", r"\btyke\b", r"moppet", r"half-?pint", r"imouto",
    r"randoseru", r"bo\s+peep", r"looks\s+very\s+young",
    r"flat-?chested", r"doll-?sized", r"size\s+of\s+a\s+doll", r"\bpixie\b", r"3\s*(?:ft|feet|foot)\s+tall",
    r"huge\s+head\s+and\s+stubby\s+limbs", r"youthful-?looking",
    r"chibi\s+elf", r"little\s+ones?",
    r"boyish\s+figure", r"girlish\s+figure",
    r"at\s+(?:age\s+)?(?:[1-9]|1[0-7]|six|seven|eight|nine|ten|eleven|"
    r"twelve|thirteen|fourteen|fifteen|sixteen|seventeen)\b",
    # FR
    r"petite\s+fille", r"petit\s+garçon", r"petit\s+garcon",
    r"\benfant\b", r"bébé", r"bebe", r"nouveau-né", r"nouveau-ne",
    r"maternelle", r"uniforme\s+scolaire",
    r"\bfils\b", r"\bfille\b", r"sœur", r"soeur", r"\bfrère\b", r"\bfrere\b",
    # ES
    r"\bniña\b", r"\bnina\b", r"\bniño\b", r"\bnino\b", r"bebé", r"bebe",
    r"recién\s+nacid[oa]", r"recien\s+nacid[oa]",
    r"\bhijo\b", r"\bhija\b", r"hermana", r"hermano",
    r"guardería", r"guarderia", r"uniforme\s+escolar",
    # DE
    r"kleines\s+mädchen", r"kleines\s+madchen", r"kleiner\s+junge",
    r"\bkind\b", r"kinder\b", r"neugeboren(?:es|e|er)?", r"\bbaby\b",
    r"\bsohn\b", r"tochter", r"schwester", r"\bbruder\b", r"\bbrüder\b",
    r"kindergarten", r"schuluniform",
    # PT
    r"menina", r"menino", r"garotinha", r"mocinha", r"bebê", r"bebe",
    r"recém-nascido", r"recem-nascido",
    r"\bfilho\b", r"\bfilha\b", r"\birmã\b", r"\birma\b", r"\birmão\b", r"\birmao\b",
    r"jardim\s+de\s+infância", r"jardim\s+de\s+infancia",
    # IT
    r"bambina", r"bambino", r"ragazzina", r"ragazzino", r"neonato", r"neonata",
    r"\bfiglio\b", r"\bfiglia\b", r"sorella", r"fratello", r"\basilo\b",
    # NL
    r"\bmeisje\b", r"\bjongen\b", r"pasgeboren", r"\bzoon\b", r"dochter",
    r"\bzus\b", r"\bbroer\b", r"kleuterschool",
    # PL
    r"dziewczynka", r"chłopiec", r"chlopiec", r"dziecko", r"niemowlę",
    r"niemowle", r"\bsyn\b", r"córka", r"corka", r"siostra", r"\bbrat\b",
    r"przedszkole",
    # RU / UK
    r"девочка", r"мальчик", r"ребёнок", r"ребенок", r"младенец",
    r"девчонка", r"малыш",
    r"\bсын\b", r"дочь", r"сестра", r"брат", r"детский\s+сад",
    r"дівчинка", r"хлопчик", r"дитина", r"немовля", r"дитячий\s+садок",
    # JA
    r"少女", r"少年", r"子供", r"子ども", r"こども", r"赤ちゃん", r"幼児",
    r"女の子", r"おさない", r"ガキ",
    r"息子", r"娘", r"妹", r"弟", r"幼稚園", r"制服", r"ちび", r"ロリ",
    # ZH
    r"小女孩", r"小男孩", r"小孩", r"儿童", r"兒童", r"婴儿", r"嬰兒",
    r"女孩", r"男孩",
    r"幼儿", r"幼兒", r"儿子", r"兒子", r"女儿", r"女兒", r"妹妹", r"弟弟",
    r"幼儿园", r"幼兒園", r"校服", r"Q版",
    # KO
    r"소녀", r"소년", r"아이", r"어린이", r"아기", r"유아", r"아들", r"딸",
    r"여동생", r"남동생", r"유치원", r"교복",
    # TR
    r"küçük\s+kız", r"kucuk\s+kiz", r"küçük\s+erkek", r"\bçocuk\b", r"\bcocuk\b",
    r"\bbebek\b", r"\boğul\b", r"\bogul\b", r"kız\s+kardeş", r"erkek\s+kardeş",
    r"anaokulu",
    # HI
    r"बच्ची", r"बच्चा", r"शिशु", r"बेटा", r"बेटी", r"बहन", r"भाई", r"बालक",
    r"छोटी\s+लड़की",
    # AR
    r"طفلة", r"طفل", r"رضيع", r"ابن", r"ابنة", r"أخت", r"اخ", r"أخ", r"روضة",
    # ID
    r"gadis\s+kecil", r"anak\s+laki", r"\bbayi\b", r"\banak\b", r"\bputra\b",
    r"\bputri\b", r"taman\s+kanak",
    # VI
    r"cô\s+bé", r"cậu\s+bé", r"trẻ\s+em", r"em\s+bé", r"con\s+trai",
    r"con\s+gái", r"em\s+gái", r"mẫu\s+giáo", r"mau\s+giao",
    # TH
    r"เด็กหญิง", r"เด็กชาย", r"ทารก", r"ลูกชาย", r"ลูกสาว", r"น้องสาว", r"อนุบาล",
    # SV / NO
    r"\bflicka\b", r"\bpojke\b", r"\bbebis\b", r"nyfödd", r"nyfodd",
    r"\bdotter\b", r"syster", r"\bbror\b", r"förskola", r"forskola",
    r"\bjente\b",
    # CS / HU / EL / HE / BN / TA
    r"dívka", r"divka", r"lány", r"lany", r"κορίτσι", r"בת", r"דוגעל",
    r"ছোট\s+মেয়ে", r"சிறுமி",
    # Romanized and additional languages (Urdu, Arabic, Welsh, Irish,
    # Danish, Afrikaans, and broader coverage).
    r"chhoti\s+ladki", r"chhoti\s+larki", r"choti\s+ladki",
    r"shoujo", r"shōjo", r"shoujo", r"\bkodomo\b",
    r"xiao\s+nuhai", r"xiao\s+nvhai", r"xiao\s+nühai", r"xiaonuhai",
    r"bachi", r"bacha", r"chhoti\s+larki",
    r"بچہ", r"بچی", r"چھوٹی\s+لڑکی", r"بنت\s+صغيرة", r"بنت\s+صغیره",
    r"\bplentyn\b", r"merch\s+fach", r"bachgen\s+bach",
    r"páiste", r"paiste", r"cailín\s+beag", r"cailin\s+beag",
    r"buachaill\s+beag", r"\bleanbh\b",
    r"lille\s+pige", r"lille\s+dreng", r"\bbarnlige\b",
    r"\bkindjie\b", r"klein\s+meisie", r"klein\s+seun",
    r"kanak[\-\s]*kanak", r"\bbudak\b",
    r"djevojčica", r"djevojcica", r"\bdijete\b",
    r"\binfano\b", r"\bknabino\b",
    r"\bpuella\b", r"\binfans\b",
    r"పిల్ల", r"చిన్న\s+పిల్ల", r"కుర్రాది",
    r"ਕੁੜੀ", r"ਬੱਚੀ", r"ਬੱਚਾ",
    r"मुन्नी", r"मुन्ना",
    r"pikkulapsi", r"\blapsi\b", r"\btyttö\b", r"tytto", r"pikkutyttö",
    r"niñita", r"ninita", r"nenita", r"\bchamaca\b", r"chiquilla",
    r"\bnena\b", r"niñito", r"nenito",
    r"\bchildlike\b", r"child-like",
)

# Idioms that would otherwise trip a family or stage word.
_BENIGN_RE = re.compile(
    r"(?is)baby\s*grand|babysitters?|babysitting|scout\s*drones?|"
    r"son\s*of\s*a\s*gun|el\s*ni[ñn]o|"
    r"fire\s*trucks?|camp\s*fires?|bon\s*fires?|fire\s*places?|"
    r"by\s+the\s+fire|beside\s+the\s+fire|near\s+the\s+fire|"
    r"in\s+front\s+of\s+the\s+fire|"
    r"roast(?:ed)?\s+(?:chicken|beef|pork|turkey|vegetables?|veggies|"
    r"potatoes|potato|lamb|duck|ham)|"
    r"pot\s*roast|sunday\s*roast|"
    r"wedding\s+cakes?|birthday\s+cakes?|engagement\s+cakes?"
)



def _compile() -> tuple[re.Pattern, re.Pattern, re.Pattern]:
    size = "|".join(_SIZE_CUES)
    person = "|".join(_PERSON)
    always = "|".join(f"(?:{p})" for p in _ALWAYS)
    obj = "|".join(_OBJECT)
    near_words = "|".join(_ANTHRO_NEAR)
    near = re.compile(
        rf"(?is)(?:(?:{size})\b(?:\W+\w+){{0,6}}\W+\b(?:{person})\b|"
        rf"\b(?:{person})\b(?:\W+\w+){{0,6}}\W+(?:{size})\b)"
    )
    always_re = re.compile(rf"(?is)(?:{always})")
    anthro = re.compile(
        rf"(?is)(?:(?:{obj})\b(?:\W+\w+){{0,6}}\W+\b(?:{near_words})\b|"
        rf"\b(?:{near_words})\b(?:\W+\w+){{0,6}}\W+(?:{obj})\b)"
    )
    return near, always_re, anthro


_NEAR, _ALWAYS_RE, _ANTHRO_RE = _compile()

# Broad object / animal / food / plant / household class. Used as one
# cue class in the structural rule, not as the only nouns that count.
_OBJECT_LEXICON = frozenset({
    "twig", "stick", "match", "matchstick", "sapling", "acorn", "object",
    "pencil", "eraser", "crayon", "candle", "wick", "lantern", "teacup",
    "cup", "mug", "teapot", "saucer", "spoon", "fork", "knife", "plate",
    "bowl", "loaf", "bread", "cookie", "cookies", "biscuit", "cake", "pie",
    "apple", "pear", "orange", "banana", "carrot", "potato", "onion",
    "pepper", "pumpkin", "gingerbread", "donut", "doughnut", "pretzel",
    "umbrella", "dustpan", "broom", "mop", "bucket", "sponge", "soap",
    "sock", "mitten", "glove", "hat", "boot", "shoe", "coat", "scarf",
    "puppet", "doll", "teddy", "toy", "marble", "button", "needle", "thread",
    "pin", "nail", "screw", "hammer", "wrench", "pliers", "clock", "watch",
    "lamp", "bulb", "torch", "flashlight", "book", "page", "notebook",
    "paper", "envelope", "letter", "stamp", "key", "lock", "door", "window",
    "chair", "table", "stool", "desk", "shelf", "vase", "pot", "kettle",
    "tree", "leaf", "leaves", "branch", "root", "flower", "rose", "tulip",
    "daisy", "seed", "pinecone", "pebble", "stone", "rock", "shell",
    "cloud", "raindrop", "snowflake", "icicle", "flame", "spark", "ember",
    "bunny", "rabbit", "kitten", "cat", "puppy", "dog", "mouse", "mice",
    "rat", "hamster", "fox", "wolf", "bear", "cub", "deer", "fawn", "owl",
    "bird", "duck", "chick", "chicken", "hen", "goose", "swan", "frog",
    "toad", "fish", "whale", "dolphin", "seal", "otter", "squirrel",
    "chipmunk", "hedgehog", "raccoon", "panda", "koala", "monkey", "ape",
    "elephant", "giraffe", "lion", "tiger", "zebra", "horse", "pony",
    "pig", "cow", "goat", "sheep", "lamb", "chicken", "bee", "butterfly",
    "bug", "insect", "spider", "ant", "worm", "snail", "turtle", "lizard",
    "snake", "dragon", "unicorn", "phoenix",
    "twigg", "twiglet", "stickman", "matchstickman",
    # Kitchen, office, and generic food / toy classes.
    "toaster", "oven", "fridge", "freezer", "skillet", "pan", "whisk",
    "spatula", "colander", "blender", "mixer", "grater", "ladle",
    "stapler", "pen", "paperclip", "folder", "binder", "clipboard",
    "fruit", "vegetable", "berry", "berries", "meat", "soup", "sandwich",
    "noodle", "noodles", "pasta", "rice", "cheese", "muffin", "cupcake",
    "bagel", "waffle", "pancake", "cereal",
    "crayonbox", "actionfigure", "stuffed", "plush", "plushie",
    "ball", "balloon", "kite", "yoyo", "block", "blocks",
    "sockpuppet", "handpuppet",
    "tazon", "tazón", "taza", "cuchara", "cuchillo", "tenedor", "plato",
    "manzana", "muneca", "muñeca", "juguete", "calcetin", "calcetín",
    "theiere", "théière", "tasse", "cuillere", "cuillère", "assiette",
    "pomme", "poupee", "poupée", "jouet", "chaussette",
    "teekanne", "loffel", "löffel", "teller", "apfel", "puppe",
    "spielzeug", "xicara", "xícara", "colher", "prato", "maca", "maçã",
    "boneca", "brinquedo", "tazza", "teiera", "cucchiaio", "piatto",
    "mela", "bambola", "giocattolo",
    "чашка", "ложка", "тарелка", "яблоко", "кукла", "игрушка",
    "茶杯", "勺子", "杯子", "玩偶", "玩具", "人形", "おもちゃ",
    "찻잔", "숟가락", "인형", "장난감",
    # Match, pencil, eraser, candle, crayon, twig in ES FR DE PT IT JA ZH KO RU.
    "cerilla", "fosforo", "fósforo", "lapiz", "lápiz", "goma", "borrador",
    "vela", "candela", "crayon", "crayón", "ramita", "ramilla",
    "allumette", "bougie", "chandelle", "gomme", "brindille", "rameau",
    "streichholz", "bleistift", "radiergummi", "kerze", "wachsmalstift",
    "buntstift", "zweig",
    "lapis", "lápis", "borracha", "graveto", "galho",
    "fiammifero", "matita", "gomma", "pastello", "rametto", "ramoscello",
    "マッチ", "鉛筆", "えんぴつ", "消しゴム", "ろうそく", "キャンドル",
    "クレヨン", "小枝",
    "火柴", "铅笔", "橡皮", "橡皮擦", "蜡烛", "蠟燭", "蜡笔", "蠟筆", "树枝", "嫩枝",
    "성냥", "연필", "지우개", "양초", "크레용", "크레파스", "나뭇가지", "잔가지",
    "спичка", "карандаш", "ластик", "свеча", "свечка", "мелок", "веточка",
    "прутик", "ветка",
})

# Classes used to keep ordinary cooking, decor and pet scenes from
# looking like personified incidents.
_ANIMAL_LEXICON = frozenset({
    "bunny", "rabbit", "kitten", "cat", "puppy", "dog", "mouse", "mice",
    "rat", "hamster", "fox", "wolf", "bear", "cub", "deer", "fawn", "owl",
    "bird", "duck", "chick", "chicken", "hen", "goose", "swan", "frog",
    "toad", "fish", "whale", "dolphin", "seal", "otter", "squirrel",
    "chipmunk", "hedgehog", "raccoon", "panda", "koala", "monkey", "ape",
    "elephant", "giraffe", "lion", "tiger", "zebra", "horse", "pony",
    "pig", "cow", "goat", "sheep", "lamb", "bee", "butterfly", "bug",
    "insect", "spider", "ant", "worm", "snail", "turtle", "lizard",
    "snake", "dragon", "unicorn", "phoenix",
})
_FOOD_LEXICON = frozenset({
    "loaf", "bread", "cookie", "cookies", "biscuit", "cake", "pie",
    "apple", "pear", "orange", "banana", "carrot", "potato", "onion",
    "pepper", "pumpkin", "gingerbread", "donut", "doughnut", "pretzel",
    "fruit", "vegetable", "berry", "berries", "meat", "soup", "sandwich",
    "noodle", "noodles", "pasta", "rice", "cheese", "muffin", "cupcake",
    "bagel", "waffle", "pancake", "cereal",
    "manzana", "pomme", "apfel", "maca", "maçã", "mela", "яблоко",
})
_CANDLE_LEXICON = frozenset({
    "candle", "wick", "vela", "candela", "bougie", "chandelle", "kerze",
    "свеча", "свечка", "蜡烛", "蠟燭", "ろうそく", "キャンドル", "양초",
})

_JOB_STEMS = frozenset({
    "fire", "police", "sales", "business", "chair", "sports", "stunt",
    "camera", "anchor", "congress", "front", "fresh", "post", "repair",
    "work", "craft", "crew", "door", "watch", "guard", "bar", "super",
    "fore", "hang", "marks", "new", "gentle", "yeo", "mail", "milk",
    "garbage", "refuse", "insurance", "congress", "fresh", "otters",
})

# Hero and job compounds whose stem is also an object or animal word.
# Spider-Man, fisherman, horseman, and similar must not count as incidents.
_HUMAN_COMPOUND_ALLOW = frozenset({
    "spider", "fish", "horse", "ant", "cat", "bat", "iron", "star",
    "pan", "wolf", "bird", "sun", "moon", "ice", "sand", "sea",
    "super", "wonder", "aqua", "air", "snow", "business",
})

_PERSON_WORDS = frozenset(p.lower() for p in _PERSON) | {
    "man", "men", "woman", "women", "guy", "guys", "lady", "ladies",
    "gal", "gals", "fellow", "fellows", "boy", "boys", "girl", "girls",
    "person", "people", "human", "humanoid", "someone", "somebody",
    "character", "characters", "figure", "figures",
}

_STOP = frozenset({
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for",
    "with", "as", "by", "from", "into", "over", "under", "this", "that",
    "these", "those", "is", "are", "was", "were", "be", "been", "being",
    "it", "its", "their", "his", "her", "they", "them", "he", "she",
    "we", "you", "i", "my", "your", "our", "out", "off", "up", "down",
    "then", "than", "so", "very", "just", "about", "into", "near",
    "after", "before", "while", "when", "where", "who", "whom", "which",
})

_HARM_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:"
    r"fire|fires|firing|burn|burns|burning|burnt|burned|"
    r"ignite|ignites|ignited|igniting|ablaze|blaze|blazing|"
    r"scorch|scorched|charred|smolder|smoldering|smouldering|"
    r"melt|melts|melted|melting|"
    r"scream|screams|screaming|cry|cries|crying|howl|howls|howling|"
    r"devour|devoured|devouring|"
    r"sliced|slice|slicing|snapped|snap|snapping|"
    r"explode|explodes|exploding|explosion|"
    r"wither|withers|withered|withering|"
    r"drown|drowned|drowning|strangle|strangled|stab|stabbed|"
    r"crush|crushed|shatter|shattered|"
    r"incinerate|incinerated|incinerating|incinerates|"
    r"torch(?:ed|es|ing)?|combusts?|combusting|combusted|"
    r"electrocute|electrocuted|electrocuting|electrocutes|"
    r"squash(?:ed|es|ing)?|disintegrate[sd]?|disintegrating|"
    r"fizzle[sd]?|fizzling|alight|"
    r"on\s+fire|set\s+(?:on\s+fire|alight)|up\s+in\s+flames|"
    r"goes\s+up\s+in\s+flames|"
    r"catch(?:es)?\s+fire|caught\s+fire|"
    r"burst(?:s|ing)?\s+into\s+flames|"
    # ES FR DE PT IT
    r"quema(?:r|do|da|ndo)?|incendio|incendiar(?:se)?|"
    r"br[uû]l(?:er|é|ee|és|ée|ant)?|en\s+feu|"
    r"brenn(?:en|t|end)?|verbrannt|in\s+brand|"
    r"queim(?:ar|ado|ando)|em\s+chamas|"
    r"bruci(?:are|ato|ata|ando)|in\s+fiamme|"
    r"поджиг(?:ать|ает)?|сжиг(?:ать|ает|ая)?|горит|горение"
    r")\b"
    r"|セット.*火|燃(?:え|やす|えている)|炎に|"
    r"燃烧|着火|放火|火焰|"
    r"불타|불태|화염|감전"
    r")"
)
_ROMANCE_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:"
    r"kiss|kisses|kissing|peck|pecks|pecking|smooch|smooches|smooching|"
    r"love|loves|loving|lover|lovers|in\s+love|"
    r"boyfriend|girlfriend|sweetheart|sweethearts|"
    r"couple|couples|romance|romantic|dating|"
    r"on\s+a\s+date|first\s+date|"
    r"marry|married|wedding|bride|groom|husband|wife|"
    r"darling|beloved|valentine|flirt|flirting|flirts|"
    r"cuddle|cuddles|cuddling|embrace|embraces|embracing|"
    r"hug|hugs|hugging|snuggle|snuggles|nuzzle|nuzzles|"
    r"bes(?:o|os|ar|ando|an|a)|enamor(?:ado|ada|ados|adas|ar(?:se)?)|"
    r"novio|novia|esposo|esposa|cari[ñn]o|"
    r"embrass(?:er|é|ée|ant|e|ent)|baiser|c[aâ]lin|amoureux|amoureuse|"
    r"[eé]poux|[eé]pouse|mari(?:age|er|é)|"
    r"k[uü]ss(?:en|t|end)?|lieb(?:e|t|en|end)?|umarm(?:en|t|ung)|"
    r"geliebte[rn]?|braut|br[aä]utigam|ehemann|ehefrau|"
    r"beij(?:o|ar|ando|a|am)|abra(?:c|ç)(?:ar|ando|o|a)|abraz(?:ar|ando|o|a)|apaixonad[oa]|namorad[oa]|espos[oa]|"
    r"baci(?:o|are|ando|a|ano)|innamorat[oa]|spos[oa]|abbracci(?:o|are|ando|a)|"
    r"поцелуй|целова(?:ть|л|ла)|целу(?:ет|ют|ются|ющ(?:ий|ая|ее|ие)?)|влюбленн(?:ый|ая)|любовь|обнимать|жених|невеста"
    r")\b"
    r"|キス|恋愛|抱きし|恋人|"
    r"亲吻|接吻|恋爱|拥抱|新婚|"
    r"키스|사랑|포옹|연인|신혼"
    r")"
)
_HUMANIZER_RE = re.compile(
    r"(?i)\b(?:"
    r"face|faces|faced|sentient|living|alive|anthropomorphic|anthro|"
    r"spirit|sprite|personified|"
    r"googly(?:\s+eyes)?|big\s+eyes|cute\s+(?:face|eyes)|large\s+eyes"
    r")\b"
)
_COMPOUND_RE = re.compile(
    r"(?i)\b([^\W\d_]{3,})[\- ]?(?:man|woman|men|women|guy|gal|girl|boy|lady|fellow)s?\b"
)
_LIVING_NOUN_RE = re.compile(
    r"(?i)\b(?:living|sentient|anthropomorphic|anthro)\s+([^\W\d_]{3,})\b"
)
_NOUN_WITH_FACE_RE = re.compile(
    r"(?i)\b([^\W\d_]{3,})\s+with\s+(?:an?\s+)?(?:big|googly|cute|large|wide)\s+(?:eyes|face|eye)\b"
)
_LEET_ONE_AS_L = str.maketrans({"1": "l"})
# o/e/a and common digit-for-letter substitutions, applied to mixed tokens.
_LEET_MAP = str.maketrans({
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a",
})

# Romance that describes a human relationship or occasion, not an object acting.
_PEOPLE_ROMANCE_RE = re.compile(
    r"(?i)\b(?:"
    r"bride|groom|husband|wife|boyfriend|girlfriend|"
    r"wedding|married|marry|marriage|dating|valentine|"
    r"novio|novia|esposo|esposa|namorad[oa]|spos[oa]|"
    r"[eé]poux|[eé]pouse|ehemann|ehefrau|брак|жених|невеста"
    r")\b"
)
_DISTRESS_RE = re.compile(
    r"(?i)\b(?:"
    r"scream|screams|screaming|cry|cries|crying|sob|sobs|sobbing|"
    r"wail|wails|wailing|weep|weeps|weeping|shriek|shrieks|shrieking|"
    r"howl|howls|howling|beg|begs|begging|plead|pleads|pleading"
    r")\b"
)
_MILD_HARM_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:slice|slices|sliced|slicing|melt|melts|melted|melting)\b|"
    r"catch(?:es)?\s+fire|caught\s+fire|"
    r"burst(?:s|ing)?\s+into\s+flames"
    r")"
)

# Common emoji → words for romance, harm, distress and staple objects.
_EMOJI_WORDS = {
    "💋": " kiss ", "😘": " kiss ", "💏": " kiss ", "💑": " love ",
    "💕": " love ", "❤": " love ", "❤️": " love ", "💖": " love ",
    "💗": " love ", "💓": " love ", "💞": " love ", "😍": " love ",
    "🔥": " fire ", "💥": " fire ", "😢": " cry ", "😭": " cry ",
    "😱": " scream ", "😩": " cry ",
    "✏": " pencil ", "✏️": " pencil ", "📝": " pencil ",
    "🖍": " crayon ", "🖍️": " crayon ",
    "🕯": " candle ", "🕯️": " candle ",
    "✂": " sliced ", "✂️": " sliced ",
}
_EMOJI_RE = re.compile("|".join(re.escape(k) for k in sorted(_EMOJI_WORDS, key=len, reverse=True)))

_IRREGULAR_PLURALS = {
    "matches": "match", "knives": "knife", "leaves": "leaf",
    "mice": "mouse", "geese": "goose", "wolves": "wolf",
    "calves": "calf", "lives": "life", "wives": "wife",
    "halves": "half", "loaves": "loaf", "shelves": "shelf",
    "berries": "berry",
    "cerillas": "cerilla", "fosforos": "fosforo", "fósforos": "fósforo",
    "lapices": "lapiz", "lápices": "lápiz",
    "allumettes": "allumette", "bougies": "bougie",
    "streichholzer": "streichholz", "streichhölzer": "streichholz",
    "kerzen": "kerze", "bleistifte": "bleistift",
    "fiammiferi": "fiammifero", "matite": "matita",
    "спички": "спичка", "свечи": "свеча", "карандаши": "карандаш",
}


import unicodedata


def _unwrap_enclosed(text: str) -> str:
    """Fold circled, squared and parenthesized letter forms to ASCII."""
    out: list[str] = []
    for ch in text or "":
        name = unicodedata.name(ch, "")
        if name:
            m = re.search(r"LETTER\s+([A-Z])\s*$", name)
            if m and any(
                tag in name
                for tag in (
                    "CIRCLED", "SQUARED", "PARENTHESIZED",
                    "REGIONAL INDICATOR", "NEGATIVE",
                )
            ):
                out.append(m.group(1).lower())
                continue
        out.append(ch)
    return "".join(out)


def _strip_bidi(text: str) -> str:
    return re.sub(r"[\u202a-\u202e\u2066-\u2069]", "", text or "")


def _collapse_repeats(text: str) -> str:
    # Runs of 3+ only, so "room" and "little" stay intact and "baaaby" folds.
    return re.sub(r"(.)\1{2,}", r"\1", text or "")


def _dehyphen(text: str) -> str:
    return (text or "").replace("-", "")


def _map_emoji(text: str) -> str:
    if not text or not _EMOJI_RE.search(text):
        return text or ""
    return _EMOJI_RE.sub(lambda m: _EMOJI_WORDS.get(m.group(0), " "), text)


def _flatten_screenplay(text: str) -> str:
    """Turn dialogue/action lines and parentheticals into a flat sentence."""
    raw = text or ""
    raw = re.sub(r"[()\[\]]", " ", raw)
    raw = re.sub(r"[\r\n]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _singular_forms(token: str) -> list[str]:
    """Token plus a simple English singular, including irregulars."""
    word = (token or "").lower()
    if not word:
        return []
    out = [word]
    irregular = _IRREGULAR_PLURALS.get(word)
    if irregular:
        out.append(irregular)
    if word.endswith("ies") and len(word) > 4:
        out.append(word[:-3] + "y")
    elif word.endswith(("ches", "shes", "sses", "xes", "zes")) and len(word) > 4:
        out.append(word[:-2])
    elif word.endswith("ves") and len(word) > 4:
        out.append(word[:-3] + "f")
        out.append(word[:-3] + "fe")
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")) and len(word) > 3:
        out.append(word[:-1])
    return list(dict.fromkeys(out))


def _forms(text: str) -> list[str]:
    """Raw text plus normalized, folded, reversed, de-hyphenated and collapsed forms."""
    from core.blocklist import _folded, _join_spaced_letters, _join_hyphenated, normalize

    out: list[str] = []
    seen: set[str] = set()
    raw = text or ""
    seeds = [raw, _unwrap_enclosed(raw), _strip_bidi(raw)]
    mapped = _map_emoji(raw)
    if mapped != raw:
        seeds.append(mapped)
    flat = _flatten_screenplay(raw)
    if flat and flat != raw:
        seeds.append(flat)
        if _map_emoji(flat) != flat:
            seeds.append(_map_emoji(flat))
    # Reverse only when bidi controls were present, so ordinary sentences
    # are not inspected backwards (that invented "nina" inside "cleaning").
    stripped = _strip_bidi(raw)
    if stripped != raw:
        seeds.append(stripped[::-1])
        seeds.append(_strip_bidi(_unwrap_enclosed(raw))[::-1])
    try:
        for seed in list(seeds):
            seeds.append(normalize(seed))
    except Exception:
        pass
    for seed in seeds:
        variants = [
            seed,
            _folded(seed),
            _folded(seed.translate(_LEET_ONE_AS_L)),
            _folded(seed.translate(_LEET_MAP)),
            seed.translate(_LEET_MAP),
            _join_spaced_letters(seed),
            _join_spaced_letters(_folded(seed)),
            _join_hyphenated(seed),
            _dehyphen(seed),
            _dehyphen(_folded(seed)),
            _collapse_repeats(seed),
            _collapse_repeats(_folded(seed)),
            re.sub(r"[\s_]+", "", seed or ""),
            re.sub(r"[\s_]+", "", _folded(seed)),
        ]
        for item in variants:
            if item and item not in seen:
                seen.add(item)
                out.append(item)
    return out


def _structural_forms(text: str) -> list[str]:
    """Forms for the incident rule, without dropping spaces or hyphens.

    Despaced text would turn 'fisherman' and 'Spider-Man' into a substring
    of an object word, which over-blocks ordinary adult prompts.
    """
    compact = re.sub(r"[\s_\-]+", "", (text or "").lower())
    out: list[str] = []
    for form in _forms(text):
        if re.sub(r"[\s_\-]+", "", (form or "").lower()) == compact and compact and (
            " " not in form and "-" not in form and form.lower() != (text or "").lower()
            and len(form) == len(compact)
        ):
            continue
        out.append(form)
    return out


def _object_stems(text: str) -> set[str]:
    """Nouns marked as objects by a humanizer pattern (POS-free)."""
    found: set[str] = set()
    for m in _COMPOUND_RE.finditer(text or ""):
        stem = m.group(1).lower()
        if (
            stem in _OBJECT_LEXICON
            and stem not in _HUMAN_COMPOUND_ALLOW
            and stem not in _JOB_STEMS
            and stem not in _PERSON_WORDS
            and stem not in _STOP
        ):
            found.add(stem)
    # "living room", "living wages", etc. are not personified objects.
    living_idiom = {
        "room", "rooms", "space", "area", "areas", "quarters",
        "arrangement", "wage", "wages", "standard", "conditions",
        "expense", "expenses", "cost", "costs", "legend", "proof",
        "color", "colour", "history", "document", "memory", "thing",
        "things",
    }
    for m in _LIVING_NOUN_RE.finditer(text or ""):
        word = m.group(1).lower()
        if word not in _STOP and word not in _PERSON_WORDS and word not in living_idiom:
            found.add(word)
    for m in _NOUN_WITH_FACE_RE.finditer(text or ""):
        word = m.group(1).lower()
        if word not in _STOP and word not in _PERSON_WORDS:
            found.add(word)
    return found


def _lexicon_hits(text: str) -> set[str]:
    found: set[str] = set()
    raw = (text or "").lower()
    tokens = re.findall(r"[^\W\d_]{3,}", raw)
    for match in re.finditer(r"\bpairs?\s+of\s+([^\W\d_]{3,})\b", raw):
        tokens.append(match.group(1))
    for token in tokens:
        for form in _singular_forms(token):
            if form in _OBJECT_LEXICON:
                found.add(form)
    return found


def _anthro_incident(text: str) -> bool:
    """A non-human subject (object, food, animal or toy) in romance or harm.

    A strong humanizer (living/sentient X, X with a face or big eyes,
    anthropomorphic X, or X-man when X is on the object list) is enough
    on its own. Romance or harm on a lexicon object no longer needs a
    face or living word. Ordinary adult compounds (Spider-Man,
    fisherman, snowman) are not subjects.
    """
    raw = text or ""
    if not raw.strip():
        return False
    stems = _object_stems(raw)
    lexicon = _lexicon_hits(raw)
    objects = stems | lexicon
    if not objects:
        return False
    if stems or _HUMANIZER_RE.search(raw):
        # Personified object: enough on its own, or with romance/harm.
        return True
    romance = bool(_ROMANCE_RE.search(raw))
    harm = bool(_HARM_RE.search(raw))
    distress = bool(_DISTRESS_RE.search(raw))
    animals = objects & _ANIMAL_LEXICON
    foods_candles = objects & (_FOOD_LEXICON | _CANDLE_LEXICON)
    other = objects - animals - foods_candles

    # Food or candles + cut/melt/catch-fire only count with distress or
    # object-directed romance, so cooking and decor scenes stay allowed.
    if foods_candles and not other and not animals:
        mild_only = bool(_MILD_HARM_RE.search(raw)) and not (
            harm and _HARM_RE.search(_MILD_HARM_RE.sub(" ", raw))
        )
        object_romance = romance and not (
            _PEOPLE_ROMANCE_RE.search(raw) and not re.search(
                r"(?i)\b(?:kiss|kisses|kissing|smooch|cuddle|cuddling|"
                r"love|loves|in\s+love|embrace|peck)\b",
                raw,
            )
        )
        if mild_only and not distress and not object_romance:
            harm = False
        if romance and not object_romance and not harm:
            romance = False

    # Pets cuddling is not an incident. Animal + romance needs harm too,
    # or a humanizer (already handled above).
    if animals and not other and not foods_candles:
        if romance and not harm:
            romance = False

    return bool(romance or harm)


def _masked(text: str) -> str:
    return _BENIGN_RE.sub(" ", text or "")


def _is_mostly_single_letters(raw: str) -> bool:
    tokens = re.findall(r"[^\W\d_]+", raw or "")
    if len(tokens) < 4:
        return False
    singles = sum(1 for tok in tokens if len(tok) == 1)
    return singles * 2 >= len(tokens)


def _is_despaced_copy(raw: str, form: str) -> bool:
    """True when form is the original with spaces/hyphens removed, or its reverse.

    Spaced-letter evasions such as 'a n e w b o r n' must still be
    inspected in compact form.
    """
    if not raw or not form or not re.search(r"[\s\-]", raw):
        return False
    if _is_mostly_single_letters(raw):
        return False
    compact = re.sub(r"[\s_\-]+", "", raw.lower())
    low = form.lower()
    return bool(compact) and (low == compact or low == compact[::-1])


def media_prompt_blocked(text: str) -> bool:
    """True when an asset prompt uses a media-only age, family or style cue."""
    raw = text or ""
    if not raw.strip():
        return False
    for form in _forms(raw):
        inspect = _masked(form)
        if not inspect.strip():
            continue
        if _is_despaced_copy(raw, form) or _is_despaced_copy(raw, inspect):
            continue
        if (
            _ALWAYS_RE.search(inspect)
            or _NEAR.search(inspect)
            or _ANTHRO_RE.search(inspect)
        ):
            return True
    for form in _structural_forms(raw):
        inspect = _masked(form)
        if inspect.strip() and _anthro_incident(inspect):
            return True
    return False
