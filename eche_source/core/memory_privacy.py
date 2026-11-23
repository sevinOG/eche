# core/memory_privacy.py
# Privacy post-filter for long-term memory and episodes.
#
# The summarizer prompt already says: no names of other people, no sensitive
# details, nothing about minors. This is the code check behind it, applied to
# every stored memory entry and every episode summary. It fails closed: an
# entry that looks like it names someone else or holds a sensitive detail is
# dropped (memory) or has the name replaced (episode summaries).

from __future__ import annotations

import re

# Common given names. Words that are also ordinary English, product, or
# language names (Will, May, Ruby, Grace, Max ...) are left out on purpose.
_FIRST_NAMES = frozenset(
    """
    aaron abby abigail adam adrian aiden alan albert alex alexa alexander alexis alice alicia
    allison amanda amy andre andrea andrew andy angela anna anne annie anthony antonio ashley
    austin bailey barbara becky ben benjamin bernard beth betty billy bob bobby brad bradley
    brandon brenda brian brittany brooke bruce bryan caleb cameron carl carla carlos carol
    caroline casey catherine charles charlie charlotte chloe chris christian christina
    christine christopher cindy claire cody colin connor courtney craig crystal cynthia dale
    dan dana daniel danielle danny darren dave david dean debbie deborah denise dennis derek
    diana diane donald donna doug douglas dylan eddie edward elena elijah elizabeth ellie
    emily emma eric erica erik ethan evan frank fred gabriel gary george gerald gina greg
    gregory hailey hannah harold harry heather helen henry holly howard ian isaac isabella
    jacob jake james jamie jane janet jared jasmine jason jeff jeffrey jen jenna jennifer
    jeremy jerry jesse jessica jill jim jimmy joan joe joel john johnny jon jonathan jordan
    jose joseph josh joshua juan julia julie justin karen kate katherine kathy katie keith
    kelly ken kenneth kevin kim kimberly kyle larry laura lauren leah linda lisa logan lucas
    luis luke lydia madison maria marie marilyn mario martha martin mary matt matthew megan
    melissa michael michelle mike miranda molly monica nancy natalie nathan nicholas nick
    nicole noah olivia oscar pamela patricia patrick paul peter philip phillip rachel ralph
    randy raymond rebecca richard rick ricky rob robert robin roger ronald ross russell ryan
    sam samantha samuel sandra sara sarah scott sean sharon shawn sophia stacy stephanie
    stephen steve steven susan tammy tanya taylor teresa terry thomas tiffany tim timothy
    tina todd tom tommy tony tracy travis tyler valerie vanessa veronica victoria vincent
    walter wayne wendy william zach zachary
    """.split()
)

# Words around a capitalized word that make it a person.
_PERSON_CUES = re.compile(
    r"(?i)\b(?:friend|buddy|pal|coworker|co-worker|colleague|boss|roommate|neighbou?r|"
    r"girlfriend|boyfriend|wife|husband|partner|ex|brother|sister|mom|mother|dad|father|"
    r"cousin|uncle|aunt|son|daughter|named|called)\s+([A-Z][a-z]{2,})\b"
)
_CAP_WORD = re.compile(r"\b([A-Z][a-z]{2,})\b")

_SENSITIVE = (
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),  # email
    re.compile(r"(?<!\d)(?:\+?\d[\s().-]?){9,14}\d(?!\d)"),  # phone-like
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # ssn
    re.compile(r"\b(?:\d[ -]?){13,16}\b"),  # card-like
    re.compile(
        r"(?i)\b\d{1,5}\s+\w+(?:\s+\w+)?\s+(?:street|st|avenue|ave|road|rd|boulevard|blvd|lane|ln|drive|dr|court|ct)\b"
    ),
    re.compile(r"(?i)\b(?:password|passcode|passwd|pin code|api key|secret key|token is|seed phrase)\b"),
    re.compile(
        r"(?i)\b(?:diagnos\w*|medication|prescri\w*|therap(?:y|ist)|psychiatri\w*|hiv|std|sti|"
        r"pregnan\w*|miscarr\w*|rehab|overdos\w*|suicid\w*|self[- ]harm|chemo\w*|cancer)\b"
    ),
    re.compile(r"(?i)\b(?:salary|bank account|routing number|credit score|in debt|owes? (?:me|him|her|them)|net worth)\b"),
    re.compile(r"(?i)\b(?:sex life|sexual orientation|hook(?:ed)? up with)\b"),
)

# Ages under 18 and minor words. summary_safety handles youth cues next to
# sexual content; memory drops any mention of minors outright.
_MINOR = re.compile(
    r"(?i)\b(?:minor|minors|underage|kid|kids|child|children|toddler|teen|teens|teenager\w*|"
    r"high[- ]?school\w*|middle[- ]?school\w*|grade\s*(?:[1-9]|1[0-2])|"
    r"(?:[1-9]|1[0-7])\s*(?:years?\s*old|yrs?\s*old|y/?o))\b"
)

_ALWAYS_OK = frozenset({"eche", "them", "they", "eche's"})


def _own(names) -> set[str]:
    out: set[str] = set()
    for name in names or ():
        for part in re.findall(r"[A-Za-z]{2,}", str(name or "")):
            out.add(part.lower())
    return out


def third_party_names(text: str, own_names=(), known_names=()) -> list[str]:
    """Names of other people found in text.

    own_names are the person's own names (allowed). known_names are other
    members' display names (each counts as a name when it appears
    capitalized). A capitalized word counts when it is a common given name,
    a known member name, or follows a person word ("my friend Dale").
    """
    body = text or ""
    mine = _own(own_names) | _ALWAYS_OK
    known = {n.lower() for n in _own(known_names) if len(n) >= 3} - mine
    found: list[str] = []
    for match in _PERSON_CUES.finditer(body):
        word = match.group(1)
        if word.lower() not in mine and word not in found:
            found.append(word)
    for match in _CAP_WORD.finditer(body):
        word = match.group(1)
        low = word.lower()
        if low in mine or word in found:
            continue
        if low in _FIRST_NAMES or low in known:
            found.append(word)
    return found


def is_sensitive(text: str) -> bool:
    body = text or ""
    return any(rx.search(body) for rx in _SENSITIVE)


def mentions_minor(text: str) -> bool:
    body = text or ""
    if _MINOR.search(body):
        return True
    try:
        from core.summary_safety import risky

        return risky(body)
    except Exception:
        return False


def entry_problem(text: str, own_names=(), known_names=()) -> str:
    """Why a memory entry may not be stored, or "" when it may."""
    if mentions_minor(text):
        return "minor"
    if is_sensitive(text):
        return "sensitive"
    if third_party_names(text, own_names, known_names):
        return "third-party name"
    return ""


def redact_names(text: str, own_names=(), known_names=()) -> str:
    """Episode summaries: other people's names become "someone"."""
    out = text or ""
    for name in third_party_names(out, own_names, known_names):
        out = re.sub(rf"\b{re.escape(name)}(?:'s)?\b", "someone", out)
    return out
