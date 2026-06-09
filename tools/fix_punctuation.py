#!/usr/bin/env python3
"""
文本规范化工具：遍历所有 *.md 文件，执行以下操作：
1. 备份至 backup/ 文件夹，保留目录架构
2. 将英文单双引号替换为中文引号
3. 数字间连字符 `-` 统一为 `~`
4. 数字与非数字（含中文、英文）之间的空格调整为有且仅有一个
5. 加粗符号 `**` 紧贴其内容（删除 `**` 与其内容之间的空格）
6. 代码块（```...```）和行内代码（`...`）内部不做变动
"""

import shutil
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def is_inside_pair(text: str, pos: int, left: str, right: str) -> bool:
    """检查 pos 位置是否被成对的 left/right 包裹（跨行不考虑，仅单行内）。"""
    before = text[:pos]
    after = text[pos:]
    return before.count(left) > before.count(right) and after.count(right) > after.count(left)


def protect_inline_code(line: str) -> tuple[str, list[str]]:
    """将行内 `code` 提取为占位符，返回 (替换后文本, 原片段列表)。"""
    parts: list[str] = []
    placeholder_prefix = "\\x00CODE"
    idx = 0

    def repl(m: re.Match) -> str:
        nonlocal idx
        parts.append(m.group(1))
        placeholder = f"{placeholder_prefix}{idx}\\x01"
        idx += 1
        return placeholder

    # 匹配 `...` 行内代码（跳过空的反引号）
    result = re.sub(r"`([^`]+)`", repl, line)
    return result, parts


def restore_inline_code(text: str, parts: list[str]) -> str:
    """将占位符还原为原始行内代码。"""
    for i, p in enumerate(parts):
        text = text.replace(f"\\x00CODE{i}\\x01", f"`{p}`")
    return text


def process_line(line: str) -> str:
    """对单行文本执行所有标点规范化处理。"""
    # 1. 提取行内代码，保护后处理
    cleaned, code_parts = protect_inline_code(line)

    # 2. 英文双引号 → 中文双引号
    #    匹配 "content"（content 不含换行、不含双引号）
    cleaned = re.sub(r'"([^"]+)"', '\u201c\\1\u201d', cleaned)

    # 3. 英文单引号 → 中文单引号
    #    匹配 'content'（content 不含换行、不含单引号）
    cleaned = re.sub(r"'([^']+)'", '\u2018\\1\u2019', cleaned)

    # 4. 数字间连字符 → ~  （去掉两端空格）
    cleaned = re.sub(r'(\d)\s*-\s*(\d)', r'\1~\2', cleaned)

    # 5. 数字与非数字之间的空格 → 有且仅有一个（先执行，可能产生 ** 内部临时空格）
    #    数字后跟中文或字母：多余空格 → 一个；无空格 → 添加一个
    cleaned = re.sub(r'(\d)\s*([\u4e00-\u9fffa-zA-Z])', r'\1 \2', cleaned)
    #    中文或字母后跟数字
    cleaned = re.sub(r'([\u4e00-\u9fffa-zA-Z])\s*(\d)', r'\1 \2', cleaned)
    #    数字后跟 **（确保一个空格，如 2**1** → 2 **1**）
    cleaned = re.sub(r'(\d)\s*(\*\*)', r'\1 \2', cleaned)
    #    ** 后跟数字（确保一个空格，如 **1**2 → **1** 2）
    cleaned = re.sub(r'(\*\*)\s*(\d)', r'\1 \2', cleaned)

    # 6. 加粗符号 ** 与非数字之间的空格 → 删除
    #    ** 文字 ** → **文字**（非数字，删空格）
    #    ** 1 ** → ** 1 **（数字，保留空格）
    #    非数字 = 中文 + 英文字母 + 日文假名（非标点/格式符号）
    non_digit = r'[\u4e00-\u9fffa-zA-Z\u3040-\u309f\u30a0-\u30ff]'
    cleaned = re.sub(r'\*\*\s+(' + non_digit + r')', r'**\1', cleaned)    # ** 后跟非数字
    cleaned = re.sub(r'(' + non_digit + r')\s+\*\*', r'\1**', cleaned)    # 非数字后跟 **

    # 7. 还原行内代码
    cleaned = restore_inline_code(cleaned, code_parts)

    return cleaned


def should_skip_file(path: Path) -> bool:
    """跳过非 md 文件以及 tools/、backup/、.git/ 等目录。"""
    rel = path.relative_to(BASE_DIR)
    parts = rel.parts
    skip_dirs = {"tools", "backup", ".git", "node_modules", "__pycache__"}
    return any(p in skip_dirs for p in parts)


def backup_file(src: Path) -> None:
    """将源文件备份至 backup/ 对应子目录。"""
    rel = src.relative_to(BASE_DIR)
    dst = BASE_DIR / "backup" / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    print(f"  [BACKUP] {rel} -> backup/{rel}")


def fix_file(path: Path) -> bool:
    """处理单个 .md 文件，返回是否有修改。"""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=False)
    new_lines: list[str] = []
    in_code_block = False
    modified = False

    for line in lines:
        stripped = line.strip()
        # 检测代码块边界（``` 开头，可能跟语言标识）
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            new_lines.append(line)
            continue

        if in_code_block:
            # 代码块内部不处理
            new_lines.append(line)
            continue

        processed = process_line(line)
        if processed != line:
            modified = True
        new_lines.append(processed)

    if modified:
        new_content = "\n".join(new_lines)
        # 确保末尾有换行（如果原文件有）
        if path.read_text(encoding="utf-8").endswith("\n"):
            new_content += "\n"
        path.write_text(new_content, encoding="utf-8")
        print(f"  [FIXED] {path.relative_to(BASE_DIR)}")

    return modified


def main() -> None:
    print("=" * 60)
    print("  提瓦特新世界 · 文本规范化工具")
    print("=" * 60)

    md_files = sorted(
        p for p in BASE_DIR.rglob("*.md")
        if not should_skip_file(p) and p.is_file()
    )

    if not md_files:
        print("未找到任何 .md 文件，退出。")
        return

    print(f"\n找到 {len(md_files)} 个 .md 文件")

    # 阶段 1：备份
    print("\n--- 阶段 1：备份 ---")
    for path in md_files:
        backup_file(path)
    print(f"共备份 {len(md_files)} 个文件至 backup/")

    # 阶段 2：修改
    print("\n--- 阶段 2：规范化 ---")
    fixed_count = 0
    for path in md_files:
        if fix_file(path):
            fixed_count += 1

    print(f"\n完成：共处理 {len(md_files)} 个文件，其中 {fixed_count} 个有修改。")
    print("备份文件位于 backup/ 目录下。")


if __name__ == "__main__":
    main()