# Arcanum

A collectible card battler client — navy & gold, built for an eventual online, server-hosted future. This is the **framework milestone**: launchable app, full login flow, settings, theming, and all the infrastructure seams for Supabase + a game server.

## Run it

```bash
pip install -r requirements.txt   # installs pygame-ce
python main.py
```

The game opens **borderless at your native desktop resolution** by default. Change screen mode (windowed / borderless / fullscreen) and resolution in **Settings → Graphics**; audio buses in **Settings → Audio**. All settings persist to `data/settings.json`.

On the login screen you can create an account, sign in, use *Forgot password?*. *Remember me* keeps you signed in across launches. Accounts are currently stored locally (`data/local_users.json`, PBKDF2-hashed passwords) — this is the offline stand-in for Supabase.

## Architecture

```
main.py                        entry point
arcanum/
├── core/
│   ├── app.py                 window/display management, main loop, boot flow
│   ├── scene.py               scene stack + fade transitions
│   ├── config.py              typed, persisted settings (graphics/audio)
│   ├── events.py              pub/sub event bus (thread-safe queue for netcode)
│   └── constants.py           paths, presets, Supabase/server config slots
├── ui/
│   ├── theme.py               navy/gold palette, fonts, draw helpers (single reskin point)
│   ├── widgets.py             Button, TextInput, Checkbox, Slider, Dropdown, SegmentedControl
│   ├── animation.py           easing curves, tweens, frame-rate-independent smoothing
│   └── background.py          animated backdrop (gold motes, aurora sweep, vignette)
├── scenes/
│   ├── login.py               sign in / sign up / forgot password / remember me / dev bypass
│   ├── home.py                main menu hub (Play, Collection, Store, Settings, Log Out)
│   └── settings.py            Graphics + Audio tabs
├── services/
│   ├── backend.py             ★ service resolution — the one place online mode gets switched on
│   ├── auth.py                AuthService interface; LocalAuthService (dev), SupabaseAuthService (stub)
│   ├── session.py             current user + remember-me token store
│   └── net/
│       ├── protocol.py        versioned message envelopes, intent/event message types
│       ├── client.py          NetworkClient interface; OfflineClient (now), WebSocketClient (plan)
│       └── matchmaking.py     queue join/leave facade
├── audio/audio.py             bus mixer: master / music / sfx / ui
└── game/models.py             early domain models (CardDef, Deck, PlayerProfile)
```

### Design decisions locked in for the online future

- **Server-authoritative protocol.** Clients send *intents* (`intent.play_card`, `intent.attack`, …); the server replies with authoritative *events* (`event.state_delta`, …). Standard TCG anti-cheat model.
- **Interface-first services.** Scenes only know `AuthService` / `NetworkClient` / `Session`. Going live = finishing `SupabaseAuthService` + `WebSocketClient` and filling in `SUPABASE_URL` in `core/constants.py`. `Backend.create()` is the only switch.
- **Threading model.** Network and auth I/O run on worker threads; results cross to the main thread via the event bus queue or per-scene mailboxes. UI code never blocks and never touches sockets.
- **Remember-me = opaque token**, never a password. Maps 1:1 onto Supabase refresh tokens later.
- **One theme file.** Every color and font in the game routes through `ui/theme.py`.

## Roadmap (suggested next milestones)

1. **Match scene skeleton** — board layout, hand fan, drag-to-play interactions against `game/models.py`.
2. **Rules engine** — deterministic core sim usable by both the client (prediction/animation) and the future server (authority).
3. **Deck builder + Collection scene** backed by `PlayerProfile`.
4. **Supabase go-live** — implement `SupabaseAuthService`, add `profiles`/`cards`/`decks` tables with row-level security.
5. **Game server** — implement `WebSocketClient`, stand up matchmaking + match hosting speaking `protocol.py`.
