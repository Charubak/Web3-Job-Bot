"""Conservative, explainable matching for mid/senior growth / GTM / PMM roles.

A first-party role marked "Remote" can qualify without a stated location
restriction, but does not establish guaranteed worldwide eligibility. Only
first-party role evidence can qualify a listing. Incomplete or ambiguous
listings are retained for review, while definite mismatches are rejected.
This is a text-based screening aid, not a guarantee of an employer's eligibility.
"""

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import Literal, Optional
from urllib.parse import urlparse

MAX_AGE_DAYS = 30
EMPLOYER_EVIDENCE_MAX_AGE_DAYS = 90


@dataclass(frozen=True)
class FilterDecision:
    job: object
    status: Literal["qualified", "review", "rejected"]
    reasons: tuple[str, ...]
    timezone_note: str = ""

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)


@dataclass
class FilterResults:
    # Keep ordinary Job objects here so existing storage/notifier code works.
    qualified: list = field(default_factory=list)
    review: list[FilterDecision] = field(default_factory=list)
    rejected: list[FilterDecision] = field(default_factory=list)


def _decode(text: str) -> str:
    return html.unescape(str(text or ""))


def _normalise(text: str) -> str:
    text = _decode(text)
    text = re.sub(r"<\s*(?:script|style)\b[^>]*>.*?</(?:script|style)>", " ", text, flags=re.I | re.S)
    text = re.sub(r"</?(?:p|div|li|ul|ol|br|h[1-6])\b[^>]*>", ". ", text, flags=re.I)
    text = re.sub(r"<[^>]*>", " ", text)
    text = text.lower().translate(str.maketrans({"–": "-", "—": "-", "‑": "-", "’": "'"}))
    text = re.sub(r"\bu\.?s\.?a\b\.?", "usa", text)
    text = re.sub(r"\bu\.s\.(?=\W|$)", "us", text)
    text = re.sub(r"\bu\.k\.(?=\W|$)", "uk", text)
    # A list item is a clause boundary, not a space. Otherwise a US-only
    # retirement benefit can acquire hiring keywords from an unrelated bullet.
    text = re.sub(r"[\r\n]+", ". ", text)
    return re.sub(r"\s+", " ", text).strip(" .")


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text, flags=re.I) is not None


# Word boundaries matter: "intern" must not match "international", and "US"
# must not match "business". The location list is an aid, not an allowlist:
# unfamiliar location strings still cannot supply global-remote evidence.
_GEOGRAPHIES = (
    "united states|united kingdom|great britain|united arab emirates|"
    "north america|south america|latin america|south africa|south korea|"
    "new zealand|middle east|asia pacific|asia-pacific|"
    "europe|european union|european|americas|america|emea|apac|latam|"
    "asia|africa|oceania|usa|us|uk|eu|uae|"
    "canada|mexico|brazil|argentina|colombia|chile|peru|"
    "germany|france|spain|italy|netherlands|poland|portugal|sweden|"
    "norway|denmark|finland|belgium|austria|switzerland|ireland|greece|"
    "czechia|czech republic|hungary|romania|bulgaria|estonia|latvia|"
    "lithuania|ukraine|russia|serbia|croatia|cyprus|malta|iceland|"
    "india|pakistan|bangladesh|sri lanka|nepal|singapore|hong kong|"
    "china|japan|korea|taiwan|thailand|vietnam|malaysia|indonesia|"
    "philippines|australia|turkey|israel|saudi arabia|qatar|"
    "nigeria|kenya|egypt|morocco|ghana|"
    "new york|san francisco|los angeles|chicago|seattle|boston|austin|"
    "miami|nyc|bay area|california|texas|washington|"
    "london|berlin|paris|amsterdam|madrid|rome|lisbon|tallinn|"
    "toronto|vancouver|sydney|melbourne|dubai|abu dhabi|"
    "mumbai|bangalore|bengaluru|delhi|hyderabad|pune|chennai|"
    "tokyo|shanghai|beijing|seoul|jakarta|bangkok"
)
_GEO = rf"\b(?:{_GEOGRAPHIES})\b"
_REMOTE = r"\b(?:remote(?:ly)?|work(?:ing)? from (?:home|anywhere))\b"
_GLOBAL = r"\b(?:worldwide|world-wide|globally|global|anywhere|any country|all countries|any location|around the world|across the globe)\b"
_TIMEZONE = (
    r"\b(?:time[ -]?zones?|utc\s*[+-]?\s*\d{0,2}(?::\d{2})?|"
    r"gmt\s*[+-]?\s*\d{0,2}(?::\d{2})?|pst|pdt|est|edt|cst|cdt|"
    r"cet|cest|ist|aest|aedt)\b"
)


def _without_timezones(text: str) -> str:
    # Strip the geography only when it explicitly modifies a timezone / working
    # hours requirement. "Based in the US, with timezone overlap" stays intact.
    text = re.sub(
        rf"(?:{_GEO}|\b(?:american|eastern|western|pacific|central|indian|asian)\b)"
        r"(?:[ /-]+(?:eastern|western|pacific|central))?[ -]+"
        r"(?:time[ -]?zones?|(?:(?:business|working|office)\s+)?hours|time)\b",
        "timezone", text,
    )
    return re.sub(_TIMEZONE, "timezone", text)


def _timezone_note(location: str, description: str) -> str:
    parts = []
    for sentence in re.split(r"[.!?;\n]+", f"{location}. {description}"):
        sentence = sentence.strip()
        if sentence and (_has(_TIMEZONE, sentence) or _has(r"\b(?:business|working) hours\b|\bhours? (?:of )?overlap\b", sentence) or _without_timezones(sentence) != sentence):
            parts.append(sentence[:240])
    if not parts:
        return ""
    return "Timezone / hours requirement needs a personal availability check: " + "; ".join(dict.fromkeys(parts))[:600]


def _location_rejection(title: str, location: str, description: str) -> Optional[str]:
    loc = _without_timezones(location)
    if _has(r"\b(?:on[ -]?site|in[ -]?office|hybrid|office[ -]based)\b", f"{title} {loc}"):
        return "Location requires office or hybrid attendance"
    if _has(r"\b(?:partial(?:ly)?|partly|part|semi|mostly|occasionally)[ -]+remote\b", f"{title} {loc}"):
        return "Title or location permits only partially remote work"
    if _has(r"\b(?:not|non)[ -](?:fully[ -])?remote\b", f"{title} {loc}"):
        return "Title or location explicitly rules out remote work"
    if _has(_GEO, loc):
        return "Location is restricted to a country, city, or region"
    if _has(r"\b(?:except|excluding|excludes|restricted to|limited to|residents? only)\b", loc):
        return "Location includes an explicit geographic restriction or exclusion"
    # A title can carry a restriction that the location field omits.
    if _has(rf"\bremote\s*[-(/,:]*\s*(?:{_GEOGRAPHIES})\b|\b(?:{_GEOGRAPHIES})\s*(?:only|remote)\b", _without_timezones(title)):
        return "Title specifies a geographically restricted remote role"

    for sentence in re.split(r"[.!?;\n]+", description):
        sentence = _without_timezones(sentence.strip())
        if not sentence:
            continue
        # Benefit eligibility is not applicant residency. Remove only the
        # parenthetical qualifier, preserving any actual hiring clause.
        if _has(r"\b(?:401k|retirement|medical|dental|vision|insurance|benefits?)\b", sentence):
            sentence = re.sub(r"\((?:us|usa|uk|eu)(?:\s*\+\s*some other countries|\s+only)?\)", "", sentence)
        if _has(r"\b(?:role|position|work)\b.{0,30}\b(?:cannot|can't|may not) be performed in\s+india\b|\b(?:do not|cannot|can't)\s+(?:hire|employ)\s+(?:in|from)\s+india\b", sentence):
            return "Description explicitly excludes India"
        # Negated requirements must not become restrictions themselves.
        if _has(r"\b(?:no|without)\s+(?:geographic(?:al)?|country|location|residency)\s+(?:restrictions?|requirements?)\b", sentence):
            sentence = re.sub(r"\b(?:no|without)\s+(?:geographic(?:al)?|country|location|residency)\s+(?:restrictions?|requirements?)\b", "", sentence)
        sentence = re.sub(r"\b(?:no|not)\s+(?:(?:mandatory|required|compulsory)\s+)?(?:office|on[ -]?site|in[ -]?office|hybrid)\s+(?:work|attendance|visits?|requirements?)\b", "", sentence)
        sentence = re.sub(r"\bno\s+(?:(?:us|usa|eu|uk)[ -]only\s*(?:,\s*(?:or\s+)?|or\s+)?)+restrictions?\b", "", sentence)
        sentence = re.sub(r"\bno\s+hybrid\s+schedule\b", "", sentence)
        sentence = re.sub(r"\b(?:not required|no requirement)\s+to\s+(?:be based|reside|live|be located)\s+in\b[^,;]*", "", sentence)

        if _has(r"\b(?:(?:not|isn't) (?:(?:a|an|fully|completely) )?remote|no remote work|remote work (?:is )?(?:unavailable|not (?:available|allowed|supported))|(?:do not|don't|cannot|can't) (?:offer|support|allow) remote work)\b", sentence):
            return "Description explicitly rules out remote work"
        if _has(r"\b(?:partial(?:ly)?|partly|part|semi|mostly|occasionally)[ -]+remote\b", sentence):
            return "Description permits only partially remote work"
        if _has(r"\b(?:must|required to|need to|expected to|have to|needs to|should)\s+(?:currently\s+)?(?:be\s+)?(?:based|located|resid(?:e|ing)|liv(?:e|ing))\s+(?:in|within)\s+(?!anywhere\b|any country\b|any location\b|(?:(?:a|an|the|any|compatible) )?timezone\b)", sentence):
            return "Description requires residence in a particular location"
        if _has(r"\b(?:residen(?:ce|cy)|relocation)\s+(?:is\s+)?(?:required|mandatory)\b|\bmust relocate\b|\b(?:residen(?:ce|cy)|citizenship)\s+(?:in|of)\b.{0,60}\b(?:required|mandatory)\b|\bmust be (?:a )?(?:resident|citizen) of\b", sentence):
            return "Description requires residency or relocation"
        if _has(r"\b(?:must|required|requires|need|needs)\b.{0,45}\b(?:resident|citizen|residency|citizenship|passport|relocat(?:e|ion))\b", sentence):
            return "Description requires residency, citizenship, or relocation"
        # Open-ended location clauses catch places absent from the dictionary.
        # A country/city list can never prove that a role is globally open.
        place_match = re.search(
            r"\b(?:(?:candidates?|applicants?)\s+(?:(?:must|should|need to|may not|must not|cannot)\s+)?(?:be\s+)?(?:based|located|residing|living)\s+(?:in|within)|"
            r"(?:work(?:ing)? location|location|residence)\s+(?:must|needs to|has to)\s+(?:be\s+)?(?:in|within)|"
            r"(?:must|need to|required to)\s+work\s+from|"
            r"(?:hire|hiring|employ|employing)\s+(?:candidates?\s+|applicants?\s+|employees?\s+)?(?:in|from)|"
            r"(?:eligible|supported|permitted|approved)\s+(?:countries|locations)\s*(?::|are|include))\s*(.+)",
            sentence,
        )
        if place_match and not _has(r"^(?:anywhere|any country|any location|all countries|all locations|worldwide|globally|(?:(?:a|an|the|any|compatible) )?timezone|(?:a |your )?home(?: office)?)\b", place_match.group(1)):
            return "Description narrows hiring or residence to specified locations"
        if _has(rf"\b(?:based|located|residing|living|residents?|citizens?)\s+(?:only\s+)?(?:in|of)\s+(?:the\s+)?(?:{_GEOGRAPHIES})\b", sentence):
            # HQ/team history is not the candidate's residence requirement.
            if _has(r"\b(?:candidate|applicant|you|your|this role|this position|eligible|eligibility|only|require|must|need)\b", sentence):
                return "Description limits applicants to a particular location"
        if _has(r"\b(?:authorized|authorised|eligible|eligibility|authorization|authorisation|right|permit)\s+(?:to\s+)?work\s+in\b|\bwork (?:authorization|authorisation|permit)\b", sentence):
            if _has(_GEO, sentence):
                return "Description requires country-specific work authorization"
        if _has(rf"\bremote(?:ly)?\s*(?:[-(:,/]\s*|(?:only\s+)?(?:in|within|across|from|throughout)\s+)(?:the\s+)?(?:{_GEOGRAPHIES})\b", sentence):
            return "Description restricts remote work to a country or region"
        if _has(rf"\b(?:only|limited|restricted)\b.{{0,50}}\b(?:{_GEOGRAPHIES})\b|\b(?:{_GEOGRAPHIES})\b.{{0,25}}\bonly\b", sentence):
            if _has(r"\b(?:hire|hiring|work|working|remote|role|position|candidate|applicant|available|open|based|reside|location|eligible|eligibility)\b", sentence):
                return "Description limits hiring to specific locations"
        if _has(r"\b(?:cannot|can't|do not|don't|not able to|unable to)\s+(?:currently\s+)?(?:hire|employ|accept applicants|consider candidates)\b|\b(?:excluding|except|not available in|not open to|not eligible|ineligible)\b", sentence):
            if _has(_GEO, sentence) or _has(r"\bcountr(?:y|ies)|\bregions?\b", sentence):
                return "Description explicitly excludes or restricts hiring locations"
        if _has(r"\b(?:specific|selected|supported|eligible|certain)\s+countries\b|\bcountries where we (?:have|can|are)\b|\bonly (?:able to )?hire (?:in|from)\b", sentence):
            return "Description limits hiring to a subset of countries"
        if _has(r"\b(?:approved|specific|selected|supported|eligible|certain)\s+(?:jurisdictions?|regions?|locations?)\b|\b(?:limited|restricted)\s+to\b.{0,50}\b(?:jurisdictions?|regions?|locations?|countries)\b", sentence) and _has(r"\b(?:remote|hire|hiring|employment|role|position|work)\b", sentence):
            return "Description limits hiring to approved locations or jurisdictions"
        if _has(r"\bfollowing\s+(?:countries|locations)\b", sentence) and _has(r"\b(?:hire|hiring|work|remote|eligible|available|role)\b", sentence):
            return "Description enumerates eligible hiring locations"
        if _has(rf"\b(?:open|available)\s+(?:only\s+)?to\s+(?:candidates?|applicants?|residents?)\s+(?:based\s+|located\s+)?(?:in|from|of)\s+(?:the\s+)?(?:{_GEOGRAPHIES})\b", sentence):
            return "Description specifies a restricted applicant location"
        if _has(r"\b(?:must|required to|need to|expected to)\s+(?:(?:be able|be willing|be available)\s+to\s+)?(?:work|attend|come|be|visit|travel)\b.{0,45}\b(?:offices?|on[ -]?site|in[ -]?person)\b|\b(?:hybrid|on[ -]?site|in[ -]?office)\s+(?:role|position|work|schedule)\b|\b\d+\s+days?\s+(?:per |a |each )?week\s+(?:in|at)\s+(?:the |our )?office\b", sentence):
            return "Description requires regular office attendance"
        if _has(r"\bwork(?:ing)?\b.{0,40}\boffice\b.{0,40}\b(?:days?|weekly|per week)\b", sentence):
            return "Description specifies recurring office attendance"
        if _has(r"\b(?:mandatory|compulsory)\s+(?:\w+\s+){0,3}(?:office|on[ -]?site|in[ -]?person)\b|\b(?:office|on[ -]?site|in[ -]?person)\s+(?:attendance|visits?|work)\s+(?:(?:is|are)\s+)?(?:required|mandatory|compulsory)\b", sentence):
            return "Description mandates office or on-site attendance"
    return None


def _global_remote_evidence(location: str, description: str) -> bool:
    loc = _without_timezones(location)
    # Explicit, structured location metadata is stronger than company prose.
    if _has(_GLOBAL, loc) and (_has(_REMOTE, loc) or re.fullmatch(r"(?:worldwide|world-wide|anywhere|any country|all countries|any location)", loc)):
        return True
    for sentence in re.split(r"[.!?;\n]+", description):
        if _has(r"\b(?:not|isn't|is not|cannot|can't|don't)\b.{0,35}\b(?:remote|worldwide|anywhere|globally)\b", sentence):
            continue
        if _has(r"\bwork(?:ing)?\s+(?:remotely\s+)?from\s+(?:anywhere|any country|any location|any part of the world)\b", sentence):
            return True
        if _has(r"\b(?:remote|remotely)\s*(?:[-(:,/]\s*)?(?:worldwide|globally|anywhere|across the globe|around the world)\b", sentence):
            return True
        if _has(r"\b(?:role|position|job|opportunity)\b.{0,35}\b(?:worldwide|globally|global)\s+(?:fully\s+)?remote\b", sentence):
            return True
        if _has(_REMOTE, sentence) and _has(r"\b(?:no|without)\s+(?:geographic(?:al)?|country|location|residency)\s+(?:restrictions?|requirements?)\b", sentence):
            return True
        if _has(r"\b(?:hire|hiring|candidates?|applicants?)\b.{0,60}\b(?:anywhere|worldwide|any country|all countries)\b", sentence) and _has(_REMOTE, f"{location} {description}"):
            return True
    return False


def _remote_role_evidence(title: str, location: str, description: str) -> bool:
    """Find actual role-level remote evidence without inferring global hiring.

    Structured metadata and an explicit role/title are sufficient under the
    user's screening preference. Generic distributed-company prose is not.
    Restrictions and unexplained location qualifiers are checked separately.
    """
    if _has(_REMOTE, location) or _has(r"\bremote\b", title):
        return True
    for sentence in re.split(r"[.!?;\n]+", description):
        if _has(r"\b(?:not|isn't|is not|cannot|can't|don't)\b.{0,35}\b(?:remote|remotely|work from home)\b", sentence):
            continue
        if _has(
            r"\b(?:this |the |our |a |an )?(?:role|position|job|opportunity)\s+"
            r"(?:(?:is|will be|can be|offers|supports|allows)\s+)?"
            r"(?:(?:fully|completely|entirely|100%|permanently)\s+)?remote\b|"
            r"\b(?:(?:fully|completely|entirely|100%|permanently)\s+)?remote\s+"
            r"(?:role|position|job|opportunity)\b|"
            r"\byou\s+(?:(?:will|can|may)\s+)?(?:work remotely|work from home)\b|"
            r"^\s*(?:work remotely|work from home)\b|"
            r"\b(?:workplace type|work arrangement|work location|location)\s*:\s*"
            r"(?:fully\s+)?remote\b",
            sentence,
        ):
            return True
    return False


def _location_review_reasons(location: str, description: str, title: str = "") -> list[str]:
    reasons = []
    if location:
        # Unknown city names / country codes cannot be hidden by a conflicting
        # worldwide claim in the description. Preserve uncertainty for review.
        remainder = _without_timezones(location)
        remainder = re.sub(_GLOBAL, " ", remainder)
        remainder = re.sub(r"\bremote[ -]first\b", "remote", remainder)
        remainder = re.sub(_REMOTE, " ", remainder)
        remainder = re.sub(r"\b(?:fully|only|full[ -]time|any|timezones?|to|and|or|hours?|hrs?|flexible|work|home)\b", " ", remainder)
        remainder = re.sub(r"[\W\d_]+", "", remainder)
        if remainder:
            reasons.append("Location contains an unexplained qualifier; eligibility needs verification")
    # Apply the same unknown-location check to remote title qualifiers. A city
    # absent from the geography dictionary is still not an unrestricted role.
    title_qualifiers = re.findall(r"[\[(]([^\])]*\bremote\b[^\])]*)[\])]", title)
    suffix = re.search(r"\bremote\s*[-,:/(\[]\s*(.+)$", title)
    if suffix:
        title_qualifiers.append("remote " + suffix.group(1))
    if any(_location_review_reasons(qualifier, "") for qualifier in title_qualifiers):
        reasons.append("Remote title contains an unexplained location qualifier; eligibility needs verification")
    if _has(r"\bwork (?:authorization|authorisation|permit)\b|\b(?:authorized|authorised|eligible|right|permit)\s+(?:to\s+)?work\s+in\b", description):
        reasons.append("Work-authorization requirements need country-specific verification")
    # A bare Remote tag must not override an explicit denial of open hiring,
    # even when the employer does not name its permitted countries.
    if _has(
        r"\b(?:not|isn't|is not|cannot|can't|don't|do not)\b.{0,45}"
        r"\b(?:hire worldwide|hiring worldwide|work from anywhere|globally remote|"
        r"worldwide remote|remote worldwide|remote globally)\b|"
        r"\b(?:worldwide|global)\s+(?:hiring|remote work)\s+(?:is\s+)?"
        r"(?:not (?:available|supported|allowed)|unavailable)\b",
        description,
    ):
        reasons.append("Worldwide hiring is explicitly limited; eligible locations are unconfirmed")
    for sentence in re.split(r"[.!?;\n]+", description):
        if _has(r"\bvisa\b", sentence) and _has(
            r"\b(?:required|require|requires|must|need|needs|no sponsorship|"
            r"no visa sponsorship|(?:cannot|can't|do not|don't) sponsor|"
            r"sponsorship (?:is )?(?:unavailable|not available|not provided|not offered))\b",
            sentence,
        ) and not _has(r"\b(?:no visa (?:is )?required|visa (?:is )?not required)\b", sentence):
            reasons.append("Visa requirements or sponsorship limits need eligibility verification")
            break
    return reasons


_TECHNICAL_OR_SALES = (
    r"\b(?:engineer(?:ing)?|developer|devrel|devops|architect|programmer|"
    r"data scien(?:tist|ce)|analyst|quantitative|research scientist|"
    r"sales|account executive|account manager|business[ -]development|bd|"
    r"revenue operations|salesforce|sdr|bdr|customer success|customer support|"
    r"customer care|recruit(?:er|ing|ment)|talent acquisition|human resources|"
    r"legal|compliance|finance|financial|risk manager)\b"
)
_JUNIOR_TITLE = r"\b(?:intern(?:ship)?|junior|jr\.?|entry[ -]level|graduate|trainee|apprentice|assistant|coordinator|associate|ambassador|moderator|moderation)\b"
_CORE_TITLE = r"\b(?:growth|gtm|go[ -]to[ -]market|product marketing|pmm)\b"
_ADJACENT_TITLE = r"\b(?:marketing|community|content|brand|partnerships?|ecosystem|communications?|public relations|social media|kol|influencer)\b"
_SENIOR_TITLE = r"\b(?:senior|sr\.?|lead|principal|staff|head|director)\b"
_OWNERSHIP = r"\b(?:own|ownership|lead|leading|drive|driving|define|defining|build|building|develop|developing|set|setting|shape|shaping)\b"
_GROWTH_MANDATE = r"\b(?:go[ -]to[ -]market|gtm|positioning|messaging|product launches?|product marketing|growth strateg(?:y|ies)|growth marketing|acquisition|activation|retention|conversion|growth experiments?|growth loops?|demand generation)\b"
_HANDS_ON = r"\b(?:hands[ -]on|player[ -]coach|individual contributor|first (?:growth|marketing)|founding (?:growth|marketer|marketing)|own (?:end[ -]to[ -]end )?product marketing|build from (?:scratch|zero)|zero[ -]to[ -]one|0[ -]to[ -]1)\b"
_EARLY_STAGE = r"\b(?:startup mode|resource[ -]lean|small,? (?:globally )?distributed team|early[ -]stage|seed[ -]stage|pre[ -]seed|series a|small team|lean team|founding team|first (?:growth|marketing)|founding (?:growth|marketer|marketing))\b"
_EXPERIENCED_REQUIREMENT = r"\b(?:[3-9]|1[0-9]|three|four|five|six|seven|eight|nine|ten)\s*(?:\+|(?:-|to)\s*(?:\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten))?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:(?:relevant|professional|growth|product|marketing|gtm|go[ -]to[ -]market|work|overall|total)\s+)*experience\b"
_LOW_EXPERIENCE = r"\b(?:[012]|zero|one|two)\s*(?:\+|(?:-|to)\s*(?:\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten))?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:(?:relevant|professional|growth|product|marketing|gtm|go[ -]to[ -]market|work|overall|total)\s+)*experience\b"


_RELEVANT_TENURE = r"\b(?:[3-9]|1[0-9]|three|four|five|six|seven|eight|nine|ten)\s*(?:\+|(?:-|to)\s*(?:\d+|three|four|five|six|seven|eight|nine|ten))?\s*(?:years?|yrs?)\s+in\s+(?:product marketing|growth marketing|marketing|gtm|go[ -]to[ -]market)\b"
_LOW_RELEVANT_TENURE = r"\b(?:[012]|zero|one|two)\s*(?:\+|(?:-|to)\s*(?:\d+|zero|one|two|three|four|five|six|seven|eight|nine|ten))?\s*(?:years?|yrs?)\s+in\s+(?:product marketing|growth marketing|marketing|gtm|go[ -]to[ -]market)\b"


def _has_experienced_requirement(description: str) -> bool:
    # A low-tenure range's upper endpoint is not a separate overall minimum.
    # This also covers spelled-out and mixed ranges such as "one to five".
    low_spans = [match.span() for match in re.finditer(rf"(?:{_LOW_EXPERIENCE}|{_LOW_RELEVANT_TENURE})", description, re.I)]
    return any(not any(start <= match.start() < end for start, end in low_spans)
               for match in re.finditer(rf"(?:{_EXPERIENCED_REQUIREMENT}|{_RELEVANT_TENURE})", description, re.I))


def _role_rejection(title: str, description: str) -> Optional[str]:
    if _has(r"\bsocial[ -]media\s+(?:marketing\s+)?manager\b|\bmanager\s*[,/|:-]?\s*social[ -]media\b", title):
        return "Dedicated Social Media Manager roles are excluded"
    if _has(_TECHNICAL_OR_SALES, title):
        return "Technical, sales, support, or other non-target role"
    if _has(r"\b(?:community|events?|eventmarketing|field[ -]marketing|partnerships?)\b", title):
        return "Community, partnerships / BD, or event-focused role is excluded"
    if _has(_JUNIOR_TITLE, title) or _has(r"\bmarketing executive\b", title):
        return "Junior or execution-support title does not fit the target seniority"
    if _has(r"\bproduct manager\b", title) and not _has(r"\bproduct marketing\b", title):
        return "Product management role rather than growth marketing / GTM / PMM"
    if not _has(rf"(?:{_CORE_TITLE}|{_ADJACENT_TITLE})", title):
        return "Title is outside growth, GTM, and product marketing"
    if _has(r"\b(?:entry[ -]level (?:role|position|opportunity)|(?:role|position) is (?:an? )?entry[ -]level|(?:ideal|suitable|perfect) for (?:fresh |recent )?graduates|no (?:prior |previous |professional )?experience (?:is )?required)\b", description):
        return "Description explicitly targets early-career candidates"
    for sentence in re.split(r"[.!?;\n]+", description):
        for match in re.finditer(rf"(?:{_LOW_EXPERIENCE}|{_LOW_RELEVANT_TENURE})", sentence):
            if _has(r"\b(?:mentor|mentoring|coach|coaching|junior colleagues|junior team)\b", sentence):
                continue
            # An experienced marketer may need only 1-2 years in a specific domain.
            # Do not confuse that with their overall experience requirement.
            domain_tenure = _has(r"^\s+(?:working\s+)?(?:in|with|using)\s+(?:the\s+)?(?:crypto|defi|web3|blockchain|saas|b2b|b2c|fintech|gaming)\b", sentence[match.end():])
            if not (domain_tenure and _has_experienced_requirement(description)):
                return "Description targets early-career experience"
    if _has(r"\b(?:primary focus|primarily|main responsibility|role focuses on|role is focused on)\b.{0,55}\b(?:community|moderation|events?|conferences?|business[ -]development)\b", description):
        return "Description is primarily community, events, or business development"
    if _has(r"\b(?:carry|carrying|own|meet|hit|exceed)\b.{0,30}\b(?:sales |revenue )?quota\b|\b(?:cold call(?:ing)?|outbound prospecting|close deals|closing deals)\b", description):
        return "Description is primarily quota-carrying sales / business development"
    if _has(r"\b(?:moderate|moderating)\s+(?:community|discord|telegram|discussions|forums)\b|\b(?:own|manage|managing|lead|run|running)\b.{0,30}\b(?:community (?:activation|management)|ambassador program|discord|telegram)\b", description):
        return "Description assigns community-management or moderation responsibilities"
    if _has(r"\b(?:manage|managing|own|build|building)\b.{0,25}\bsales pipeline\b|\b(?:negotiate|negotiating|execute|executing)\b.{0,35}\bcommercial deals\b|\bsource prospects\b", description):
        return "Description assigns sales-pipeline or commercial-deal responsibilities"
    return None


def _role_review_reasons(title: str, description: str) -> list[str]:
    reasons = []
    relevant_content = (
        _has(r"\b(?:content|copywriter|writer)\b", title)
        and not _has(r"\b(?:technical writer|documentation)\b", title)
        and _has(r"\b(?:web3|crypto|defi|blockchain)\b", f"{title} {description}")
        and _has(r"\b(?:marketing|audience|brand|storytelling|editorial|social|campaigns?)\b", description)
    )
    if not _has(_CORE_TITLE, title) and not relevant_content:
        if not _has(r"\bmarketing\b", title) or not _has(_GROWTH_MANDATE, description) or _has(r"\b(?:content|brand|social media|communications?|public relations|influencer|kol|seo)\b", title):
            reasons.append("Adjacent marketing specialty; growth / GTM / PMM ownership is not established")
    # Relevant ownership is checked separately below. Experienced ICs and
    # specialists need not have a leadership word or manager title.
    if not _has(_SENIOR_TITLE, title) and not _has_experienced_requirement(description):
        reasons.append("Mid/senior individual-contributor or manager experience needs verification")
    if not _has(_OWNERSHIP, description) or not (_has(_GROWTH_MANDATE, description) or relevant_content):
        reasons.append("Description does not establish ownership of growth, GTM, or product marketing")
    if _has(r"\b(?:chief|cmo|vp|vice president|svp|evp)\b", title):
        reasons.append("Executive-level scope needs review against a senior IC / player-coach target")
    if _has(r"\b(?:head|director)\b", title):
        if not (_has(_HANDS_ON, description) and _has(_EARLY_STAGE, description)):
            reasons.append("Head / director role needs evidence of early-stage, hands-on scope")
    if _has(r"\bmanage\b.{0,20}\b(?:large|global|multiple)\b.{0,20}\bteams?\b|\bmanage\b.{0,20}\bmanagers\b", description):
        reasons.append("Large-team people-management scope needs review")
    if _has(_GEO, _without_timezones(title)):
        reasons.append("Regional title may describe a market or a hiring restriction; verify eligibility")
    return reasons


def _is_primary_source(job) -> bool:
    # These prefixes are assigned only by the direct public-ATS adapters. An
    # aggregator merely linking to an ATS does not authenticate its own text.
    source = str(getattr(job, "source", "") or "").lower().strip()
    if any(source.startswith(prefix) and source[len(prefix):].strip() for prefix in ("greenhouse/", "lever/", "ashby/")):
        return True
    exact_sources = {
        "ecosystem/solana/hylo": {
            "https://jobs.solana.com/companies/hylo-2/jobs/97132369-marketing-growth-lead",
        },
        "company/curated": {
            "https://job-boards.eu.greenhouse.io/defuselabs/jobs/4997160101",
            "https://talent.yzilabs.com/jobs/c2ce0aad-fee1-49a3-9abc-b6ad32480795",
            "https://intercepta.io/careers/marketing-marketing-lead-remote",
            "https://www.thegroupthree.com/careers",
            "https://jobs.sogalventures.com/companies/tyb/jobs/95076705-lifecycle-marketing-lead",
            "https://cosign.co/jobs/0jw5ou60xx",
            "https://getblock.factorial.com/job_posting/full-stack-marketing-manager-marketing-pmm-327722",
        },
    }
    if source in exact_sources:
        return str(getattr(job, "url", "") or "").rstrip("/") in exact_sources[source]
    # These exact official company pages are fetched directly by the bounded
    # company-careers adapter, never inferred from an aggregator's outbound link.
    company_pages = {
        "company/gains.trade": ("hiring.gains.trade", "/roles/"),
        "company/hacken.io": ("hacken.io", "/careers/"),
    }
    if source in company_pages:
        try:
            parsed = urlparse(str(getattr(job, "url", "") or ""))
            host, prefix = company_pages[source]
            return parsed.scheme == "https" and parsed.hostname == host and parsed.path.startswith(prefix)
        except ValueError:
            return False
    return False


def _employer_review_reason(job, now: datetime) -> Optional[str]:
    """Country metadata must be enriched from a separately verified source.

    Never infer the employer's base from a job location, an Indian office, a
    founder's background, or the ATS slug. No network access occurs here.
    """
    country = str(getattr(job, "employer_country", "") or "").strip()
    source = str(getattr(job, "employer_country_source", "") or "").strip()
    verified_at = str(getattr(job, "employer_country_verified_at", "") or "").strip()
    if not country or country.lower() in {"unknown", "remote", "global", "worldwide", "international", "decentralized", "n/a"}:
        return "Employer home country is unknown; verified non-India employer evidence is required"
    try:
        parsed_source = urlparse(source)
        valid_source = parsed_source.scheme in ("http", "https") and bool(parsed_source.hostname)
    except ValueError:
        valid_source = False
    if not valid_source:
        return "Employer country lacks a verifiable source URL"
    try:
        checked = datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
        checked = checked.replace(tzinfo=timezone.utc) if checked.tzinfo is None else checked.astimezone(timezone.utc)
    except ValueError:
        return "Employer country evidence has no valid verification date"
    if checked > now + timedelta(days=1) or checked < now - timedelta(days=EMPLOYER_EVIDENCE_MAX_AGE_DAYS):
        return "Employer country evidence is stale or future-dated; re-verification is required"
    return None


def _employer_is_india_based(job, description: str) -> bool:
    country = _normalise(getattr(job, "employer_country", ""))
    if country in {"in", "ind", "india", "republic of india"} or _has(r"\bindia\b", country):
        return True
    # Headquarters/company-base language only: an office or team in India is
    # not evidence that an international employer is headquartered there.
    return _has(
        r"\b(?:headquartered in|headquarters (?:are |is )?in|our hq is in|"
        r"(?:our company|the company|we) (?:is|are) based in)\s+india\b|"
        r"\bindia[ -]based\s+(?:company|startup|start-up|business|employer)\b", description,
    )


def _parse_posted_date(posted: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """Parse board date formats as aware UTC; unknown dates stay unknown."""
    if not posted:
        return None
    posted = str(posted).strip()
    if not posted or posted.lower() in ("none", "null", "0"):
        return None
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    lowered = posted.lower()
    if lowered in ("now", "today", "just now"):
        return now
    if lowered == "yesterday":
        return now - timedelta(days=1)
    relative = re.fullmatch(r"(\d+)\s*(h|d|w|mo|m)", lowered)
    if relative:
        amount, unit = int(relative.group(1)), relative.group(2)
        scale = {"h": 3600, "d": 86400, "w": 604800, "mo": 2592000, "m": 2592000}[unit]
        try:
            return now - timedelta(seconds=amount * scale)
        except OverflowError:
            return None
    relative = re.fullmatch(r"(\d+)\s*(minute|hour|day|week|month)s?\s+ago", lowered)
    if relative:
        scale = {"minute": 60, "hour": 3600, "day": 86400, "week": 604800, "month": 2592000}[relative.group(2)]
        try:
            return now - timedelta(seconds=int(relative.group(1)) * scale)
        except OverflowError:
            return None
    if re.fullmatch(r"\d{10}|\d{13}", posted):
        try:
            return datetime.fromtimestamp(int(posted) / (1000 if len(posted) == 13 else 1), tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    try:
        dt = datetime.fromisoformat(posted[:-1] + "+00:00" if posted.endswith("Z") else posted)
    except ValueError:
        try:
            dt = parsedate_to_datetime(posted)
        except (ValueError, TypeError, OverflowError):
            return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _is_too_old(job, now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(timezone.utc)
    posted = _parse_posted_date(getattr(job, "posted", ""), now=now)
    upper = _parse_posted_date(getattr(job, "published_at_upper_bound", ""), now=now)
    # A recent republication cannot qualify an undated role; an old one proves
    # the role already existed more than MAX_AGE_DAYS ago.
    return any(value is not None and value < now - timedelta(days=MAX_AGE_DAYS)
               for value in (posted, upper))


def classify_job(job, now: Optional[datetime] = None) -> FilterDecision:
    """Return one explainable decision without mutating the original job.

    Explicit disqualifiers win over unknowns. Unrestricted Remote evidence can
    qualify, with country eligibility left unconfirmed. Other missing evidence
    still needs review. A timezone note is an availability check, not residency.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    title = _normalise(getattr(job, "title", ""))
    location = _normalise(getattr(job, "location", ""))
    description = _normalise(getattr(job, "description", ""))
    timezone_note = _timezone_note(location, description)
    rejection = _role_rejection(title, description) or _location_rejection(title, location, description)
    if rejection:
        return FilterDecision(job, "rejected", (rejection,), timezone_note)
    if _employer_is_india_based(job, description):
        return FilterDecision(job, "rejected", ("Employer is based in India",), timezone_note)
    if _is_too_old(job, now=now):
        return FilterDecision(job, "rejected", (f"Posting is older than {MAX_AGE_DAYS} days",), timezone_note)
    posted = _parse_posted_date(getattr(job, "posted", ""), now=now)

    reasons = _role_review_reasons(title, description)
    reasons.extend(_location_review_reasons(location, description, title))
    employer_reason = _employer_review_reason(job, now)
    if employer_reason:
        reasons.append(employer_reason)
    if len(description.split()) < 15:
        reasons.append("Full role description is missing or too thin to verify requirements")
    if not _is_primary_source(job):
        reasons.append("First-party employer / ATS evidence is missing; aggregator claims need verification")
    global_remote = _global_remote_evidence(location, description)
    if not (global_remote or _remote_role_evidence(title, location, description)):
        reasons.append("Role-level remote work is unconfirmed; empty locations and distributed-company prose are insufficient")
    if not getattr(job, "url", ""):
        reasons.append("Application URL is missing")
    current_open_verified = getattr(job, "current_open_verified", False) is True
    # Exclusion of other jurisdictions is not proof of India ineligibility,
    # and bare Remote is not proof that India is supported. Research it.
    if _has(r"\b(?:cannot|can't|may not) be performed in\b", description):
        reasons.append("Role has jurisdiction exclusions; verify India eligibility against the exact exclusion and ATS country metadata")
    if posted is None and not current_open_verified:
        reasons.append("Posting date is missing or unparseable; recency needs verification")
    if posted and posted > now:
        reasons.append("Posting date is in the future and needs verification")
    if reasons:
        return FilterDecision(job, "review", tuple(reasons), timezone_note)
    qualified_reasons = (
        "Mid/senior marketing ownership fits the target scope",
        ("First-party role explicitly advertises worldwide remote work; no residence restriction detected"
         if global_remote else
         "First-party role is Remote with no stated location restriction; country eligibility is unconfirmed"),
        "Employer country has recent sourced verification outside India",
    )
    if posted is None:
        qualified_reasons += ("Current opening verified; original posting date is unconfirmed",)
    return FilterDecision(job, "qualified", qualified_reasons, timezone_note)


def filter_jobs(jobs: list, now: Optional[datetime] = None) -> FilterResults:
    """Split listings into alert-worthy jobs, review decisions, and rejections."""
    results = FilterResults()
    for job in jobs:
        decision = classify_job(job, now=now)
        if decision.status == "qualified":
            results.qualified.append(job)
        elif decision.status == "review":
            results.review.append(decision)
        else:
            results.rejected.append(decision)
    # Keep source order within each group; dated openings precede verified-open
    # undated ones without inventing an original date from a recent republication.
    results.qualified.sort(key=lambda job: _parse_posted_date(getattr(job, "posted", ""), now=now) is None)
    return results


def apply_filters(jobs: list) -> list:
    """Backwards-compatible alert path: return only qualified Job objects."""
    return filter_jobs(jobs).qualified
