# core/asset_filter.py
# Media-only prompt gate for ?asset. Stricter than the chat word list.
# Size, age, family, stage and stylization cues in ~20 languages. The
# anthropomorphic / personified-object rule was removed 2026-11-14 (owner
# decision); minors are covered here, by the blocklist, and by the narrow
# model child check. Does not change core/blocklist.py.

from __future__ import annotations

import re
import unicodedata

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
    r"son\s*of\s*a\s*gun|el\s*ni[ñn]o"
)


def _compile() -> tuple[re.Pattern, re.Pattern]:
    size = "|".join(_SIZE_CUES)
    person = "|".join(_PERSON)
    always = "|".join(f"(?:{p})" for p in _ALWAYS)
    near = re.compile(
        rf"(?is)(?:(?:{size})\b(?:\W+\w+){{0,6}}\W+\b(?:{person})\b|"
        rf"\b(?:{person})\b(?:\W+\w+){{0,6}}\W+(?:{size})\b)"
    )
    always_re = re.compile(rf"(?is)(?:{always})")
    return near, always_re


_NEAR, _ALWAYS_RE = _compile()


_LEET_ONE_AS_L = str.maketrans({"1": "l"})
# o/e/a and common digit-for-letter substitutions, applied to mixed tokens.
_LEET_MAP = str.maketrans({
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a",
})


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


def _flatten_screenplay(text: str) -> str:
    """Turn dialogue/action lines and parentheticals into a flat sentence."""
    raw = text or ""
    raw = re.sub(r"[()\[\]]", " ", raw)
    raw = re.sub(r"[\r\n]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _split_camel(text: str) -> str:
    """Insert spaces at lower-to-upper boundaries: PencilKissesEraser."""
    if not text:
        return ""
    return re.sub(r"([a-z])([A-Z])", r"\1 \2", text)


def _forms(text: str) -> list[str]:
    """Raw text plus normalized, folded, reversed, de-hyphenated and collapsed forms."""
    from core.blocklist import _folded, _join_spaced_letters, _join_hyphenated, normalize

    out: list[str] = []
    seen: set[str] = set()
    raw = text or ""
    seeds = [raw, _unwrap_enclosed(raw), _strip_bidi(raw)]
    flat = _flatten_screenplay(raw)
    if flat and flat != raw:
        seeds.append(flat)
    camel = _split_camel(raw)
    if camel != raw:
        seeds.append(camel)
        flat_camel = _flatten_screenplay(camel)
        if flat_camel and flat_camel != camel:
            seeds.append(flat_camel)
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


# 18+ only: stylized media and real or identifiable people are blocked
# in addition to every ordinary minors check.
_ADULT_STYLE_RE = re.compile(
    r"(?isx)"
    r"(?:"
    # Requesting a named studio or style transfer.
    r"\bin\s+the\s+style\s+of\b"
    r"|\b(?:pixar|ghibli|disney|dreamworks|studio\s+ghibli)(?:[\s\-]?style)?\b"
    # Hyphenated "foo-style" (lifestyle / hairstyle / freestyle are one word).
    r"|(?<![a-z0-9])(?!life|hair|free|life\b)[a-z0-9]+-style\b"
    r"|"
    r"\b(?:"
    r"anime|manga|cartoon(?:s|ish|y)?|toon(?:s|y)?|chibi|moe|"
    r"dolls?|figurines?|stylized|stylised|"
    r"cel[\s\-]?shad(?:ed|ing)|"
    r"webtoon|manhwa|manhua|"
    r"comic(?:s|[\s\-]?book|[\s\-]?style)?|"
    r"kawaii|waifu|hentai|"
    r"2[\s\-]?d|3[\s\-]?d[\s\-]+render|"
    r"illustrated|illustration|drawing|drawings|"
    r"vtuber|v[\s\-]?tuber|"
    r"claymation|stop[\s\-]?motion|"
    r"animated|animation|"
    r"mannequin|pvc[\s\-]?figure|pvc\s+fig|"
    r"shortstack|short[\s\-]?stack"
    r")\b"
    # Nine-language style families (EN above, plus JA, ZH, KO, DE, FR, ES, PT, IT).
    r"|アニメ|マンガ|漫画|かわいい|二次元|三次元|フィギュア|イラスト|同人|萌え|ちび"
    r"|動漫|动漫|卡通|插画|插畫|手绘|手繪"
    r"|만화|애니|웹툰|그림체"
    r"|Zeichentrick|Zeichnung"
    r"|dessin(?:s)?\s+anim[ée]s?|bande\s+dessin[ée]e|illustr[ée]s?"
    r"|dibujo(?:s)?\s+animado(?:s)?|historieta(?:s)?|ilustrad[oa]s?"
    r"|desenho(?:s)?\s+animado(?:s)?|ilustrad[oa]s?"
    r"|cartone(?:s)?\s+animat[oi]|fumett[oi]|illustrat[oi]"
    r")"
)
_ADULT_REAL_PERSON_RE = re.compile(
    r"(?is)(?:"
    r"\b(?:real|identifiable|famous|well[\s\-]?known)\s+"
    r"(?:person|people|man|woman|men|women|human|celebrity|celebrities|"
    r"actor|actress|politician|influencer)\b"
    r"|\b(?:celebrity|celebrities|photorealistic|photo[\s\-]?real|"
    r"lookalike|look[\s\-]?alike|selfie)\b"
    r"|\b(?:photo|photograph|picture|portrait|image)\s+of\s+(?:a\s+)?"
    r"(?:real\s+)?(?:person|people|man|woman|celebrity|actor|actress)\b"
    r"|\blooks\s+like\s+(?:a\s+|an\s+)?(?:real\s+)?"
    r"(?:person|people|man|woman|men|women|human|celebrity|celebrities|"
    r"actor|actress|politician|influencer)\b"
    r")"
)


def adult_style_or_real_person(text: str) -> bool:
    """True when an 18+ prompt asks for stylized media or a real person."""
    for form in _forms(text or ""):
        inspect = _masked(form)
        if not inspect.strip():
            continue
        if _is_despaced_copy(text or "", form) or _is_despaced_copy(text or "", inspect):
            continue
        if _ADULT_STYLE_RE.search(inspect) or _ADULT_REAL_PERSON_RE.search(inspect):
            return True
    return False


def media_prompt_blocked(text: str, *, adult: bool | None = None) -> bool:
    """True when an asset prompt uses a media-only age, family or style cue."""
    raw = text or ""
    if not raw.strip():
        return False
    if adult is None:
        try:
            from core.asset_safety import asset_adult_enabled
            adult = asset_adult_enabled()
        except Exception:
            adult = False
    for form in _forms(raw):
        inspect = _masked(form)
        if not inspect.strip():
            continue
        if _is_despaced_copy(raw, form) or _is_despaced_copy(raw, inspect):
            continue
        if _ALWAYS_RE.search(inspect) or _NEAR.search(inspect):
            return True
        if adult and (_ADULT_STYLE_RE.search(inspect) or _ADULT_REAL_PERSON_RE.search(inspect)):
            return True
    return False


# School cues combined with sexual or fetish wording (owner-approved
# 2026-11-14 10:34 ET, after a model-written prompt at 10:01 ET mixed a school
# jacket and "at school" with a pet-play collar). Blocks in every channel,
# 18+ included, for user-written and model-written prompts. Either list alone
# does not block, so "a yellow school bus" or "a sexy vampire" still run.
_SCHOOL_CUES = {
    "school": (
        r"(?<!old\s)(?<!old-)\bschool(?:s|ing|yard|house|work|day|days|bag|bags)?\b"
        r"(?!\s+of\s+(?:fish|thought))"
    ),
    "classroom": (
        r"\bclass\s*rooms?\b|\bhome\s*room\b|\bhall\s*pass(?:es)?\b|\bclass\s*mates?\b|"
        r"\bdetention\b|\brecess\b|\bteacher'?s\s+pet\b"
    ),
    "student": (
        r"\bstudents?\b|\bpupils?\b|\bschool\s*(?:girl|boy)s?\b|"
        r"\bteacher\s*(?:and|&|/|x|-)\s*student\b|\bstudent\s*(?:and|&|/|x|-)\s*teacher\b"
    ),
    "grade": (
        r"\bfreshm[ae]n\b|\bsophomores?\b|\bjunior\s*high\b|\bjr\.?\s*high\b|\bhigh\s*school(?:ers?)?\b|"
        r"\bmiddle\s*school(?:ers?)?\b|\bgrade\s*school(?:ers?)?\b|\belementary\b|"
        r"\b\d{1,2}(?:st|nd|rd|th)\s*grade(?:rs?)?\b|"
        r"\b(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth)\s*grade(?:rs?)?\b|"
        r"\bgraders?\b"
    ),
    "uniform": (
        r"\bschool\s*(?:uniform|jacket|blazer|skirt|backpack|tie|shirt|sweater|cardigan|hoodie|shoes|socks)s?\b|"
        r"\b(?:plaid|pleated|tartan)\s+(?:mini\s*)?skirts?\b|\bsailor\s*(?:uniform|fuku|suit)\b"
    ),
}
_SEXUAL_CUES = {
    "sexual": (
        r"\bsexy\b|\bsexual\w*|\bsex\b|\bseduc\w*|\berotic\w*|\blewd\w*|\bhorny\b|\bsultry\b|"
        r"\bprovocative\w*|\bnsfw\b|\b18\s*\+|\bslutt?y\b|\bsluts?\b|\bnaughty\b|\bsteamy\b|"
        r"\baroused\b|\bporn\w*|\bhentai\b|\bxxx\b|\bonlyfans\b"
    ),
    "nudity": (
        r"\bnude\w*|\bnudity\b|\bnaked\b|\btopless\b|\bbottomless\b|\bundress\w*|"
        r"\bstrip(?:per|pers|ping|tease)\b"
    ),
    "lingerie": (
        r"\blingerie\b|\bpant(?:y|ies)\b|\bthongs?\b|\bbras?\b|\bunderwear\b|\bbikinis?\b|"
        r"\bgarters?\b|\bfishnets?\b"
    ),
    "fetish": (
        r"\bfetish\w*|\bkink\w*|\bbdsm\b|\bbondage\b|\bpet\s*-?\s*play\b|\bsubmissive\b|\bdominatrix\b|"
        r"\bspank\w*|\bleash(?:ed)?\b|"
        r"\b(?:pet|dog|slave|choker|leather|spiked|bdsm)\s+collars?\b|\bcollar(?:ed)?\s+and\s+leash(?:ed)?\b|"
        r"\bcollars?\s+with\s+(?:a\s+)?(?:\w+\s+){0,2}(?:tag|leash|bell|ring)\b|\bwearing\s+(?:a\s+)?collar\b"
    ),
}
_SCHOOL_RES = {name: re.compile(f"(?i)(?:{pat})") for name, pat in _SCHOOL_CUES.items()}
_SEXUAL_RES = {name: re.compile(f"(?i)(?:{pat})") for name, pat in _SEXUAL_CUES.items()}


def _cue_forms(text: str) -> list[str]:
    """Spacing-preserving forms only, so joined words cannot invent a cue."""
    from core.blocklist import _folded, normalize

    raw = text or ""
    out = [raw]
    for fn in (normalize, _folded, lambda s: _folded(s.translate(_LEET_MAP)), lambda s: _dehyphen(_folded(s))):
        try:
            item = fn(raw)
        except Exception:
            continue
        if item and item not in out:
            out.append(item)
    return out


def school_sexual_cues(text: str) -> tuple[list[str], list[str]]:
    """(school categories, sexual categories) found in the text. Both non-empty blocks."""
    school: set[str] = set()
    sexual: set[str] = set()
    for form in _cue_forms(text):
        school.update(name for name, rx in _SCHOOL_RES.items() if rx.search(form))
        sexual.update(name for name, rx in _SEXUAL_RES.items() if rx.search(form))
    return sorted(school), sorted(sexual)


def school_sexual_blocked(text: str) -> tuple[bool, str]:
    """(blocked, short reason naming the matched cue categories, never the prompt)."""
    school, sexual = school_sexual_cues(text)
    if school and sexual:
        return True, f"school={','.join(school)};sexual={','.join(sexual)}"
    return False, ""
