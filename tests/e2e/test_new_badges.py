"""PUL-94: per-item czyszczenie badge'y NOWE (Ogłoszenia + Obserwowane).

Cztery scenariusze rdzenia z planu announcement-seen-badges:
render przy pre-seedowanym progu, popup-clear + persystencja po reloadzie,
navigate-away, logout. pagehide/visibilitychange weryfikowane manualnie
(trudne do wiarygodnego wysterowania w Playwright).
"""

from datetime import datetime, timezone

from playwright.sync_api import Page, expect

from tests.e2e.conftest import E2E_ADMIN_EMAIL, E2E_PASSWORD, e2e_login_email

# Próg między starymi wierszami fixture (2026-01-01) a świeżymi bump'ami
# (now-1h/-2h) — badge'ują dokładnie dwa świeże wiersze. Seed jest FORCE
# (bez guardu if-absent): reload odpala pagehide, które celowo przesuwa próg,
# więc test persystencji per-item setu musi przywrócić stary próg — inaczej
# przechodziłby trywialnie, niczego nie dowodząc.
_OLD_SEEN_MS = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _seed_old_thresholds(page: Page) -> None:
    page.add_init_script(
        f"localStorage.setItem('faro_seen_announcements', '{_OLD_SEEN_MS}');"
        f"localStorage.setItem('faro_seen_my_wallet', '{_OLD_SEEN_MS}');"
    )


def _badges(page: Page):
    return page.locator("#table-body").get_by_text("NOWE")


def _login_admin(page: Page, base_url: str) -> None:
    # Ogłoszenia badge'ują w e2e tylko dla admina — list_announcements_user
    # jest mockowane na [] (pusta tabela usera nie ma czego badge'ować).
    e2e_login_email(page, base_url, email=E2E_ADMIN_EMAIL)


def test_badge_renders_against_preseeded_threshold(page: Page, live_server_url: str):
    """Risk (PUL-94): fundament pozostałych scenariuszy — przy progu starszym
    od published_at świeżych wierszy NOWE renderuje się dokładnie na nich
    (stare wiersze 2026-01-01 zostają bez badge)."""
    _seed_old_thresholds(page)
    _login_admin(page, live_server_url)

    expect(_badges(page)).to_have_count(2)


def test_popup_open_clears_badge_and_persists_across_reload(page: Page, live_server_url: str):
    """Risk (PUL-94 AC1): otwarcie popupu gasi badge klikniętego ogłoszenia
    natychmiast (bez re-renderu) i trwale (faro_seen_items przeżywa reload);
    nieklikany świeży wiersz wciąż badge'uje — to odróżnia per-item set od
    zwykłego awansu progu."""
    _seed_old_thresholds(page)
    _login_admin(page, live_server_url)

    first_row = page.locator("#table-body tr").first
    expect(first_row.get_by_text("NOWE")).to_be_visible()

    first_row.click()
    expect(page.locator("#modal-overlay")).to_be_visible()
    page.keyboard.press("Escape")

    expect(first_row.get_by_text("NOWE")).not_to_be_visible()
    expect(_badges(page)).to_have_count(1)

    page.reload()
    expect(page.locator("#page-label")).to_have_text("Strona 1")

    expect(_badges(page)).to_have_count(1)
    expect(page.locator("#table-body tr").first.get_by_text("NOWE")).not_to_be_visible()


def test_navigate_away_clears_badges_on_return(page: Page, live_server_url: str):
    """Risk (PUL-94 AC2): wyjście z Ogłoszeń do innego widoku awansuje próg,
    a powrót re-renderuje z cache (bez fetcha — pilnuje tego osobno
    test_watchlist_guard) i badge'y znikają już w tej samej sesji."""
    _seed_old_thresholds(page)
    _login_admin(page, live_server_url)
    expect(_badges(page)).to_have_count(2)

    page.get_by_role("button", name="Obserwowane").click()
    expect(page.locator("#my-wallet-view")).to_be_visible()

    page.get_by_role("button", name="Ogłoszenia").click()
    expect(page.locator("#announcements-view")).to_be_visible()

    expect(_badges(page)).to_have_count(0)


def test_logout_clears_badges_for_next_login(page: Page, live_server_url: str):
    """Risk (PUL-94 AC3): user obejrzał listę, nic nie kliknął i się wylogował —
    następne logowanie nie pokazuje już starych badge'y."""
    _seed_old_thresholds(page)
    _login_admin(page, live_server_url)
    expect(_badges(page)).to_have_count(2)

    page.locator("#profile-menu-btn").click()
    page.get_by_role("menuitem", name="Wyloguj").click()

    # Re-login z landing BEZ page.goto — nawigacja re-odpaliłaby force-seed
    # init-scriptu i przywróciła stary próg, unieważniając asercję. Ten sam
    # dokument = init-script milczy, a próg zaawansowany przez doLogout
    # obowiązuje (in-memory + localStorage).
    page.locator(".landing-nav").get_by_role("button", name="Zaloguj się").click()
    form = page.locator("#email-login-form")
    expect(form).to_be_visible()
    form.get_by_label("E-mail").fill(E2E_ADMIN_EMAIL)
    form.get_by_label("Hasło", exact=True).fill(E2E_PASSWORD)
    form.get_by_role("button", name="Zaloguj się").click()
    expect(page.locator("#page-label")).to_have_text("Strona 1")

    expect(_badges(page)).to_have_count(0)
