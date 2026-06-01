# GBA Cheat Patcher Studio v1.0

**Maintainer / Creator:** Dax-Dot
A simple Windows desktop tool for patching supported CodeBreaker cheats into Game Boy Advance ROMs.

The app detects a loaded `.gba` ROM by CRC32, shows matching cheat entries, and creates a patched ROM using the currently supported direct-write CodeBreaker codes.

## Features

- Automatic GBA ROM detection by CRC32.
- ROM metadata matching using No-Intro.org data.
- Cheat matching using GameHacking.org CodeBreaker data.
- Supported cheat selection with checkboxes.
- Code preview buttons for each listed cheat.
- Manual CodeBreaker cheat entry for games or cheats not found in the database.
- Light/Dark theme toggle.
- Custom app icon, About dialog, and portable Windows EXE build script.

## Current patching profile

The app creates a patched ROM using the recommended profile:

- early hook 1
- VBlank off
- constant Always-ON writes
- no visual trainer menu

This profile works well for many games, but it is not guaranteed for every ROM.

## Supported CodeBreaker types

Currently supported:

- Codes starting with **3**: 8-bit RAM write
- Codes starting with **8**: 16-bit RAM write
- Codes starting with **2**: 16-bit OR write
- Codes starting with **6**: 16-bit AND write

Not supported yet:

- Codes starting with **4**: slide / repeated-write codes
- Codes starting with **5**: super / multi-line block codes
- Codes starting with **7, A, B, C, F**: conditional codes
- Codes starting with **D**: button activators
- Codes starting with **0, 1, 9**: master / enabler / encryption / metadata
- Codes containing **??**: user value required

## Compatibility warning

The default hook profile is generic. Some games may crash, freeze, show visual glitches, or simply not apply cheats. If a patched ROM behaves badly, stop using that patched ROM and try fewer cheats or another game.

Known technical limitations:

- The hook wrapper re-executes the 3 original instructions from a different address. If those instructions are PC-relative loads, the effective address may differ.
- The hook wrapper uses the top 36 bytes of EWRAM (`0x0203FFDC-0x02040000`) as a register scratch area. ROMs that use this exact range for their own data may crash.

## Data sources

- Cheats: GameHacking.org
- ROM metadata: No-Intro.org

This tool does **not** include ROMs or BIOS files. Use only with legally obtained backups.

See `ATTRIBUTIONS.md` for source notices, upstream links, and contact/removal request information.

## Development note

This project was created with the help of AI-assisted / vibe coding. Human testing, review, packaging, and release decisions are still required.

## License

Project source code is licensed under GPL-3.0-only. See `LICENSE`.

Bundled data and third-party-origin material remain attributed to their respective upstream sources. If you are a rights holder or upstream maintainer and believe something is misattributed or should not be redistributed, please open a GitHub issue or contact the maintainer so it can be reviewed and fixed.

## Requirements

- Windows 10/11 recommended
- Python 3.8 to 3.13 recommended
- No third-party packages required to run from source
- Build only: PyInstaller from `requirements-build.txt`
- Official Python for Windows from python.org is recommended for building, because it includes Tcl/Tk support required by the Tkinter GUI.

## Run from source

```bat
run_gba_cheat_studio.bat
```

## Build portable EXE

```bat
build_windows_exe.bat
```

The build script creates a portable folder at:

```text
dist\GBA-Cheat-Patcher-Studio\
```

Zip that folder for distribution. **Do not share only the EXE**; the bundled database and assets must be included.

The build script creates a local `.venv-build` folder and installs the pinned build dependency there. This keeps PyInstaller isolated from the user's global Python installation.
