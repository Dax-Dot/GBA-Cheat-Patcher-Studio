# GBA Cheat Patcher Studio

**Maintainer:** Dax-Dot

GBA Cheat Patcher Studio is a Windows desktop tool for patching supported CodeBreaker cheats directly into Game Boy Advance ROM backups.

The app detects a loaded `.gba` ROM by CRC32, shows matching cheat entries, lets you select supported cheats, and creates a patched ROM.

## Download

For most users, download the portable Windows ZIP from the latest release:

- Go to **Releases**
- Download `GBA-Cheat-Patcher-Studio-v1.0-Windows-Portable.zip`
- Extract the ZIP
- Run `GBA-Cheat-Patcher-Studio.exe`

Do not move the EXE away from the `_internal` folder.

## Features

- Automatic GBA ROM detection by CRC32
- Cheat matching using bundled CodeBreaker data
- ROM metadata matching using bundled No-Intro CRC data
- Supported cheat selection with checkboxes
- Code preview for listed cheats
- Manual CodeBreaker cheat entry
- Light and dark theme support
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

Not supported yet:

- Codes starting with **4**: slide / repeated-write codes
- Codes starting with **5**: super / multi-line block codes
- Codes starting with **7, A, B, C, F**: conditional codes
- Codes starting with **D**: button activators
- Codes starting with **0, 1, 9**: master / enabler / encryption / metadata
- Codes containing **??**: user value required

## Technical Notes

The app currently uses this patching profile:

- early hook 1
- VBlank off
- constant Always-ON writes
- no visual trainer menu

Known limitations:

- The hook wrapper re-executes the 3 original instructions from a different address. If those instructions are PC-relative loads, the effective address may differ.
- The hook wrapper uses the top 36 bytes of EWRAM (`0x0203FFDC-0x02040000`) as a register scratch area. ROMs that use this exact range for their own data may crash.

## Run From Source

Requirements:

- Windows 10/11 recommended
- Python 3.8 to 3.13 recommended
- No third-party packages required to run from source

Run:

```bat
run_gba_cheat_studio.bat
```

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

See `ATTRIBUTIONS.md` for source notices, upstream links, and contact/removal request information.

## License

Project source code is licensed under GPL-3.0-only. See `LICENSE`.

Bundled data and third-party-origin material remain attributed to their respective upstream sources.

