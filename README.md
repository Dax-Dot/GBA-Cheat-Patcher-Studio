# GBA Cheat Patcher Studio

**Maintainer:** Dax-Dot

GBA Cheat Patcher Studio is a desktop tool for patching supported CodeBreaker cheats directly into Game Boy Advance ROM backups.

The app detects a loaded `.gba` ROM by CRC32, shows matching cheat entries, lets you select supported cheats, and creates a patched ROM.

It is built and distributed for Windows, and the source can also be run on macOS and Linux (see **Run From Source**).

## Download

For most users, download the portable Windows ZIP from the latest release:

- Go to **Releases**
- Download the latest `GBA-Cheat-Patcher-Studio-vX.Y-Windows-Portable.zip`
- Extract the ZIP
- Run `GBA-Cheat-Patcher-Studio.exe`

Do not move the EXE away from the `_internal` folder.

The portable EXE is Windows-only. On macOS or Linux, run from source instead.

## Features

- Automatic GBA ROM detection by CRC32
- Cheat matching using bundled CodeBreaker data
- ROM metadata matching using bundled No-Intro CRC data
- Supported cheat selection with checkboxes
- Type 7 conditional cheats (beta), opt-in
- Code preview for listed cheats
- Manual CodeBreaker cheat entry
- Light and dark theme support
- Native system fonts on Windows, macOS and Linux
- Portable Windows build from source

## Important Notice

This tool does **not** include ROMs or BIOS files. Use only with legally obtained backups.

Compatibility is not guaranteed for every game. Some patched ROMs may crash, freeze, show glitches, or fail to apply cheats. If a patched ROM behaves badly, stop using it and try fewer cheats or another game.

## Supported CodeBreaker Types

Currently supported:

- Codes starting with **3**: 8-bit RAM write
- Codes starting with **8**: 16-bit RAM write
- Codes starting with **2**: 16-bit OR write
- Codes starting with **6**: 16-bit AND write

### Type 7 conditional cheats (beta)

The app can also apply a safe subset of **Type 7** conditional codes, disabled by default.

A supported Type 7 cheat is a two-line pair: one Type 7 condition line (`IF [address] == value`) followed by one already-supported write line (type 3, 8, 2 or 6). When the condition is true, the write is applied; otherwise it is skipped. Anything more complex (chained conditions, button activators such as `74000130`, blocks larger than two lines, or conditions over unsupported code types) is intentionally left out.

To enable it, tick **"Include conditional cheats (Type 7, beta)"** in the cheat list after a ROM is detected. Because it is beta, always test the patched ROM in an emulator before relying on it.

Not supported yet:

- Codes starting with **4**: slide / repeated-write codes
- Codes starting with **5**: super / multi-line block codes
- Codes starting with **A, B, C, F**: other conditional codes
- Complex or multi-line Type 7 blocks (only the two-line pair above is supported)
- Codes starting with **D**: button activators
- Codes starting with **0, 1, 9**: master / enabler / encryption / metadata
- Codes containing **??**: user value required

## Technical Notes

The app uses an Always-ON runtime RAM-write engine injected via an ARM hook, with no visual trainer menu. The recommended profile uses a single hook with the VBlank guard off and constant writes.

Type 7 conditional pairs reuse the same hook and engine: the conditional logic is emitted as a small ARM block (read halfword, compare, branch over the write if the condition is false), so they add no new hook behavior.

Known limitations:

- The hook wrapper re-executes the original instructions from a different address. If those instructions are PC-relative loads, the effective address may differ.
- The hook wrapper uses a small EWRAM scratch area for registers. ROMs that use that exact range for their own data may crash.

## Run From Source

Requirements:

- Windows 10/11, macOS, or Linux
- Python 3.8 to 3.13 recommended
- Tk/tkinter available (bundled with the official Python installers; on some Linux distros install the `python3-tk` package)
- No third-party packages required to run from source

On Windows:

```bat
run_gba_cheat_studio.bat
```

On macOS or Linux:

```sh
python3 gba_cheat_patcher_studio.py
```

The UI automatically selects a native system font on each platform (Segoe UI / Consolas on Windows, Helvetica Neue / Menlo on macOS, DejaVu Sans / DejaVu Sans Mono on Linux), with safe fallbacks, so text renders correctly everywhere.

## Build Portable EXE

Requirements:

- Official Python for Windows from python.org recommended
- Internet connection for the first build

Build:

```bat
build_windows_exe.bat
```

The build script creates a local `.venv-build` folder, installs the pinned PyInstaller version, and creates:

```text
dist\GBA-Cheat-Patcher-Studio\
```

Zip that entire folder for distribution. Do not share only the EXE.

## Data Sources

- Cheats: GameHacking.org
- ROM metadata: No-Intro.org

The bundled cheat database is deduplicated: cheats that share an identical code within the same game are collapsed to a single entry, keeping the most descriptive title. Cheats with different codes (even if similarly named) are preserved.

See `ATTRIBUTIONS.md` for source notices, upstream links, and contact/removal request information.

## License

Project source code is licensed under GPL-3.0-only. See `LICENSE`.

Bundled data and third-party-origin material remain attributed to their respective upstream sources.
