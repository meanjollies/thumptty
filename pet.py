"""Rabbit pet logic: state, time catch-up, random events, actions, shop.

No screen code lives here, so everything is easy to test. The one rule of the
design: the pet never "runs". Time is applied in bulk by catch_up(), which
compares now to pet["last_seen"] and simulates what happened in between.
"""

import datetime
import json
import os
import random
import tempfile
import time
from pathlib import Path

STATE_FILE = Path(os.environ.get("THUMPTTY_STATE") or Path.home() / ".thumptty.json")

# ---------- tuning knobs ----------
STATS = ("hunger", "happiness", "energy", "cleanliness")
DECAY_PER_HOUR = {"hunger": 6.0, "happiness": 4.0, "energy": 3.0, "cleanliness": 2.0}
MAX_CATCHUP_HOURS = 48         # absences longer than this are treated as this long
SLEEP_ENERGY_PER_HOUR = 12.0   # energy regained while asleep
WAKE_ENERGY = 95.0             # a sleeping rabbit wakes up at this energy
LOW = 15.0                     # a stat below this counts as "neglected"
HEALTH_DRAIN = 2.0             # health lost per neglected stat per hour
HEALTH_REGEN = 2.0             # health regained per hour when nothing is wrong
ILLNESS_DRAIN = 1.5            # extra health lost per hour while ill
EVENT_CHANCE_PER_HOUR = 0.10   # chance per awake hour that something happens
HISTORY_LIMIT = 60
STAGES = (("kit", 0), ("juvenile", 24), ("adult", 72))  # (name, starts at hour N)
STAGE_LABEL = {"kit": "baby", "juvenile": "young", "adult": "adult"}
STAGE_PHRASE = {"kit": "a baby rabbit", "juvenile": "a young rabbit", "adult": "an adult rabbit"}

DECOR_HAPPY_MULT = 0.9         # each piece of yard decor slows happiness decay
FRIEND_HAPPY_MULT = 0.8        # company slows it too
FRIEND_CHANCE_PER_HOUR = 0.02
FRIEND_MIN_TRUST = 40
GIFT_MIN_HOURS = 3.0           # shorter absences never produce gifts
VET_COST = 25
BIRTHDAY_COINS = 10
AWAY_HEALTH_FLOOR = 10.0       # an absence can't push health below this (no dying while away)
ILLNESS_HOURS = 24.0           # untreated illness clears up on its own after this long
FREE_VET_HEALTH = 40.0         # below this, the vet treats her free if you're broke
AWAY_SLOW_AFTER = 12.0         # during an absence, decay slows down after this many hours...
AWAY_SLOW_MULT = 0.5           # ...to this fraction of the normal speed
MEAL_LIMIT = 10                # how many recent meals she remembers
GAME_COIN_CAP = 20             # most coins the mini-games pay out per day

# label, description, decay multipliers
TRAITS = {
    "greedy": ("greedy", "always hungry", {"hunger": 1.4}),
    "bouncy": ("bouncy", "burns through energy", {"energy": 1.5}),
    "tidy": ("tidy", "rarely needs a clean-up", {"cleanliness": 0.5}),
    "clingy": ("clingy", "gets lonely fast", {"happiness": 1.5}),
}

# Word forms for each pronoun choice. "s" is the verb ending ("she loves", "they love").
PRONOUNS = {
    "she": {"sub": "she", "obj": "her", "pos": "her", "is": "is", "was": "was", "s": "s"},
    "he": {"sub": "he", "obj": "him", "pos": "his", "is": "is", "was": "was", "s": "s"},
    "they": {"sub": "they", "obj": "them", "pos": "their", "is": "are", "was": "were", "s": ""},
}

# (what she likes, how a single serving reads)
FOODS = [
    ("carrots", "a carrot"),
    ("hay", "some hay"),
    ("dandelions", "a dandelion leaf"),
    ("parsley", "a bit of parsley"),
    ("apple slices", "a slice of apple"),
    ("cilantro", "a sprig of cilantro"),
]

TRUST_LEVELS = ((75, "devoted"), (50, "friendly"), (25, "curious"), (0, "wary"))
CHRONO_WINDOWS = {"dawn": (5, 9), "dusk": (17, 21)}   # her favorite time of day
TOYS = ("ball", "chew toy")

# (name, rarity weight, coins, minimum trust to find it)
GIFTS = [
    ("a dandelion", 10, 1, 0),
    ("a shiny button", 8, 2, 0),
    ("a smooth pebble", 8, 1, 0),
    ("an acorn", 6, 2, 0),
    ("a bottle cap", 6, 2, 10),
    ("a feather", 5, 3, 25),
    ("a four-leaf clover", 2, 8, 40),
    ("a golden carrot", 1, 20, 60),
]

# name -> (kind, price, description)
ITEMS = {
    "bow": ("hat", 10, "a little bow to wear between the ears"),
    "party hat": ("hat", 15, "pointy and festive"),
    "flower crown": ("hat", 20, "fresh-picked daisies"),
    "top hat": ("hat", 30, "very distinguished"),
    "ball": ("toy", 15, "playtime is less tiring"),
    "chew toy": ("toy", 12, "playtime is extra fun"),
    "tunnel": ("decor", 25, "a cozy place to hide"),
    "hay bale": ("decor", 20, "smells like summer"),
    "treat": ("consumable", 3, "a banana slice (don't overdo it)"),
    "medicine": ("consumable", 10, "for when your rabbit is under the weather"),
}
SHOP_ORDER = list(ITEMS)
REQUIRED_KEYS = ("name", "born", "last_seen", *STATS, "health")

FRIEND_NAMES = ["Nibbles", "Pepper", "Maple", "Biscuit", "Juniper", "Thistle", "Mochi", "Clementine"]

ILLNESS_TEXT = {
    "tummy ache": "has a tummy ache. Go easy on the treats.",
    "sniffles": "has the sniffles. Some medicine would help.",
}


def clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def pronouns(pet):
    """Word forms for this rabbit. Capitalized keys give capitalized words: w['Sub'] -> 'She'."""
    forms = dict(PRONOUNS[pet.get("pronouns", "she")])
    forms.update({k.capitalize(): v.capitalize() for k, v in list(forms.items())})
    return forms


def fmt_hours_long(hours):
    """Spelled-out duration for sentences: '6 hours', '1 hour', '2.5 days'."""
    def n(v, unit):
        return f"{v} {unit}" + ("" if v == 1 else "s")
    minutes = round(hours * 60)
    if hours * 60 < 1:
        return "less than a minute"
    if minutes < 60:
        return n(minutes, "minute")
    if hours < 47.5:
        return n(round(hours), "hour")
    return n(round(hours / 24, 1), "day").replace(".0 ", " ")


def fmt_ago(hours):
    return "just now" if hours * 60 < 1 else f"{fmt_hours(hours)} ago"


def fmt_hours(hours):
    if hours < 1:
        return f"{int(hours * 60)}m"
    if hours < 48:
        return f"{hours:.0f}h"
    return f"{hours / 24:.1f}d"


# ---------- creation, persistence ----------

def stage_for(age_hours):
    name = STAGES[0][0]
    for stage, start in STAGES:
        if age_hours >= start:
            name = stage
    return name


def age_hours(pet, now=None):
    now = time.time() if now is None else now
    return max(0.0, (now - pet["born"]) / 3600)


def log(pet, text, when=None):
    pet["history"].append({"t": time.time() if when is None else when, "text": text})
    del pet["history"][:-HISTORY_LIMIT]


def new_pet(name, now=None, rng=random, pronouns="she"):
    now = time.time() if now is None else now
    pet = {
        "version": 3, "name": name, "born": now, "last_seen": now,
        "hunger": 80.0, "happiness": 80.0, "energy": 80.0,
        "cleanliness": 90.0, "health": 100.0,
        "sleeping": False, "alive": True, "stage": "kit",
        "trait": rng.choice(list(TRAITS)),
        "favorite": rng.choice(FOODS)[0],
        "pronouns": pronouns,
        "history": [],
    }
    upgrade(pet, rng)
    log(pet, f"{name} came home.", now)
    return pet


def upgrade(pet, rng=random):
    """Fill in fields that older save files don't have."""
    if pet.get("trait") not in TRAITS:
        pet["trait"] = rng.choice(list(TRAITS))
    if pet.get("favorite") not in dict(FOODS):
        pet["favorite"] = rng.choice(FOODS)[0]
    if pet.get("pronouns") not in PRONOUNS:
        pet["pronouns"] = "she"
    pet.setdefault("history", [])
    pet.setdefault("sleeping", False)
    pet.setdefault("alive", True)
    pet.setdefault("stage", stage_for(age_hours(pet)))
    # version 3 additions
    pet.setdefault("trust", 10.0)
    pet.setdefault("coins", 10)
    pet.setdefault("inventory", {})
    pet.setdefault("hat", None)
    pet.setdefault("collection", {})
    pet.setdefault("streak", {"last_day": None, "count": 0})
    pet.setdefault("illness", None)
    pet.setdefault("illness_hours", 0.0)   # how long the current illness has lasted
    pet.setdefault("friend", None)
    pet.setdefault("birthdays", 0)
    pet.setdefault("treat_log", [])
    pet.setdefault("rhythm_day", None)
    pet.setdefault("meals", [])
    pet.setdefault("game_coins", {"day": None, "earned": 0})
    pet.setdefault("spot", round(rng.uniform(0.15, 0.85), 2))
    if pet.get("favorite_toy") not in TOYS:
        pet["favorite_toy"] = rng.choice(TOYS)
    if pet.get("chrono") not in CHRONO_WINDOWS:
        pet["chrono"] = rng.choice(list(CHRONO_WINDOWS))
    pet["version"] = 3
    return pet


def load_state(path=None):
    path = Path(path or STATE_FILE)
    if not path.exists():
        return None
    bad = SystemExit(f"Couldn't read {path}; it may be corrupted.")
    try:
        data = json.loads(path.read_text())
    except (ValueError, OSError):            # JSONDecodeError and bad UTF-8 are ValueErrors
        raise bad
    if not isinstance(data, dict) or any(k not in data for k in REQUIRED_KEYS):
        raise bad
    try:
        return upgrade(data)
    except (TypeError, ValueError, KeyError, AttributeError):
        raise bad


def save_state(pet, path=None):
    """Atomic write: a crash mid-save can't corrupt the existing file."""
    path = Path(path or STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".rabbit_")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(pet, f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ---------- small helpers ----------

def has(pet, item):
    return pet["inventory"].get(item, 0) > 0


def decor_count(pet):
    return sum(1 for name, (kind, _, _) in ITEMS.items() if kind == "decor" and has(pet, name))


def bond(pet, amount):
    pet["trust"] = clamp(pet["trust"] + amount)


def trust_level(pet):
    for floor, label in TRUST_LEVELS:
        if pet["trust"] >= floor:
            return label


def add_to_collection(pet, thing):
    pet["collection"][thing] = pet["collection"].get(thing, 0) + 1


def _note(pet, notes, text, when):
    notes.append(text)
    log(pet, text, when)


def _hours_below(start, rate, hours, threshold=LOW):
    """How many of `hours` a stat spends under the threshold as it decays."""
    if rate <= 0:
        return 0.0
    if start <= threshold:
        return hours
    return max(0.0, hours - (start - threshold) / rate)


# ---------- clock-based world: seasons, weather, time of day ----------

def season_for(month):
    """Northern-hemisphere seasons."""
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "autumn"


def conditions(now=None):
    """(season, weather) for today. Weather is a fixed roll per calendar day."""
    day = datetime.date.fromtimestamp(time.time() if now is None else now)
    season = season_for(day.month)
    roll = random.Random(day.isoformat()).random()
    if roll < 0.50:
        weather = "clear"
    elif roll < 0.75:
        weather = "cloudy"
    else:
        weather = "snowy" if season == "winter" else "rainy"
    return season, weather


def activity(hour):
    """Rabbits are crepuscular: busy at dawn and dusk, drowsy at midday."""
    if 5 <= hour < 9 or 17 <= hour < 21:
        return "active"
    if 10 <= hour < 16:
        return "drowsy"
    return "normal"


# ---------- random events ----------

def _get_sick(p, illness):
    p["illness"] = illness
    p["illness_hours"] = 0.0
    return f"{p['name']} {ILLNESS_TEXT[illness]}"

def _ev_carrot(p):
    p["hunger"] = clamp(p["hunger"] + 10)
    return f"{p['name']} found a dropped carrot and ate it."

def _ev_rug(p):
    p["cleanliness"] = clamp(p["cleanliness"] - 10)
    p["happiness"] = clamp(p["happiness"] + 5)
    return f"{p['name']} dug a hole in the rug. Very satisfying."

def _ev_zoomies(p):
    p["happiness"] = clamp(p["happiness"] + 5)
    p["energy"] = clamp(p["energy"] - 5)
    return f"{p['name']} had a sudden attack of the zoomies."

def _ev_tummy(p):
    return _get_sick(p, "tummy ache")

def _ev_sniffles(p):
    return _get_sick(p, "sniffles")

def _ev_sunbeam(p):
    p["happiness"] = clamp(p["happiness"] + 5)
    p["energy"] = clamp(p["energy"] + 3)
    return f"{p['name']} napped in a sunbeam."

def _ev_cable(p):
    p["happiness"] = clamp(p["happiness"] + 3)
    p["cleanliness"] = clamp(p["cleanliness"] - 5)
    return f"{p['name']} chewed on a cable. Bad bun!"

def _ev_groom(p):
    p["happiness"] = clamp(p["happiness"] + 6)
    return f"{p['name']} and {p['friend']['name']} groomed each other."

def _ev_friend_gift(p):
    add_to_collection(p, "a dandelion")
    p["coins"] += 1
    return f"{p['friend']['name']} brought {p['name']} a dandelion. (+1 coin)"

EVENTS = [_ev_carrot, _ev_rug, _ev_zoomies, _ev_tummy, _ev_sunbeam, _ev_cable, _ev_sniffles]
FRIEND_EVENTS = [_ev_groom, _ev_friend_gift]
SICK_EVENTS = (_ev_tummy, _ev_sniffles)


def _pick_gift(pet, rng):
    pool = [g for g in GIFTS if pet["trust"] >= g[3]]
    roll = rng.random() * sum(g[1] for g in pool)
    for gift in pool:
        roll -= gift[1]
        if roll <= 0:
            return gift
    return pool[-1]


# ---------- time passing ----------

def catch_up(pet, now=None, rng=random, away=False):
    """Apply the time since last_seen. Returns (hours_applied, [notes]).

    Safe to call as often as you like: the app calls it once at startup for
    the long absence (away=True: gentler decay after a while, and it can't
    kill her) and then every second while it's open.
    """
    now = time.time() if now is None else now
    notes = []
    if not pet["alive"]:
        pet["last_seen"] = max(pet["last_seen"], now)
        return 0.0, notes
    if now <= pet["last_seen"]:
        # No time passed, or the clock went backwards. Keep last_seen as it is,
        # so that when the clock comes forward again, time isn't counted twice.
        return 0.0, notes

    elapsed = min((now - pet["last_seen"]) / 3600, MAX_CATCHUP_HOURS)
    pet["last_seen"] = now
    name = pet["name"]
    w = pronouns(pet)

    # Sleeping: gain energy until rested, then the rest of the time is awake.
    slept = 0.0
    if pet["sleeping"]:
        need = max(0.0, (WAKE_ENERGY - pet["energy"]) / SLEEP_ENERGY_PER_HOUR)
        slept = min(elapsed, need)
        pet["energy"] = clamp(pet["energy"] + SLEEP_ENERGY_PER_HOUR * slept)
        if slept >= need:
            pet["sleeping"] = False
            _note(pet, notes, f"{name} woke up, fully rested.", now - (elapsed - slept) * 3600)
    awake = elapsed - slept

    # Decay. Asleep, everything except energy decays at half speed.
    # During a long absence, decay slows down after AWAY_SLOW_AFTER hours.
    mults = TRAITS[pet["trait"]][2]
    happy_mult = DECOR_HAPPY_MULT ** decor_count(pet) * (FRIEND_HAPPY_MULT if pet["friend"] else 1.0)
    slow = 1.0
    if away and elapsed > AWAY_SLOW_AFTER:
        slow = (AWAY_SLOW_AFTER + (elapsed - AWAY_SLOW_AFTER) * AWAY_SLOW_MULT) / elapsed
    neglected_hours = 0.0
    for stat in STATS:
        rate = DECAY_PER_HOUR[stat] * mults.get(stat, 1.0) * slow
        if stat == "happiness":
            rate *= happy_mult
        hours = awake if stat == "energy" else awake + 0.5 * slept
        start = pet[stat]
        pet[stat] = clamp(start - rate * hours)
        neglected_hours += _hours_below(start, rate, hours)

    # Illness only drains health until it clears up on its own.
    ill_hours = 0.0
    if pet["illness"]:
        ill_hours = min(elapsed, max(0.0, ILLNESS_HOURS - pet["illness_hours"]))
        pet["illness_hours"] += ill_hours

    health_before = pet["health"]
    drain = HEALTH_DRAIN * neglected_hours + ILLNESS_DRAIN * ill_hours
    if drain > 0:
        pet["health"] = clamp(pet["health"] - drain)
    else:
        pet["health"] = clamp(pet["health"] + HEALTH_REGEN * elapsed)
    if away:   # she can't die while you're gone; this never raises health
        pet["health"] = max(pet["health"], min(AWAY_HEALTH_FLOOR, health_before))
    # Trust erodes with neglect and with leaving her sick.
    bond(pet, -0.3 * neglected_hours - 0.2 * ill_hours)

    if pet["health"] <= 0:
        pet["alive"] = False
        pet["sleeping"] = False
        why = (f"{w['Sub']} {w['was']} too sick." if pet["illness"]
               else f"Neglect caught up with {w['obj']}.")
        _note(pet, notes, f"{name} has passed away. {why}", now)
        return elapsed, notes

    if pet["illness"] and pet["illness_hours"] >= ILLNESS_HOURS:
        _note(pet, notes, f"{name} got over {w['pos']} {pet['illness']} on {w['pos']} own.", now)
        pet["illness"], pet["illness_hours"] = None, 0.0

    # Growing up and birthdays.
    stage = stage_for(age_hours(pet, now))
    if stage != pet["stage"]:
        pet["stage"] = stage
        _note(pet, notes, f"{name} has grown into {STAGE_PHRASE[stage]}!", now)
    weeks = int(age_hours(pet, now) // 168)
    if weeks > pet["birthdays"]:
        pet["birthdays"] = weeks
        pet["coins"] += BIRTHDAY_COINS
        _note(pet, notes, f"Happy {weeks}-week birthday, {name}! (+{BIRTHDAY_COINS} coins)", now)

    if not pet["sleeping"] and awake > 0:
        t0 = now - awake * 3600

        # A wild friend may wander into the yard once she trusts you.
        if (not pet["friend"] and pet["stage"] == "adult" and pet["trust"] >= FRIEND_MIN_TRUST
                and rng.random() < 1 - (1 - FRIEND_CHANCE_PER_HOUR) ** awake):
            friend = rng.choice([n for n in FRIEND_NAMES if n != name])
            pet["friend"] = {"name": friend, "arrived": now}
            _note(pet, notes, f"A wild rabbit named {friend} hopped into the yard "
                              f"and doesn't seem to want to leave.", now)

        # Random events, one roll per awake hour (plus a fractional roll).
        whole = int(awake)
        chances = [EVENT_CHANCE_PER_HOUR] * whole + [EVENT_CHANCE_PER_HOUR * (awake - whole)]
        pool = EVENTS + (FRIEND_EVENTS if pet["friend"] else [])
        for i, chance in enumerate(chances):
            if rng.random() < chance:
                event = rng.choice(pool)
                if event in SICK_EVENTS and pet["illness"]:
                    continue
                _note(pet, notes, event(pet), min(now, t0 + (i + 1) * 3600))

    # Gifts left behind during a long absence; better ones as trust grows.
    if elapsed >= GIFT_MIN_HOURS:
        chance = clamp(0.25 + elapsed * 0.02 + pet["trust"] / 400, 0.0, 0.85)
        for _ in range(1 + int(elapsed >= 24)):
            if rng.random() < chance:
                gift, _, coins, _ = _pick_gift(pet, rng)
                add_to_collection(pet, gift)
                pet["coins"] += coins
                _note(pet, notes, f"While you were away, {name} left you {gift}. "
                                  f"(+{coins} coin{'s' if coins != 1 else ''})", now)
    return elapsed, notes


def check_in(pet, now=None):
    """Visit streaks and her favorite time of day. Each reward is given at most once a day."""
    now = time.time() if now is None else now
    notes = []
    if not pet["alive"]:
        return notes
    name = pet["name"]
    today = datetime.date.fromtimestamp(now)
    streak = pet["streak"]
    if streak["last_day"] != today.isoformat():
        yesterday = (today - datetime.timedelta(days=1)).isoformat()
        streak["count"] = streak["count"] + 1 if streak["last_day"] == yesterday else 1
        streak["last_day"] = today.isoformat()
        reward = 2 + min(streak["count"] - 1, 5)
        pet["coins"] += reward
        bond(pet, 1.0)
        text = (f"Day {streak['count']} in a row! (+{reward} coins)" if streak["count"] > 1
                else f"Thanks for visiting today. (+{reward} coins)")
        _note(pet, notes, text, now)

    start, end = CHRONO_WINDOWS[pet["chrono"]]
    hour = datetime.datetime.fromtimestamp(now).hour
    if start <= hour < end and pet["rhythm_day"] != today.isoformat() and not pet["sleeping"]:
        pet["rhythm_day"] = today.isoformat()
        pet["happiness"] = clamp(pet["happiness"] + 10)
        bond(pet, 1.0)
        when = "mornings are" if pet["chrono"] == "dawn" else "evenings are"
        _note(pet, notes, f"{name} was waiting for you. {when.capitalize()} "
                          f"{pronouns(pet)['pos']} favorite time of day.", now)
    return notes


# ---------- mood ----------

def mood(pet):
    if not pet["alive"]:
        return "gone"
    if pet["sleeping"]:
        return "sleeping"
    if pet["illness"] or pet["health"] < 50:
        return "sick"
    if min(pet[s] for s in STATS) < 20:
        return "unhappy"
    if pet["happiness"] > 70 and pet["hunger"] > 50:
        return "happy"
    return "okay"


def status_text(pet):
    """How the mood reads in a sentence: 'feeling happy', 'sleeping', 'sick (sniffles)'."""
    m = mood(pet)
    if m == "sick" and pet["illness"]:
        return f"sick ({pet['illness']})"
    return {"sleeping": "sleeping", "gone": "passed away"}.get(m, f"feeling {m}")


# ---------- actions: each returns (message, animation-name-or-None) ----------

def _asleep(pet):
    return f"Shh, {pet['name']} is asleep.", None


def feed(pet, rng=random):
    if pet["sleeping"]:
        return _asleep(pet)
    if pet["hunger"] > 90:
        return f"{pet['name']} isn't hungry.", None
    if rng.random() < 0.4:
        kind = pet["favorite"]
        phrase = dict(FOODS)[kind]
    else:
        kind, phrase = rng.choice(FOODS)
    pet["hunger"] = clamp(pet["hunger"] + 25)
    pet["meals"] = (pet["meals"] + [kind])[-MEAL_LIMIT:]
    bond(pet, 0.4)
    if kind == pet["favorite"]:
        pet["hunger"] = clamp(pet["hunger"] + 10)
        pet["happiness"] = clamp(pet["happiness"] + 10)
        return f"{pet['name']} munches {phrase}. {pronouns(pet)['Pos']} favorite!", "eat"
    return f"{pet['name']} munches {phrase}.", "eat"


def can_play(pet):
    """None if she's up for playing, otherwise the reason why not."""
    if pet["sleeping"]:
        return f"Shh, {pet['name']} is asleep."
    if pet["energy"] < LOW:
        return f"{pet['name']} is too tired to play."
    return None


def play(pet, rng=random):
    why = can_play(pet)
    if why:
        return why, None
    w = pronouns(pet)
    gain = 20 + (5 if has(pet, "chew toy") else 0)
    extra = ""
    if has(pet, pet["favorite_toy"]):
        gain += 8
        extra = f" {w['Sub']} love{w['s']} {w['pos']} {pet['favorite_toy']}!"
    pet["happiness"] = clamp(pet["happiness"] + gain)
    pet["energy"] = clamp(pet["energy"] - (6 if has(pet, "ball") else 10))
    pet["hunger"] = clamp(pet["hunger"] - 5)
    pet["cleanliness"] = clamp(pet["cleanliness"] - 3)
    bond(pet, 0.6)
    return f"{pet['name']} does a binky!{extra}", "binky"


def clean(pet, rng=random):
    if pet["sleeping"]:
        return _asleep(pet)
    pet["cleanliness"] = clamp(pet["cleanliness"] + 40)
    bond(pet, 0.3)
    return f"You tidy the hutch. {pet['name']} looks pleased.", "wash"


def cuddle(pet, rng=random):
    if pet["sleeping"]:
        return _asleep(pet)
    pet["happiness"] = clamp(pet["happiness"] + (14 if pet["trait"] == "clingy" else 8))
    bond(pet, 1.0)
    return f"{pet['name']} leans into the scratches.", "heart"


def sleep(pet, rng=random):
    if pet["sleeping"]:
        pet["sleeping"] = False
        return f"{pet['name']} wakes up and stretches.", None
    if pet["energy"] >= WAKE_ENERGY:
        return f"{pet['name']} isn't sleepy.", None
    pet["sleeping"] = True
    return f"{pet['name']} curls up for a nap.", None


def treat(pet, rng=random, now=None):
    if pet["sleeping"]:
        return _asleep(pet)
    if not has(pet, "treat"):
        return "You're out of treats. The shop (b) sells more.", None
    now = time.time() if now is None else now
    pet["inventory"]["treat"] -= 1
    pet["hunger"] = clamp(pet["hunger"] + 10)
    pet["happiness"] = clamp(pet["happiness"] + 12)
    bond(pet, 0.5)
    pet["treat_log"] = [t for t in pet["treat_log"] if now - t < 6 * 3600] + [now]
    msg = f"{pet['name']} nibbles a banana slice. Yum!"
    chance = 0.25 * (len(pet["treat_log"]) - 1)    # first treat is safe; more within 6h risk a tummy ache
    if not pet["illness"] and rng.random() < chance:
        msg += (f" ...Uh oh, {pronouns(pet)['pos']} tummy gurgles. "
                f"{_get_sick(pet, 'tummy ache')}")
    return msg, "eat"


def medicine(pet, rng=random):
    name = pet["name"]
    if pet["sleeping"]:
        return _asleep(pet)
    if not pet["illness"]:
        return f"{name} isn't sick.", None
    w = pronouns(pet)
    if has(pet, "medicine"):
        pet["inventory"]["medicine"] -= 1
        msg = f"You give {name} {w['pos']} medicine. {w['Sub']} {w['is']} on the mend."
    elif pet["coins"] >= VET_COST:
        pet["coins"] -= VET_COST
        msg = f"No medicine left, so you rush {name} to the vet. (-{VET_COST} coins) All better!"
    elif pet["health"] < FREE_VET_HEALTH:
        msg = f"The vet sees how poorly {name} is and treats {w['obj']} for free. All better!"
    else:
        return (f"{name} needs medicine ({ITEMS['medicine'][1]} coins) or a vet visit "
                f"({VET_COST} coins). You have {pet['coins']}. "
                f"{w['Sub']} will get over it on {w['pos']} own in time."), None
    log(pet, f"{name} recovered from {w['pos']} {pet['illness']}.")
    pet["illness"], pet["illness_hours"] = None, 0.0
    pet["health"] = clamp(pet["health"] + 15)
    bond(pet, 2.0)
    return msg, "heart"


def wardrobe(pet, rng=random):
    hats = [n for n, (kind, _, _) in ITEMS.items() if kind == "hat" and has(pet, n)]
    if not hats:
        return "You don't own any hats yet. The shop (b) has a few.", None
    cycle = [None] + hats
    nxt = cycle[(cycle.index(pet["hat"]) + 1) % len(cycle)] if pet["hat"] in cycle else hats[0]
    pet["hat"] = nxt
    if nxt is None:
        return f"{pet['name']} takes off {pronouns(pet)['pos']} hat.", None
    return f"{pet['name']} is wearing the {nxt}!", "heart"


def buy(pet, item):
    """Returns a message. Hats are put on right away."""
    kind, price, _ = ITEMS[item]
    if kind != "consumable" and has(pet, item):
        return f"You already have the {item}."
    if pet["coins"] < price:
        return f"The {item} costs {price} coins. You have {pet['coins']}."
    pet["coins"] -= price
    pet["inventory"][item] = pet["inventory"].get(item, 0) + 1
    if kind == "hat":
        pet["hat"] = item
        return f"Bought the {item}. {pet['name']} puts it on right away!"
    if item == "medicine":
        return "Bought some medicine."
    return f"Bought {'an' if item[0] in 'aeiou' else 'a'} {item}."


# ---------- mini-game rewards ----------

def _game_coins(pet, coins, now=None):
    """Pay out mini-game coins, up to GAME_COIN_CAP per day. Returns how many were paid."""
    now = time.time() if now is None else now
    today = datetime.date.fromtimestamp(now).isoformat()
    gc = pet["game_coins"]
    if gc["day"] != today:
        gc["day"], gc["earned"] = today, 0
    paid = max(0, min(coins, GAME_COIN_CAP - gc["earned"]))
    gc["earned"] += paid
    pet["coins"] += paid
    return paid

LIMIT_NOTE = " (Daily game coin limit reached.)"


def finish_catch(pet, score, now=None):
    """Apply the results of the carrot-catching game. Score = carrots - 2 per rotten one."""
    pet["happiness"] = clamp(pet["happiness"] + min(30, 6 + score * 2))
    pet["energy"] = clamp(pet["energy"] - min(15, 5 + score // 2))
    pet["hunger"] = clamp(pet["hunger"] - 5)
    wanted = score // 3
    coins = _game_coins(pet, wanted, now)
    bond(pet, 0.8)
    if score == 0:
        return f"{pet['name']} didn't score any points. Better luck next time!"
    msg = f"{pet['name']} scored {score} point{'s' if score != 1 else ''}"
    if coins:
        msg += f" and earned {coins} coin{'s' if coins != 1 else ''}"
    return msg + "!" + (LIMIT_NOTE if coins < wanted else "")


def finish_paws(pet, wins, now=None):
    """Apply the results of the 'which paw' guessing game."""
    pet["happiness"] = clamp(pet["happiness"] + 4 + 4 * wins)
    pet["energy"] = clamp(pet["energy"] - 3)
    coins = _game_coins(pet, wins * 2, now)
    bond(pet, 0.5)
    return (f"You guessed right {wins} out of 3 times." + (f" (+{coins} coins)" if coins else "")
            + (LIMIT_NOTE if coins < wins * 2 else ""))


ACTIONS = {"f": feed, "c": clean, "t": cuddle, "s": sleep, "r": treat, "m": medicine, "w": wardrobe}