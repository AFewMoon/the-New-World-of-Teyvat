"""
文本规范化工具：遍历所有 *.md 文件，执行以下操作：
1. 将英文单双引号替换为中文引号
2. 数字间连字符 `-` 统一为 `~`
3. 数字与非数字（含中文、英文）之间的空格调整为有且仅有一个
4. 加粗符号 `**` 紧贴其内容（删除 `**` 与其内容之间的空格）
5. 代码块（```...```）和行内代码（`...`）内部不做变动
"""

import re
import time
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

    # 6. 删除 ** 与数字之间的多余空格（紧贴加粗内容）
    #    ** 85%** → **85%**
    cleaned = re.sub(r'\*\*\s+(\d)', r'**\1', cleaned)
    cleaned = re.sub(r'(\d)\s+\*\*', r'\1**', cleaned)

    # 7. 外部中文/字母/假名与 ** 之间 → 紧贴（删除所有空格）
    #    保证加粗符号用于汉字之间时不应存在空格
    external = r'[\u4e00-\u9fffa-zA-Z\u3040-\u309f\u30a0-\u30ff]'
    cleaned = re.sub(r'\*\*\s+(' + external + r')', r'**\1', cleaned)
    cleaned = re.sub(r'(' + external + r')\s+\*\*', r'\1**', cleaned)
    #    处理 ** 文字 ** → **文字**（两侧空格都删除；捕获多个字符）
    cleaned = re.sub(r'\*\*\s+(' + external + r'+)\s+\*\*', r'**\1**', cleaned)
    #    修复 ** 左侧为全角/CJK 标点时右侧中文误加空格：** 对 → **对
    cleaned = re.sub(r'([\u3000-\u303f\uff00-\uffef])\*\*\s+(' + external + r')', r'\1**\2', cleaned)
    #    修复中文与 ** 之间误加的空格：平衡的 ** 城市 → 平衡的**城市
    cleaned = re.sub(r'(' + external + r')\s+\*\*\s+(' + external + r')', r'\1**\2', cleaned)
    #    最终清理：中文与 ** 之间不应有空格（任一侧有空格即删除）
    cleaned = re.sub(r'(' + external + r')\s+\*\*\s*(' + external + r')', r'\1**\2', cleaned)
    cleaned = re.sub(r'(' + external + r')\s*\*\*\s+(' + external + r')', r'\1**\2', cleaned)

    # 8. 还原行内代码
    cleaned = restore_inline_code(cleaned, code_parts)

    return cleaned


def should_skip_file(path: Path) -> bool:
    """跳过非 md 文件以及 tools/、.git/ 等目录。"""
    rel = path.relative_to(BASE_DIR)
    parts = rel.parts
    skip_dirs = {"tools", ".git", "node_modules", "__pycache__"}
    return any(p in skip_dirs for p in parts)


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

    # 计算所有文件总大小
    total_bytes = sum(p.stat().st_size for p in md_files)
    total_mb = total_bytes / (1024 * 1024)

    # 开始处理并计时
    print("\n--- 开始规范化 ---")
    start_time = time.perf_counter()
    fixed_count = 0
    for path in md_files:
        if fix_file(path):
            fixed_count += 1
    end_time = time.perf_counter()

    elapsed = end_time - start_time
    seconds_per_mb = elapsed / total_mb if total_mb > 0 else 0
    mb_per_second = total_mb / elapsed if elapsed > 0 else 0

    print(f"\n完成：共处理 {len(md_files)} 个文件，其中 {fixed_count} 个有修改。")
    print(f"\n处理统计：")
    print(f"  总耗时      : {elapsed:.3f} 秒")
    print(f"  总数据量    : {total_mb:.3f} MB")
    print(f"  处理速度    : {seconds_per_mb:.3f} 秒/MB")
    print(f"               {mb_per_second:.3f} MB/秒")


if __name__ == "__main__":
    main()