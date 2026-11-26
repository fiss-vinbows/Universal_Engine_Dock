# Engine specification and registration

[日本語](ENGINE_SPEC.md) | **English**

How to add your own or public Puyo Puyo and Tetris engines (AI, bots) to Universal Engine Dock (the "dock" below).
Tetris engines talk TBP (Tetris Bot Protocol), Puyo engines talk JSON over standard input and output.

This specification follows the idea of USB. Just as USB unified sockets that differed for every device, it unifies the way engines exchange data,
so that **an engine by anyone can be swapped in, as long as it follows the specification**. Three rules are kept for that.

- **One port per game** — use a widely used standard if there is one (TBP for Tetris). If not, define it here (Puyo)
- **Any programming language** — talk only over standard input/output and JSON
- **Additions do not break anything** — unknown fields and unknown messages are ignored, so existing engines keep working when fields are added

Samples that follow the specification are in [samples/](samples/) (weak engines that need only the Python standard library. Their comments are in Japanese).

- [1. Common rules](#1-common-rules)
- [2. Registration](#2-registration)
- [3. Tetris protocol (TBP)](#3-tetris-protocol-tbp)
- [4. Puyo protocol](#4-puyo-protocol)
- [5. Troubleshooting](#5-troubleshooting)

## 1. Common rules

An engine is an executable that the dock starts as a **separate process** and talks to over standard input and output.
Programs that have their own window and operate the game directly cannot be registered.

| Item | Rule |
|---|---|
| Communication | Receive requests from standard input, answer on standard output |
| Format | **One JSON per line**. Line ends are `\n` (`\r\n` is fine). Do not break a line in the middle |
| Encoding | UTF-8 (no BOM) |
| Flushing | **Always flush** after each line (otherwise it does not reach the dock and times out) |
| Standard output | Nothing but JSON. Logs and messages go to standard error |
| Unknown items | Ignore unknown fields and unknown kinds of messages and keep working |
| Window | No console window is created. Do not show anything that waits for input |
| Exit | Exit by yourself on the quit message or the end of standard input (EOF). Otherwise you are terminated |

Requests come one at a time. For Puyo, the next request does not come until the answer is received. For Tetris, messages also come while thinking.

## 2. Registration

### From the window

1. Press "Register engines…" in the main window and choose a game
2. Press "Add…" and answer in this order
   - **Executable** — the exe of the engine
   - **Name** — the name shown in the list. It must be unique within the game (names of plugin engines cannot be used)
   - **Launch arguments** — leave empty if there are none
   - **Evaluation weights** — whether to pass a weights file (see the table below. "No" if unsure)
3. Press "Save" (it is not registered until you do)
4. Choose it in "Engine:" of a side in the main window

### Replacing a bundled engine with a new version

The bundled YamaNEKO (Puyo) and KasugAI (Tetris) are in the list of registered engines from the first start.
While they use the files in the release, "(bundled)" is shown after the name.

- **Use a new version** — select it in the list, press "Change…" and choose the executable of the new version.
  The name stays the same, so the choices on the main window are kept. Answer "No" to keep the current weights
- **Go back to the release's files** — press "Restore bundled". This also brings them back if you removed them
- **Remove** — press "Remove". A removed bundled engine is not added again at the next start

The simulators also use the registered YamaNEKO and KasugAI.
Settings only for YamaNEKO (reading the real piece sequence, CPU use and so on) and the opener choice only for KasugAI work
when you keep the name and the executable's name starts the same way as the bundled one (`yamaneko`, `tetrisai-bot`).

| | Tetris | Puyo |
|---|---|---|
| Started as | `<exe> [--config <weights>] [arguments…]` | `<exe> [arguments…]` |
| Working folder | The folder the dock was started in (do not rely on it) | The folder of the exe |
| Evaluation weights | Passed as `--config <absolute path>` | **Not used** (pass it as an argument if needed) |

Writing arguments:

- Separate with spaces. Enclose paths that contain spaces in `"`
- `\` is special, so write paths with `/` (example: `--weights "C:/my engine/w.json"`)

If a Tetris engine does not know `--config`, answer "No" to the weights.

### Engines written in Python and so on

Set `python.exe` as the executable and the script as an argument.

| Item | Example |
|---|---|
| Executable | `C:\Python312\python.exe` |
| Launch arguments | `-u "C:/UniversalEngineDock/docs/samples/tetris_engine_sample.py"` |

In Python, add `-u` or use `print(..., flush=True)`. `pythonw.exe` and `.bat` may not work.

### Settings file

Registrations are kept in `config/engines.json`. You may edit it directly while the dock is closed.

```json
{
  "engines": [
    {"game": "puyo", "name": "YamaNEKO", "exe": "engines/yamaneko/engine/bin/yamaneko-bridge.exe", "args": [], "weights": ""},
    {"game": "puyo", "name": "My Puyo AI", "exe": "C:/bots/mypuyo.exe", "args": [], "weights": ""},
    {"game": "tetris", "name": "KasugAI", "exe": "engines/kasugai/tetrisai-bot.exe", "args": [], "weights": "engines/kasugai/run16.toml"},
    {"game": "tetris", "name": "My Bot", "exe": "C:/bots/mybot.exe", "args": ["--threads", "4"], "weights": ""}
  ],
  "bundled": ["puyo/YamaNEKO", "tetris/KasugAI"]
}
```

- Relative paths are seen from the dock's folder (the folder of `UniversalEngineDock.exe`)
- `bundled` marks the bundled engines already added to the registration. If you delete it, the bundled engines are added again at the next start

## 3. Tetris protocol (TBP)

Engines that follow the [Tetris Bot Protocol (TBP)](https://github.com/tetris-bot-protocol/tbp-spec) can be registered.
This section describes the part the dock actually uses.

### Using Cold Clear 2

[Cold Clear 2](https://github.com/MinusKelvin/cold-clear-2) (Apache License 2.0 or MIT License) works without any changes
(confirmed with `ed8b193` of the original repository). Executables are not distributed, so build it yourself. It is not bundled with the dock.

1. Install [Rust](https://www.rust-lang.org/tools/install) and [Git](https://git-scm.com/), then run these one line at a time in PowerShell

   ```powershell
   git clone https://github.com/MinusKelvin/cold-clear-2
   ```

   ```powershell
   cargo build --release --manifest-path cold-clear-2/Cargo.toml
   ```

2. In "Register engines…", choose Tetris and add `cold-clear-2/target/release/cold-clear-2.exe`. Arguments can be empty
3. For the evaluation weights, choose one of these
   - **No** — the standard play of Cold Clear 2
   - **Yes** → `cold_clear_beginner.json` (in the folder that opens first. If not, in `_internal/assets/`) —
     a beginner-friendly play without the bonuses for T-Spin Singles and Mini T-Spins

The line of play after the next move is an extension that TBP does not have (`plan`), so the original Cold Clear 2 does not show it. The next move is shown the same way.

### Flow

```
engine → {"type":"info", ...}          send by yourself on start (within 5 seconds)
dock   → {"type":"rules"}
engine → {"type":"ready"}              within 5 seconds
dock   → {"type":"start", ...}         gives a position. Start thinking here
dock   → {"type":"suggest"}            asks for the best move so far (comes many times, about every 0.1 s)
engine → {"type":"suggestion", ...}    answer at once (within 5 seconds)
dock   → {"type":"play", ...}          the suggested move was placed
dock   → {"type":"new_piece", ...}     a new next piece became visible
   …
dock   → {"type":"quit"}
```

- `start` comes many times: on new turns, rising garbage and board mismatches. Discard the previous position when it comes
- Answer `suggest` without waiting; an unfinished result is fine. If there is no move yet, return `"moves": []` (you will be asked again)
- Return **exactly one** `suggestion` for each `suggest`. Do not send it when not asked

### Engine → dock

**info**

```json
{"type":"info","name":"My Bot","version":"1.0","author":"me","features":[]}
```

**suggestion**

```json
{"type":"suggestion",
 "moves":[{"location":{"type":"T","orientation":"north","x":4,"y":0},"spin":"none"}],
 "move_info":{"nodes":12345,"nps":1000000}}
```

| Field | Required | Meaning |
|---|---|---|
| `moves` | yes | List of moves. The dock uses **only the first** |
| `location.type` | yes | The piece to place (`I O T L J S Z`). If it differs from the current piece, it is shown as "hold, then place" |
| `location.orientation` | yes | `north` (spawn orientation) / `east` (rotated clockwise) / `south` / `west` |
| `location.x`, `location.y` | yes | Center of the piece. x is 0-9 from the left, y is 0 **from the bottom** |
| `spin` | yes | `none` / `mini` / `full` |
| `move_info.nodes`, `nps` | | Positions searched, and per second (written to the log) |
| `plan` | | Our extension: line of play. The same form as the elements of `moves`, in order from the current move (used to show the second move and later) |
| `aim` | | Our extension: a string describing the aim. Shown in the information panel (`\n` allowed) |
| `ren_target` | | Our extension: the REN (combo) count being aimed for (integer) |

The dock also checks that the move can be placed. Moves that overlap blocks, float, or use a piece that is neither current nor held are not shown.

### Center and the four cells

In `north`, the four cells `(dx, dy)` from the center (x, y) are (dy is positive upwards):

| Piece | Cells |
|---|---|
| I | (-1,0) (0,0) (1,0) (2,0) |
| O | (0,0) (1,0) (0,1) (1,1) |
| T | (-1,0) (0,0) (1,0) (0,1) |
| L | (-1,0) (0,0) (1,0) (1,1) |
| J | (-1,0) (0,0) (1,0) (-1,1) |
| S | (-1,0) (0,0) (0,1) (1,1) |
| Z | (-1,1) (0,1) (0,0) (1,0) |

For other orientations, rotate each cell: `east` = (dy, -dx), `south` = (-dx, -dy), `west` = (-dy, dx).

### Dock → engine

**start**

```json
{"type":"start","hold":null,"queue":["T","I","O","S","Z","J"],
 "combo":0,"back_to_back":false,"board":[[null,null,"I"],["…"]]}
```

| Field | Meaning |
|---|---|
| `hold` | The held piece, or `null` |
| `queue` | The current piece + visible next pieces (usually 6. Fewer if some cannot be read because of effects) |
| `board` | 40 rows × 10 columns. **The first row is the bottom**. Values are `null` (empty), a piece letter, or `"G"` (garbage or a block of unknown color) |
| `combo`, `back_to_back` | Always `0` and `false` in assist mode (they cannot be known from the screen) |

- In the turn right after a hold, `hold` may be the same as the current piece, to prevent holding again
- The board is read from the screen and may be wrong. Do not crash on inconsistent positions (full rows, floating blocks, a `queue` that does not follow the 7-bag)

**play** — `{"type":"play","move":{…}}`. `move` is exactly the move the engine returned in `suggestion`. The current piece advances by one

**new_piece** — `{"type":"new_piece","piece":"L"}`. Add one to the end of `queue`

**quit** — exit

## 4. Puyo protocol

Our own format (the one YamaNEKO uses). **Each request is completed by one line of answer.** You do not need to remember the state of previous requests.

> The executable of the public [ama](https://github.com/citrus610/ama) does not talk this format.
> To use ama, write a program (bridge) that calls ama's search and talks this format, and register that.

### Flow

```
engine → {"ready":true}     send by yourself when ready (within 60 seconds)
dock   → {"field":…}        request (one line)
engine → {…}                answer (one line, within 10 seconds. Aim for under 1 second so it is in time for the turn)
   …
dock   → quit               exit (not JSON, just the four letters quit)
```

Executables whose name does not start with `yamaneko` are restarted every 300 requests (to keep memory from growing). Start up quickly.

### Board, coordinates and pairs

| Thing | Representation |
|---|---|
| Board `field` | 13 strings. **The first is the top** (13th row = the hidden row), the last is the bottom (1st row). Each has 6 characters from the left |
| Cell | `R` `Y` `G` `B` = colored puyo, `#` = garbage puyo, `.` = empty |
| Color | The four letters only tell four colors apart and may not be the color on the screen (for example, purple may be assigned to `B`) |
| Pairs `queue` | `["RG","BY",…]`. The first is the current pair, then next, then next-next if visible. First letter is the axis puyo, second is the child puyo |
| Coordinates `[x, y]` | x is 0-5 from the left, y is 0-12 **from the bottom** |
| Direction `r` | Position of the child seen from the axis: `UP` / `RIGHT` / `DOWN` / `LEFT` |

### Kinds of requests

| `mode` | When it comes | Required |
|---|---|---|
| (none) or `build` | Solo mode, the first move of a match and so on. Asks for a placement without considering the opponent | yes |
| `analyze` | Right after `build`. Asks about the chains of the board | yes |
| `battle` | Versus mode. Asks for a placement considering the opponent | yes (not needed if used only in solo mode) |

Other modes such as `candidates` are sent only to the bundled YamaNEKO. Return `{"error":"…"}` for unknown modes.

### build

Request:

```json
{"field":["......","......","......","......","......","......","......","......","......","......","......","......","RG...."],
 "queue":["RG","BY","RR"]}
```

`width` and `depth` (hints for the search width and depth) may be added. You may ignore them.

Answer:

```json
{"x":2,"r":"UP","score":1200,"axis":[2,0],"child":[2,1],"ms":35}
```

| Field | Required | Meaning |
|---|---|---|
| `x` | yes | Column of the axis puyo |
| `r` | yes | Position of the child puyo |
| `score` | yes | Evaluation (integer. Larger is better) |
| `axis`, `child` | yes | Cells of the axis and child puyo after placement (after falling, before chains) |
| `ms` | | Time spent thinking (milliseconds) |
| `field_after` | | The board after placement and the end of chains (13 rows) |
| `form` | | The form aimed for. `"GTR"` / `"FRON"` / `"SGTR"` / `null` |
| `candidates` | | List of candidates `[{"x":0,"r":"UP","score":1}]` |

### analyze

The request is `{"mode":"analyze","field":…,"queue":…}` (`queue` has at least 2 pairs). Answer:

```json
{"immediate":{"count":0,"score":0,"garbage":0,"x":0,"r":"UP","frame":0},
 "max":{"count":2,"score":360,"garbage":5,"x":3,"r":"LEFT","frame":1},
 "harass":{"count":2,"score":360,"garbage":5,"x":3,"r":"LEFT","frame":1},
 "potential":0,"garbage_max":5}
```

| Field | Required | Meaning |
|---|---|---|
| `immediate` | yes | The largest chain that can be fired with the current pair |
| `max` | yes | The largest chain that can be fired with the visible pairs |
| `harass` | yes | The largest chain of 3 steps or fewer |
| `potential` | yes | Expected score of the future main chain (0 if unknown) |
| `garbage_max` | yes | `max` converted to garbage |
| `fire_points` | | Places where adding one puyo starts a chain `[{"x":0,"y":3,"color":"R","count":4,"score":2000}]` (highest score first) |
| `potential_chain` | | Number of steps of the chain being built |

All fields of a chain are required: `count` steps (0 if none), `score`, `garbage`, `x` and `r` where the first pair is placed, `frame` the turn in which it fires (0 = the current pair).

### battle

Request:

```json
{"mode":"battle","target_point":70,
 "self":{"field":["…13 rows…"],"queue":["RG","BY"],"attack":0,"attack_frame":0,"dropping":0,
         "all_clear":false,"bonus":0,"chain_bonus":false},
 "enemy":{"field":["…13 rows…"],"queue":["GG","RY"],"attack":0,"attack_frame":0,"dropping":0,
          "all_clear":false,"bonus":0,"chain_bonus":false}}
```

| Field | Meaning |
|---|---|
| `target_point` | Score needed per garbage puyo (starts at 70 and decreases over time) |
| `enemy_unknown` | If `true`, the opponent could not be read and `enemy` is a temporary empty board |
| `field`, `queue` | Board and pairs of that player |
| `attack` | Pending garbage that will **fall on that player** |
| `attack_frame` | Rough number of turns until the attacking side's chain ends (0 = not attacked) |
| `dropping` | Garbage currently falling |
| `all_clear` | Has an all-clear bonus |
| `bonus` | Score added to the next chain |
| `chain_bonus` | The chain in progress carries an all-clear bonus |
| `chain_field` | May be added only during a chain: the board just before firing (13 rows) |
| `attack_frame_est` | May be added: estimated number of turns the opponent can place until the chain ends |
| `carry`, `history`, `placed` | May be added: leftover attack score, pairs so far, number of pairs placed |

Answer:

```json
{"x":2,"r":"UP","eval":1200,"axis":[2,0],"child":[2,1],"label":"build",
 "self":{"immediate":{…},"max":{…},"harass":{…},"potential":0,"garbage_max":0},
 "enemy":{"immediate":{…},"max":{…},"harass":{…},"potential":0,"garbage_max":0},"ms":40}
```

| Field | Required | Meaning |
|---|---|---|
| `x`, `r`, `axis`, `child` | yes | Same as build |
| `eval` | yes | Evaluation (integer) |
| `label` | yes | Meaning of the move: `build` / `main` fire the main chain / `harass` / `accept` take the attack / `all_clear` / `prepare` prepare to fire |
| `self`, `enemy` | yes | The analyze answer for each board |
| `ms`, `form`, `field_after` | | Same as build |
| `chain_after` | | The chain this move starts `{"count":5,"score":4840}` |
| `plan_steps` | | For an attack that includes preparing moves, the number of turns until it fires |
| `incoming`, `incoming_chain` | | Estimated garbage that will fall on you, and the steps of that chain |

### Errors

```json
{"error":"no valid placement"}
```

- If there is nowhere to place, always return `no valid placement`. The dock clears the suggestion and asks again next turn
- Any other `error` stops assist mode as an engine failure
- The board is read from the screen and may contain floating puyos or four connected puyos. Do not crash

## 5. Troubleshooting

Error messages are in Japanese. The quoted part is what to look for.

| Symptom | Cause and what to do |
|---|---|
| "(not found)" in the list | The exe was moved, deleted or quarantined. Register it again |
| "5.0秒間応答がありませんでした" (Tetris: no response for 5 seconds) | `info` is not sent by the engine, or it is not flushed |
| "TBP 互換…の思考エンジンではない可能性" (may not be a TBP engine) | The first line is not `info`. Something other than JSON is written to standard output |
| "ルールを受理しませんでした" (rules not accepted) | `ready` is not returned for `rules` |
| "60.0秒間応答がありませんでした" (Puyo: no response for 60 seconds) | `{"ready":true}` is not sent, or it is not flushed |
| "プロセスが終了しました" (the process exited) | The engine crashed: a missing DLL, wrong arguments, unknown `--config` and so on. Start the exe directly from PowerShell to see the reason |
| It is registered but no dots appear | The coordinates differ from the specification (Tetris: center and y from the bottom, Puyo: y from the bottom). Moves that cannot be placed are not shown |

To check the behavior by hand, do this in PowerShell (example for the Puyo sample).

```powershell
'{"field":["......","......","......","......","......","......","......","......","......","......","......","......","......"],"queue":["RG","BY"]}', 'quit' | python -u docs/samples/puyo_engine_sample.py
```
