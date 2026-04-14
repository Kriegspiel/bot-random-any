from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot


class BotTests(unittest.TestCase):
    def test_under_active_game_limit_caps_parallel_games_at_five(self) -> None:
        self.assertTrue(bot.under_active_game_limit([{"state": "active"}] * 4))
        self.assertFalse(bot.under_active_game_limit([{"state": "active"}] * 5))

    def test_open_bot_lobby_candidates_only_include_other_bot_waiting_games(self) -> None:
        with patch.dict("os.environ", {"KRIEGSPIEL_BOT_USERNAME": "randobotany"}):
            candidates = bot.open_bot_lobby_candidates(
                [
                    {
                        "game_code": "BOT123",
                        "created_by": "gptnano",
                        "rule_variant": "berkeley_any",
                    },
                    {
                        "game_code": "SELF12",
                        "created_by": "randobotany",
                        "rule_variant": "berkeley_any",
                    },
                    {
                        "game_code": "HUM123",
                        "created_by": "fil",
                        "rule_variant": "berkeley_any",
                    },
                ],
                profile_lookup=lambda username: {"role": "bot" if username == "gptnano" else "user"},
            )

        self.assertEqual([game["game_code"] for game in candidates], ["BOT123"])

    def test_open_bot_lobby_candidates_only_include_supported_rule_variants(self) -> None:
        with patch.dict("os.environ", {"KRIEGSPIEL_BOT_USERNAME": "randobotany", "KRIEGSPIEL_SUPPORTED_RULE_VARIANTS": "berkeley_any"}):
            candidates = bot.open_bot_lobby_candidates(
                [
                    {"game_code": "BER123", "created_by": "gptnano", "rule_variant": "berkeley"},
                    {"game_code": "ANY123", "created_by": "gptnano", "rule_variant": "berkeley_any"},
                ],
                profile_lookup=lambda username: {"role": "bot"},
            )

        self.assertEqual([game["game_code"] for game in candidates], ["ANY123"])

    def test_choose_bot_game_to_join_returns_candidate(self) -> None:
        games = [{"game_code": "BOT123", "created_by": "gptnano", "rule_variant": "berkeley_any"}]

        with patch.dict("os.environ", {"KRIEGSPIEL_BOT_USERNAME": "randobotany"}):
            with patch.object(bot.random, "choice", side_effect=lambda items: items[0]):
                with patch.object(bot, "get_public_user", return_value={"role": "bot"}):
                    self.assertEqual(bot.choose_bot_game_to_join(games, rng=bot.random)["game_code"], "BOT123")

    def test_maybe_join_bot_lobby_game_records_attempt_even_when_probability_misses(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / ".bot-state.json"
            mine = {"games": []}
            open_games = {"games": [{"game_code": "BOT123", "created_by": "gptnano", "rule_variant": "berkeley_any"}]}

            def fake_get_json(path: str) -> dict:
                if path == "/api/game/mine":
                    return mine
                if path == "/api/game/open":
                    return open_games
                raise AssertionError(path)

            with patch.object(bot, "STATE_PATH", state_path):
                with patch.dict("os.environ", {"KRIEGSPIEL_BOT_USERNAME": "randobotany"}):
                    with patch.object(bot, "get_json", side_effect=fake_get_json):
                        with patch.object(bot, "get_public_user", return_value={"role": "bot"}):
                            with patch.object(bot.random, "choice", side_effect=lambda items: items[0]):
                                with patch.object(bot.random, "random", return_value=0.9):
                                    with patch.object(bot.time, "time", return_value=0.0):
                                        with patch.object(bot, "post_json") as post_mock:
                                            self.assertFalse(bot.maybe_join_bot_lobby_game(rng=bot.random))

                self.assertFalse(bot.can_attempt_bot_join(now=30.0))
                self.assertTrue(bot.can_attempt_bot_join(now=61.0))
                post_mock.assert_not_called()

    def test_can_attempt_bot_join_uses_local_cooldown_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / ".bot-state.json"
            with patch.object(bot, "STATE_PATH", state_path):
                bot.record_bot_join_attempt(now=100.0)
                self.assertFalse(bot.can_attempt_bot_join(now=120.0))
                self.assertTrue(bot.can_attempt_bot_join(now=161.0))

    def test_has_own_waiting_game_detects_existing_lobby(self) -> None:
        with patch.dict("os.environ", {"KRIEGSPIEL_BOT_USERNAME": "randobot"}):
            self.assertTrue(bot.has_own_waiting_game([{"game_code": "ABC123", "created_by": "randobot"}]))
            self.assertFalse(bot.has_own_waiting_game([{"game_code": "XYZ789", "created_by": "gptnano"}]))

    def test_maybe_play_game_asks_any_before_random_move(self) -> None:
        states = [
            {
                "state": "active",
                "turn": "white",
                "your_color": "white",
                "possible_actions": ["move", "ask_any"],
                "allowed_moves": ["e2e4", "d2d4"],
            },
            {
                "state": "active",
                "turn": "white",
                "your_color": "white",
                "possible_actions": ["move"],
                "allowed_moves": ["d2d4"],
            },
        ]
        posts: list[tuple[str, dict | None]] = []

        def fake_get_json(path: str) -> dict:
            self.assertEqual(path, "/api/game/game-1/state")
            return states.pop(0)

        def fake_post_json(path: str, payload: dict | None = None) -> dict:
            posts.append((path, payload))
            if path.endswith("/ask-any"):
                return {"announcement": "No pawn captures."}
            return {"announcement": "Move complete", "move_done": True}

        with patch.object(bot, "get_json", side_effect=fake_get_json):
            with patch.object(bot, "post_json", side_effect=fake_post_json):
                self.assertTrue(bot.maybe_play_game("game-1"))

        self.assertEqual(
            posts,
            [
                ("/api/game/game-1/ask-any", None),
                ("/api/game/game-1/move", {"uci": "d2d4"}),
            ],
        )

    def test_maybe_play_game_retries_moves_with_delay_until_one_succeeds(self) -> None:
        state = {
            "state": "active",
            "turn": "white",
            "your_color": "white",
            "possible_actions": ["move"],
            "allowed_moves": ["e2e4", "d2d4", "g1f3"],
        }
        posts: list[tuple[str, dict | None]] = []
        results = [
            {"announcement": "Illegal move", "move_done": False},
            {"announcement": "Move complete", "move_done": True},
        ]

        def fake_post_json(path: str, payload: dict | None = None) -> dict:
            posts.append((path, payload))
            return results.pop(0)

        with patch.object(bot, "get_json", return_value=state):
            with patch.object(bot, "choose_random_moves", return_value=["d2d4", "e2e4", "g1f3"]):
                with patch.object(bot, "post_json", side_effect=fake_post_json):
                    with patch.object(bot.time, "sleep") as sleep_mock:
                        self.assertTrue(bot.maybe_play_game("game-1"))

        self.assertEqual(
            posts,
            [
                ("/api/game/game-1/move", {"uci": "d2d4"}),
                ("/api/game/game-1/move", {"uci": "e2e4"}),
            ],
        )
        sleep_mock.assert_called_once_with(bot.FAILED_MOVE_RETRY_DELAY_SECONDS)


if __name__ == "__main__":
    unittest.main()
