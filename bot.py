"""Reference implementation of a random Kriegspiel bot that asks first.

This bot behaves like the plain random bot, but whenever the server offers the
"ask any pawn captures?" action it uses that first, refreshes its state, and
then picks a random allowed move from the narrower follow-up position.
"""

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
STATE_PATH = BASE_DIR / ".bot-state.json"
ENV_PATH = BASE_DIR / ".env"
DEFAULT_TIMEOUT_SECONDS = 20
BOT_JOIN_COOLDOWN_SECONDS = 60
BOT_GAME_PICK_PROBABILITY = 0.5
MAX_ACTIVE_GAMES = 10
FAILED_MOVE_RETRY_DELAY_SECONDS = 1


def load_env_file(path: str | Path = ENV_PATH) -> None:
    """Load simple KEY=VALUE pairs from a local .env file if it exists."""

    env_path = Path(path)
    if not env_path.exists():
        return

    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def base_url() -> str:
    """Return the API base URL without a trailing slash."""

    return os.environ.get("KRIEGSPIEL_API_BASE", "http://localhost:8000").rstrip("/")


def auth_headers() -> dict[str, str]:
    """Build bearer auth headers from the bot token in the environment."""

    token = os.environ.get("KRIEGSPIEL_BOT_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def bot_username() -> str:
    return os.environ.get("KRIEGSPIEL_BOT_USERNAME", "").strip().lower()


def load_state() -> dict:
    return json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2))


def save_token(token: str) -> None:
    """Persist a newly-issued bot token locally for later runs."""

    state = load_state()
    state["token"] = token
    save_state(state)


def maybe_restore_token() -> None:
    """Restore a previously saved token when the environment is empty."""

    if os.environ.get("KRIEGSPIEL_BOT_TOKEN"):
        return
    if STATE_PATH.exists():
        token = load_state().get("token")
        if token:
            os.environ["KRIEGSPIEL_BOT_TOKEN"] = token


def register_bot() -> None:
    """Register the bot account and store the returned API token."""

    response = requests.post(
        f"{base_url()}/api/auth/bots/register",
        headers={"X-Bot-Registration-Key": os.environ["KRIEGSPIEL_BOT_REGISTRATION_KEY"]},
        json={
            "username": os.environ["KRIEGSPIEL_BOT_USERNAME"],
            "display_name": os.environ["KRIEGSPIEL_BOT_DISPLAY_NAME"],
            "owner_email": os.environ["KRIEGSPIEL_BOT_OWNER_EMAIL"],
            "description": os.environ.get("KRIEGSPIEL_BOT_DESCRIPTION", ""),
            "supported_rule_variants": supported_rule_variants(),
        },
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    save_token(payload["api_token"])
    print(json.dumps(payload, indent=2))


def get_json(path: str) -> dict:
    """GET a JSON API endpoint and raise for non-success responses."""

    response = requests.get(f"{base_url()}{path}", headers=auth_headers(), timeout=DEFAULT_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def get_public_user(username: str) -> dict:
    response = requests.get(f"{base_url()}/api/user/{username}", headers=auth_headers(), timeout=DEFAULT_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def post_json(path: str, payload: dict | None = None) -> dict:
    """POST JSON to the API and return the decoded payload."""

    response = requests.post(
        f"{base_url()}{path}",
        headers=auth_headers(),
        json=payload or {},
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def auto_create_enabled() -> bool:
    raw = os.environ.get("KRIEGSPIEL_AUTO_CREATE_LOBBY_GAME", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def create_payload() -> dict[str, str]:
    return {
        "rule_variant": os.environ.get("KRIEGSPIEL_AUTO_CREATE_RULE_VARIANT", "berkeley_any").strip() or "berkeley_any",
        "play_as": os.environ.get("KRIEGSPIEL_AUTO_CREATE_PLAY_AS", "random").strip() or "random",
        "time_control": "rapid",
        "opponent_type": "human",
    }


def supported_rule_variants() -> list[str]:
    raw = os.environ.get("KRIEGSPIEL_SUPPORTED_RULE_VARIANTS", "berkeley_any")
    variants: list[str] = []
    for item in raw.split(","):
        value = item.strip()
        if value in {"berkeley", "berkeley_any"} and value not in variants:
            variants.append(value)
    return variants or ["berkeley_any"]


def active_games(games: list[dict]) -> list[dict]:
    return [game for game in games if game.get("state") == "active"]


def waiting_games(games: list[dict]) -> list[dict]:
    return [game for game in games if game.get("state") == "waiting"]


def under_active_game_limit(games: list[dict]) -> bool:
    return len(active_games(games)) < MAX_ACTIVE_GAMES


def open_bot_lobby_candidates(open_games: list[dict], *, profile_lookup=None) -> list[dict]:
    profile_lookup = profile_lookup or get_public_user
    own_username = bot_username()
    candidates = []
    for game in open_games:
        creator_username = str(game.get("created_by") or "").strip()
        if not creator_username:
            continue
        if str(game.get("rule_variant") or "").strip() not in supported_rule_variants():
            continue
        creator_username_lower = creator_username.lower()
        if creator_username_lower == own_username:
            continue

        try:
            profile = profile_lookup(creator_username)
        except requests.RequestException:
            continue

        is_bot = bool(profile.get("is_bot")) or str(profile.get("role") or "").strip().lower() == "bot"
        if not is_bot:
            continue
        candidates.append(game)
    return candidates


def has_own_waiting_game(open_games: list[dict]) -> bool:
    own_username = bot_username()
    for game in open_games:
        created_by = str(game.get("created_by") or "").strip().lower()
        if created_by and created_by == own_username:
            return True
    return False


def can_attempt_bot_join(now: float | None = None) -> bool:
    current = time.time() if now is None else now
    last_attempt = load_state().get("last_bot_game_join_attempt_at", 0)
    try:
        last_attempt = float(last_attempt)
    except (TypeError, ValueError):
        last_attempt = 0
    return current - last_attempt >= BOT_JOIN_COOLDOWN_SECONDS


def record_bot_join_attempt(now: float | None = None) -> None:
    state = load_state()
    state["last_bot_game_join_attempt_at"] = time.time() if now is None else now
    save_state(state)


def choose_bot_game_to_join(open_games: list[dict], *, rng: random.Random = random) -> dict | None:
    candidates = open_bot_lobby_candidates(open_games)
    if not candidates:
        return None
    if rng.random() >= BOT_GAME_PICK_PROBABILITY:
        return None
    return rng.choice(candidates)


def maybe_join_bot_lobby_game(*, rng: random.Random = random) -> bool:
    mine = get_json("/api/game/mine")
    if not under_active_game_limit(mine.get("games", [])):
        return False
    if not can_attempt_bot_join():
        return False

    open_games = get_json("/api/game/open").get("games", [])
    candidate = choose_bot_game_to_join(open_games, rng=rng)
    if not candidate:
        return False

    game_code = candidate.get("game_code")
    if not isinstance(game_code, str) or not game_code.strip():
        return False

    record_bot_join_attempt()
    joined = post_json(f"/api/game/join/{game_code.strip()}")
    print(f"joined bot lobby game {joined['game_id']} ({joined['game_code']})")
    return True


def should_create_lobby_game(games: list[dict]) -> bool:
    if not auto_create_enabled():
        return False
    if not under_active_game_limit(games):
        return False
    return not waiting_games(games)


def maybe_create_lobby_game(games: list[dict]) -> bool:
    if not should_create_lobby_game(games):
        return False

    open_games = get_json("/api/game/open").get("games", [])
    if has_own_waiting_game(open_games):
        return False

    created = post_json("/api/game/create", create_payload())
    print(f"created lobby game {created['game_id']} ({created['game_code']})")
    return True


def choose_random_moves(allowed_moves: list[str]) -> list[str]:
    """Return the server-provided legal moves in random order.

    The backend already filtered the move list to this player's currently legal
    possibilities, so the bot only randomizes ordering.
    """

    moves = list(allowed_moves)
    random.shuffle(moves)
    return moves


def maybe_play_game(game_id: str) -> bool:
    """Play one turn in the specified game if it is currently ours."""

    state = get_json(f"/api/game/{game_id}/state")
    if state.get("state") != "active" or state.get("turn") != state.get("your_color"):
        return False

    possible_actions = state.get("possible_actions", [])

    if "ask_any" in possible_actions:
        result = post_json(f"/api/game/{game_id}/ask-any")
        print(f"{game_id}: ask-any -> {result['announcement']}")
        state = get_json(f"/api/game/{game_id}/state")
        if state.get("state") != "active" or state.get("turn") != state.get("your_color"):
            return False
        possible_actions = state.get("possible_actions", [])

    if "move" not in possible_actions:
        return False

    moves = choose_random_moves(state.get("allowed_moves", []))
    if not moves:
        return False

    for index, uci in enumerate(moves):
        result = post_json(f"/api/game/{game_id}/move", {"uci": uci})
        print(f"{game_id}: tried {uci} -> {result['announcement']}")
        if result.get("move_done"):
            return True
        if index < len(moves) - 1:
            time.sleep(FAILED_MOVE_RETRY_DELAY_SECONDS)
    return False


def run_loop(poll_seconds: float) -> None:
    """Poll the bot's games forever and act whenever a turn is available."""

    while True:
        try:
            mine = get_json("/api/game/mine")
            games = mine.get("games", [])
            maybe_create_lobby_game(games)
            maybe_join_bot_lobby_game()
            for game in active_games(games):
                maybe_play_game(game["game_id"])
        except requests.RequestException as exc:
            print(f"poll failed: {exc}", file=sys.stderr, flush=True)
        time.sleep(poll_seconds)


def main() -> None:
    load_env_file()
    maybe_restore_token()

    parser = argparse.ArgumentParser(description="Run the reference Kriegspiel random bot.")
    parser.add_argument("--register", action="store_true", help="Register the bot and persist the returned token.")
    parser.add_argument("--poll-seconds", type=float, default=3.0, help="Seconds between /api/game/mine polls.")
    args = parser.parse_args()

    if args.register:
        register_bot()
        return

    if not os.environ.get("KRIEGSPIEL_BOT_TOKEN"):
        raise SystemExit("KRIEGSPIEL_BOT_TOKEN is missing. Run with --register first.")

    run_loop(args.poll_seconds)


if __name__ == "__main__":
    main()
