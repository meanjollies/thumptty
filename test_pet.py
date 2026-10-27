import json
import os
import random
import tempfile
import unittest
from pathlib import Path

import pet as P

H = 3600


class Always:   # every dice roll succeeds, always picks the first choice
    def random(self): return 0.0
    def choice(self, seq): return seq[0]
    def randint(self, lo, hi): return lo


class Never:    # no dice roll ever succeeds
    def random(self): return 1.0
    def choice(self, seq): return seq[0]
    def randint(self, lo, hi): return lo


def make(**over):
    pet = P.new_pet("Clover", now=0, rng=random.Random(0))
    pet["trait"] = "tidy"   # only affects cleanliness decay
    pet.update(over)
    return pet


class CatchUp(unittest.TestCase):
    def test_no_time_no_change(self):
        pet = make()
        before = dict(pet)
        P.catch_up(pet, now=0, rng=Never())
        self.assertEqual(pet["hunger"], before["hunger"])

    def test_decay(self):
        pet = make()
        P.catch_up(pet, now=10 * H, rng=Never())
        self.assertAlmostEqual(pet["hunger"], 20)
        self.assertAlmostEqual(pet["happiness"], 40)
        self.assertAlmostEqual(pet["energy"], 50)
        self.assertAlmostEqual(pet["cleanliness"], 80)   # tidy: half speed

    def test_trait_multiplier(self):
        pet = make(trait="greedy")
        P.catch_up(pet, now=5 * H, rng=Never())
        self.assertAlmostEqual(pet["hunger"], 80 - 6 * 1.4 * 5)

    def test_cap(self):
        pet = make()
        hours, _ = P.catch_up(pet, now=1000 * H, rng=Never())
        self.assertEqual(hours, P.MAX_CATCHUP_HOURS)

    def test_clock_going_backwards_is_harmless(self):
        pet = make(last_seen=100 * H)
        hours, _ = P.catch_up(pet, now=0, rng=Never())
        self.assertEqual(hours, 0)
        self.assertEqual(pet["hunger"], 80)

    def test_clock_going_backwards_does_not_double_count(self):
        pet = make(last_seen=100 * H, born=0)
        P.catch_up(pet, now=0, rng=Never())          # clock jumps back
        self.assertEqual(pet["last_seen"], 100 * H)
        hours, _ = P.catch_up(pet, now=101 * H, rng=Never())
        self.assertAlmostEqual(hours, 1)             # only the real hour counts
        self.assertAlmostEqual(pet["hunger"], 80 - 6)

    def test_sleep_partial(self):
        pet = make(sleeping=True, energy=50.0)
        P.catch_up(pet, now=2 * H, rng=Never())
        self.assertAlmostEqual(pet["energy"], 74)
        self.assertTrue(pet["sleeping"])

    def test_sleep_wakes_up(self):
        pet = make(sleeping=True, energy=50.0)
        _, notes = P.catch_up(pet, now=10 * H, rng=Never())
        self.assertFalse(pet["sleeping"])
        self.assertAlmostEqual(pet["energy"], 95 - 3 * (10 - 45 / 12))
        self.assertTrue(any("woke up" in n for n in notes))

    def test_long_neglect_is_fatal(self):
        pet = make()
        P.catch_up(pet, now=48 * H, rng=Never())
        self.assertFalse(pet["alive"])
        self.assertIn("passed away", pet["history"][-1]["text"])

    def test_short_absence_is_not(self):
        pet = make()
        P.catch_up(pet, now=12 * H, rng=Never())
        self.assertTrue(pet["alive"])

    def test_away_absence_is_not_fatal(self):
        pet = make()
        P.catch_up(pet, now=48 * H, rng=Never(), away=True)
        self.assertTrue(pet["alive"])
        self.assertEqual(pet["health"], P.AWAY_HEALTH_FLOOR)

    def test_away_floor_never_raises_health(self):
        pet = make(health=5.0, illness="sniffles")
        P.catch_up(pet, now=10 * H, rng=Never(), away=True)
        self.assertTrue(pet["alive"])
        self.assertEqual(pet["health"], 5.0)

    def test_gentler_decay_on_long_absence(self):
        live, away = make(), make()
        P.catch_up(live, now=24 * H, rng=Never())
        P.catch_up(away, now=24 * H, rng=Never(), away=True)
        self.assertAlmostEqual(live["energy"], 80 - 3 * 24)
        self.assertAlmostEqual(away["energy"], 80 - 3 * (12 + 12 * P.AWAY_SLOW_MULT))

    def test_dead_pets_stay_put(self):
        pet = make(alive=False)
        P.catch_up(pet, now=10 * H, rng=Always())
        self.assertEqual(pet["hunger"], 80)

    def test_events_and_history(self):
        pet = make()
        _, notes = P.catch_up(pet, now=2 * H, rng=Always())   # under 3h: no gifts
        self.assertEqual(len(notes), 2)
        self.assertEqual(len([h for h in pet["history"] if "carrot" in h["text"]]), 2)

    def test_no_events_while_asleep(self):
        pet = make(sleeping=True, energy=0.0)
        _, notes = P.catch_up(pet, now=1 * H, rng=Always())
        self.assertEqual(notes, [])

    def test_growing_up(self):
        pet = make(last_seen=80 * H - 60)
        _, notes = P.catch_up(pet, now=80 * H, rng=Never())
        self.assertEqual(pet["stage"], "adult")
        self.assertTrue(any("grown into an adult rabbit" in n for n in notes))

    def test_history_is_capped(self):
        pet = make()
        for i in range(200):
            P.log(pet, str(i))
        self.assertEqual(len(pet["history"]), P.HISTORY_LIMIT)


class Wording(unittest.TestCase):
    def test_status_text(self):
        self.assertEqual(P.status_text(make(sleeping=True)), "sleeping")
        self.assertEqual(P.status_text(make(alive=False)), "passed away")
        self.assertEqual(P.status_text(make()), "feeling happy")

    def test_durations_read_naturally(self):
        L = P.fmt_hours_long
        self.assertEqual(L(0.001), "less than a minute")
        self.assertEqual(L(0.5), "30 minutes")
        self.assertEqual(L(1 / 60), "1 minute")
        self.assertEqual(L(1), "1 hour")
        self.assertEqual(L(6), "6 hours")
        self.assertEqual(L(0.999), "1 hour")
        self.assertEqual(L(48), "2 days")
        self.assertEqual(L(60), "2.5 days")
        self.assertEqual(P.fmt_ago(0.001), "just now")
        self.assertEqual(P.fmt_ago(3), "3h ago")

    def test_pronouns(self):
        they = make(pronouns="they", inventory={"ball": 1}, favorite_toy="ball")
        self.assertIn("They love their ball!", P.play(they)[0])
        he = make(pronouns="he", illness="sniffles", inventory={"medicine": 1})
        self.assertIn("his medicine. He is on the mend", P.medicine(he)[0])
        self.assertEqual(P.upgrade({"born": 0, "pronouns": "xyz"})["pronouns"], "she")


class Actions(unittest.TestCase):
    def test_sleeping_blocks_actions(self):
        pet = make(sleeping=True)
        for fn in (P.feed, P.play, P.clean, P.cuddle):
            self.assertIn("asleep", fn(pet)[0])

    def test_favorite_food_bonus(self):
        pet = make(hunger=40.0, happiness=50.0)
        msg, anim = P.feed(pet, rng=Always())     # Always -> picks the favorite
        self.assertIn("favorite", msg)
        self.assertEqual(anim, "eat")
        self.assertEqual(pet["hunger"], 75)
        self.assertEqual(pet["happiness"], 60)

    def test_full_rabbit_refuses(self):
        self.assertIn("isn't hungry", P.feed(make(hunger=95.0))[0])

    def test_meals_are_remembered(self):
        pet = make()
        for _ in range(P.MEAL_LIMIT + 2):
            pet["hunger"] = 0.0
            P.feed(pet, rng=Always())
        self.assertEqual(pet["meals"], [pet["favorite"]] * P.MEAL_LIMIT)
        full = make(hunger=95.0)
        P.feed(full)
        self.assertEqual(full["meals"], [])

    def test_exhausted_rabbit_wont_play(self):
        self.assertIn("too tired", P.play(make(energy=5.0))[0])

    def test_sleep_toggle(self):
        pet = make()
        P.sleep(pet)
        self.assertTrue(pet["sleeping"])
        P.sleep(pet)
        self.assertFalse(pet["sleeping"])

    def test_wont_nap_when_rested(self):
        pet = make(energy=96.0)
        self.assertIn("isn't sleepy", P.sleep(pet)[0])
        self.assertFalse(pet["sleeping"])


class Persistence(unittest.TestCase):
    def test_roundtrip_and_no_temp_leftovers(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "pet.json"
            pet = make()
            P.save_state(pet, path)
            self.assertEqual(P.load_state(path)["name"], "Clover")
            self.assertEqual(os.listdir(d), ["pet.json"])

    def test_save_creates_missing_folder(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "a" / "b" / "pet.json"
            P.save_state(make(), path)
            self.assertTrue(path.exists())

    def test_old_save_files_are_upgraded(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "pet.json"
            path.write_text(json.dumps({
                "name": "Old", "born": 0, "last_seen": 0, "hunger": 50.0, "happiness": 50.0,
                "energy": 50.0, "cleanliness": 50.0, "health": 100.0,
                "sleeping": False, "alive": True}))
            pet = P.load_state(path)
            self.assertIn(pet["trait"], P.TRAITS)
            self.assertEqual(pet["history"], [])
            P.catch_up(pet, now=2 * H, rng=Never())   # shouldn't crash

    def test_wrong_shaped_save_is_reported_not_crashed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "pet.json"
            for content in ("[]", '{"name": "NoStats"}', '"just a string"'):
                path.write_text(content)
                with self.assertRaises(SystemExit):
                    P.load_state(path)

    def test_missing_file(self):
        self.assertIsNone(P.load_state("/nonexistent/path.json"))


class World(unittest.TestCase):
    """Trust, gifts, streaks, rhythm, friends, birthdays, illness, shop."""

    def test_new_pet_has_everything_and_old_saves_upgrade(self):
        pet = make()
        for key in ("trust", "coins", "inventory", "collection", "streak", "friend",
                    "favorite_toy", "chrono", "spot", "illness", "illness_hours",
                    "meals", "pronouns", "game_coins"):
            self.assertIn(key, pet)
        old = {"name": "Old", "born": 0, "last_seen": 0, "hunger": 50.0, "happiness": 50.0,
               "energy": 50.0, "cleanliness": 50.0, "health": 100.0, "version": 2,
               "trait": "greedy", "favorite": "hay", "history": [], "stage": "kit"}
        P.upgrade(old)
        self.assertEqual(old["version"], 3)
        self.assertEqual(old["coins"], 10)
        self.assertEqual(old["pronouns"], "she")

    def test_long_absence_leaves_gifts(self):
        pet = make(trust=80.0)
        _, notes = P.catch_up(pet, now=10 * H, rng=Always())
        gifts = [n for n in notes if "left you" in n]
        self.assertEqual(len(gifts), 1)
        self.assertEqual(sum(pet["collection"].values()), 1)
        self.assertGreater(pet["coins"], 10)

    def test_short_absence_no_gifts_and_rare_gifts_need_trust(self):
        pet = make()
        P.catch_up(pet, now=2.9 * H, rng=Always())
        self.assertEqual(pet["collection"], {})
        low = [g[0] for g in P.GIFTS if g[3] <= 0]
        self.assertNotIn("a golden carrot", low)

    def test_neglect_costs_trust(self):
        pet = make(trust=50.0)
        P.catch_up(pet, now=30 * H, rng=Never())
        self.assertLess(pet["trust"], 50)

    def test_care_builds_trust(self):
        pet = make(hunger=40.0)
        before = pet["trust"]
        P.feed(pet, rng=Never())
        P.cuddle(pet)
        self.assertGreater(pet["trust"], before + 1)
        self.assertEqual(P.trust_level(make(trust=60.0)), "friendly")

    def test_visit_streak(self):
        pet = make()
        day = 1_700_000_000
        n1 = P.check_in(pet, now=day)
        self.assertEqual(pet["streak"]["count"], 1)
        self.assertTrue(any("Thanks for visiting" in n for n in n1))
        self.assertEqual([n for n in P.check_in(pet, now=day + 60) if "Day" in n or "Thanks" in n], [])
        P.check_in(pet, now=day + 86400)
        self.assertEqual(pet["streak"]["count"], 2)
        P.check_in(pet, now=day + 5 * 86400)          # missed days: streak resets
        self.assertEqual(pet["streak"]["count"], 1)

    def test_favorite_time_of_day_bonus_once_a_day(self):
        import datetime
        start, _ = P.CHRONO_WINDOWS["dawn"]
        t = datetime.datetime(2026, 5, 4, start + 1).timestamp()
        pet = make(chrono="dawn", happiness=50.0)
        notes = P.check_in(pet, now=t)
        self.assertTrue(any("waiting for you" in n for n in notes))
        self.assertEqual(pet["happiness"], 60)
        P.check_in(pet, now=t + 600)
        self.assertEqual(pet["happiness"], 60)

    def test_activity_by_hour(self):
        self.assertEqual(P.activity(6), "active")
        self.assertEqual(P.activity(12), "drowsy")
        self.assertEqual(P.activity(2), "normal")

    def test_seasons_and_weather(self):
        self.assertEqual(P.season_for(1), "winter")
        self.assertEqual(P.season_for(10), "autumn")
        t = 1_700_000_000
        self.assertEqual(P.conditions(t), P.conditions(t + 60))      # fixed per day
        self.assertIn(P.conditions(t)[1], ("clear", "cloudy", "rainy", "snowy"))
        winter = [P.conditions(t + d * 86400)[1] for d in range(0, 60) if P.conditions(t + d * 86400)[0] == "winter"]
        self.assertNotIn("rainy", winter)

    def test_friend_arrives_for_trusted_adults_only(self):
        kit = make(trust=90.0)
        P.catch_up(kit, now=5 * H, rng=Always())
        self.assertIsNone(kit["friend"])
        adult = make(trust=90.0, stage="adult", born=-100 * H)
        _, notes = P.catch_up(adult, now=5 * H, rng=Always())
        self.assertIsNotNone(adult["friend"])
        self.assertTrue(any("hopped into the yard" in n for n in notes))

    def test_friend_slows_loneliness(self):
        alone, together = make(), make(friend={"name": "Pepper", "arrived": 0})
        for pet in (alone, together):
            P.catch_up(pet, now=5 * H, rng=Never())
        self.assertGreater(together["happiness"], alone["happiness"])

    def test_decor_slows_happiness_decay(self):
        plain, fancy = make(), make(inventory={"tunnel": 1, "hay bale": 1})
        for pet in (plain, fancy):
            P.catch_up(pet, now=5 * H, rng=Never())
        self.assertGreater(fancy["happiness"], plain["happiness"])

    def test_birthday(self):
        pet = make(last_seen=7 * 24 * H - 60)
        _, notes = P.catch_up(pet, now=7 * 24 * H, rng=Never())
        self.assertTrue(any("1-week birthday" in n for n in notes))
        self.assertEqual(pet["coins"], 10 + P.BIRTHDAY_COINS)


class Illness(unittest.TestCase):
    def test_illness_drains_health_and_trust(self):
        sick, well = make(illness="sniffles", trust=50.0), make(trust=50.0)
        for pet in (sick, well):
            P.catch_up(pet, now=10 * H, rng=Never())
        self.assertLess(sick["health"], well["health"])
        self.assertLess(sick["trust"], well["trust"])
        self.assertEqual(P.mood(make(illness="sniffles")), "sick")
        self.assertEqual(P.status_text(make(illness="sniffles")), "sick (sniffles)")

    def test_illness_clears_on_its_own(self):
        pet = make(illness="sniffles")
        _, notes = P.catch_up(pet, now=30 * H, rng=Never(), away=True)
        self.assertIsNone(pet["illness"])
        self.assertTrue(any("got over" in n for n in notes))

    def test_illness_clock_carries_over(self):
        pet = make(illness="sniffles", illness_hours=20.0)
        P.catch_up(pet, now=5 * H, rng=Never())
        self.assertIsNone(pet["illness"])
        self.assertAlmostEqual(pet["health"], 100 - 4 * P.ILLNESS_DRAIN)   # only 4 sick hours

    def test_medicine_cures(self):
        pet = make(illness="tummy ache", health=60.0, inventory={"medicine": 1})
        msg, anim = P.medicine(pet)
        self.assertIsNone(pet["illness"])
        self.assertEqual(pet["inventory"]["medicine"], 0)
        self.assertEqual(pet["health"], 75)

    def test_vet_when_out_of_medicine(self):
        pet = make(illness="tummy ache", coins=30)
        msg, _ = P.medicine(pet)
        self.assertIn("vet", msg)
        self.assertEqual(pet["coins"], 30 - P.VET_COST)
        self.assertIsNone(pet["illness"])

    def test_free_vet_when_poorly_and_broke(self):
        pet = make(illness="sniffles", coins=0, health=30.0)
        msg, _ = P.medicine(pet)
        self.assertIn("free", msg)
        self.assertIsNone(pet["illness"])
        self.assertEqual(pet["coins"], 0)

    def test_cannot_afford_treatment(self):
        pet = make(illness="tummy ache", coins=5)
        P.medicine(pet)
        self.assertEqual(pet["illness"], "tummy ache")

    def test_medicine_when_healthy_does_nothing(self):
        pet = make(inventory={"medicine": 1})
        self.assertIn("isn't sick", P.medicine(pet)[0])
        self.assertEqual(pet["inventory"]["medicine"], 1)

    def test_one_illness_at_a_time(self):
        pet = make(illness="sniffles")
        _, notes = P.catch_up(pet, now=2 * H, rng=type("R", (), {
            "random": lambda s: 0.0, "choice": lambda s, seq: P._ev_tummy})())
        self.assertEqual(pet["illness"], "sniffles")

    def test_too_many_treats_cause_tummy_ache(self):
        pet = make(inventory={"treat": 5})
        for i in range(4):
            P.treat(pet, rng=Always(), now=1000 + i)       # Always: the dice roll always hits
        self.assertEqual(pet["illness"], "tummy ache")
        self.assertEqual(pet["inventory"]["treat"], 1)

    def test_first_treat_is_safe(self):
        pet = make(inventory={"treat": 1})
        P.treat(pet, rng=Always(), now=1000)        # Always: any nonzero chance would hit
        self.assertIsNone(pet["illness"])

    def test_treat_requires_stock(self):
        self.assertIn("out of treats", P.treat(make())[0])


class Shop(unittest.TestCase):
    def test_buy_and_wear_hat(self):
        pet = make(coins=20)
        self.assertIn("puts it on", P.buy(pet, "bow"))
        self.assertEqual((pet["coins"], pet["hat"]), (10, "bow"))
        self.assertIn("already have", P.buy(pet, "bow"))

    def test_cannot_overspend(self):
        pet = make(coins=5)
        self.assertIn("costs", P.buy(pet, "top hat"))
        self.assertEqual(pet["coins"], 5)
        self.assertFalse(P.has(pet, "top hat"))

    def test_consumables_stack(self):
        pet = make(coins=20)
        P.buy(pet, "treat")
        P.buy(pet, "treat")
        self.assertEqual(pet["inventory"]["treat"], 2)

    def test_wardrobe_cycles_through_hats_and_bare(self):
        pet = make(coins=100)
        self.assertIn("don't own any hats", P.wardrobe(pet)[0])
        P.buy(pet, "bow")
        P.buy(pet, "top hat")
        seen = []
        for _ in range(3):
            P.wardrobe(pet)
            seen.append(pet["hat"])
        self.assertEqual(seen, [None, "bow", "top hat"])   # last bought hat was worn first

    def test_toys_change_playtime(self):
        plain, toy = make(happiness=40.0), make(happiness=40.0, inventory={"ball": 1, "chew toy": 1})
        P.play(plain)
        P.play(toy)
        self.assertGreater(toy["happiness"], plain["happiness"])
        self.assertGreater(toy["energy"], plain["energy"])

    def test_every_shop_item_is_buyable(self):
        pet = make(coins=10_000)
        for item in P.SHOP_ORDER:
            P.buy(pet, item)
            self.assertTrue(P.has(pet, item))

    def test_purchase_wording(self):
        pet = make(coins=100)
        self.assertEqual(P.buy(pet, "medicine"), "Bought some medicine.")
        self.assertEqual(P.buy(pet, "treat"), "Bought a treat.")
        self.assertEqual(P.buy(pet, "tunnel"), "Bought a tunnel.")

    def test_shop_descriptions_have_no_pronouns(self):
        import re
        pronouns = {"she", "she's", "her", "hers", "he", "he's", "him", "his",
                    "they", "they're", "them", "their"}
        for name, (_, _, desc) in P.ITEMS.items():
            words = set(re.findall(r"[a-z']+", desc.lower()))
            self.assertFalse(words & pronouns, f"{name}: {desc}")


class MiniGames(unittest.TestCase):
    def test_catch_game_scores_and_ends(self):
        import games
        class Alternating:      # spawn chance hits (0.0), then "not rotten" (0.9), repeating
            n = 0
            def random(self):
                self.n += 1
                return 0.0 if self.n % 2 else 0.9
            def randint(self, lo, hi): return lo
        g = games.CatchGame(40, 6, 8, rng=Alternating())   # carrots always fall at x=0
        g.px = 0
        while not g.done:
            g.step()
        self.assertGreater(g.score, 0)
        self.assertEqual(g.steps, games.CatchGame.DURATION)

    def test_catch_game_misses_score_nothing_and_rotten_hurts(self):
        import games
        far = games.CatchGame(40, 6, 8, rng=Always())     # Always: rotten items at x=0
        far.px = 30
        for _ in range(60):
            far.step()
        self.assertEqual(far.score, 0)
        bad = games.CatchGame(40, 6, 8, rng=Never())
        bad.px, bad.score = 0, 5
        bad.items = [[1, 4.9, "x"]]
        bad.step()
        self.assertEqual(bad.score, 3)

    def test_movement_is_clamped(self):
        import games
        g = games.CatchGame(40, 6, 8, rng=Never())
        for _ in range(50):
            g.move(1)
        self.assertEqual(g.px, 32)
        for _ in range(50):
            g.move(-1)
        self.assertEqual(g.px, 0)

    def test_paw_game(self):
        import games
        g = games.PawGame(rng=Always())                  # hidden paw is always "l"
        self.assertTrue(g.guess("l"))
        self.assertFalse(g.guess("r"))
        self.assertTrue(g.guess("l"))
        self.assertTrue(g.done)
        self.assertEqual(g.wins, 2)
        self.assertFalse(g.guess("l"))                   # no more rounds

    def test_catch_timer_starts_at_15(self):
        import games
        self.assertEqual(games.CatchGame(40, 6, 8, rng=Never()).seconds_left, 15)

    def test_game_rewards(self):
        pet = make(happiness=50.0, energy=80.0, coins=0)
        self.assertIn("scored 9 points and earned 3 coins", P.finish_catch(pet, 9))
        self.assertEqual(pet["coins"], 3)
        self.assertEqual(P.finish_catch(make(), 0), "Clover didn't score any points. Better luck next time!")
        pet = make(coins=0)
        self.assertIn("2 out of 3", P.finish_paws(pet, 2))
        self.assertEqual(pet["coins"], 4)

    def test_daily_game_coin_cap(self):
        pet = make(coins=0)
        t = 1_700_000_000
        for _ in range(3):
            msg = P.finish_catch(pet, 30, now=t)       # wants 10 coins each time
        self.assertEqual(pet["coins"], P.GAME_COIN_CAP)
        self.assertIn("limit", msg)
        P.finish_paws(pet, 3, now=t + 2 * 86400)       # a new day pays again
        self.assertEqual(pet["coins"], P.GAME_COIN_CAP + 6)


try:
    import thumptty as rabbit
except ImportError:
    rabbit = None


@unittest.skipIf(rabbit is None, "curses not available")
class Messages(unittest.TestCase):
    def test_notes_queue_and_replies_interrupt(self):
        s = rabbit.Scene(make())
        s.note("first")
        s.note("second")
        s.update(80)
        self.assertEqual(s.msg, "first")
        s.msg_ttl = 1
        s.update(80)
        self.assertEqual(s.msg, "second")
        s.say("reply")                      # a key press interrupts the note...
        self.assertEqual(s.msg, "reply")
        s.msg_ttl = 1
        s.update(80)
        self.assertEqual(s.msg, "second")   # ...which comes back afterwards

    def test_long_gaps_and_idle_count_as_away(self):
        pet = make(last_seen=1000)
        self.assertFalse(rabbit._away(pet, 1001, last_key=1000))
        self.assertTrue(rabbit._away(pet, 1000 + 601, last_key=1000 + 600))   # laptop slept
        self.assertTrue(rabbit._away(pet, 1001, last_key=1000 - 1801))         # left open, no keys


if __name__ == "__main__":
    unittest.main()
