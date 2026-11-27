# Installing Universal Engine Dock

[日本語](INSTALL.md) | **English**

How to install Universal Engine Dock, which shows the moves of Puyo Puyo and Tetris engines (AI) on Puyo Puyo Tetris 2, on Windows and start assist mode.
To add your own or public engines, see [ENGINE_SPEC.en.md](ENGINE_SPEC.en.md).

## 1. What you need

| Item | Details |
|---|---|
| OS | Windows 10 (version 2004 or later) / 11, 64-bit |
| Game | Puyo Puyo Tetris 2 on Steam (windowed) |
| CPU | The bundled YamaNEKO needs BMI2 instructions (roughly 4th-gen Intel Core or later, or AMD Ryzen or later) |
| Rights | Administrator rights are not needed |

You do not need to install Python or anything else.

## 2. Install

1. Download `UniversalEngineDock-<version>.zip` from [Releases](https://github.com/fiss-vinbows/Universal_Engine_Dock/releases)
2. Check that the zip is genuine (optional). Run this in PowerShell and compare the result with the SHA-256 on the release page

   ```powershell
   Get-FileHash .\UniversalEngineDock-*.zip
   ```

3. Once checked, right-click the zip **before extracting** → "Properties" → tick "Unblock" → "OK"
   (the files inside are then no longer treated as files from the internet, and warnings become less likely. If there is no such item, skip this)
4. Extract the zip anywhere you like. Settings are written to `config/` in the same folder, so avoid `Program Files`
5. Run `UniversalEngineDock.exe`. It does not work if you move the exe alone (make a shortcut if you want it on the desktop)

### If you see a warning

| What you see | What to do |
|---|---|
| "Windows protected your PC" (SmartScreen) | "More info" → "Run anyway" |
| Norton or another product stops it for analysis | Wait for the analysis and start it again. Engine exes (inside `engines/`) may also be analyzed the first time |
| The exe was quarantined | After checking that the SHA-256 in step 2 matches, restore it in your security software and exclude the extracted folder |

If the SHA-256 does not match, do not use the file. Download it again from Releases.

### Switch the display to English

The app starts in Japanese. At the top right of the main window, choose "English" from "表示の言語 (Language):".
The main window, settings, simulators and the display on top of the game change to English. Logs, settings files and some engine error messages stay in Japanese.

## 3. Start assist mode

1. Start the game **in a window**. With a 16:9 size, the board position is found automatically
   (choose a "Game window size" and press "Resize to this" to set that size)
2. Press "Detect game window". If it is not found, choose it from the list (your choice is remembered)
3. Choose the "Versus mode" or "Solo mode" tab
4. For each side (1P and 2P, or solo), choose a game (Puyo / Tetris / No assist) and an engine
5. Press "Start assist mode". Preparing the engine may take tens of seconds
6. To stop, press "Stop assist mode" shown on top of the game

The main window is hidden during assist mode. If you move the game window, assist mode stops, so start it again.

### What you see

- **Dots** — where to place the next piece
- **Information panel** — what the engine is aiming for, expected chains, and so on

These are not captured by screen capture. For streaming and recording, use "Show on OBS (for streaming/recording)".

### Using a capture board

If there is no game window, press "Puyo calibration (capture board)" or "Tetris calibration (capture board)" and click the positions of the board, hold, next and so on as guided.
Depending on the colors and borders of the video, it may not be read correctly.

### Adding plugins

1. Press "Plugins…" and open `plugins/` with "Open folder"
2. Put the plugin's folder (the one with `plugin.py` inside) into `plugins/` as it is
3. Press "Save and restart". Universal Engine Dock closes, starts again and loads the plugin

Plugins are loaded at start, so putting one in the folder is not enough. If you open "Plugins…" after putting it there, the dock tells you so.

To load a plugin kept outside the Universal Engine Dock folder, choose its folder with "Add location…" in "Plugins…" (the plugin folder itself, or a folder that contains plugin folders). The added locations are kept in `config/plugin_folders.json`.

## 4. Troubleshooting

| Symptom | What to check |
|---|---|
| The window is not found | Is the game windowed? Choose it from the list of "Detect game window" |
| The size cannot be changed | Is it full-screen? Is only the game running as administrator? |
| The dots are misplaced | Is the window 16:9? Is another window covering the game? |
| An engine shows "(not found)" | Was the exe in `engines/` quarantined by security software? |
| "Already running" | It cannot be started twice. Choose both 1P and 2P in the main window |
| It is slow | For Puyo, reduce "CPU use" or turn on "Lower the engine's priority" |
| It crashes | See `crash_log.txt` in the same folder as the exe |

When you report a problem, turn on "Write recognition results and suggestions to a log (debug_logs/)" and reproduce it. A record is left in `debug_logs/`.
**The log may contain screenshots, so look through it before giving it to anyone.**

## 5. Updating and removing

- **Update** — extract the new zip to another folder and copy `config/` if you want to keep your settings.
  The bundled engines in the new folder are used (engines you replaced with "Change…" keep pointing where you chose)
- **Update only an engine** — select it in "Register engines…" and press "Change…" ([details](ENGINE_SPEC.en.md#replacing-a-bundled-engine-with-a-new-version))
- **Remove** — delete the whole folder. Nothing is written elsewhere, including the registry
