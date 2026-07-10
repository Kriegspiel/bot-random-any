# Release Notes

These notes summarize the bot runtime release history reconstructed from the
current repository state. Add a new section at the top for runtime,
deployment-facing, or user-visible bot behavior changes. Test-only and
docs-only changes do not need entries unless they affect operator workflow.

## Current Runtime Baseline

- **Bot Identity**: `randobotany`, the random bot that asks first.
- **Rulesets**: supports `berkeley_any`.
- **Runtime Shape**: runs one process with one lightweight runner thread per
  active game, discovers up to 100 assigned active games, and caps intentional
  parallel play at 10 active games.
- **Lobby Policy**: can keep one human-joinable Berkeley+Any lobby game open
  and can join a compatible bot-created waiting game with 1% probability on a
  five-minute scan.
- **Move Policy**: asks `Any pawn captures?` whenever that action is available,
  then chooses randomly among legal move attempts exposed by the Kriegspiel API.
