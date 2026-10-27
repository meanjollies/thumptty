#!/usr/bin/env python3
"""Animated terminal rabbit. Game logic is in pet.py; this file is the screen.

Keys:  f feed   p play   c clean   t cuddle   s sleep/wake   r treat   m medicine
       b shop   w change hat   i about/collection   h history   q quit
(On Windows: pip install windows-curses)
"""

import curses
import json
import locale
import math
import os
import random
import textwrap
import time
from collections import Counter, deque

import games as G
import pet as P

FRAME_S = 0.12      # animation frame length
SCENE_H = 9
TOP = 3             # first row of the scene
GROUND = TOP + SCENE_H - 1
STATS_ROW = TOP + SCENE_H + 1
MSG_ROW = STATS_ROW + 6
HELP_ROW = MSG_ROW + 1
MIN_COLS, MIN_ROWS = 58, HELP_ROW + 2
AWAY_GAP_S = 10 * 60        # a gap this long between ticks (e.g. laptop sleep) counts as an absence
IDLE_AWAY_S = 30 * 60       # so does this long without a key press (app left open)

FACES = {"happy": "^.^", "okay": "o.o", "unhappy": ";.;",
         "sick": "~.~", "sleeping": "-.-", "gone": "x.x"}
ANIM_FACES = {"binky": "^.^", "heart": "^.^", "wash": ">.<", "thump": ">.<", "flop": "^.^"}
ANIM_LENGTH = {"eat": 20, "binky": 16, "wash": 18, "heart": 14, "thump": 8, "flop": 28}
COLORS = {"green": 1, "yellow": 2, "cyan": 3, "red": 4, "magenta": 5, "white": 6}
EARS = {"normal": "(\\_/)", "lop": "(\\_\\)"}
HAT_ART = {"bow": ("><", "magenta"), "party hat": ("/\\", "yellow"),
           "flower crown": ("@*@", "magenta"), "top hat": ("[_]", "white")}

_MIRROR = str.maketrans("()/\\<>", ")(\\/><")


# ---------- drawing helpers ----------

def sprite(stage, face, twitch=False, ears="normal"):
    top = EARS[ears]
    if twitch:
        top = top.replace("\\", "|", 1)
    head = [top, f"({face})"]
    if stage == "kit":
        return head
    if stage == "juvenile":
        return head + ['(")(")']
    return [" " + head[0], " " + head[1], 'c(")(")']


def flopped(face):
    return ["~(" + face + ")====c", '   ""  "" ']


def mirror(lines):
    w = max(len(l) for l in lines)
    return [l.ljust(w)[::-1].translate(_MIRROR) for l in lines]


def put(scr, y, x, s, attr=0):
    rows, cols = scr.getmaxyx()
    if y < 0 or y >= rows or x >= cols:
        return
    if x < 0:
        s, x = s[-x:], 0
    s = s[: cols - x]
    if s:
        try:
            scr.addstr(y, x, s, attr)
        except curses.error:
            pass  # writing the bottom-right cell raises; harmless


def init_colors():
    try:
        curses.start_color()
        curses.use_default_colors()
        for i, c in enumerate([curses.COLOR_GREEN, curses.COLOR_YELLOW, curses.COLOR_CYAN,
                               curses.COLOR_RED, curses.COLOR_MAGENTA, curses.COLOR_WHITE], 1):
            curses.init_pair(i, c, -1)
        return True
    except curses.error:
        return False


class Wanderer:
    """Hopping-around behavior, shared by the pet and her friend."""

    def __init__(self, x, rng):
        self.x, self.dir = float(x), 1
        self.mode, self.idle = "idle", rng.randint(5, 30)
        self.target, self.walk_c, self.hop = float(x), 0, 0

    def update(self, width, sw, rng, lazy=False, eager=False, pref=None):
        maxx = max(2, width - sw - 3)
        self.x = min(max(self.x, 1), maxx)
        if self.mode == "idle":
            self.hop = 0
            self.idle -= 1
            if self.idle <= 0:
                lo, hi = (60, 120) if lazy else (10, 35) if eager else (20, 60)
                self.idle = rng.randint(lo, hi)
                goal = pref if pref is not None and rng.random() < 0.5 else rng.randint(2, maxx)
                self.target = min(max(goal, 2), maxx)
                if abs(self.target - self.x) > 3:
                    self.mode, self.walk_c = "walk", 0
                    self.dir = 1 if self.target > self.x else -1
        else:
            phase = self.walk_c % 4          # hop cycle: ground, up, up, ground
            self.walk_c += 1
            self.hop = 1 if phase in (1, 2) else 0
            if phase in (1, 2):
                self.x += self.dir
            if (self.dir > 0 and self.x >= self.target) or (self.dir < 0 and self.x <= self.target):
                self.mode, self.hop = "idle", 0
                self.idle = rng.randint(15, 50)


# ---------- the scene ----------

class Scene:
    def __init__(self, pet, use_color=False):
        self.pet = pet
        self.use_color = use_color
        self.rng = random.Random()
        self.w = 80
        self.tick = 0
        self.me = Wanderer(2, self.rng)
        self.fw = None                      # the friend's Wanderer, once she exists
        self.anim = None                    # (name, t, duration)
        self.parts = []                     # [x, y, char, ttl, vy, color, vx]
        self.msg, self.msg_ttl, self.msg_is_note = "", 0, False
        self.queue = deque()                # notes waiting to be shown: (text, ttl)
        self.show_hist = False
        self.overlay = None                 # None, shop, about, play, catch, paws
        self.game, self.game_msg = None, ""
        self.shop_msg = ""
        self.cool = 0                       # cooldown for foot-thumping
        self.cloud_x = 4.0
        self.season, self.weather = P.conditions()
        self.greet = pet["trust"] >= 25 and pet["alive"] and not pet["sleeping"]
        self.grass = [self.rng.choice('""\',.') for _ in range(400)]
        self.stars = [(self.rng.random(), self.rng.random()) for _ in range(16)]

    # --- small utilities ---
    def attr(self, color=None, bold=False):
        a = curses.A_BOLD if bold else 0
        if self.use_color and color:
            a |= curses.color_pair(COLORS[color])
        return a

    def say(self, text, ttl=70):
        """Show right away (replies to key presses). An interrupted note is shown again later."""
        if self.msg_ttl > 0 and self.msg_is_note:
            self.queue.appendleft((self.msg, max(self.msg_ttl, 30)))
        self.msg, self.msg_ttl, self.msg_is_note = text, ttl, False

    def note(self, text, ttl=100):
        """Queue something that happened on its own; notes are shown one at a time."""
        self.queue.append((text, ttl))

    def _next_msg(self):
        if self.msg_ttl <= 0 and self.queue:
            (self.msg, self.msg_ttl), self.msg_is_note = self.queue.popleft(), True

    def sprite_size(self):
        lines = sprite(self.pet["stage"], "o.o")
        return max(len(l) for l in lines), len(lines)

    def add_part(self, x, y, ch, color, vy=-0.3, ttl=10, vx=0.0):
        self.parts.append([x, y, ch, ttl, vy, color, vx])

    def spawn(self, ch, color, vy=-0.3, ttl=10, spread=3.0, dy=0.0):
        sw, sh = self.sprite_size()
        x = self.me.x + sw / 2 + self.rng.uniform(-spread, spread)
        self.add_part(x, GROUND - sh - self.me.hop + dy, ch, color, vy, ttl)

    def is_night(self):
        h = time.localtime().tm_hour
        return self.pet["sleeping"] or h >= 21 or h < 6

    def start_anim(self, name):
        self.anim = (name, 0, ANIM_LENGTH[name])
        self.me.mode, self.me.hop = "idle", 0

    # --- input ---
    def handle_key(self, key):
        """Returns True if the user wants to quit."""
        c = chr(key).lower() if 0 <= key < 256 else ""
        if self.overlay:
            self._overlay_key(key, c)
            return False
        if c == "q":
            return True
        p = self.pet
        if c == "h":
            self.show_hist = not self.show_hist
        elif c == "i":
            self.overlay = "about"
        elif not p["alive"]:
            pass
        elif c == "b":
            self.overlay, self.shop_msg = "shop", ""
        elif c == "p":
            why = P.can_play(p)
            if why:
                self.say(why)
            else:
                self.overlay = "play"
        elif c in P.ACTIONS:
            self._do(P.ACTIONS[c](p))
        return False

    def _do(self, result):
        msg, anim = result
        self.say(msg)
        if anim:
            self.start_anim(anim)
        if self.pet["sleeping"]:
            self.anim, self.me.mode, self.me.hop = None, "idle", 0

    def _overlay_key(self, key, c):
        p, ov = self.pet, self.overlay
        if ov == "about":
            self.overlay = None
        elif ov == "shop":
            if c in ("b", "q") or key == 27:
                self.overlay = None
            elif c.isdigit():
                idx = 9 if c == "0" else int(c) - 1
                if idx < len(P.SHOP_ORDER):
                    self.shop_msg = P.buy(p, P.SHOP_ORDER[idx])
        elif ov == "play":
            if c in ("p", "q") or key == 27:
                self.overlay = None
            elif c == "1":
                self.overlay = None
                self._do(P.play(p))
            elif c == "2":
                sw, sh = self.sprite_size()
                self.game = G.CatchGame(self.w - 2, GROUND - sh - TOP, sw, self.rng)
                self.overlay = "catch"
            elif c == "3":
                self.game, self.game_msg = G.PawGame(self.rng), ""
                self.overlay = "paws"
        elif ov == "catch":
            if key == curses.KEY_LEFT or c == "a":
                self.game.move(-1)
            elif key == curses.KEY_RIGHT or c == "d":
                self.game.move(1)
            elif c == "q" or key == 27:
                self._end_catch()
        elif ov == "paws":
            if key == curses.KEY_LEFT or c in ("1", "l"):
                self._guess("l")
            elif key == curses.KEY_RIGHT or c in ("2", "r"):
                self._guess("r")
            elif c == "q" or key == 27:
                self.overlay, self.game = None, None

    def _guess(self, side):
        g = self.game
        g.guess(side)
        _, answer, ok = g.last
        side_name = {"l": "left", "r": "right"}[answer]
        self.game_msg = (f"Right! It was in the {side_name} paw." if ok
                         else f"Nope, it was in the {side_name} paw.")
        if g.done:
            self.overlay, self.game = None, None
            self.say(f"{self.game_msg} {P.finish_paws(self.pet, g.wins)}", 100)
            if g.wins >= 2:
                self.start_anim("binky")

    def _end_catch(self):
        g = self.game
        if g.steps < 20:
            msg = "You stopped the game."
        else:
            msg = P.finish_catch(self.pet, g.score)
            if g.score >= 4:
                self.start_anim("binky")
        self.overlay, self.game = None, None
        self.say(msg, 100)

    # --- per-frame update ---
    def update(self, cols):
        self.tick += 1
        self.w = cols
        if self.tick % 3000 == 0:
            self.season, self.weather = P.conditions()
        self.cloud_x = (self.cloud_x + 0.06) % (cols + 12)
        if self.msg_ttl > 0:
            self.msg_ttl -= 1
        self._next_msg()
        for part in self.parts:
            part[0] += part[6]
            part[1] += part[4]
            part[3] -= 1
        self.parts = [part for part in self.parts if part[3] > 0]

        if self.overlay == "catch":
            self.game.step()
            if self.game.done:
                self._end_catch()
            return
        if self.overlay:
            return
        self._weather(cols)

        p = self.pet
        sw, sh = self.sprite_size()
        if p["friend"] and self.fw is None:
            self.fw = Wanderer(cols - 10, self.rng)
        if not p["alive"]:
            return
        part_of_day = P.activity(time.localtime().tm_hour)
        lazy = part_of_day == "drowsy" or p["energy"] < 25 or bool(p["illness"]) or p["health"] < 50
        eager = part_of_day == "active"

        if self.greet and self.tick == 1:           # hop over to say hello
            self.me.mode, self.me.walk_c, self.me.dir = "walk", 0, 1
            self.me.target = max(3, cols // 2 - sw // 2)
        if p["sleeping"]:
            self.me.hop = 0
            if self.tick % 9 == 0:
                self.spawn(self.rng.choice("zZ"), "cyan", vy=-0.25, ttl=14, spread=1.0, dy=-1)
        elif self.anim:
            self._anim_step()
        else:
            self.me.update(cols, sw, self.rng, lazy, eager, pref=p["spot"] * cols)
            if self.greet and self.me.mode == "idle" and self.tick > 3:
                self.greet = False
                self.start_anim("heart")
                self.say(f"{p['name']} hopped over to greet you!")
            else:
                self._quirks()
        if self.fw and not p["sleeping"]:
            self.fw.update(cols, 8, self.rng, lazy, eager, pref=self.me.x + self.rng.choice((-8, 8)))
        elif self.fw:
            self.fw.hop = 0

    def _weather(self, cols):
        rng = self.rng
        if self.weather == "rainy":
            for _ in range(2):
                self.add_part(rng.randint(1, cols - 2), TOP, "'", "cyan", vy=1.0, ttl=8)
        elif self.weather == "snowy" and rng.random() < 0.5:
            self.add_part(rng.randint(1, cols - 2), TOP, "*", "white", vy=0.3, ttl=26,
                          vx=rng.uniform(-0.15, 0.15))
        if self.season == "autumn" and rng.random() < 0.12:
            self.add_part(rng.randint(1, cols - 2), TOP, rng.choice(",~"), rng.choice(["yellow", "red"]),
                          vy=0.25, ttl=30, vx=rng.uniform(-0.3, 0.3))

    def _quirks(self):
        """Little habits that show up on their own."""
        p, r = self.pet, self.rng.random()
        if self.cool > 0:
            self.cool -= 1
        if self.me.mode != "idle":
            return
        if (p["happiness"] < 35 or p["hunger"] < 25) and self.cool == 0 and r < 0.01:
            w = P.pronouns(p)
            self.start_anim("thump")
            self.cool = 250
            self.say(f"{p['name']} thumps {w['pos']} foot. {w['Sub']} want{w['s']} some attention!")
        elif p["happiness"] > 85 and p["trust"] >= 25 and r < 0.006:
            self.start_anim("flop")
        elif p["trust"] >= 50 and r < 0.004:
            self.start_anim("heart")

    def _anim_step(self):
        name, t, dur = self.anim
        t += 1
        if name == "binky":
            self.me.hop = round(3 * abs(math.sin(t * math.pi / (dur / 2))))
            if t % 3 == 0:
                self.spawn(self.rng.choice("*+"), "yellow", vy=-0.1, ttl=6, spread=5, dy=2)
        elif name == "wash" and t % 2 == 0:
            self.spawn(self.rng.choice("oO"), "cyan", vy=-0.3, ttl=9, spread=4, dy=2)
        elif name == "heart" and t % 3 == 1:
            self.spawn("<3", "red", vy=-0.35, ttl=9, spread=2, dy=-1)
        elif name == "thump":
            self.me.hop = 1 if t % 2 else 0
            if t % 2 == 0:
                self.spawn("!", "red", vy=-0.2, ttl=4, spread=3, dy=-1)
        if t >= dur:
            self.anim, self.me.hop = None, 0
        else:
            self.anim = (name, t, dur)

    # --- drawing: header, scene, panels ---
    def draw(self, scr):
        scr.erase()
        rows, cols = scr.getmaxyx()
        if cols < MIN_COLS or rows < MIN_ROWS:
            put(scr, 0, 0, f"Window too small: need {MIN_COLS}x{MIN_ROWS}.")
            scr.refresh()
            return
        p = self.pet
        trait = P.TRAITS[p["trait"]][0]
        put(scr, 0, 1, f"{p['name']} the {P.STAGE_LABEL[p['stage']]} rabbit", self.attr("white", True))
        coins = f"{p['coins']} coins"
        put(scr, 0, cols - len(coins) - 1, coins, self.attr("yellow", True))
        put(scr, 1, 1, f"age {P.fmt_hours(P.age_hours(p))} | {P.status_text(p)} | "
                       f"{trait}, loves {p['favorite']}")
        streak = p["streak"]["count"]
        extra = f" | {streak}-day streak" if streak > 1 else ""
        put(scr, 2, 1, f"{self.season}, {self.weather} | trust: {P.trust_level(p)}{extra}")

        if self.overlay == "catch":
            self._draw_catch(scr, cols)
        elif self.overlay:
            self._draw_overlay(scr, cols)
        else:
            self._sky(scr, cols)
            self._ground(scr, cols)
            self._decor(scr, cols)
            if p["alive"]:
                if self.fw:
                    self._draw_friend(scr)
                self._draw_pet(scr)
            else:
                self._grave(scr, cols)
            for x, y, ch, _, _, col, _ in self.parts:
                put(scr, int(y), int(x), ch, self.attr(col))
            self._panel(scr, cols)
        if self.msg_ttl > 0:
            put(scr, MSG_ROW, 1, self.msg, self.attr("white", True))
        if not self.overlay:                       # menus and games show their own keys
            put(scr, HELP_ROW, 1, "f:feed p:play c:clean t:cuddle s:sleep r:treat m:medicine")
            put(scr, HELP_ROW + 1, 1, "b:shop w:hat i:about h:history q:quit")
        scr.refresh()

    def _sky(self, scr, cols):
        clear = self.weather == "clear"
        if self.is_night():
            if self.weather not in ("rainy", "snowy"):
                for i, (fx, fy) in enumerate(self.stars):
                    ch = "*" if (self.tick // 7 + i) % 5 == 0 else "."
                    put(scr, TOP + int(fy * 4), 1 + int(fx * (cols - 3)), ch, self.attr("yellow"))
        elif clear:
            for i, l in enumerate([" \\|/ ", "-(O)-", " /|\\ "]):
                put(scr, TOP + i, cols - 9, l, self.attr("yellow", True))
        clouds = [int(self.cloud_x) - 8]
        if not clear:
            clouds.append(int((self.cloud_x * 0.7 + cols // 2) % (cols + 12)) - 8)
        for cx in clouds:
            put(scr, TOP + 1, cx, " .--. ", self.attr("white"))
            put(scr, TOP + 2, cx, "(____)", self.attr("white"))

    def _ground(self, scr, cols):
        color = {"winter": "white", "autumn": "yellow"}.get(self.season, "green")
        put(scr, GROUND, 1, "".join(self.grass[x % len(self.grass)] for x in range(cols - 2)),
            self.attr(color))
        flowers = {"spring": ((0.08, "@", "magenta"), (0.22, "@", "yellow"), (0.45, "*", "red"),
                              (0.78, "@", "magenta"), (0.92, "*", "yellow")),
                   "summer": ((0.13, "@", "red"), (0.40, "*", "magenta"), (0.85, "@", "yellow")),
                   }.get(self.season, ())
        for frac, ch, col in flowers:
            put(scr, GROUND - 1, int(frac * cols), ch, self.attr(col, True))

    def _decor(self, scr, cols):
        p = self.pet
        if P.has(p, "tunnel"):
            put(scr, GROUND - 2, int(cols * 0.30), " .---. ", self.attr("white"))
            put(scr, GROUND - 1, int(cols * 0.30), "/_____\\", self.attr("white"))
        if P.has(p, "hay bale"):
            put(scr, GROUND - 1, int(cols * 0.68), "[###]", self.attr("yellow", True))
        if P.has(p, "ball"):
            put(scr, GROUND - 1, int(cols * 0.52), "o", self.attr("red", True))
        if P.has(p, "chew toy"):
            put(scr, GROUND - 1, int(cols * 0.58), "(o)", self.attr("cyan", True))

    def _bunny(self, scr, x, hop, direction, face, stage, ears="normal", color="white",
               hat=None, twitch=False, flop=False):
        """Draw one rabbit standing on the ground. Returns (top_row, width)."""
        lines = flopped(face) if flop else sprite(stage, face, twitch, ears)
        if direction < 0:
            lines = mirror(lines)
        sw = max(len(l) for l in lines)
        x = int(x)
        y0 = GROUND - len(lines) - hop
        for i, line in enumerate(lines):
            for j, ch in enumerate(line):
                if ch != " ":
                    put(scr, y0 + i, x + j, ch, self.attr(color, True))
        if hat and not flop:
            art, hat_color = HAT_ART[hat]
            head_col = len(lines[0]) - len(lines[0].lstrip())
            put(scr, y0 - 1, x + head_col + (5 - len(art)) // 2, art, self.attr(hat_color, True))
        return y0, sw

    def _draw_pet(self, scr):
        p, anim, me = self.pet, self.anim, self.me
        name = anim[0] if anim else None
        face = ANIM_FACES.get(name) or FACES[P.mood(p)]
        if name == "eat" and (anim[1] // 2) % 2 == 0:
            face = face[0] + "w" + face[2]
        twitch = me.mode == "idle" and not anim and not p["sleeping"] and self.tick % 40 < 3
        y0, sw = self._bunny(scr, me.x, me.hop, me.dir, face, p["stage"], hat=p["hat"],
                             twitch=twitch, flop=name == "flop")
        if name == "eat":
            k = max(1, 3 - anim[1] * 3 // anim[2])      # carrot shrinks as it's eaten
            if me.dir > 0:
                put(scr, y0 + 1, int(me.x) + sw, "==>"[3 - k:], self.attr("yellow", True))
            else:
                put(scr, y0 + 1, int(me.x) - k, "<=="[:k], self.attr("yellow", True))

    def _draw_friend(self, scr):
        p, fw = self.pet, self.fw
        if p["sleeping"]:
            face = "-.-"
        elif self.anim and self.anim[0] in ("heart", "binky"):
            face = "^.^"
        else:
            face = "o.o"
        self._bunny(scr, fw.x, fw.hop, fw.dir, face, "adult", ears="lop", color="magenta")

    def _grave(self, scr, cols):
        inner = max(5, min(len(self.pet["name"]) + 2, 20))
        name = self.pet["name"][:inner].center(inner)
        stone = [" ." + "-" * inner + ".",
                 " /" + " " * inner + "\\",
                 " |" + "RIP".center(inner) + "|",
                 " |" + name + "|",
                 " |" + " " * inner + "|"]
        gx = cols // 2 - (inner + 4) // 2
        for i, l in enumerate(stone):
            put(scr, GROUND - len(stone) + i, gx, l, self.attr("white"))

    def _panel(self, scr, cols):
        p = self.pet
        if self.show_hist:
            put(scr, STATS_ROW, 1, "Recent history (h to close)", self.attr("white", True))
            entries = p["history"][-5:][::-1]
            if not entries:
                put(scr, STATS_ROW + 1, 1, "Nothing yet.")
            for i, e in enumerate(entries):
                ago = P.fmt_ago(max(0.0, (time.time() - e["t"]) / 3600))
                put(scr, STATS_ROW + 1 + i, 1, f"{ago:>9}  {e['text']}"[: cols - 2])
            return
        barw = min(24, cols - 26)
        for i, (label, key) in enumerate([("Fullness", "hunger"), ("Happiness", "happiness"),
                                          ("Energy", "energy"), ("Cleanliness", "cleanliness"),
                                          ("Health", "health")]):
            v = p[key]
            fill = round(v / 100 * barw)
            color = "green" if v >= 50 else "yellow" if v >= 20 else "red"
            put(scr, STATS_ROW + i, 1, f"{label:<12}[")
            put(scr, STATS_ROW + i, 14, "#" * fill + "-" * (barw - fill), self.attr(color))
            put(scr, STATS_ROW + i, 14 + barw, f"] {v:3.0f}")

    # --- drawing: full-screen overlays and games ---
    def _draw_overlay(self, scr, cols):
        p = self.pet
        title, lines = {"shop": self._shop_lines, "about": lambda: self._about_lines(cols),
                        "play": self._play_lines, "paws": self._paws_lines}[self.overlay]()
        put(scr, TOP, 1, title, self.attr("white", True))
        for i, line in enumerate(lines[: MSG_ROW - TOP - 2]):
            put(scr, TOP + 2 + i, 1, line[: cols - 2])
        if self.overlay == "paws":
            face = "^.^" if self.game.last and self.game.last[2] else "o.o"
            self._bunny(scr, cols // 2 - 3, 0, 1, face, p["stage"], hat=p["hat"])
            put(scr, GROUND, 1, "".join(self.grass[x % len(self.grass)] for x in range(cols - 2)),
                self.attr("green"))

    def _shop_lines(self):
        p, lines = self.pet, []
        for i, name in enumerate(P.SHOP_ORDER):
            kind, price, desc = P.ITEMS[name]
            n = p["inventory"].get(name, 0)
            status = ("owned" if n and kind != "consumable" else f"x{n}" if n else "")
            key = "0" if i == 9 else str(i + 1)
            lines.append(f"{key}  {name:<13}{price:>3}c  {status:<6} {desc}")
        lines += ["", "Press a number to buy. b, q or Esc to close.", self.shop_msg]
        return f"Shop - you have {p['coins']} coins", lines

    def _play_lines(self):
        name = self.pet["name"]
        return f"How should {name} play?", [
            "1  Free play      hop around and do a binky",
            "2  Carrot catch   catch carrots, dodge the rotten ones",
            "3  Which paw?     guess where the treat is hiding",
            "", "p, q or Esc to cancel"]

    def _paws_lines(self):
        g = self.game
        return f"Which paw is hiding the treat?   (round {min(g.round + 1, g.ROUNDS)} of {g.ROUNDS})", [
            "1 or left arrow:  left paw",
            "2 or right arrow: right paw",
            "q or Esc:         stop playing",
            "", self.game_msg if g.last else "", f"Correct so far: {g.wins}"]

    def _about_lines(self, cols):
        p = self.pet
        w = P.pronouns(p)
        label, desc, _ = P.TRAITS[p["trait"]]
        spot = ("on the left side of the yard" if p["spot"] < 0.35 else
                "on the right side of the yard" if p["spot"] > 0.65 else "in the middle of the yard")
        wait = {"dawn": "early mornings", "dusk": "evenings"}[p["chrono"]]
        toy = p["favorite_toy"] + (" (owned)" if P.has(p, p["favorite_toy"]) else "")
        friend = (f"{p['friend']['name']} has been around for "
                  f"{P.fmt_hours_long(max(0.0, (time.time() - p['friend']['arrived']) / 3600))}."
                  if p["friend"] else
                  f"No friend yet. Wild rabbits visit once {w['sub']} trust{w['s']} you.")
        owned = [f"{n} x{c}" if P.ITEMS[n][0] == "consumable" else n
                 for n, c in p["inventory"].items() if c > 0]
        found = [f"{n} x{c}" if c > 1 else n for n, c in p["collection"].items()]
        meals = [f"{n} x{c}" if c > 1 else n for n, c in Counter(p["meals"]).most_common()]
        gc = p["game_coins"]
        earned = gc["earned"] if gc["day"] == time.strftime("%Y-%m-%d") else 0
        nothing_found = f"nothing yet ({w['sub']} leave{w['s']} gifts when you are away)"
        paras = [
            f"{p['name']} is {P.STAGE_PHRASE[p['stage']]}, {P.fmt_hours_long(P.age_hours(p))} old.",
            f"Personality: {label} ({desc}). Loves {p['favorite']}. Favorite toy: {toy}.",
            f"Recent meals: {', '.join(meals) or 'nothing yet'}.",
            f"{w['Sub']} like{w['s']} to rest {spot} and {w['is']} liveliest in the {wait}.",
            f"Trust: {P.trust_level(p)} ({p['trust']:.0f}/100). Visit streak: {p['streak']['count']} "
            f"day{'s' if p['streak']['count'] != 1 else ''}.",
            f"Friend: {friend}",
            f"Wearing: {p['hat'] or 'nothing'}.  You own: {', '.join(owned) or 'nothing yet'}.",
            f"Found so far: {', '.join(found) or nothing_found}.",
            f"Mini-game coins today: {earned}/{P.GAME_COIN_CAP}.",
        ]
        lines = []
        for para in paras:
            lines += textwrap.wrap(para, max(20, cols - 4)) or [""]
        return "About your rabbit (press any key to close)", lines

    def _draw_catch(self, scr, cols):
        g, p = self.game, self.pet
        put(scr, TOP, 1, f"Score {g.score}   Time {g.seconds_left}s   (a/d or arrows, q or Esc quits)",
            self.attr("white", True))
        self._ground(scr, cols)
        for x, y, kind in g.items:
            put(scr, TOP + 1 + int(y), 1 + x, "v" if kind == "c" else "x",
                self.attr("yellow" if kind == "c" else "red", True))
        self._bunny(scr, 1 + g.px, 0, 1, "^.^", p["stage"], hat=p["hat"])
        for x, y, ch, _, _, col, _ in self.parts:
            put(scr, int(y), int(x), ch, self.attr(col))


# ---------- main loop ----------

def _state_key(pet):
    """Snapshot of everything except the clock, to tell whether a key changed anything."""
    return json.dumps({k: v for k, v in pet.items() if k != "last_seen"}, sort_keys=True)


def _away(pet, now, last_key):
    """True if this catch-up should count as an absence: no dying, gentler decay."""
    return now - pet["last_seen"] > AWAY_GAP_S or now - last_key > IDLE_AWAY_S


def run(stdscr, pet):
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    stdscr.keypad(True)
    stdscr.timeout(int(FRAME_S * 1000))
    scene = Scene(pet, init_colors())
    last_clock = last_frame = last_save = last_key = time.time()
    try:
        while True:
            key = stdscr.getch()
            now = time.time()
            if key not in (-1, curses.KEY_RESIZE):
                notes = P.catch_up(pet, now, away=_away(pet, now, last_key))[1]
                last_key = now
                for note in notes:
                    scene.note(note)
                before = _state_key(pet)
                if scene.handle_key(key):
                    break                                # the finally block saves
                if notes or _state_key(pet) != before:
                    P.save_state(pet)
                    last_save = now
            if now - last_clock >= 1.0:                  # live time passing
                last_clock = now
                away = _away(pet, now, last_key)
                notes = P.catch_up(pet, now, away=away)[1] + P.check_in(pet, now)
                for note in notes:
                    scene.note(note)
                if notes:
                    P.save_state(pet)
                    last_save = now
            if now - last_frame >= FRAME_S * 0.9:
                last_frame = now
                scene.update(stdscr.getmaxyx()[1])
                scene.draw(stdscr)
            if now - last_save > 30:
                last_save = now
                P.save_state(pet)
    finally:
        P.catch_up(pet)
        P.save_state(pet)


PRONOUN_ALIASES = {"her": "she", "him": "he", "them": "they"}


def create_pet():
    choice = input("Is your new rabbit a she, he or they? [she] ").strip().lower()
    choice = PRONOUN_ALIASES.get(choice, choice)
    pronouns = choice if choice in P.PRONOUNS else "she"
    w = P.pronouns({"pronouns": pronouns})
    name = input(f"You have a new rabbit! What will you name {w['obj']}? ").strip() or "Bun"
    pet = P.new_pet(name, pronouns=pronouns)
    label, desc, _ = P.TRAITS[pet["trait"]]
    print(f"Meet {name}! {w['Sub']} {w['is']} {label} ({desc}) and love{w['s']} {pet['favorite']}.")
    print(f"{w['Sub']} start{w['s']} with 10 coins. Press b in the game to visit the shop.")
    for note in P.check_in(pet):
        print(f"  - {note}")
    input("Press Enter to go to the hutch...")
    return pet


def away_report(pet, hours, notes):
    if hours < 0.02 and not notes:
        return
    if hours >= 0.02:
        away = P.fmt_hours_long(hours)
        away = away if away.startswith("less") else f"about {away}"
        print(f"\nWelcome back! You were away for {away}.")
    if notes:
        print("Since you last visited:")
        for n in notes:
            print(f"  - {n}")
    input("\nPress Enter to continue...")


def _main():
    pet = P.load_state()
    if pet is None:
        pet = create_pet()
    else:
        hours, notes = P.catch_up(pet, away=True)
        away_report(pet, hours, notes + P.check_in(pet))
    P.save_state(pet)

    while True:
        curses.wrapper(run, pet)
        if pet["alive"]:
            print(f"Bye! {pet['name']} will be here when you get back.")
            break
        if input("Adopt a new rabbit? [y/N] ").strip().lower().startswith("y"):
            pet = create_pet()
            P.save_state(pet)
        else:
            break


def main():
    locale.setlocale(locale.LC_ALL, "")
    os.environ.setdefault("ESCDELAY", "25")   # curses waits 1s after Esc by default
    try:
        _main()
    except (KeyboardInterrupt, EOFError):
        # run() saves in its finally block, so a Ctrl-C mid-game loses nothing.
        print("\nBye!")


if __name__ == "__main__":
    main()