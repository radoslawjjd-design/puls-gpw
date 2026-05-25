"""
Broker Duel agent - komentarz 2 brokerow (standard + short) na watek X.

Dwa formaty:
  - split:    4 niezalezne komentarze (o_sobie + do_przeciwnika per broker)
  - exchange: 4-turowa wymiana zdan (standard -> short -> standard -> short)

Manualny trigger: `python broker_duel.py --eod "..."`.
Persony z broker/persona_*.md, bez BQ, bez historii.
"""
import logging
from pathlib import Path

from agents.vertex_client import call_gemini_json

logger = logging.getLogger(__name__)

_MAX_COMMENT_CHARS = 500
_MAX_TURN_CHARS = 300
_PERSONAS_CACHE: dict[str, tuple[str, str]] = {}


def load_duel_personas() -> tuple[str, str]:
    """Czyta broker/persona_standard.md + broker/persona_short.md.

    Cached per process. Jesli plik nie istnieje, zwraca pusty string -
    agent dziala, ale komentarze beda generyczne.
    """
    if "personas" in _PERSONAS_CACHE:
        return _PERSONAS_CACHE["personas"]

    def _read(path: str) -> str:
        p = Path(path)
        if p.exists():
            return p.read_text(encoding="utf-8")
        logger.warning(f"Persona file not found: {path}")
        return ""

    standard = _read("broker/persona_standard.md")
    short = _read("broker/persona_short.md")
    _PERSONAS_CACHE["personas"] = (standard, short)
    return standard, short


_SYSTEM_PROMPT = """Jestes moderatorem pojedynku dwoch brokerow na GPW komentujacych miniony tydzien sesji (pojedynki rozgrywaja sie co tydzien, nie codziennie).
Kazdy ma wlasna persone (styl, strategie, czerwone linie). Trzymaj sie person ostro -
kazda ma sekcje 'Anti-AI rules', czytaj ja i przestrzegaj.

========================================================================
FAKTY vs FIKCJA - NAJWAZNIEJSZA ZASADA (dotyczy OBU brokerow):
========================================================================
EOD podany przez operatora to JEDYNE zrodlo faktow. Nie wolno ci zmyslac
zadnych konkretow spoza EOD.

ZERO PROWIZJI: w tej symulacji brokerzy NIE placa zadnych prowizji od
transakcji. Nie wspominaj w ogole o prowizjach - ani swoich, ani przeciwnika.
Nie drwij ze 'ile zostawil prowizji brokerowi', nie komentuj 'netto po
prowizji' itp. Temat prowizji NIE ISTNIEJE w tym pojedynku.
- ZAKAZANE deklarowanie akcji ktorych EOD nie potwierdza: 'zamknalem pozycje',
  'wszedlem', 'scalpnalem', 'kupilem', 'sprzedalem', 'wyszedlem na +X%',
  'zgarnalem +Y zl', 'otworzylem short na Z' - NIE, chyba ze operator to
  wprost podal w EOD.
- ZAKAZANE wymyslanie liczb, procentow, cen, tickerow, spolek, katalizatorow
  ktorych nie ma w EOD.
- ZAKAZANE obietnice przyszlych ruchow ('jutro kupie X', 'dzis wieczorem
  zamykam'). Mozesz co najwyzej powiedziec ogolnie 'szukam katalizatorow',
  'czekam na sygnal' - bez konkretnych tickerow.

CO WOLNO:
- Komentowac to co jest w EOD (wlasny wynik, wyniki spolek, ruchy spolek
  ktore operator opisal).
- Reagowac na wypowiedzi przeciwnika (atakowac jego styl, filozofie,
  horyzont, rotacje).
- Wyrazac emocje, opinie, generalne podejscie do rynku ('momentum jest',
  'fundamenty trzymaja', 'rynek nerwowy') - bez konkretnych tradow.
- Uzywac zargonu ('scalp', 'longi', 'rakieta', 'flush') OGOLNIE jako slownika,
  ale NIE do deklarowania ze wlasnie cos zrobiles.

Reguly za zlamanie: kazdy zmyslony konkret = komentarz do wyrzucenia.

========================================================================
KRYTYCZNE zasady stylu (dotycza OBU brokerow):
========================================================================
- Pisz jak zywy czlowiek po polsku, nie jak asystent AI. Lam rytm zdan.
  Wrzucaj rownowazniki, urwane zdania, pauzy typu 'no.', 'ok.', 'serio?'.
- NIE wygladzaj. Jak persona mowi 'zaczepny' albo 'sucho-ironiczny' - badz
  zaczepny i sucho-ironiczny. Gdy masz watpliwosc 'czy to nie za ostre',
  zostaw ostrzejsza wersje. Grzeczne = nudne = zle.
- NIE rob idealnie wywazonych zdan typu 'z jednej strony... z drugiej...',
  'trzeba przyznac', 'warto zauwazyc', 'podsumowujac'.
- NIE opisuj swojego stylu ('jako konserwatywny...', 'bedac spekulantem...') -
  pokazuj go przez konkret i ton.
- Odwoluj sie do LICZB i SPOLEK z EOD. Konkret > ogolnik. ALE tylko do tego
  co EOD faktycznie zawiera.
- Zero sztucznego szacunku miedzy brokerami. Nie gratuluja sobie. Docinaja.
- ZAKAZ mowienia 'jutro' i konkretnych dni tygodnia w kontekscie nastepnego
  odcinka serii. Pojedynek jest TYGODNIOWY (co piatek) - mow 'w przyszlym
  tygodniu', 'w nastepnym odcinku', 'za tydzien'. Jesli w KONTEKST SERII lub
  sekcji NASTEPNA SESJA jest podana konkretna informacja - mozesz jej uzyc.
- Numerujesz serie TYGODNIAMI, nie dniami. Mowisz 'tydzien 2', 'trzeci
  tydzien', a NIE 'dzien 2' / 'trzeci dzien'. Slowo 'dzien' dozwolone tylko
  gdy mowisz o konkretnym dniu tygodnia ('piatkowa sesja', 'czwartek').

W tekstach uzywaj pelnych polskich znakow diakrytycznych (a, e, l, o, s, c,
z) - tresc idzie bezposrednio na X.

========================================================================
META-ZASADA (najwazniejsza): NATURALNOSC > LISTA WYMAGAN
========================================================================
Jesli masz wybor miedzy spelnieniem wszystkich regul z tego promptu
a brzmieniem jak zywy czlowiek - wybieraj czlowieka.

TWARDE LIMITY (zeby nie bylo cringe):
- KSYWKA PRZECIWNIKA: maksymalnie 1 raz w CALYM 4-tweetowym watku. Nie 1
  raz w swoim tweecie - 1 raz w calym pojedynku. Domyslnie ZERO.
  Jesli ksywka pojawila sie w turn 0, kolejne 3 turny MAJA jej nie zawierac.
  Wrzucic ksywke tylko jak wejdzie sama, w jednym miejscu, dla puenty.
- ZERO metafor ze swiata zwierzat, wyscigu, mety, pancerza, drzemki,
  lusterka, pedu, linii startu. Zero storytellingu z bajek. Nawet jesli
  ksywka do tego zacheca - nie idz tam. Mowa ma byc z rynku kapitalowego,
  nie z podrecznika do szkoly podstawowej.
- Liczby z EOD przywolujesz wtedy kiedy pracuja na pointe, nie 'bo trzeba'.
- Lepiej 1 zdanie ktore zostaje w glowie niz 3 poprawne ktore nikt nie
  zapamieta.

CO BRZMI NATURALNIE (wskazowki dla dialogu):
- Nie kazdy tweet musi odpowiadac na wszystko co powiedzial przeciwnik.
  Mozna cos zignorowac, wrocic do swojego tematu.
- Dlugosci tweetow moga byc rozne (jeden 80 znakow, drugi 280). Nie szukaj
  symetrii.
- Emocje dnia: raz mozesz byc zmeczony, raz rozbawiony, raz zirytowany.
  Nie musisz byc pomnikiem swojej persony w kazdym zdaniu.
- Czasem jeden ma wieksza ochote na walke niz drugi - drugi odpuszcza.

CRINGE LIST (czego NIGDY nie pisz):
- Wciskanej ksywki w kazdy tweet
- Metafor bajkowych w stylu 'biegniesz do mety', 'schowaj sie do pancerza'
- Influencerskich CTA: 'a wy z kim dzis?', 'lajk jesli zgadzacie'
- Emoji jako puenta
- Pozowanej zaczepnosci 'a ja ci mowie ze...'
- Ogolnikow typu 'rynek jest trudny', 'fundamenty zawsze wygraja'"""


def build_duel_prompt(
    eod_text: str,
    persona_standard: str,
    persona_short: str,
) -> str:
    """Prompt na format SPLIT - jedno wywolanie Gemini, 4 niezalezne komentarze."""
    return f"""{_SYSTEM_PROMPT}

========================================================================
PERSONA BROKERA STANDARD (konserwatywny, dlugoterminowy):
========================================================================
{persona_standard}

========================================================================
PERSONA BROKERA SHORT (spekulant, krotkoterminowy):
========================================================================
{persona_short}

========================================================================
STAN PORTFELI + SPOLKI NA KONIEC SESJI (dane od operatora):
========================================================================
{eod_text}

========================================================================
ZADANIE
========================================================================
Wygeneruj:
1. headline - tweet startowy watku X (~260 znakow). Neutralny ton moderatora,
   wynik dnia obu brokerow (liczby z EOD), zajawka 'oto jak komentuja'.
2. standard.o_sobie - broker standard komentuje wlasny wynik.
3. standard.do_przeciwnika - broker standard mowi do brokera short.
4. short.o_sobie - broker short komentuje wlasny wynik.
5. short.do_przeciwnika - broker short mowi do brokera standard.

Kazdy komentarz 2-4 zdania, maksymalnie 500 znakow. Odwoluj sie do konkretow
z EOD. Trzymaj sie tonu swojej persony.

Zwroc CZYSTY JSON:
{{
  "headline": "...",
  "standard": {{
    "o_sobie": "...",
    "do_przeciwnika": "..."
  }},
  "short": {{
    "o_sobie": "...",
    "do_przeciwnika": "..."
  }}
}}"""


def _truncate(text: str, limit: int = _MAX_COMMENT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "\u2026"  # ellipsis


def validate_duel_response(resp: dict) -> dict:
    """Sprawdza schemat SPLIT i tnie komentarze do 500 znakow."""
    if not isinstance(resp, dict):
        raise ValueError("Response must be a dict")

    if "headline" not in resp or not resp["headline"]:
        raise ValueError("Missing required field: headline")

    for broker_key in ("standard", "short"):
        if broker_key not in resp or not isinstance(resp[broker_key], dict):
            raise ValueError(f"Missing or invalid broker block: {broker_key}")
        for sub in ("o_sobie", "do_przeciwnika"):
            if sub not in resp[broker_key] or not resp[broker_key][sub]:
                raise ValueError(
                    f"Missing required field: {broker_key}.{sub}"
                )

    return {
        "headline": _truncate(resp["headline"]),
        "standard": {
            "o_sobie": _truncate(resp["standard"]["o_sobie"]),
            "do_przeciwnika": _truncate(resp["standard"]["do_przeciwnika"]),
        },
        "short": {
            "o_sobie": _truncate(resp["short"]["o_sobie"]),
            "do_przeciwnika": _truncate(resp["short"]["do_przeciwnika"]),
        },
    }


def generate_duel_commentary(eod_text: str) -> dict:
    """Format SPLIT - jedno wywolanie Gemini, 4 niezalezne komentarze."""
    persona_standard, persona_short = load_duel_personas()
    prompt = build_duel_prompt(eod_text, persona_standard, persona_short)

    logger.info(f"Duel prompt length: {len(prompt)} chars")

    response = call_gemini_json(
        prompt,
        max_retries=2,
        metadata={"agent": "broker_duel"},
    )

    if response is None:
        raise RuntimeError("Gemini zwrocil None - wszystkie retry zawiodly")

    logger.info(f"Duel response keys: {list(response.keys()) if isinstance(response, dict) else type(response)}")
    return validate_duel_response(response)


# =========================================================================
# Exchange format - 4-turowa wymiana (standard -> short -> standard -> short)
# =========================================================================
# Kazdy kolejny broker widzi tekst poprzednich turnow. 5 wywolan Gemini:
# headline (1) + 4 turny (4). Limit 300 znakow per turn.

_EXCHANGE_ORDER = ("standard", "short", "standard", "short")


def validate_exchange_response(resp: dict) -> dict:
    """Sprawdza schemat exchange + tnie kazdy turn do 300 znakow."""
    if not isinstance(resp, dict):
        raise ValueError("Response must be a dict")

    if "headline" not in resp or not resp["headline"]:
        raise ValueError("Missing required field: headline")

    turns = resp.get("exchange")
    if not isinstance(turns, list) or len(turns) != 4:
        got = len(turns) if isinstance(turns, list) else "non-list"
        raise ValueError(f"Exchange must have exactly 4 turns, got {got}")

    for i, (turn, expected_broker) in enumerate(zip(turns, _EXCHANGE_ORDER, strict=False)):
        if not isinstance(turn, dict):
            raise ValueError(f"Turn {i} must be a dict")
        if turn.get("broker") != expected_broker:
            raise ValueError(
                f"Turn {i} must alternate (expected broker={expected_broker}, "
                f"got {turn.get('broker')!r})"
            )
        if not turn.get("text"):
            raise ValueError(f"Turn {i} missing text")

    return {
        "headline": _truncate(resp["headline"], _MAX_COMMENT_CHARS),
        "exchange": [
            {"broker": t["broker"], "text": _truncate(t["text"], _MAX_TURN_CHARS)}
            for t in turns
        ],
    }


_TURN_INSTRUCTIONS = {
    0: (
        "OTWIERASZ watek. Rzuc czym tydzien pachnial - swoj wynik, co sie dzialo "
        "ze spolkami. Bez grzecznosci, bez 'witam', po prostu zacznij. Konczysz "
        "jak czlowiek ktory wie ze druga strona zaraz zabierze glos - nie "
        "zapowiadasz tego slownie, po prostu zostawiasz miejsce."
    ),
    1: (
        "ODPOWIADASZ. Widziales co napisal wyzej - reaguj na to konkretnie. "
        "Mozesz zacytowac jego slowo albo fraze i ja wywrocic. Trzymaj swoj ton."
    ),
    2: (
        "RIPOSTA. Widzisz jego odpowiedz - kontruj. Punktuj jesli jest co, "
        "albo po prostu zbagatelizuj. Trzymaj swoj ton."
    ),
    3: (
        "ZAMYKASZ watek. To twoje ostatnie slowo dzisiaj. Niech bedzie mocne - "
        "ale nie upychaj 3 rzeczy naraz. Wybierz JEDNO z ponizszych, to co ci "
        "pasuje do rytmu wymiany: "
        "(a) sama puenta/riposta - jedno zdanie ktore zostaje w glowie; "
        "(b) zapowiedz czegos na nastepny tydzien (pojedynek jest tygodniowy). "
        "NIE mow 'jutro' - pojedynek wraca w nastepny piatek. Mow 'w przyszlym "
        "tygodniu', 'w nastepnym odcinku', 'za tydzien'. Jesli w KONTEKST SERII "
        "jest NASTEPNA SESJA - mozesz uzyc wprost; "
        "(c) zaczepne pytanie rzucone mimochodem (nie bezposrednie 'a wy z kim' "
        "jak u influencera - raczej cos jakby do siebie, co czytelnik chce "
        "skomentowac). "
        "Jedno z tych trzech. Nie wszystkie. Naturalnosc > checklista."
    ),
}


def _format_history(history: list[dict], nick_std: str = "", nick_sht: str = "") -> str:
    if not history:
        return "(brak - zaczynasz watek)"
    lines = []
    nickname_used = False
    combined = " ".join(t.get("text", "") for t in history).lower()
    for nick in (nick_std.lower(), nick_sht.lower()):
        if nick and nick in combined:
            nickname_used = True
            break

    for t in history:
        label = "STANDARD" if t["broker"] == "standard" else "SHORT"
        lines.append(f"[{label}]: {t['text']}")

    out = "\n\n".join(lines)
    if nickname_used:
        out += (
            "\n\n!! UWAGA: ksywka przeciwnika zostala juz uzyta w tym watku. "
            "NIE uzywaj jej ponownie - twoja wypowiedz MA byc bez ksywki."
        )
    return out


def _extract_nicknames_from_state_context(state_context: str) -> tuple[str, str]:
    """Best-effort parser ksywek ze sformatowanego state_context.

    Zwraca (nick_standard_dla_short, nick_short_dla_standard).
    Jesli state_context nie zawiera ksywek - zwraca ("", "").
    """
    nick_std, nick_sht = "", ""
    for line in state_context.splitlines():
        line = line.strip()
        if "STANDARD chce uzyc ksywki SHORT-a" in line or "jak STANDARD mowi o SHORT" in line:
            if "'" in line:
                nick_std = line.split("'")[1]
        elif "SHORT chce uzyc ksywki STANDARD-a" in line or "jak SHORT mowi o STANDARD" in line:
            if "'" in line:
                nick_sht = line.split("'")[1]
    return nick_std, nick_sht


def build_turn_prompt(
    eod_text: str,
    persona_self: str,
    persona_opponent: str,
    turn_index: int,
    history: list[dict],
    state_context: str = "",
    next_session: str = "",
) -> str:
    """Prompt na pojedynczy turn wymiany.

    persona_self     - pelna persona brokera ktory teraz mowi.
    persona_opponent - persona przeciwnika (dla swiadomosci kontekstu).
    history          - poprzednie turny (widzi je broker ktory teraz odpowiada).
    state_context    - opcjonalny kontekst serii (ksywki, leaderboard, streak).
    """
    instruction = _TURN_INSTRUCTIONS[turn_index]
    who_speaks = _EXCHANGE_ORDER[turn_index]
    who_opponent = "short" if who_speaks == "standard" else "standard"

    state_block = ""
    if state_context:
        state_block = f"""

========================================================================
KONTEKST SERII (leaderboard + ksywki - MUSISZ to uwzglednic):
========================================================================
{state_context}
"""

    next_session_block = ""
    if next_session:
        next_session_block = f"""

========================================================================
NASTEPNA SESJA GIELDOWA (mozesz sie powolywac w cliffhangerze):
========================================================================
{next_session}
"""

    return f"""{_SYSTEM_PROMPT}

========================================================================
TWOJA PERSONA (grasz role brokera {who_speaks.upper()}):
========================================================================
{persona_self}

========================================================================
PRZECIWNIK (broker {who_opponent.upper()}) - dla swiadomosci kontekstu:
========================================================================
{persona_opponent}
{state_block}{next_session_block}
========================================================================
STAN PORTFELI + SPOLKI NA KONIEC SESJI (dane od operatora):
========================================================================
{eod_text}

========================================================================
DOTYCHCZASOWA WYMIANA (to juz padlo):
========================================================================
{_format_history(history, *_extract_nicknames_from_state_context(state_context))}

========================================================================
TWOJ RUCH (turn {turn_index + 1} / 4):
========================================================================
{instruction}

LIMIT: max 300 znakow (1 tweet). Krotko, rytmicznie, z charakterem.

Zwroc CZYSTY JSON:
{{
  "text": "..."
}}"""


def _build_headline_prompt(eod_text: str, state_context: str = "") -> str:
    state_block = ""
    if state_context:
        state_block = f"""

========================================================================
KONTEKST SERII (wykorzystaj w headline - numer dnia, leaderboard, streak):
========================================================================
{state_context}
"""

    return f"""{_SYSTEM_PROMPT}

========================================================================
STAN PORTFELI + SPOLKI NA KONIEC SESJI:
========================================================================
{eod_text}
{state_block}
========================================================================
ZADANIE
========================================================================
Napisz headline - tweet startowy watku X (max 260 znakow). Jestes PLOTKARSKIM
moderatorem, piszesz jak portal sportowy komentujacy mecz (nie jak gazeta
gieldowa). Nagloek ma:
  - Podac numer TYGODNIA serii (jesli jest w KONTEKST SERII) i wynik tygodnia
    obu brokerow (liczby z EOD).
  - Zajawic KONFLIKT, nie tylko relacjonowac wyniki. Przyklady tonu:
    'Short w tyle pierwszy tydzien z rzedu', 'Drugi tydzien rozjazdu
    w tabeli', 'Remis na tygodniowym, ale Standard prowadzi skumulowanie'.
  - Ksywek uzywaj TYLKO jak wpasuja sie naturalnie (nie na sile).
  - Konczyc sie zajawka ('oto co mowia', 'rozmowa ponizej', 'komentarze ⬇️').

Zero grzecznosci. Zero nudnego 'Pojedynek brokerow - tydzien X. Standard +A,
Short +B. Komentarze ponizej'. To ma zaczepiac, budowac napiecie.

ZAKAZ slowa 'dzien' w kontekscie numeracji serii. Pojedynek jest tygodniowy -
mow 'tydzien', nie 'dzien'. 'Dzien' dozwolony tylko jak mowisz o konkretnym
dniu tygodnia ('piatkowa sesja', 'czwartkowy ruch') - nie jako numer odcinka.

Dopuszczalny max 1 emoji. Uzyj pelnych polskich diakrytykow.

Zwroc CZYSTY JSON:
{{
  "headline": "..."
}}"""


def generate_duel_exchange(
    eod_text: str,
    state_context: str = "",
    next_session: str = "",
) -> dict:
    """Format EXCHANGE - 5 wywolan Gemini: headline + 4 sekwencyjne turny.

    Kazdy kolejny broker widzi tekst poprzednich turnow (z history list).
    state_context - opcjonalny kontekst serii (leaderboard, streak, ksywki).
    next_session  - opcjonalna informacja o nastepnej sesji (np. 'poniedzialek
                    21.04, po dlugim weekendzie'). Bez niej prompt wymusza
                    neutralne sformulowania zamiast 'jutro'.
    """
    persona_standard, persona_short = load_duel_personas()

    # 1. Headline
    headline_resp = call_gemini_json(
        _build_headline_prompt(eod_text, state_context=state_context),
        max_retries=2,
        metadata={"agent": "broker_duel", "phase": "headline"},
    )
    if headline_resp is None:
        raise RuntimeError("Gemini zwrocil None na headline - wszystkie retry zawiodly")
    headline = headline_resp.get("headline", "")

    # 2-5. Cztery turny sekwencyjnie
    history: list[dict] = []
    for i, broker in enumerate(_EXCHANGE_ORDER):
        persona_self = persona_standard if broker == "standard" else persona_short
        persona_opp = persona_short if broker == "standard" else persona_standard

        turn_prompt = build_turn_prompt(
            eod_text=eod_text,
            persona_self=persona_self,
            persona_opponent=persona_opp,
            turn_index=i,
            history=history,
            state_context=state_context,
            next_session=next_session,
        )
        logger.info(f"Turn {i + 1}/4 ({broker}) prompt length: {len(turn_prompt)} chars")

        turn_resp = call_gemini_json(
            turn_prompt,
            max_retries=2,
            metadata={"agent": "broker_duel", "phase": f"turn_{i + 1}", "broker": broker},
        )
        if turn_resp is None:
            raise RuntimeError(
                f"Gemini zwrocil None na turn {i + 1} ({broker}) - wszystkie retry zawiodly"
            )

        text = turn_resp.get("text", "")
        history.append({"broker": broker, "text": text})

    return validate_exchange_response({
        "headline": headline,
        "exchange": history,
    })
