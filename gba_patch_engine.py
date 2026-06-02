#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""
GBA Cheat Patcher Studio v1.0 - Patch Engine

Applies supported CodeBreaker cheats (types 3, 8, 2, 6) to GBA ROMs by
injecting a minimal Always-ON RAM-write engine via an ARM hook.

Not a full GBAATM replacement:
- no trainer menu, no YES/NO toggles
- only simple types 3, 8, 2, 6 are supported
- recommended mode: early hook 1, VBlank off, constant Always-ON writes
"""
from __future__ import annotations

import argparse
import binascii
import json
import os
import re
import struct
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

APP = "GBA Cheat Patcher Studio"
VERSION = "1.0"
SUPPORTED_TYPES = {"3", "8", "2", "6"}
GBA_ROM_BASE = 0x08000000
MIN_SAFE_HOOK_OFFSET = 0x1000
SIZEOF_HOOK_JUMP_WORDS = 15
MAX_ROM_SIZE = 32 * 1024 * 1024

# ARM helper snippets adapted from GBAATM-Rebirth's hook wrapper style.
VBLANK_WORDS = [
    0xE59F1010,  # ldr r1, [pc, #0x10]
    0xE5910000,  # ldr r0, [r1]
    0xE20000FF,  # and r0, r0, #0xff
    0xE35000A0,  # cmp r0, #0xa0
    0xAA000001,  # bge +1
    0xE12FFF1E,  # bx lr
    0x04000006,  # VCOUNT
]

EXEC_EVERY_WORDS = [
    0xE59F101C,
    0xE5D12003,
    0xE3A03000,
    0xE2822001,
    0xE1520003,
    0x03A02000,
    0xE5C12003,
    0x0A000001,
    0xE12FFF1E,
    0xFFFFFFFF,  # patched with RAM address
]


@dataclass
class CheatLine:
    raw: str
    address: int
    value: int
    type: str
    message: str


@dataclass
class SelectedCheat:
    title: str
    lines: List[CheatLine]




KNOWN_WORKING_PRESETS = {
    "AMKE": {
        "title": "Mario Kart - Super Circuit (USA, Europe)",
        "profile": "early_hook1_vblank_off_constant",
        "notes": "Confirmed: Max Coins works; Always Have Star works when pressing item button, even if item box may not display it.",
    },
    "AX4E": {
        "title": "Super Mario Advance 4 - Super Mario Bros. 3 (USA)",
        "profile": "early_hook1_vblank_off_constant",
        "notes": "Confirmed: 99 lives and 98 coins work. Safe hook variants did not apply cheats.",
    },
    "A2CE": {
        "title": "Castlevania - Aria of Sorrow (USA)",
        "profile": "early_hook1_vblank_off_constant",
        "notes": "Confirmed: Infinite Magic Power and Infinite Money work with recommended mode.",
    },
}



KNOWN_PARTIAL_GAMES = {
    "DK": "Donkey Kong Country games may apply cheats with visual glitches or fail with safe hooks. Use with caution.",
}

def partial_warning_for_title(title: str) -> Optional[str]:
    t = title.lower()
    if "donkey kong country" in t:
        return KNOWN_PARTIAL_GAMES["DK"]
    return None

RECOMMENDED_PROFILE_NAME = "early_hook1_vblank_off_constant"


def default_cheat_suffix(cheat_ids: List[int]) -> str:
    """Return filename suffix like CHT.3.13 using the DB cheat IDs the user selected."""
    clean_ids = []
    for cid in cheat_ids:
        try:
            clean_ids.append(str(int(cid)))
        except Exception:
            pass
    if not clean_ids:
        return "CHT"
    return "CHT." + ".".join(clean_ids)

@dataclass
class HookCandidate:
    offset: int
    hook_type: int


def log_print(lines: List[str], msg: str = "") -> None:
    if getattr(sys, "stdout", None) is not None:
        try:
            print(msg)
        except Exception:
            pass
    lines.append(msg)


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def crc32_file(path: Path) -> str:
    crc = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            crc = binascii.crc32(chunk, crc)
    return f"{crc & 0xFFFFFFFF:08X}"


def gba_header_info(data: bytes) -> Dict[str, str]:
    def clean(b: bytes) -> str:
        return b.decode("ascii", errors="ignore").strip("\x00 ")
    return {
        "title": clean(data[0xA0:0xAC]) if len(data) >= 0xC0 else "",
        "game_code": clean(data[0xAC:0xB0]) if len(data) >= 0xC0 else "",
        "maker_code": clean(data[0xB0:0xB2]) if len(data) >= 0xC0 else "",
    }


def u32_list(data: bytes) -> List[int]:
    pad = (-len(data)) % 4
    if pad:
        data += b"\x00" * pad
    return list(struct.unpack("<" + "I" * (len(data) // 4), data))


def write_u32(data: bytearray, offset: int, value: int) -> None:
    data[offset:offset+4] = struct.pack("<I", value & 0xFFFFFFFF)


def read_u32(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def find_rom_end(data: bytes) -> int:
    """Return offset of the last meaningful byte, conservative padding trim."""
    if not data:
        return 0
    i = len(data) - 1
    # Most GBA ROMs are padded with 0x00 or 0xFF. Trim trailing runs only.
    while i > 0 and data[i] in (0x00, 0xFF):
        i -= 1
    return i


def align4(x: int) -> int:
    return (x + 3) & ~3


def arm_branch(from_offset: int, to_offset: int, link: bool = False) -> int:
    """Create ARM B/BL from file offset to file offset."""
    # ARM branch target = PC + 8 + imm24*4
    diff_words = (to_offset - (from_offset + 8)) // 4
    if not -(1 << 23) <= diff_words < (1 << 23):
        raise ValueError("ARM branch target out of range")
    opcode = 0xEB000000 if link else 0xEA000000
    return opcode | (diff_words & 0x00FFFFFF)


def scan_hooks(data: bytes, log: List[str]) -> List[HookCandidate]:
    words = u32_list(data)
    candidates: List[HookCandidate] = []
    max_i = max(0, len(words) - 8)
    for i in range(max_i):
        hook_type = 0
        temp_hook_offset = 0
        w0 = words[i]
        w1 = words[i+1]
        w2 = words[i+2]
        # Type 1
        if ((w0 & 0xffff0fff) == 0xe3a00301 and
            (w1 & 0xfff00fff) == 0xe2800c02 and
            (w2 & 0xfff00fff) == 0xe5d00008 and
            (w2 & 0xffff0000) != 0xe59f0000):
            temp_hook_offset = i * 4
            hook_type = 1
        # Type 2
        if ((w0 & 0xffff0fff) == 0xe3a00301 and
            (w1 & 0xfff00fff) == 0xe2800c02 and
            (w2 & 0xfff00fff) == 0xe5900000 and
            (w2 & 0xffff0000) != 0xe59f0000):
            temp_hook_offset = i * 4
            hook_type = 2
        # Type 3
        if ((w0 & 0xffff0000) == 0xe92d0000 and
            (w1 & 0xffff0fff) == 0xe3a00301 and
            (w2 & 0xfff00fff) == 0xe5b00200 and
            (words[i+3] & 0xffff0000) != 0xe59f0000):
            temp_hook_offset = (i + 1) * 4
            hook_type = 3
        # Type 4
        if ((w0 & 0xffff0fff) == 0xe3a00640 and
            (w1 & 0xfff00fff) == 0xe5b00200 and
            (w2 & 0xfff00000) == 0xe1d00000 and
            (words[i+5] & 0xffff0000) != 0xe59f0000 and
            (words[i+6] & 0xffff0000) != 0xe59f0000 and
            (words[i+7] & 0xffff0000) != 0xe59f0000):
            temp_hook_offset = (i + 5) * 4
            hook_type = 4
        # Type 5
        if ((w0 & 0xffff0fff) == 0xe3a00301 and
            (w1 & 0xfff00fff) == 0xe5b00200 and
            (w2 & 0xfff00fff) == 0xe1d000b8):
            temp_hook_offset = i * 4
            hook_type = 5
        # Type 6
        if ((w0 & 0xffff0000) == 0xe59f0000 and
            (w1 & 0xfff000ff) == 0xe5900000 and
            (w1 & 0xffff0000) != 0xe59f0000 and
            (w2 & 0xffff0000) == 0xe1a00000 and
            (words[i+3] & 0xffff0000) != 0xe59f0000):
            temp_hook_offset = (i + 1) * 4
            hook_type = 6

        if temp_hook_offset:
            # Avoid nearby conditional branch operands, same spirit as GBAATM.
            unsafe = False
            base = temp_hook_offset // 4
            for opctr in range(4):
                if base + opctr < len(words):
                    op = words[base + opctr]
                    if ((op & 0x000F0000) == 0x000D0000) or ((op & 0x0000F000) == 0x0000D000):
                        unsafe = True
                        break
            if unsafe:
                continue
            candidates.append(HookCandidate(temp_hook_offset, hook_type))
    return candidates




def classify_cheat_behavior(title: str, lines: List[CheatLine], profile: str = "auto") -> str:
    """Return behavior mode for a cheat: constant or refill_if_zero.

    auto profile keeps counters as constant writes, but treats common item-slot cheats
    as refill-if-empty so they do not continuously overwrite active item state.
    """
    if profile == "constant":
        return "constant"
    if profile == "refill":
        return "refill_if_zero"
    t = title.lower()
    item_words = [
        "always have", "single green", "triple green", "single red", "triple red",
        "blue shell", "banana", "mushroom", "star", "ghost", "lightening", "lightning",
        "babomb", "bob-omb", "item"
    ]
    counter_words = ["coin", "money", "retry", "retries", "lives", "life", "time", "health", "hp", "ammo"]
    if any(w in t for w in counter_words):
        return "constant"
    if any(w in t for w in item_words):
        return "refill_if_zero"
    # If a 16-bit type 8 writes into likely item/state RAM, prefer constant unless title hints item.
    return "constant"


def append_refill_if_zero(out: List[int], line: CheatLine) -> None:
    """Append ARM code that writes only if the current target value is zero.

    This is useful for item-slot cheats: refill the item when empty, but do not
    overwrite the game's active item state every frame while the item is being used.
    """
    addr = line.address & 0x0FFFFFFF
    val = line.value & 0xFFFF
    t = line.type.upper()
    if t == "3":
        # byte: if (*(u8*)addr == 0) *(u8*)addr = val
        out.extend([
            0xE59F1010,  # ldr r1, [pc, #0x10] -> addr
            0xE5D10000,  # ldrb r0, [r1]
            0xE3500000,  # cmp r0, #0
            0xE3A02000 | (val & 0xFF),  # mov r2, #val
            0x05C12000,  # strbeq r2, [r1]
            0xEA000000,  # skip addr literal
            addr,
        ])
        return
    if t == "8":
        # halfword: if (*(u16*)addr == 0) *(u16*)addr = val
        out.extend([
            0xE59F1010,  # ldr r1, [pc, #0x10] -> addr literal at word 6
            0xE1D100B0,  # ldrh r0, [r1]
            0xE3500000,  # cmp r0, #0
            0xE59F2008,  # ldr r2, [pc, #8] -> value literal at word 7
            0x01C120B0,  # strheq r2, [r1]
            0xEA000001,  # skip addr + value literals
            addr,
            val,
        ])
        return
    # OR/AND codes are not good refill-if-zero candidates; fall back to constant behavior.
    # Caller should avoid sending type 2/6 here, but keep safe fallback.
    raise ValueError(f"refill_if_zero is not supported for type {t}")

# --- Type 7 conditional support (beta) ---------------------------------------
#
# CodeBreaker conditional families, per GBATEK:
#   7aaaaaaa yyyy : IF [aaaaaaa] == yyyy THEN (next code)
#   Aaaaaaaa yyyy : IF [aaaaaaa] <> yyyy THEN (next code)
#   Baaaaaaa yyyy : IF [aaaaaaa]  > yyyy THEN (next code)  (signed)
#   Caaaaaaa yyyy : IF [aaaaaaa]  < yyyy THEN (next code)  (signed)
#
# This engine implements ONLY the safe 2-line pattern:
#   line 1: type 7 (IF equal) condition
#   line 2: a single already-supported write of type 3/8/2/6
#
# The condition is read as a 16-bit halfword from the target address, matching
# the most common CodeBreaker usage where the conditional value is yyyy (16-bit).
# If the comparison fails, the conditional write is skipped this frame; other
# (unconditional) cheats in the same engine are unaffected.
CONDITIONAL_TYPES = {"7"}


def _arm_conditional_write_words(cond: CheatLine, write: CheatLine) -> List[int]:
    """Emit ARM that performs `write` only if [cond.address] (u16) == cond.value.

    Layout (PC-relative literals placed right after the block, skipped by a branch):
        ldr   r1, [pc, #..]      ; r1 = cond addr
        ldrh  r0, [r1]           ; r0 = current value
        ldr   r2, [pc, #..]      ; r2 = expected value
        cmp   r0, r2
        bne   <skip writes>      ; condition false -> skip
        <inline write to write.address>
      skip:
        b     <past literals>
        .word cond_addr
        .word cond_value
        (.word write literals as needed)

    To keep things simple and robust we build the inner write body first, then
    compute the conditional branch offset over it.
    """
    cond_addr = cond.address & 0x0FFFFFFF
    cond_val = cond.value & 0xFFFF
    t = write.type.upper()
    waddr = write.address & 0x0FFFFFFF
    wval = write.value & 0xFFFF

    # Build the write body as self-contained words (same shapes as the constant
    # writers below, but always using PC-relative literals so the body length is
    # deterministic and easy to branch over).
    body: List[int] = []
    if t == "3":
        body = _arm_write8_pcrel(waddr, wval)
    elif t == "8":
        body = _arm_write16_pcrel(waddr, wval)
    elif t in ("2", "6"):
        body = _arm_writelogic16_pcrel(waddr, wval, is_or=(t == "2"))
    else:
        raise ValueError(f"Type {t} not allowed as conditional write target")

    n_body = len(body)
    # Conditional prologue. Literals (cond_addr, cond_val) live AFTER everything,
    # reached via a final unconditional branch.
    # Words:
    #   0: ldr r1, [pc, #off_addr]
    #   1: ldrh r0, [r1]
    #   2: ldr r2, [pc, #off_val]
    #   3: cmp r0, r2
    #   4: bne skip            (skip the body -> jump to word 5+n_body)
    #   5 .. 5+n_body-1: body
    #   5+n_body: b past_literals
    #   6+n_body: .word cond_addr
    #   7+n_body: .word cond_val
    out: List[int] = [0] * (8 + n_body)
    addr_literal_index = 6 + n_body
    val_literal_index = 7 + n_body

    # ldr r1, [pc, #imm] ; pc = (index0+2)*4 base. imm = (addr_literal_index-0-2)*4
    out[0] = 0xE59F1000 | (((addr_literal_index - 0 - 2) * 4) & 0xFFF)
    out[1] = 0xE1D100B0          # ldrh r0, [r1]
    out[2] = 0xE59F2000 | (((val_literal_index - 2 - 2) * 4) & 0xFFF)
    out[3] = 0xE1500002          # cmp r0, r2
    # bne skip: branch to word (5 + n_body). From word 4: target_words = (5+n_body)-(4+2)
    out[4] = 0x1A000000 | (((5 + n_body) - (4 + 2)) & 0x00FFFFFF)
    for k, w in enumerate(body):
        out[5 + k] = w
    # b past_literals: from word (5+n_body) jump to word (8+n_body)
    branch_word = 5 + n_body
    out[branch_word] = 0xEA000000 | (((8 + n_body) - (branch_word + 2)) & 0x00FFFFFF)
    out[addr_literal_index] = cond_addr
    out[val_literal_index] = cond_val
    return out


def _arm_write8_pcrel(addr: int, val: int) -> List[int]:
    """Self-contained 8-bit write using only PC-relative literals.

    Layout (word indices):
        0: mov  r0, #val
        1: ldr  r1, [pc, #imm]   -> word4 (addr) ; imm=(4-1-2)*4=4
        2: strb r0, [r1]
        3: b    +0               -> word4..end is data; jump to word4? no: jump past literal -> word5
        4: .word addr
    """
    return [
        0xE3A00000 | (val & 0xFF),
        0xE59F1000 | (((4 - 1 - 2) * 4) & 0xFFF),  # ldr r1,[pc,#imm] -> word4
        0xE5C10000,                                # strb r0, [r1]
        0xEA000000 | (((5 - 3 - 2) * 4 // 4) & 0x00FFFFFF),  # b -> word5 (past literal)
        addr & 0x0FFFFFFF,
    ]


def _arm_write16_pcrel(addr: int, val: int) -> List[int]:
    """Self-contained 16-bit write using only PC-relative literals.

    Layout (word indices):
        0: ldr  r0, [pc, #imm]   -> word4 (val)  ; imm=(4-0-2)*4=8
        1: ldr  r1, [pc, #imm]   -> word5 (addr) ; imm=(5-1-2)*4=8
        2: strh r0, [r1]
        3: b    -> word6 (past both literals)
        4: .word val
        5: .word addr
    """
    return [
        0xE59F0000 | (((4 - 0 - 2) * 4) & 0xFFF),  # ldr r0,[pc,#8] -> val
        0xE59F1000 | (((5 - 1 - 2) * 4) & 0xFFF),  # ldr r1,[pc,#8] -> addr
        0xE1C100B0,                                # strh r0, [r1]
        0xEA000000 | ((6 - 3 - 2) & 0x00FFFFFF),   # b -> word6
        val & 0xFFFF,
        addr & 0x0FFFFFFF,
    ]


def _arm_writelogic16_pcrel(addr: int, val: int, is_or: bool) -> List[int]:
    """Self-contained 16-bit OR/AND using only PC-relative literals.

    Layout (word indices):
        0: ldr  r1, [pc, #imm]   -> word6 (addr) ; imm=(6-0-2)*4=16=0x10
        1: ldr  r2, [pc, #imm]   -> word7 (val)  ; imm=(7-1-2)*4=16=0x10
        2: ldrh r0, [r1]
        3: orr/and r0, r0, r2
        4: strh r0, [r1]
        5: b    -> word8 (past both literals)
        6: .word addr
        7: .word val
    """
    op = 0xE1800002 if is_or else 0xE0000002  # orr/and r0, r0, r2
    return [
        0xE59F1000 | (((6 - 0 - 2) * 4) & 0xFFF),  # ldr r1,[pc,#0x10] -> addr
        0xE59F2000 | (((7 - 1 - 2) * 4) & 0xFFF),  # ldr r2,[pc,#0x10] -> val
        0xE1D100B0,                                # ldrh r0, [r1]
        op,
        0xE1C100B0,                                # strh r0, [r1]
        0xEA000000 | ((8 - 5 - 2) & 0x00FFFFFF),   # b -> word8
        addr & 0x0FFFFFFF,
        val & 0xFFFF,
    ]


def split_conditional_pair(cheat: SelectedCheat) -> Optional[Tuple[CheatLine, CheatLine]]:
    """Return (cond, write) if this cheat is exactly the safe 2-line type-7 pair.

    Safe pattern (per the recommended first implementation):
      - exactly 2 lines
      - line 1 type == "7"
      - line 2 type in {3, 8, 2, 6}
    Otherwise return None (caller treats it as unsupported for conditional mode).
    """
    if len(cheat.lines) != 2:
        return None
    cond, write = cheat.lines[0], cheat.lines[1]
    if cond.type.upper() != "7":
        return None
    if write.type.upper() not in SUPPORTED_TYPES:
        return None
    return cond, write


def convert_simple_cheats_to_arm(cheats: List[SelectedCheat], log: List[str], behavior_profile: str = "auto") -> List[int]:
    out: List[int] = []
    for cheat in cheats:
        # Type 7 conditional pair (beta). Handled as a whole cheat, before the
        # per-line constant-write logic below. Only the safe 2-line pattern
        # (7 + {3,8,2,6}) is accepted; anything else falls through unchanged.
        pair = split_conditional_pair(cheat)
        if pair is not None:
            cond, write = pair
            log_print(log, f"[INFO] Cheat: {cheat.title}")
            log_print(log, "  [INFO] Behavior: conditional (type 7, beta)")
            log_print(log, f"  [OK] {cond.raw}: IF [0x{cond.address & 0x0FFFFFFF:08X}] == 0x{cond.value & 0xFFFF:04X}")
            log_print(log, f"  [OK] {write.raw}: THEN apply type {write.type.upper()}; {write.message}")
            out.extend(_arm_conditional_write_words(cond, write))
            continue
        behavior = classify_cheat_behavior(cheat.title, cheat.lines, behavior_profile)
        log_print(log, f"[INFO] Cheat: {cheat.title}")
        log_print(log, f"  [INFO] Behavior: {behavior}")
        if behavior == "refill_if_zero":
            log_print(log, "  [INFO] This cheat will write only when the target value is empty/zero. Good for item-slot cheats.")
        for line in cheat.lines:
            t = line.type.upper()
            addr = line.address & 0x0FFFFFFF
            val = line.value & 0xFFFF
            log_print(log, f"  [OK] {line.raw}: type {t}; {line.message}")
            if behavior == "refill_if_zero" and t in ("3", "8"):
                append_refill_if_zero(out, line)
                continue
            if t == "3":
                # 8-bit constant write
                out.extend([
                    0xE3A00000 | (val & 0xFF),  # mov r0, #value
                    0xE59F1004,                 # ldr r1, [pc, #4]
                    0xE5C10000,                 # strb r0, [r1]
                    0xEA000000,                 # b +0, skip literal
                    addr,
                ])
            elif t == "8":
                # 16-bit constant write
                if val <= 0xFF:
                    first = 0xE3A00000 | val
                else:
                    first = 0xE59F000C  # ldr r0, [pc, #0xC]
                out.extend([
                    first,
                    0xE59F1004,  # ldr r1, [pc, #4] -> addr literal
                    0xE1C100B0,  # strh r0, [r1]
                    0xEA000001,  # skip addr + optional val literal
                    addr,
                    val,
                ])
            elif t in ("2", "6"):
                # 16-bit OR / AND
                if val <= 0xFF:
                    first = 0xE3A02000 | val
                    branch = 0xEA000000
                else:
                    first = 0xE59F2014
                    branch = 0xEA000001
                op = 0xE1800002 if t == "2" else 0xE0000002
                out.extend([
                    first,
                    0xE59F100C,  # ldr r1, [pc, #0xC]
                    0xE1D100B0,  # ldrh r0, [r1]
                    op,          # orr/and r0, r0, r2
                    0xE1C100B0,  # strh r0, [r1]
                    branch,
                    addr,
                ])
                if val > 0xFF:
                    out.append(val)
            else:
                raise ValueError(f"Unsupported simple type: {t}")
    out.append(0xE12FFF1E)  # bx lr
    return out


def parse_cheat_line_obj(obj: dict) -> Optional[CheatLine]:
    if obj.get("status") != "simple_supported":
        return None
    t = str(obj.get("type", "")).upper()
    if t not in SUPPORTED_TYPES:
        return None
    try:
        address = int(str(obj["address"]), 16)
        value = int(str(obj["value"]), 16)
    except Exception:
        return None
    return CheatLine(
        raw=str(obj.get("raw", f"{address:08X} {value:04X}")),
        address=address,
        value=value,
        type=t,
        message=str(obj.get("message", "")),
    )


def selectable_cheats(game: dict) -> List[Tuple[int, dict]]:
    items = []
    for idx, ch in enumerate(game.get("cheats", [])):
        if ch.get("status") != "simple_supported":
            continue
        lines = [parse_cheat_line_obj(x) for x in ch.get("lines", [])]
        lines = [x for x in lines if x]
        if lines and len(lines) == ch.get("total_code_lines", len(lines)):
            items.append((idx, ch))
    return items


def parse_any_line_obj(obj: dict) -> Optional[CheatLine]:
    """Parse a line regardless of status (used for conditional pairs).

    Unlike parse_cheat_line_obj, this does not require status == simple_supported,
    so it can read the type-7 condition line. It still requires a parseable
    address/value and a known type character.
    """
    t = str(obj.get("type", "")).upper()
    if not t:
        return None
    try:
        address = int(str(obj["address"]), 16)
        value = int(str(obj["value"]), 16)
    except Exception:
        return None
    return CheatLine(
        raw=str(obj.get("raw", f"{address:08X} {value:04X}")),
        address=address,
        value=value,
        type=t,
        message=str(obj.get("message", "")),
    )


def conditional_cheats(game: dict) -> List[Tuple[int, dict]]:
    """Return (db_index, cheat) for cheats that are the safe 2-line type-7 pair.

    Safe pattern: exactly two lines, line 1 type 7, line 2 type in {3,8,2,6}.
    These are NOT returned by selectable_cheats(); they are offered separately
    as an opt-in beta feature so the stable simple-write path stays untouched.
    """
    items: List[Tuple[int, dict]] = []
    for idx, ch in enumerate(game.get("cheats", [])):
        raw_lines = ch.get("lines", [])
        if len(raw_lines) != 2:
            continue
        cond = parse_any_line_obj(raw_lines[0])
        write = parse_any_line_obj(raw_lines[1])
        if cond is None or write is None:
            continue
        if cond.type.upper() != "7":
            continue
        if write.type.upper() not in SUPPORTED_TYPES:
            continue
        items.append((idx, ch))
    return items


def make_selected_conditional(game: dict, cheat_indices: List[int]) -> List[SelectedCheat]:
    """Build SelectedCheat objects for conditional pairs by db index."""
    selected: List[SelectedCheat] = []
    cheats = game.get("cheats", [])
    for idx in cheat_indices:
        if idx < 0 or idx >= len(cheats):
            raise ValueError(f"Cheat index out of range: {idx}")
        ch = cheats[idx]
        raw_lines = ch.get("lines", [])
        cond = parse_any_line_obj(raw_lines[0]) if len(raw_lines) >= 1 else None
        write = parse_any_line_obj(raw_lines[1]) if len(raw_lines) >= 2 else None
        if cond is None or write is None or cond.type.upper() != "7" or write.type.upper() not in SUPPORTED_TYPES:
            raise ValueError(f"Cheat is not a supported conditional pair: #{idx} {ch.get('title')}")
        selected.append(SelectedCheat(title=ch.get("title", f"Cheat {idx}"), lines=[cond, write]))
    return selected


def make_selected(game: dict, cheat_indices: List[int]) -> List[SelectedCheat]:
    selected = []
    cheats = game.get("cheats", [])
    for idx in cheat_indices:
        if idx < 0 or idx >= len(cheats):
            raise ValueError(f"Cheat index out of range: {idx}")
        ch = cheats[idx]
        lines = [parse_cheat_line_obj(x) for x in ch.get("lines", [])]
        lines = [x for x in lines if x]
        if not lines:
            raise ValueError(f"Cheat is not simple_supported: #{idx} {ch.get('title')}")
        selected.append(SelectedCheat(title=ch.get("title", f"Cheat {idx}"), lines=lines))
    return selected


def patch_rom(
    rom_path: Path,
    out_path: Path,
    selected_cheats: List[SelectedCheat],
    log: List[str],
    vblank: bool = True,
    execute_every: int = 1,
    counter_ram: int = 0x03007FA0,
    max_hooks: int = 1,
    hook_indices: Optional[List[int]] = None,
    skip_early_hooks: bool = True,
    behavior_profile: str = "auto",
) -> None:
    data = bytearray(rom_path.read_bytes())
    if len(data) < 0xC0:
        raise ValueError("Input does not look like a valid GBA ROM; too small.")
    if len(data) > MAX_ROM_SIZE:
        raise ValueError("Input ROM is larger than 32MB.")

    header = gba_header_info(data)
    log_print(log, f"ROM: {rom_path}")
    log_print(log, f"Output: {out_path}")
    log_print(log, f"ROM CRC32: {crc32_file(rom_path)}")
    log_print(log, f"ROM header: title='{header['title']}', game_code='{header['game_code']}', maker='{header['maker_code']}'")
    log_print(log, "Patch mode: Always-ON runtime RAM write engine, no visual trainer menu")
    log_print(log, f"VBlank guard: {'enabled' if vblank else 'disabled'}")
    log_print(log, f"Execute every: {execute_every} cycle(s)")
    log_print(log, f"Hook policy: {'skip early hooks < 0x08001000' if skip_early_hooks else 'ALLOW early hooks (risky)'}, max {max_hooks}")
    log_print(log, f"Cheat behavior profile: {behavior_profile}")
    if hook_indices:
        log_print(log, f"Manual safe hook indices requested: {','.join(str(x) for x in hook_indices)}")

    cheat_words = convert_simple_cheats_to_arm(selected_cheats, log, behavior_profile=behavior_profile)
    log_print(log, f"Cheat engine length: {len(cheat_words)} word(s) / {len(cheat_words)*4} byte(s)")

    original_size = len(data)
    meaningful_end = find_rom_end(data)
    insert_offset = align4(original_size)
    log_print(log, f"Original ROM size: 0x{original_size:X} byte(s)")
    log_print(log, f"Last non-padding byte heuristic: 0x{meaningful_end:X}")
    log_print(log, "Free space strategy: append engine after original ROM file size, not inside trailing padding")
    log_print(log, f"Engine insertion address selected at 0x{GBA_ROM_BASE + insert_offset:08X}")

    candidates = scan_hooks(bytes(data), log)
    selected_hooks: List[HookCandidate] = []
    skipped_early = 0
    safe_candidates: List[HookCandidate] = []
    for cand in candidates:
        log_print(log, f"Hook candidate at 0x{GBA_ROM_BASE + cand.offset:08X} using hook type {cand.hook_type}")
        if skip_early_hooks and cand.offset < MIN_SAFE_HOOK_OFFSET:
            skipped_early += 1
            log_print(log, f"  [WARN] skipped early hook at 0x{GBA_ROM_BASE + cand.offset:08X}")
            continue
        if (not skip_early_hooks) and cand.offset < MIN_SAFE_HOOK_OFFSET:
            log_print(log, f"  [RISK] early hook allowed at 0x{GBA_ROM_BASE + cand.offset:08X}; this may run at startup or crash on some ROMs")
        safe_candidates.append(cand)

    if hook_indices:
        for requested in hook_indices:
            if requested < 1 or requested > len(safe_candidates):
                raise RuntimeError(f"Requested hook index {requested} is out of range. Safe hooks available: {len(safe_candidates)}")
            cand = safe_candidates[requested - 1]
            selected_hooks.append(cand)
            log_print(log, f"Hook {len(selected_hooks)} manually selected: candidate #{requested} at 0x{GBA_ROM_BASE + cand.offset:08X} using hook type {cand.hook_type}")
    else:
        for cand in safe_candidates:
            selected_hooks.append(cand)
            log_print(log, f"Hook {len(selected_hooks)} selected at 0x{GBA_ROM_BASE + cand.offset:08X} using hook type {cand.hook_type}")
            if len(selected_hooks) >= max_hooks:
                break

    if not selected_hooks:
        raise RuntimeError(f"No selectable hooks found. Early hooks skipped: {skipped_early}")

    # Ensure file is large enough for hook patching and insertion.
    engine_words: List[int] = []
    hook_wrapper_offsets: List[int] = []
    for hook_i, hook in enumerate(selected_hooks):
        wrapper_start_word = len(engine_words)
        hook_wrapper_offsets.append(insert_offset + wrapper_start_word * 4)
        original0 = read_u32(data, hook.offset)
        original1 = read_u32(data, hook.offset + 4)
        original2 = read_u32(data, hook.offset + 8)
        # This matches GBAATM's wrapper style: save regs to EWRAM scratch, BL engine, restore, original 3 ops, return.
        engine_words.extend([
            0xE92D4000,  # push r14
            0xE3A0E402,  # mov r14, #0x02000000
            0xE28EE701,  # add r14, #0x40000
            0xE24EE004,  # sub r14, #28
            0xE90E08FF,  # stmdb [r14], r0-r7,r11
            0xEB000000,  # patched below: bl cheatfunc
            0xE3A0E402,
            0xE28EE701,
            0xE24EE028,
            0xE89E08FF,
            0xE8BD4000,  # pop r14
            original0,
            original1,
            original2,
            0xE8BD8000,  # pop r15
        ])
    # Patch BLs to cheatfunc start.
    cheatfunc_offset = insert_offset + len(engine_words) * 4
    for hook_i in range(len(selected_hooks)):
        bl_word_index = hook_i * SIZEOF_HOOK_JUMP_WORDS + 5
        bl_offset = insert_offset + bl_word_index * 4
        engine_words[bl_word_index] = arm_branch(bl_offset, cheatfunc_offset, link=True)

    if vblank:
        engine_words.extend(VBLANK_WORDS)
    if execute_every > 1:
        exec_words = EXEC_EVERY_WORDS.copy()
        exec_words[2] |= (execute_every & 0xFF)
        exec_words[9] = counter_ram & 0x0FFFFFFF
        engine_words.extend(exec_words)
    engine_words.extend(cheat_words)

    # Patch ROM hooks to jump into wrappers.
    for hook_i, hook in enumerate(selected_hooks):
        wrapper_addr = GBA_ROM_BASE + hook_wrapper_offsets[hook_i]
        write_u32(data, hook.offset, 0xE92D8000)       # push r15
        write_u32(data, hook.offset + 4, 0xE51FF004)   # ldr r15, [pc, #-4]
        write_u32(data, hook.offset + 8, wrapper_addr) # literal target
        log_print(log, f"Patched hook {hook_i+1}: 0x{GBA_ROM_BASE + hook.offset:08X} -> 0x{wrapper_addr:08X}")

    # Append engine.
    engine_bytes = struct.pack("<" + "I" * len(engine_words), *[w & 0xFFFFFFFF for w in engine_words])
    if len(data) < insert_offset:
        data.extend(b"\xFF" * (insert_offset - len(data)))
    data.extend(engine_bytes)
    # Pad to 16 bytes for cleanliness.
    pad = (-len(data)) % 16
    if pad:
        data.extend(b"\xFF" * pad)
    if len(data) > MAX_ROM_SIZE:
        raise RuntimeError("Patched ROM exceeds 32MB; aborting.")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    log_print(log, f"Patched ROM written successfully: {out_path}")
    log_print(log, f"Hooks selected: {len(selected_hooks)}; early hooks skipped: {skipped_early}; skip_early_hooks={skip_early_hooks}")
    log_print(log, f"Engine placed at: 0x{GBA_ROM_BASE + insert_offset:08X} ({len(engine_words)} word(s))")


def find_games(db: dict, query: str) -> List[dict]:
    q = query.lower().strip()
    return [g for g in db.get("games", []) if q in g.get("title", "").lower()]


def choose_game(db: dict) -> dict:
    while True:
        query = input("Search game title: ").strip()
        matches = find_games(db, query)
        if not matches:
            print("No matches. Try again.")
            continue
        for g in matches[:30]:
            s = g.get("summary", {})
            print(f"[{g['id']}] {g['title']}  simple={s.get('simple_supported',0)} total={len(g.get('cheats',[]))}")
        raw = input("Enter game ID: ").strip()
        try:
            gid = int(raw)
            for g in db.get("games", []):
                if g.get("id") == gid:
                    return g
        except Exception:
            pass
        print("Invalid game ID. Try again.")


def choose_cheats(game: dict) -> Tuple[List[int], List[int]]:
    """Return (db_indices, display_numbers).

    The UI shows simple_supported cheats renumbered from 1..N to avoid crashes when
    a game has hidden/unsupported cheats before the supported ones. The output
    filename uses the displayed option numbers, e.g. CHT.3.13.
    """
    items = selectable_cheats(game)
    if not items:
        raise RuntimeError("This game has no simple_supported cheats.")
    print(f"\nSimple supported cheats for: {game['title']}")
    option_to_db = {}
    for opt, (idx, ch) in enumerate(items, start=1):
        option_to_db[opt] = idx
        print(f"[{opt}] {ch.get('title')}  ({ch.get('simple_supported_lines',0)} line(s))  DB#{idx}")
    print("\nUse the numbers shown at the left. The DB# is only informational.")
    while True:
        raw = input("Enter cheat numbers separated by comma (example: 1,3,5): ").strip()
        try:
            display_ids = []
            db_ids = []
            for part in re.split(r"[, ]+", raw):
                if not part.strip():
                    continue
                opt = int(part.strip())
                if opt not in option_to_db:
                    raise ValueError(f"Cheat number {opt} is not in the displayed list.")
                display_ids.append(opt)
                db_ids.append(option_to_db[opt])
            if not db_ids:
                raise ValueError("No cheats selected.")
            return db_ids, display_ids
        except Exception as exc:
            print(f"Invalid selection: {exc}")
            print(f"Valid cheat numbers: 1-{len(items)}")


def choose_hook_mode() -> Tuple[int, Optional[List[int]], bool]:
    print("\nHook mode:")
    print("1. Auto: first safe hook only (most stable, default)")
    print("2. Manual: choose safe hook number(s) after scanning (advanced)")
    print("3. Auto: first 2 safe hooks (risky; may crash some games)")
    print("4. Early hook only (very risky; use only if safe hooks do not apply cheats)")
    raw = input("Choose hook mode [1]: ").strip()
    if not raw or raw == "1":
        return 1, None, True
    if raw == "3":
        return 2, None, True
    if raw == "4":
        return 1, [1], False
    if raw == "2":
        print("Manual mode uses safe hook numbers after early hooks are skipped.")
        print("Example: enter 1 for first safe hook, or 2 for second safe hook.")
        ids_raw = input("Safe hook index/indices to use (example: 1 or 2): ").strip()
        ids = [int(x.strip()) for x in re.split(r"[, ]+", ids_raw) if x.strip()]
        return len(ids), ids, True
    print("Unknown option, using default first safe hook.")
    return 1, None, True




def choose_behavior_profile() -> str:
    print("\nCheat behavior profile:")
    print("1. Constant Always-ON for all selected cheats (recommended/default)")
    print("2. Auto: counters constant, item-slot cheats refill only when empty")
    print("3. Refill-if-empty for item-like writes where possible")
    raw = input("Choose behavior profile [1]: ").strip()
    if raw == "2":
        return "auto"
    if raw == "3":
        return "refill"
    return "constant"

def ask_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    raw = input(f"{prompt} {suffix}: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "s", "si", "sí", "1", "true")


def make_test_pack(
    rom: Path,
    base_out: Path,
    selected: List[SelectedCheat],
    game: dict,
    variants: Optional[List[Tuple[str, bool, Optional[List[int]], int, bool, str]]] = None,
) -> List[Path]:
    """Generate visible-test ROM variants without requiring Memory Viewer.

    variants: (suffix, vblank_enabled, manual_hook_indices, max_hooks, skip_early_hooks, behavior_profile)
    """
    if variants is None:
        variants = [
            ("early_hook1_constant_vblank_off_RECOMMENDED", False, [1], 1, False, "constant"),
            ("early_hook1_auto_refill_vblank_off", False, [1], 1, False, "auto"),
            ("safe_hook1_constant_vblank_off", False, [1], 1, True, "constant"),
            ("all_candidates_constant_vblank_off_RISKY", False, None, 10, False, "constant"),
        ]
    outputs: List[Path] = []
    pack_dir = base_out.with_suffix("")
    pack_dir = pack_dir.parent / (pack_dir.name + "_test_pack")
    pack_dir.mkdir(parents=True, exist_ok=True)
    summary_lines: List[str] = []
    summary_lines.append(f"{APP} v{VERSION} visible test pack")
    summary_lines.append(f"Created: {datetime.now().isoformat(timespec='seconds')}")
    summary_lines.append(f"Game: [{game['id']}] {game['title']}")
    summary_lines.append(f"ROM: {rom}")
    summary_lines.append("")
    for suffix, vblank, hook_indices, max_hooks, skip_early, behavior_profile in variants:
        out = pack_dir / f"{rom.stem}_{suffix}.gba"
        log: List[str] = []
        try:
            log_print(log, f"{APP} v{VERSION}")
            log_print(log, f"Created: {datetime.now().isoformat(timespec='seconds')}")
            log_print(log, f"DB game: [{game['id']}] {game['title']}")
            log_print(log, f"Visible test pack variant: {suffix}")
            patch_rom(
                rom,
                out,
                selected,
                log,
                vblank=vblank,
                execute_every=1,
                max_hooks=max_hooks,
                hook_indices=hook_indices,
                skip_early_hooks=skip_early,
                behavior_profile=behavior_profile,
            )
            log_path = out.with_suffix(".patch-log.txt")
            log_path.write_text("\n".join(log) + "\n", encoding="utf-8")
            outputs.append(out)
            summary_lines.append(f"[OK] {suffix}: {out.name}")
        except Exception as exc:
            err_path = pack_dir / f"{rom.stem}_{suffix}.ERROR.txt"
            err_path.write_text("\n".join(log) + f"\nERROR: {exc}\n", encoding="utf-8")
            summary_lines.append(f"[ERROR] {suffix}: {exc}")
    summary_lines.append("")
    summary_lines.append("How to test without Memory Viewer:")
    summary_lines.append("1. Open each generated ROM in mGBA.")
    summary_lines.append("2. Test the same visible in-game situation, for example race start/item/coins/retries.")
    summary_lines.append("3. If one ROM works, keep its patch-log; it tells which hook/VBlank/early-hook mode worked.")
    summary_lines.append("4. RISKY variants may crash; test them after the safe variants.")
    summary_lines.append("4. If a ROM crashes, delete that variant and use the next one.")
    (pack_dir / "TEST_PACK_README.txt").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    return outputs


def interactive(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        print("Use the GUI app or provide a compatible database JSON file.")
        return 2
    db = read_json(db_path)
    print(f"{APP} v{VERSION}")
    print(f"Loaded DB: {db_path}")
    print(f"Games: {db.get('game_count', len(db.get('games', [])))}")
    game = choose_game(db)
    cheat_ids, display_cheat_ids = choose_cheats(game)
    selected = make_selected(game, cheat_ids)
    print("\nSelected cheats:")
    for ch in selected:
        print(f"- {ch.title}")
        for line in ch.lines:
            print(f"  {line.raw}  {line.message}")
    rom = Path(input("\nPath to original .gba ROM: ").strip().strip('"')).expanduser()
    if not rom.exists():
        print(f"ROM not found: {rom}")
        return 2
    default_out = rom.with_name(rom.stem + default_cheat_suffix(display_cheat_ids) + ".gba")
    out_raw = input(f"Output patched ROM [{default_out}]: ").strip().strip('"')
    out = Path(out_raw).expanduser() if out_raw else default_out

    header = gba_header_info(rom.read_bytes()) if rom.exists() else {}
    code = header.get("game_code", "")
    if code in KNOWN_WORKING_PRESETS:
        preset = KNOWN_WORKING_PRESETS[code]
        print("\nKnown working preset found:")
        print(f"- {preset['title']}")
        print(f"- Profile: {preset['profile']}")
        print(f"- Notes: {preset['notes']}")
    else:
        print("\nNo stored preset for this ROM header yet. The recommended profile will still be tried first.")

    warn = partial_warning_for_title(game.get("title", ""))
    if warn:
        print("\nCompatibility warning:")
        print(f"- {warn}")

    print("\nDefault v1.0 output uses recommended profile:")
    print("- early hook 1")
    print("- VBlank off")
    print("- constant Always-ON writes")
    print("- no visual trainer menu")

    behavior_profile = "constant"
    max_hooks, hook_indices, skip_early = 1, [1], False
    vblank = False
    log: List[str] = []
    log_print(log, f"{APP} v{VERSION}")
    log_print(log, f"Created: {datetime.now().isoformat(timespec='seconds')}")
    log_print(log, f"DB game: [{game['id']}] {game['title']}")
    log_print(log, f"Recommended profile: {RECOMMENDED_PROFILE_NAME}")
    if code in KNOWN_WORKING_PRESETS:
        log_print(log, f"Known preset notes: {KNOWN_WORKING_PRESETS[code]['notes']}")
    patch_rom(rom, out, selected, log, vblank=vblank, execute_every=1, max_hooks=max_hooks, hook_indices=hook_indices, skip_early_hooks=skip_early, behavior_profile=behavior_profile)
    log_path = out.with_suffix(".patch-log.txt")
    log_path.write_text("\n".join(log) + "\n", encoding="utf-8")
    print(f"\nDone. Patched ROM: {out}")
    print(f"Log: {log_path}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    default_db = Path.home() / "Desktop" / "GBA-Cheat-Studio-v0.1" / "gba_cheats_db.json"
    p = argparse.ArgumentParser(description=f"{APP} v{VERSION}")
    p.add_argument("--db", default=str(default_db), help="Path to gba_cheats_db.json from v0.1")
    p.add_argument("--game-id", type=int, help="Game ID from the JSON DB")
    p.add_argument("--cheat-ids", help="Comma-separated DB cheat indices from that game (non-interactive mode only)")
    p.add_argument("--rom", help="Input .gba ROM")
    p.add_argument("--out", help="Output patched .gba ROM. If omitted, uses <ROMSTEM>CHT.<ids>.gba")
    p.add_argument("--max-hooks", type=int, default=1, help="Max hooks to patch; default 1")
    p.add_argument("--hook-indices", help="Manual hook indices to use, comma-separated; with --allow-early, index 1 is usually the early hook")
    p.add_argument("--no-vblank", action="store_true", default=True, help="Disable VBlank guard (default in v0.7 recommended profile)")
    p.add_argument("--test-pack", action="store_true", help="Generate multiple visible-test ROM variants instead of one ROM")
    p.add_argument("--behavior-profile", choices=["auto", "constant", "refill"], default="constant", help="Cheat behavior: auto, constant, or refill; default constant")
    p.add_argument("--allow-early", action="store_true", default=True, help="Allow early hooks; default enabled for v0.7 recommended profile")
    args = p.parse_args(argv)

    # Non-interactive mode if all patch args present.
    if args.game_id is not None or args.cheat_ids or args.rom or args.out:
        needed = [args.game_id is not None, bool(args.cheat_ids), bool(args.rom)]
        if not all(needed):
            print("For non-interactive mode, provide --game-id --cheat-ids --rom. --out is optional.")
            return 2
        db = read_json(Path(args.db).expanduser())
        game = next((g for g in db.get("games", []) if g.get("id") == args.game_id), None)
        if not game:
            print(f"Game ID not found: {args.game_id}")
            return 2
        cheat_ids = [int(x.strip()) for x in args.cheat_ids.split(",") if x.strip()]
        selected = make_selected(game, cheat_ids)
        rom_path = Path(args.rom).expanduser()
        out = Path(args.out).expanduser() if args.out else rom_path.with_name(rom_path.stem + default_cheat_suffix(cheat_ids) + ".gba")
        if args.test_pack:
            make_test_pack(Path(args.rom).expanduser(), out, selected, game)
            print(f"Done. Test pack folder: {out.with_suffix('').parent / (out.with_suffix('').name + '_test_pack')}")
            return 0
        log: List[str] = []
        log_print(log, f"{APP} v{VERSION}")
        log_print(log, f"Created: {datetime.now().isoformat(timespec='seconds')}")
        log_print(log, f"DB game: [{game['id']}] {game['title']}")
        hook_indices = [int(x.strip()) for x in args.hook_indices.split(",") if x.strip()] if args.hook_indices else None
        patch_rom(Path(args.rom).expanduser(), out, selected, log, vblank=not args.no_vblank, max_hooks=args.max_hooks, hook_indices=hook_indices, skip_early_hooks=not args.allow_early, behavior_profile=args.behavior_profile)
        log_path = out.with_suffix(".patch-log.txt")
        log_path.write_text("\n".join(log) + "\n", encoding="utf-8")
        print(f"Done. Log: {log_path}")
        return 0

    return interactive(args)


if __name__ == "__main__":
    raise SystemExit(main())
