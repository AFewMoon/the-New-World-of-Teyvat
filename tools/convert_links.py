"""
链接格式转换工具 (Link Format Converter)

在 Obsidian 双向链接 [[...]] 和标准 Markdown 超链接之间相互转换。
用于 GitHub 推送流程中的自动格式切换。

用法:
    python convert_links.py --to-md          # wikilinks → md links
    python convert_links.py --to-wikilinks   # md links → wikilinks

正向 (--to-md):
    [[path|text]]  →  [text](path.md)
    [[path]]       →  [stem](path.md)   (stem = path 最后一段)

反向 (--to-wikilinks):
    [text](path.md)  →  [[path|text]]   (若状态记录为 "pipe")
                         [[path]]        (若状态记录为 "nopipe")
                         不处理          (状态文件中无记录)

状态文件: tools/_convert_state.json
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_FILE = BASE_DIR / "tools" / "_convert_state.json"

SKIP_DIRS = {".git", ".obsidian", ".clinerules", "node_modules", "__pycache__", "tools"}
SKIP_FILES = {"README.md", "LICENSE", "AGENTS.md"}

# ─── 正则 ──────────────────────────────────────────────────────────────────

WIKILINK_RE = re.compile(r"\[\[(.+?)(?:\|(.+?))?\]\]")
MDLINK_RE = re.compile(r"(?<!\!)\[([^\[\]]+?)\]\(([^)]+)\)")


# ─── 保护区域识别 ──────────────────────────────────────────────────────────

def find_protected_regions(text: str) -> list[tuple[int, int]]:
    """返回受保护区域列表，本工具不修改这些区域。"""
    regions: list[tuple[int, int, int]] = []  # (start, end, priority)

    def add(s: int, e: int, prio: int):
        regions.append((s, e, prio))

    # 代码块 (最高优先级)
    for m in re.finditer(r"```[\s\S]*?```", text):
        add(m.start(), m.end(), 100)
    # 行内代码
    for m in re.finditer(r"`[^`]+`", text):
        add(m.start(), m.end(), 90)
    # YAML front matter
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            add(0, end + 3, 100)
    # 标题行
    for m in re.finditer(r"^#{1,6}\s+.*$", text, re.MULTILINE):
        add(m.start(), m.end(), 80)
    # 图片
    for m in re.finditer(r"!\[.*?\]\(.*?\)", text):
        add(m.start(), m.end(), 60)
    # URL
    for m in re.finditer(r"https?://\S+", text):
        add(m.start(), m.end(), 50)

    regions.sort(key=lambda x: x[0])
    merged: list[tuple[int, int]] = []
    for s, e, _ in regions:
        if not merged:
            merged.append((s, e))
        else:
            last = merged[-1]
            if s <= last[1]:
                merged[-1] = (last[0], max(last[1], e))
            else:
                merged.append((s, e))
    return merged


def is_protected(pos: int, regions: list[tuple[int, int]]) -> bool:
    for s, e in regions:
        if s <= pos < e:
            return True
    return False


# ─── 文件扫描 ──────────────────────────────────────────────────────────────

def get_md_files() -> list[Path]:
    files: list[Path] = []
    for fp in BASE_DIR.rglob("*.md"):
        rel = fp.relative_to(BASE_DIR).as_posix()
        parts = rel.split("/")
        if any(p in SKIP_DIRS or p.startswith(".") for p in parts):
            continue
        if fp.name in SKIP_FILES:
            continue
        files.append(fp)
    return files


def content_files() -> list[Path]:
    """返回所有参与链接转换的内容文件（不含 README/LICENSE/AGENTS 等）。"""
    return get_md_files()


# ─── 状态文件管理 ──────────────────────────────────────────────────────────

def load_state() -> dict[str, str]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(state: dict[str, str]):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ─── 正向转换: wikilinks → md links ───────────────────────────────────────

def file_stem(path: str) -> str:
    """从 vault 路径中提取最后一段（无 .md）。"""
    return path.rsplit("/", 1)[-1]


def convert_to_md(text: str) -> tuple[str, dict[str, str]]:
    """将 [[wikilinks]] 转为 [text](path.md)，返回 (新文本, 状态条目)。"""
    protected = find_protected_regions(text)
    state_entries: dict[str, str] = {}
    replacements: list[tuple[int, int, str]] = []

    for m in re.finditer(WIKILINK_RE, text):
        if is_protected(m.start(), protected):
            continue
        path = m.group(1).strip()
        display_given = m.group(2)
        display = display_given.strip() if display_given else file_stem(path)

        key = f"{path}|{display}"
        state_entries[key] = "pipe" if display_given else "nopipe"
        md_link = f"[{display}]({path}.md)"
        replacements.append((m.start(), m.end(), md_link))

    if not replacements:
        return text, state_entries

    lines = list(text)
    for start, end, md_link in sorted(replacements, key=lambda x: -x[0]):
        lines[start:end] = list(md_link)
    return "".join(lines), state_entries


# ─── 反向转换: md links → wikilinks ───────────────────────────────────────

def convert_to_wikilinks(text: str, state: dict[str, str]) -> str:
    """将 [text](path.md) 还原为 [[wikilinks]]。"""
    protected = find_protected_regions(text)
    replacements: list[tuple[int, int, str]] = []

    for m in re.finditer(MDLINK_RE, text):
        if is_protected(m.start(), protected):
            continue
        display = m.group(1).strip()
        url = m.group(2).strip()

        if not url.endswith(".md"):
            continue

        path = url[:-3]
        key = f"{path}|{display}"

        if key not in state:
            continue

        fmt = state[key]
        formats = fmt.split(",")
        if formats == ["nopipe"] and display == file_stem(path):
            wikilink = f"[[{path}]]"
        else:
            wikilink = f"[[{path}|{display}]]"

        replacements.append((m.start(), m.end(), wikilink))

    if not replacements:
        return text

    lines = list(text)
    for start, end, wikilink in sorted(replacements, key=lambda x: -x[0]):
        lines[start:end] = list(wikilink)
    return "".join(lines)


# ─── 文件级操作 ────────────────────────────────────────────────────────────

def process_file_to_md(fp: Path) -> tuple[bool, dict[str, str]]:
    """处理单个文件：正向转换。返回 (是否修改, 新增状态条目)。"""
    original = fp.read_text(encoding="utf-8")
    modified, state_entries = convert_to_md(original)
    if modified != original:
        fp.write_text(modified, encoding="utf-8")
        return True, state_entries
    return False, {}


def process_file_to_wikilinks(fp: Path, state: dict[str, str]) -> bool:
    """处理单个文件：反向转换。返回 (是否修改)。"""
    original = fp.read_text(encoding="utf-8")
    modified = convert_to_wikilinks(original, state)
    if modified != original:
        fp.write_text(modified, encoding="utf-8")
        return True
    return False


# ─── 主入口 ────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) != 2:
        print("用法: python convert_links.py --to-md | --to-wikilinks")
        sys.exit(1)

    mode = sys.argv[1]
    if mode not in ("--to-md", "--to-wikilinks"):
        print("用法: python convert_links.py --to-md | --to-wikilinks")
        sys.exit(1)

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    files = content_files()
    total = len(files)
    modified_count = 0
    link_count = 0
    all_state: dict[str, str] = {}

    if mode == "--to-md":
        print(f"正向转换: wikilinks -> MD links  ({total} 个文件)")
        for fp in files:
            changed, state_entries = process_file_to_md(fp)
            if changed:
                modified_count += 1
                link_count += len(state_entries)
                # 跨文件合并：保留所有格式（pipe / nopipe）
                for key, fmt in state_entries.items():
                    existing_fmts = set()
                    if key in all_state:
                        existing_fmts.update(all_state[key].split(","))
                    existing_fmts.update(fmt.split(","))
                    all_state[key] = ",".join(sorted(existing_fmts))
                print(f"  [OK] {fp.relative_to(BASE_DIR).as_posix()}")
        # 保存状态
        existing = load_state()
        existing.update(all_state)
        save_state(existing)
        print(f"\n修改 {modified_count}/{total} 个文件，共 {link_count} 处链接")

    else:  # --to-wikilinks
        state = load_state()
        if not state:
            print("警告: 状态文件为空，跳过反向转换")
            return
        print(f"反向转换: MD links -> wikilinks  ({total} 个文件)")
        for fp in files:
            changed = process_file_to_wikilinks(fp, state)
            if changed:
                modified_count += 1
                print(f"  [OK] {fp.relative_to(BASE_DIR).as_posix()}")
        print(f"\n还原 {modified_count}/{total} 个文件")


if __name__ == "__main__":
    main()
