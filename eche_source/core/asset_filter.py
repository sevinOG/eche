# core/asset_filter.py
# Media-only prompt gate for ?asset. Stricter than the chat word list.
# Size, age, family, stage and stylization cues in ~20 languages, plus
# character+twig/match/sapling/acorn. Does not change core/blocklist.py.

from __future__ import annotations

import re

# Size / age / stylization cues that only block next to a person or character.
_SIZE_CUES = (
    "little", "tiny", "small", "petite", "smol", "chibi", "moe",
    "young-looking", "young looking", "youthful", "babyface", "baby-faced",
    "baby faced", "childlike", "child-like", "loli", "shota",
    "pigtails", "pig tails", "knee socks", "kneesocks",
    "school uniform", "sailor uniform", "sailor-fuku",
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
    "student", "pupil", "schoolgirl", "schoolboy",
    "fille", "garçon", "garcon", "enfant", "niña", "nina", "niño", "nino",
    "mädchen", "madchen", "junge", "kind", "menina", "menino", "bambina",
    "bambino", "meisje", "jongen", "dziewczynka", "chłopiec", "chlopiec",
    "девочка", "мальчик", "ребёнок", "ребенок", "少女", "少年", "子供",
    "子ども", "아이", "어린이", "소녀", "소년", "kız", "kiz", "çocuk",
    "cocuk", "बच्ची", "बच्चा", "बालक", "طفلة", "طفل", "gadis", "cô bé",
    "cau be", "cậu bé", "เด็กหญิง", "เด็กชาย", "flicka", "pojke",
    "дівчинка", "хлопчик", "дитина",
)

# Always blocked in an asset prompt (family, stage, stylized incident cues).
_ALWAYS = (
    # EN family / stage
    r"newborn", r"new-born", r"baby", r"babies", r"infant", r"toddler",
    r"kindergarten", r"kindergartner", r"nursery", r"pre-?school",
    r"cub(?:s)?(?:\s+scout)?", r"brownie\s+scout", r"scout",
    r"\bson\b", r"\bdaughter\b", r"\bsister\b", r"\bbrother\b",
    r"\bnephew\b", r"\bniece\b", r"bathtime", r"bath\s*time",
    r"pigtails", r"pig\s*tails", r"knee\s*socks", r"lollipop",
    r"school\s+uniform", r"sailor\s+uniform",
    r"at\s+(?:age\s+)?(?:[1-9]|1[0-7]|six|seven|eight|nine|ten|eleven|"
    r"twelve|thirteen|fourteen|fifteen|sixteen|seventeen)\b",
    # character + incident stylization
    r"(?:twig|match|sapling|acorn)\s+(?:\w+\s+){0,3}character",
    r"character\s+(?:\w+\s+){0,3}(?:twig|match|sapling|acorn)",
    # FR
    r"petite\s+fille", r"petit\s+garçon", r"petit\s+garcon",
    r"\benfant\b", r"bébé", r"bebe", r"nouveau-né", r"nouveau-ne",
    r"maternelle", r"uniforme\s+scolaire",
    r"\bfils\b", r"\bfille\b", r"sœur", r"soeur", r"\bfrère\b", r"\bfrere\b",
    # ES
    r"niña", r"nina", r"niño", r"nino", r"bebé", r"bebe",
    r"recién\s+nacid[oa]", r"recien\s+nacid[oa]",
    r"\bhijo\b", r"\bhija\b", r"hermana", r"hermano",
    r"guardería", r"guarderia", r"uniforme\s+escolar",
    # DE
    r"kleines\s+mädchen", r"kleines\s+madchen", r"kleiner\s+junge",
    r"\bkind\b", r"kinder\b", r"neugeboren(?:es|e|er)?", r"\bbaby\b",
    r"\bsohn\b", r"tochter", r"schwester", r"\bbruder\b", r"\bbrüder\b",
    r"kindergarten", r"schuluniform",
    # PT
    r"menina", r"menino", r"bebê", r"bebe", r"recém-nascido", r"recem-nascido",
    r"\bfilho\b", r"\bfilha\b", r"irmã", r"irma", r"irmão", r"irmao",
    r"jardim\s+de\s+infância", r"jardim\s+de\s+infancia",
    # IT
    r"bambina", r"bambino", r"neonato", r"neonata",
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
    r"\bсын\b", r"дочь", r"сестра", r"брат", r"детский\s+сад",
    r"дівчинка", r"хлопчик", r"дитина", r"немовля", r"дитячий\s+садок",
    # JA
    r"少女", r"少年", r"子供", r"子ども", r"こども", r"赤ちゃん", r"幼児",
    r"息子", r"娘", r"妹", r"弟", r"幼稚園", r"制服", r"ちび", r"ロリ",
    # ZH
    r"小女孩", r"小男孩", r"小孩", r"儿童", r"兒童", r"婴儿", r"嬰兒",
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
    # SV
    r"\bflicka\b", r"\bpojke\b", r"\bbebis\b", r"nyfödd", r"nyfodd",
    r"\bson\b", r"dotter", r"syster", r"\bbror\b", r"förskola", r"forskola",
)

def _compile() -> tuple[re.Pattern, re.Pattern, re.Pattern]:
    size = "|".join(_SIZE_CUES)
    person = "|".join(_PERSON)
    always = "|".join(f"(?:{p})" for p in _ALWAYS)
    near = re.compile(
        rf"(?is)(?:(?:{size})\b(?:\W+\w+){{0,6}}\W+\b(?:{person})\b|"
        rf"\b(?:{person})\b(?:\W+\w+){{0,6}}\W+(?:{size})\b)"
    )
    always_re = re.compile(rf"(?is)(?:{always})")
    return near, always_re, re.compile(rf"(?is)(?:{size})")


_NEAR, _ALWAYS_RE, _ = _compile()


def media_prompt_blocked(text: str) -> bool:
    """True when an asset prompt uses a media-only age, family or style cue."""
    raw = text or ""
    if not raw.strip():
        return False
    if _ALWAYS_RE.search(raw):
        return True
    if _NEAR.search(raw):
        return True
    return False
