"""Is a bidder's past work "of the same nature" as the tender's? A rule-based answer, in English and
Marathi, for when no LLM judge is configured.

Works are grouped into families by what was built. A work in the tender's own family is similar; one
in a family the tender's title also names (a road-reinstatement tender mentions the water, sewer and
cable lines it restores over) is for the committee to judge; a work in none of them is not similar.
A description with no recognisable family is never declared dissimilar — that would be a guess.
"""

from __future__ import annotations

import re

FAMILIES: dict[str, re.Pattern] = {
    "road": re.compile(
        r"\broad|roadway|asphalt|bitum|\bB\.?C\.?\b|\bDBM\b|\bWMM\b|pavement|carpet|resurfac|reinstat|footpath|"
        r"kerb|paver|paving|concrete\s*road|रस्ता|रस्त्य|रस्ते|डांबर|खडीकरण|काँक्रीटीकरण|कॉंक्रीटीकरण|कॉक्रीटीकरण|"
        r"रिईनस्टेटमेंट|रिइन्स्टेटमेंट|रीइन्स्टेटमेंट|पेव्हिंग|ब्लॉक|फुटपाथ|फूटपाथ|पथावर|पदपथ|सायकल\s*ट्रॅक|दुभाजक|divider"
        # MahaTender titles transliterate Marathi: "RSTYANVAR ... REINSTETMENT", "DAMBARIKARAN"
        r"|\br?a?sta|\brastya|\brstya|\brste\b|dambari|khadikar|reinstet|kankritikar|concreti[sz]", re.I),
    "drainage": re.compile(
        r"drain|sewer|sewage|storm\s*water|manhole|chamber|ड्रेनेज|मलनि[ःः:]?\s*सारण|मलनिःसारण|मल[\s:]*वाहिन|"
        r"मलवाहिन|पावसाळी\s*(?:लाईन|गटार|वाहिन)|चेंबर|मॅनहोल|मनहोल|सांडपाणी|गटार|malnis|pavsali", re.I),
    "water_supply": re.compile(r"water\s*supply|water\s*line|pipeline|जलवाहिन|पाणीपुरवठा|पाणी\s*पुरवठा|पाण्याची\s*लाईन|panipur|jalvahin", re.I),
    "building": re.compile(
        r"building|renovat|repair\s*of\s*(?:school|office|hall)|painting|civil\s*work|इमारत|रंगरंगोटी|स्थापत्य|शाळा|"
        r"वसाहत|कॉलनी|रुग्णालय|स्मशान|अग्निशमक|सभागृह", re.I),
    "nala": re.compile(r"\bnala|\bnallah|desilt|नाला|नाल्या|गाळ\s*काढ|साफसफाई|सफाई|safsafai", re.I),
    "retaining_wall": re.compile(r"retaining\s*wall|protection\s*wall|compound\s*wall|संरक्षक\s*भिंत|सिमाभ[िी]त|रिटेनिंग|भिंत", re.I),
    "electrical": re.compile(r"electric|cable\s*laying|street\s*light|substation|SCADA|वीज|विद्युत|केबल|पथदिवे|\bvij\b|vidyut", re.I),
    "it": re.compile(r"software|application|web\s*portal|mobile\s*app|IT\s*services|data\s*entry|GIS|survey\s*app", re.I),
}
LABELS = {"road": "road works", "drainage": "drainage / sewer works", "water_supply": "water-supply works",
          "building": "building / civil repair works", "nala": "nala cleaning / desilting",
          "retaining_wall": "retaining / protection walls", "electrical": "electrical works", "it": "IT / software"}


def families(text: str) -> set[str]:
    return {name for name, pattern in FAMILIES.items() if pattern.search(text or "")}


# What was built is named just before the verb that ends a Marathi work title ("… संरक्षक भिंत
# बांधणे", "… ड्रेनेज लाईन टाकणे", "… रस्ते डांबरीकरण करणे"); everything earlier says where.
_VERB = re.compile(r"(?:करणे|बांधणे|टाकणे|बसविणे|बसवणे|काढणे|पुरविणे|विकसित\s*करणे|सुधारणा|दुरुस्ती)\s*[.।]?\s*(?:\(|$)", re.M)
_ENGLISH_OBJECT = re.compile(r"^(?:construction|providing|laying|repair|repairs|improvement|development|maintenance)\s+of\s+", re.I)


def work_families(description: str) -> set[str]:
    """The family of the work itself: "आंबेगाव … रस्त्यालगत नाल्यास संरक्षक भिंत बांधणे" is a wall,
    though it stands beside a road."""
    d = re.sub(r"\s+", " ", description or "").strip()
    m = None
    for m in _VERB.finditer(d):
        pass
    if m is not None:
        head = d[: m.start()]
        # the last clause: after the last comma / "मध्ये" / "येथे" / "अंतर्गत" / "मधील"
        tail = re.split(r",|मध्ये|येथे|येथील|अंतर्गत|मधील|परिसरात|दरम्यान", head)[-1]
        # "रस्त्यालगत" (beside the road), "रस्त्याच्या कडेने" (along the road), "रस्त्यावरील" (on the
        # road) say where the work is, not what it is.
        tail = re.sub(r"\S*(?:लगत|कडेने|बाजूने|वरील|जवळ|समोरील)", " ", tail)
        own = families(tail + " " + m.group(0))
        if own:
            return own
    if _ENGLISH_OBJECT.search(d):
        own = families(_ENGLISH_OBJECT.sub("", d)[:80])
        if own:
            return own
    return families(d)


def judge(required: str, tender_title: str, work: str) -> tuple[str, str]:
    """(similar | uncertain | not_similar, rationale)."""
    primary = families(required)
    related = families(tender_title) - primary
    mine = work_families(work)
    if not primary:
        primary, related = families(tender_title), set()
    if not primary:
        return "uncertain", "The tender does not name the nature of work precisely enough for a rule-based check."
    if not mine:
        return "uncertain", "The work's description does not say what kind of work it was."
    if mine & primary:
        return "similar", f"{LABELS[sorted(mine & primary)[0]].capitalize()}, as the tender requires."
    if mine & related:
        return ("uncertain", f"{', '.join(LABELS[f] for f in sorted(mine))} — related to this tender's work "
                             f"({', '.join(LABELS[f] for f in sorted(primary))}); evaluator to judge similarity.")
    return "not_similar", (f"{', '.join(LABELS[f] for f in sorted(mine))}, not "
                           f"{' / '.join(LABELS[f] for f in sorted(primary))}.")
