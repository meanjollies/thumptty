# Thumptty

A virtual pet rabbit that lives in your terminal. Feed, play, cuddle,
and watch your rabbit grow up! Time keeps passing while you're away,
so there may be stories (and gifts) waiting when you come back.

[![Run Python Tests](https://github.com/meanjollies/thumptty/actions/workflows/tests.yml/badge.svg)](https://github.com/meanjollies/thumptty/actions/workflows/tests.yml)

```
 (\_/)
 (^.^)
c(")(")
""',.'"",.''"'.,""'"
```

No dependencies outside the standard library.

## Requirements

- Python 3.8 or newer
- A terminal at least **58 columns × 22 rows**
- **Windows only:** curses isn't built in, so install it first:
  ```
  pip install windows-curses
  ```

## Getting started

```
git clone https://github.com/meanjollies/thumptty.git
cd thumptty
python3 thumptty.py
```

The first time you run it, you choose your rabbit's pronouns (she, he or they)
and a name. Each rabbit gets a random personality, favorite food, favorite toy
and favorite time of day.

## Controls

### In the hutch

| Key | Action |
|-----|--------|
| `f` | Feed |
| `p` | Play (opens the play menu) |
| `c` | Clean the hutch |
| `t` | Cuddle |
| `s` | Put to sleep / wake up |
| `r` | Give a treat |
| `m` | Give medicine (or visit the vet) |
| `b` | Open the shop |
| `w` | Change hats |
| `i` | About your rabbit: personality, collection, friends, meals |
| `h` | Show or hide recent history |
| `q` | Quit |

### Menus and mini-games

| Where | Keys |
|-------|------|
| Shop | `1`–`9`, `0` to buy; `b`, `q` or `Esc` to close |
| Play menu | `1` free play, `2` carrot catch, `3` which paw; `p`, `q` or `Esc` to cancel |
| Carrot catch | `a`/`d` or arrow keys to move; `q` or `Esc` to stop |
| Which paw? | `1`, `l` or left arrow; `2`, `r` or right arrow; `q` or `Esc` to stop |
| About screen | any key to close |

## How it works

### Stats
Your rabbit has five stats, shown as bars: **Fullness, Happiness, Energy,
Cleanliness** and **Health**. The first four go down over time. If they stay very
low, health starts to drop. Higher is always better.

### Time passes while you're away
The game doesn't run in the background. Instead, when you open it, it works out
everything that happened since your last visit and shows a "while you were away"
report.

- **Your rabbit can't die while you're away.** An absence can lower health to 10
  at most. Death from neglect can only happen while the app is open and being played.
- **Long absences are gentler.** After 12 hours away, stats drop at half speed.
- **Leaving the app open counts as away.** If your computer sleeps, or nobody
  presses a key for 30 minutes, the same protections apply.
- Absences of more than 48 hours count as 48 hours.

### Growing up
Every rabbit starts as a baby, becomes a young rabbit after 1 day and an adult
after 3 days. Every week brings a birthday and 10 bonus coins.

### Personality
Each rabbit has one trait that changes how fast its stats drop:

| Trait | Effect |
|-------|--------|
| Greedy | always hungry |
| Bouncy | burns through energy |
| Tidy | rarely needs a clean-up |
| Clingy | gets lonely fast (but loves cuddles more) |

Each rabbit also has a **favorite food** (feeding it gives a bonus), a
**favorite toy**, a **favorite spot** in the yard and a **favorite time of day**
(dawn or dusk). Visiting during that time gives a happiness bonus once a day.

### Trust
Good care builds trust; neglect and untreated illness erode it. Trust goes from
*wary* to *curious*, *friendly* and *devoted*. Higher trust unlocks:
- a hop over to greet you when you arrive
- rarer gifts while you're away
- a wild **friend rabbit** who may move into the yard once your rabbit is an adult

### Coins
You start with 10 coins. Earn more from:
- daily visits (streaks pay more, up to 7 coins a day)
- gifts left for you while you're away
- birthdays
- mini-games (at most 20 coins a day)

### The shop

| Item | Price | What it does |
|------|-------|--------------|
| Bow, party hat, flower crown, top hat | 10–30 | Hats to wear (`w` to change) |
| Ball | 15 | Playtime is less tiring |
| Chew toy | 12 | Playtime is extra fun |
| Tunnel, hay bale | 20–25 | Yard decor that slows down loneliness |
| Treat | 3 | A happiness boost; too many in 6 hours can cause a tummy ache |
| Medicine | 10 | Cures illness |

### Illness
Random events can cause a tummy ache or the sniffles, and so can too many treats.
A sick rabbit slowly loses health. You can:
- give medicine (10 coins in the shop)
- visit the vet (25 coins; free if health is below 40 and you can't pay)
- wait: an illness clears up on its own after 24 hours

### Mini-games
- **Carrot catch:** 15 seconds of catching falling carrots (+1 point) and
  dodging rotten ones (−2 points).
- **Which paw?** Three rounds of guessing which paw is hiding a treat.

### The world
Seasons and weather follow your computer's clock: flowers in spring and summer,
falling leaves in autumn, snow in winter, and stars at night. Seasons assume the
northern hemisphere.

## Save file

Your rabbit is saved to `~/.thumptty.json`. Saving happens automatically, and a
crash mid-save can't corrupt the file.

To use a different file (for example, to test without touching your real rabbit),
set `THUMPTTY_STATE`:
```
THUMPTTY_STATE=/tmp/test_thumptty.json python3 thumptty.py
```

To start over, delete the save file.

## Project layout

| File | What's in it |
|------|--------------|
| `thumptty.py` | The screen: animation, menus, main loop (curses) |
| `pet.py` | All game logic: stats, time, events, shop, illness, trust (no screen code) |
| `games.py` | The mini-games as plain logic |
| `test_pet.py` | Unit tests |

## Testing

```
python3 -m unittest -q test_pet
```

All 75 tests should pass. They never wait for real time: a "two-day absence"
is just a timestamp. If curses isn't installed, the two screen tests are
skipped and the result shows `OK (skipped=2)`.

## License

MIT
