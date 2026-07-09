# bot-random-any

Minimal Kriegspiel random-move bot that asks first.

## What it does

- registers with the Kriegspiel API
- authenticates with a bot bearer token
- runs one bot process with one lightweight runner thread per active game
- polls assigned games from the main process and lets each runner poll/play its own game
- can keep one open human-joinable lobby game advertised
- can also join another bot's waiting lobby game with 1% probability when one is available
- asks `Any pawn captures?` first whenever that action is available
- then picks random kriegspiel-allowed moves exposed by the API
- intentionally caps itself at 10 active games in parallel
- keeps running through transient API failures

## Setup

Set `KRIEGSPIEL_BOT_OWNER_EMAIL` in `.env` before registering. The backend now requires it so Kriegspiel can contact the bot owner if needed.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python bot.py --register
python bot.py
```

Multiple instances should use separate env and state files:

```bash
python bot.py \
  --env-file instances/randobotany.env \
  --state-file instances/randobotany-state.json
```

By default the bot also keeps one open lobby game available for humans to join.
That behavior is controlled with:

- `KRIEGSPIEL_AUTO_CREATE_LOBBY_GAME=true|false`
- `KRIEGSPIEL_AUTO_CREATE_RULE_VARIANT=berkeley|berkeley_any`
- `KRIEGSPIEL_AUTO_CREATE_PLAY_AS=white|black|random`
- `KRIEGSPIEL_ACTIVE_GAME_DISCOVERY_LIMIT=100`
- `KRIEGSPIEL_SUPPORTED_RULE_VARIANTS=berkeley_any`

The bot will not intentionally create or join beyond 10 active games in parallel. It still keeps at most one open waiting lobby game advertised at a time.

The main loop uses `KRIEGSPIEL_ACTIVE_GAME_DISCOVERY_LIMIT` when discovering
assigned active games, then starts one runner thread per active game. A slow
game no longer blocks other assigned games in the same process. Existing runner
threads are not stopped only because a later capped discovery response omits
them; each runner exits when its own game-state poll reports completion or
unavailability.

Bot-vs-bot play is also enabled by default:

- the bot samples open waiting games at most once every five minutes
- it will only consider games created by another bot
- it will try to join one with 1% probability on a scan
- it keeps the local cooldown even when no join candidate is found, matching backend bot-join limits and avoiding tight lobby scans

## systemd

A production host can run the bot as a service with `deploy/kriegspiel-random-any-bot.service`.
