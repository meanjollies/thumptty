"""Mini-games as plain logic (no screen code), so they're easy to test."""

import random


class CatchGame:
    """Carrots (and a few rotten ones) fall; move the rabbit to catch them.

    Coordinates: x is a column in the play field, y is a row (0 = top).
    An item is caught or missed when it reaches the bottom row.
    """

    DURATION = 125        # steps (15 seconds at the app's frame rate)
    FALL_SPEED = 0.35     # rows per step
    MOVE = 2              # columns per key press

    def __init__(self, width, height, rabbit_width, rng=random):
        self.width, self.height, self.rw = width, height, rabbit_width
        self.rng = rng
        self.px = (width - rabbit_width) // 2
        self.items = []   # [x, y, kind]  kind: "c" carrot, "x" rotten
        self.score = 0
        self.steps = 0

    @property
    def done(self):
        return self.steps >= self.DURATION

    @property
    def seconds_left(self):
        return max(0, round((self.DURATION - self.steps) * 0.12))

    def move(self, direction):
        limit = max(0, self.width - self.rw)
        self.px = min(limit, max(0, self.px + direction * self.MOVE))

    def step(self):
        if self.done:
            return
        self.steps += 1
        if self.rng.random() < 0.18 and len(self.items) < 8:
            kind = "x" if self.rng.random() < 0.25 else "c"
            self.items.append([self.rng.randint(0, self.width - 1), 0.0, kind])
        remaining = []
        for item in self.items:
            item[1] += self.FALL_SPEED
            if item[1] < self.height - 1:
                remaining.append(item)
            elif self.px <= item[0] < self.px + self.rw:        # reached the rabbit
                self.score = max(0, self.score + (1 if item[2] == "c" else -2))
        self.items = remaining


class PawGame:
    """She hides a treat in one paw; guess which. Three rounds of pure luck; counts correct guesses."""

    ROUNDS = 3

    def __init__(self, rng=random):
        self.rng = rng
        self.round = 0
        self.wins = 0
        self.hidden = rng.choice("lr")
        self.last = None          # (guess, answer, correct) from the previous round

    @property
    def done(self):
        return self.round >= self.ROUNDS

    def guess(self, side):
        if self.done:
            return False
        correct = side == self.hidden
        self.last = (side, self.hidden, correct)
        self.wins += correct
        self.round += 1
        self.hidden = self.rng.choice("lr")
        return correct