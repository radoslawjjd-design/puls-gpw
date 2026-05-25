"""
Broker Duel state - trwaly stan serii pojedynkow (JSON file).

Trzyma:
  - day         : licznik dniowek (int, inkrementowany przy update)
  - cumulative  : suma P&L od startu dla kazdego brokera
  - daily_wins  : ile razy kazdy wygral (+ ties)
  - recent_days : ostatnie 7 dniowek (lista)
  - nicknames   : jak brokerzy sie nawzajem nazywaja (stale, edytowalne)

Plik: broker/duel_state.json (baked-in defaults, user edytuje ksywki).
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path("broker/duel_state.json")
_RECENT_DAYS_LIMIT = 7

_DEFAULT_STATE = {
    "day": 0,
    "cumulative_pnl": {"standard": 0.0, "short": 0.0},
    "daily_wins": {"standard": 0, "short": 0, "tie": 0},
    "recent_days": [],
    "nicknames": {
        "short_by_standard": "Kasyno",
        "standard_by_short": "Profesor",
    },
}


def load_state(path: Path | None = None) -> dict:
    """Czyta state z pliku. Jesli nie istnieje lub uszkodzony - zwraca default."""
    p = path or _DEFAULT_PATH
    if not p.exists():
        logger.info(f"Duel state file missing at {p}, using defaults")
        return _clone_default()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        # merge z defaults zeby dobic brakujace klucze przy ewolucji schematu
        merged = _clone_default()
        _deep_merge(merged, data)
        return merged
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Corrupted duel state at {p}: {e}. Using defaults.")
        return _clone_default()


def _clone_default() -> dict:
    return json.loads(json.dumps(_DEFAULT_STATE))


def _deep_merge(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


def update_state(
    standard_pnl: float,
    short_pnl: float,
    path: Path | None = None,
) -> dict:
    """Inkrementuje dzien + dopisuje do cumulative/wins/recent. Zapisuje plik.

    Zwraca nowy state (po update).
    """
    p = path or _DEFAULT_PATH
    state = load_state(p)

    state["day"] += 1
    state["cumulative_pnl"]["standard"] = round(
        state["cumulative_pnl"]["standard"] + standard_pnl, 2
    )
    state["cumulative_pnl"]["short"] = round(
        state["cumulative_pnl"]["short"] + short_pnl, 2
    )

    if standard_pnl > short_pnl:
        state["daily_wins"]["standard"] += 1
    elif short_pnl > standard_pnl:
        state["daily_wins"]["short"] += 1
    else:
        state["daily_wins"]["tie"] += 1

    state["recent_days"].append({
        "day": state["day"],
        "standard": round(standard_pnl, 2),
        "short": round(short_pnl, 2),
    })
    if len(state["recent_days"]) > _RECENT_DAYS_LIMIT:
        state["recent_days"] = state["recent_days"][-_RECENT_DAYS_LIMIT:]

    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(
        f"Duel state updated: day={state['day']} "
        f"cumulative={state['cumulative_pnl']} wins={state['daily_wins']}"
    )
    return state


def format_state_for_prompt(state: dict) -> str:
    """Formatuje state do wstrzykniecia w prompt Gemini.

    Pokazuje dzien, cumulative P&L, bilans wygranych, recent streak i ksywki.
    Dla dnia 0 (brak historii) - krotka notka ze to pierwszy dzien serii.
    """
    day = state.get("day", 0)
    cum = state.get("cumulative_pnl", {"standard": 0.0, "short": 0.0})
    wins = state.get("daily_wins", {"standard": 0, "short": 0, "tie": 0})
    recent = state.get("recent_days", [])
    nicks = state.get("nicknames", {})

    nick_std = nicks.get("short_by_standard", "Kasyno")
    nick_sht = nicks.get("standard_by_short", "Profesor")

    if day == 0:
        return (
            f"TYDZIEN 1 serii - pierwszy pojedynek. Brak historii, od dzis liczymy.\n"
            f"KSYWKI (dostepne - uzywaj max 1 raz w calym watku, tylko gdy naturalnie pasuje):\n"
            f"  - gdy STANDARD chce uzyc ksywki SHORT-a: '{nick_std}'\n"
            f"  - gdy SHORT chce uzyc ksywki STANDARD-a: '{nick_sht}'"
        )

    # Cumulative leader
    if cum["standard"] > cum["short"]:
        leader = f"STANDARD prowadzi (+{cum['standard']:.2f} vs +{cum['short']:.2f} zl)"
    elif cum["short"] > cum["standard"]:
        leader = f"SHORT prowadzi (+{cum['short']:.2f} vs +{cum['standard']:.2f} zl)"
    else:
        leader = f"REMIS na kumulatywnym (po {cum['standard']:.2f} zl)"

    # Wins ratio
    wins_line = (
        f"Bilans tygodni: Standard {wins['standard']} - {wins['short']} Short "
        f"(remisy: {wins['tie']})"
    )

    # Recent streak (ostatnie 3 tygodnie kto wygral)
    streak_parts = []
    for entry in recent[-3:]:
        s_pnl = entry.get("standard", 0.0)
        sh_pnl = entry.get("short", 0.0)
        winner = "S" if s_pnl > sh_pnl else ("H" if sh_pnl > s_pnl else "=")
        streak_parts.append(f"T{entry['day']}:{winner}")
    streak_line = "Ostatnie 3 tygodnie: " + " ".join(streak_parts) if streak_parts else ""

    lines = [
        f"TYDZIEN {day + 1} serii pojedynkow",
        f"KUMULATYWNIE: {leader}",
        wins_line,
    ]
    if streak_line:
        lines.append(streak_line)
    lines.extend([
        "",
        "KSYWKI (dostepne dla obu stron - LIMIT: max 1 uzycie w CALYM watku,",
        "domyslnie zero; tylko gdy naturalnie pasuje do puenty):",
        f"  - gdy STANDARD chce uzyc ksywki SHORT-a: '{nick_std}'",
        f"  - gdy SHORT chce uzyc ksywki STANDARD-a: '{nick_sht}'",
    ])
    return "\n".join(lines)
