# Artefakt 3 — Mapa kontrybutorska (kto co wie)

> Wide Scan, `puls-gpw`. Zebrane 2026-08-07 z historii gita (500 commitów, 2026-05-20 → 2026-08-07).

## 0. Pytanie trzeba przeformułować

Artefakt kontrybutorski ma odpowiadać: *kto zna który obszar i do kogo pójść z pytaniem*.
W tym repozytorium odpowiedź jest jednozdaniowa i bezużyteczna:

| Autor | Commity |
| -- | -- |
| `Radek <radoslaw.jjd@gmail.com>` | 369 |
| `radoslawjjd-design <radoslaw.jjd@gmail.com>` | 130 (squash merge przez UI GitHuba) |

To **jedna osoba pod dwiema tożsamościami gita** — lokalny `user.name` i konto GitHuba.
Mapa wiedzy ludzkiej ma jeden węzeł.

Sensowne pytanie w tym kontekście brzmi inaczej: **skoro autor jest jeden, gdzie właściwie
mieszka wiedza o projekcie — w głowie, czy w repozytorium?** Na to historia gita odpowiada
konkretnie.

## 1. Bus factor = 1

Fakt, nie metafora. Jeden człowiek napisał wszystko, wdrożył wszystko i jest jedynym, kto zna
kontekst decyzji. Standardowe konsekwencje (utrata osoby = utrata projektu) obowiązują.

Trzy rzeczy realnie tę ekspozycję zmniejszają i wszystkie są weryfikowalne w repo:

| Mechanizm | Dowód | Co ratuje |
| -- | -- | -- |
| **`context/archive/` — 79 zamkniętych zmian** | każda z `change.md`, `plan.md`, często `research.md`, `plan-review.md`, `impl-review.md` | *dlaczego* coś wygląda tak, a nie inaczej — najdroższa do odtworzenia warstwa wiedzy |
| **Bramka AI code review na każdym PR** | `.github/workflows/ai-code-review.yml`, status `ai-code-review/verdict` | drugie spojrzenie na kod przy jednoosobowym zespole; zastępuje nieistniejącego recenzenta |
| **`CLAUDE.md` / `AGENTS.md` + `.claude/skills/`** (58 dotknięć) | reguły projektu zapisane maszynowo | nowy człowiek *albo agent* startuje z tymi samymi regułami |

`context/changes` z 1073 dotknięciami było najgorętszym obszarem repo w artefakcie 1. Teraz
widać, po co: **to jest zewnętrzna pamięć projektu**, budowana świadomie i konsekwentnie przez
całe 79 dni. Przy bus factor 1 to nie jest biurokracja, to jedyna kopia zapasowa kontekstu.

## 2. Prawdziwa mapa współautorstwa — modele

Trailery `Co-Authored-By` niosą informację, której nie ma w polu autora. **258 z 500 commitów
(52%) deklaruje współautorstwo modelu AI**, czterech różnych:

| Model | Commity | Udział w oznaczonych |
| -- | -- | -- |
| Claude Sonnet 4.6 | 118 | 46% |
| Claude Opus 5 (1M context) | 77 | 30% |
| Claude Opus 4.8 | 42 | 16% |
| Claude Fable 5 | 22 | 9% |

241 commitów nie ma trailera — to głównie squash-merge'e generowane przez GitHuba (130) oraz
wczesne commity sprzed ustalenia konwencji.

### Gdzie pracował który model

| Model | Top 5 obszarów (liczba commitów dotykających obszaru) |
| -- | -- |
| Sonnet 4.6 | `context`:74, `src`:43, `tests`:38, `static`:33, `(root)`:23 |
| Opus 5 (1M) | `context`:61, `tests`:40, `src`:25, `static`:19, `db`:18 |
| Opus 4.8 | `context`:39, `tests`:17, `db`:13, `(root)`:11, `src`:10 |
| Fable 5 | `context`:20, `tests`:15, `static`:11, `src`:9, `db`:6 |

**Żaden model nie ma „swojego" obszaru.** Rozkład jest u wszystkich czterech taki sam co do
kształtu: najwięcej `context`, potem `tests` i `src`. To znaczy, że podział pracy nie szedł po
obszarach kodu, tylko po czasie — którym modelem akurat pracowano w danym tygodniu.

Dwie rzeczy z tego wynikają:

1. **Nie ma wiedzy uwięzionej w konkretnym modelu.** Nie istnieje obszar, który „rozumie tylko
   Sonnet". Zamiana modelu nie tworzy luki kompetencyjnej.
2. **Każdy model najpierw dotykał `context`, potem kodu.** To sygnatura workflow 10x —
   dokument przed implementacją — utrzymana niezależnie od tego, kto (co) pisał.

## 3. Czego ta mapa NIE zastępuje

Współautorstwo modelu **nie jest** wiedzą o projekcie. Model nie pamięta poprzedniej sesji;
to, co „wie", zostało mu podane w kontekście — z `CLAUDE.md`, `context/` i archiwum. Kierunek
zależności jest odwrotny, niż mogłaby sugerować tabela w §2: **to repozytorium uczy modelu, nie
model repozytorium**. Jeśli `context/` przestanie być aktualizowany, następna sesja startuje
ślepa, niezależnie od tego, jak dobry jest model.

Praktyczny wniosek: dryf dokumentacji od kodu (jak niekompletny komentarz w `tach.toml`
opisany w artefakcie 2) nie jest kosmetyką. To ubytek w jedynej pamięci, jaką projekt ma.

## 4. `unknowns`

- **Nie wiadomo, ile pracy było ręcznej.** Trailer mówi, że model współtworzył commit — nie
  mówi, jaka część kodu z niego pochodzi ani ile z tego autor przepisał.
- **241 commitów bez trailera to nie „praca bez AI".** Squash-merge gubi trailery
  z commitów składowych; brak oznaczenia jest artefaktem procesu, nie dowodem autorstwa.
- **Brak sygnału o przeglądzie ludzkim.** Historia nie pokazuje, ile z 79 zmian przeszło realną
  lekturę autora, a ile zostało przyjętych na zielonej bramce.
- **Jednoosobowe repo nie pozwala zmierzyć „obszarów sierocych"** — metryki typu „kod bez
  właściciela" nie mają tu sensu, bo właściciel jest jeden dla całości.
