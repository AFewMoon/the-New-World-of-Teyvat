"""
文本规范化工具：遍历所有 *.md 文件，执行以下操作：
1. 将英文单双引号替换为中文引号
2. 数字间连字符 `-` 统一为 `~`
3. 数字与非数字（含中文、英文）之间的空格调整为有且仅有一个
4. 加粗符号 `**` 周围空格规范化：
   内部（** 与内容之间）不留空格，紧贴任意非空字符；
   外部（** 与相邻字符之间）左侧基于前一个字符 或 加粗内容首字符：
      任一非 CJK → 补 1 空格；右侧基于后一个字符 或 加粗内容末字符：
      任一非 CJK → 补 1 空格
      （数字、% 等视为非 CJK，故 `**30%~40%**` 两侧均补空格）
5. 中文与英文之间补 1 空格（"郡GDP" → "郡 GDP"，"GDP的" → "GDP 的"）
6. 数字+百分号/连字符（45%~50%、30%）后跟中文 → 补 1 空格
7. 代码块（```...```）和行内代码（`...`）内部不做变动
8. 删除 Markdown 标题行序号（"## 一、背景" → "## 背景"，"## 1.2 经济" → "## 经济"）
9. 删除独立成行的 Markdown 分隔线 `---`
10. 连续多个空行压缩为有且仅有一个空行
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
    """将行内 `code` 提取为占位符，返回 (替换后文本, 原片段列表)。

    占位符为 \\x00{idx}\\x01（纯控制字符 + 数字，不含字母），防止被
    "字母-数字间距"类正则插入空格破坏。
    """
    parts: list[str] = []
    idx = 0

    def repl(m: re.Match) -> str:
        nonlocal idx
        parts.append(m.group(1))
        placeholder = f"{chr(0)}{idx}{chr(1)}"
        idx += 1
        return placeholder

    # 匹配 `...` 行内代码（跳过空的反引号）
    result = re.sub(r"`([^`]+)`", repl, line)
    return result, parts


def restore_inline_code(text: str, parts: list[str]) -> str:
    """将占位符还原为原始行内代码。"""
    for i, p in enumerate(parts):
        placeholder = f"{chr(0)}{i}{chr(1)}"
        text = text.replace(placeholder, f"`{p}`")
    return text


def protect_urls(line: str) -> tuple[str, list[str]]:
    """将 Markdown 链接/图片中的 URL 提取为占位符，返回 (替换后文本, 原链接列表)。

    匹配 [text](url) 和 ![alt](url)，将整个链接替换为 '\\x00\\x01{n}\\x00\\x01' 占位符
    （纯控制字符 + 数字，不含字母），避免被"字母+数字"或"数字+字母"正
    则误插入空格破坏。
    """
    parts: list[str] = []
    idx = 0

    def repl(m: re.Match) -> str:
        nonlocal idx
        parts.append(m.group(0))
        # 占位符为 \x00\x01{n}\x00\x01，纯控制字符 + 数字，无字母
        placeholder = f"{chr(0)}{chr(1)}{idx}{chr(0)}{chr(1)}"
        idx += 1
        return placeholder

    # 匹配 Markdown 链接 [text](url) 和图片 ![alt](url)
    # 括号内不能包含嵌套括号
    result = re.sub(r'!?\[(?:[^\[\]]|\[[^\[\]]*\])*\]\([^)]+\)', repl, line)
    return result, parts


def restore_urls(text: str, parts: list[str]) -> str:
    """将占位符还原为原始 Markdown 链接/图片。"""
    for i, p in enumerate(parts):
        placeholder = f"{chr(0)}{chr(1)}{i}{chr(0)}{chr(1)}"
        text = text.replace(placeholder, p)
    return text


def is_cjk(c: str) -> bool:
    """判断字符是否属于 CJK 文字体系（含汉字、CJK 标点、全角标点、通用引号破折号等）。"""
    return ('\u4e00' <= c <= '\u9fff' or    # CJK 统一汉字
            '\u3000' <= c <= '\u303f' or    # CJK 符号和标点
            '\uff00' <= c <= '\uffef' or    # 全角字符（含全角标点）
            '\u2000' <= c <= '\u206f')      # 通用标点（em dash、en dash、省略号等）


def _bold_content_first_last(text: str, start: int) -> tuple[str | None, str | None]:
    """从 ** 起始位置 start 向前扫描，找到匹配的 ** 并返回内容的第一个/最后一个非空格字符。

    返回 (first_non_space, last_non_space)，若未找到匹配 ** 则返回 (None, None)。
    """
    i = start + 2
    # 跳过 ** 后的空格
    while i < len(text) and text[i] == ' ':
        i += 1
    first = None
    last = None
    while i + 2 <= len(text):
        # 跳过 ***（粗斜体）中间出现的 ***
        if i + 3 <= len(text) and text[i:i+3] == '***':
            i += 1
            continue
        if text[i:i+2] == '**':
            content = text[start + 2:i]
            # 找到内容的第一个和最后一个非空格字符
            stripped = content.strip()
            if stripped:
                # 若加粗内容以 [[ 开头，取其显示文本（| 后）的首字符作为 first
                if stripped.startswith('[['):
                    close_pos = stripped.find(']]')
                    if close_pos != -1:
                        inner = stripped[2:close_pos]
                        pipe_pos = inner.find('|')
                        display = inner[pipe_pos + 1:] if pipe_pos >= 0 else inner
                        first = display[0] if display else stripped[0]
                    else:
                        first = stripped[0]
                else:
                    first = stripped[0]
                # 若加粗内容以 ]] 结尾，取其显示文本（| 后）的尾字符作为 last
                if stripped.endswith(']]'):
                    open_pos = stripped.rfind('[[')
                    if open_pos != -1:
                        inner = stripped[open_pos + 2:-2]
                        pipe_pos = inner.find('|')
                        display = inner[pipe_pos + 1:] if pipe_pos >= 0 else inner
                        last = display[-1] if display else stripped[-1]
                    else:
                        last = stripped[-1]
                else:
                    last = stripped[-1]
            break
        i += 1
    return first, last


def fix_bold_spacing(text: str) -> str:
    """处理 ** 加粗标记周围的空格。

    内部规则：** 与其内容之间不留空格（紧贴任意非空字符）。
    外部规则：左侧基于"前一个字符 或 加粗内容首字符"，任一非 CJK → 补 1 空格；
              右侧基于"后一个字符 或 加粗内容末字符"，任一非 CJK → 补 1 空格。
              非 CJK 字符包括：数字、英文字母、% 等符号。
              例：`-**力**`（前字符 `-` 非 CJK）+ `的**30%**`（首字符 `3` 非 CJK）均补空格。
    """
    result = []
    i = 0

    while i < len(text):
        # 跳过 ***（粗斜体）的前两个 *，当作普通字符处理
        if i + 3 <= len(text) and text[i:i+3] == '***':
            result.append(text[i])
            i += 1
            continue

        if i + 2 <= len(text) and text[i:i+2] == '**':
            # 向前扫描找到匹配的闭 **
            first_char, last_char = _bold_content_first_last(text, i)

            if first_char is None:
                # 没有匹配的闭 **，原样保留
                result.append(text[i])
                i += 1
                continue

            # --- 开启 ** ---
            # 删除 ** 前累积的空格（内部紧贴）
            while result and result[-1] == ' ':
                result.pop()
            # 外部左侧：内容以非 CJK 开头 或 前一个字符是非 CJK → 加 1 空格
            if result and first_char is not None and (not is_cjk(first_char) or not is_cjk(result[-1])):
                result.append(' ')
            result.append('**')
            # 跳过 ** 后的空格（内部紧贴）
            i += 2
            while i < len(text) and text[i] == ' ':
                i += 1

            # 将内容追加到结果（保持原样，包括可能的空格）
            content_start = i
            while i + 2 <= len(text):
                if text[i:i+3] == '***':
                    i += 1
                    continue
                if text[i:i+2] == '**':
                    content = text[content_start:i]
                    result.append(content)
                    # --- 关闭 ** ---
                    # 删除 ** 前累积的空格（内部紧贴）
                    while result and result[-1] == ' ':
                        result.pop()
                    result.append('**')
                    i += 2
                    # 跳过 ** 后的空格
                    while i < len(text) and text[i] == ' ':
                        i += 1
                    # 外部右侧：后一个字符 或 加粗内容末字符，任一非 CJK → 补 1 空格
                    if i < len(text) and last_char is not None and (not is_cjk(last_char) or not is_cjk(text[i])):
                        if result and result[-1] != ' ':
                            result.append(' ')
                    break
                i += 1
        else:
            result.append(text[i])
            i += 1

    return ''.join(result)


def remove_heading_number(text: str) -> str:
    """删除 Markdown 标题行（## 等）中的序号，包括"一、"、"1.2"、"3.4.5"等格式。

    仅当行以 # 开头（且非代码块内，由调用者保证）时生效。
    保留 # 与标题文本之间的一个空格。
    """
    m = re.match(r'^(\s*#{1,6}\s*).+', text)
    if not m:
        return text
    prefix = m.group(1)

    rest = text[len(prefix):]

    # 模式1：中文数字 + "、" — "一、" "二、" ... "十二、"
    rest = re.sub(r'^[一二三四五六七八九十百千]+[、，]\s*', '', rest)
    # 模式2：多层数字编号 + 空格 — "1.2 " "3.4.5 "
    rest = re.sub(r'^\d+(?:\.\d+)+\s+', '', rest)
    # 模式3：纯数字 + "、"/"．" — "3、" "2．"
    rest = re.sub(r'^\d+[、．，]\s*', '', rest)

    return prefix + rest


def process_line(line: str) -> str:
    """对单行文本执行所有标点规范化处理。"""
    # 1. 提取行内代码，保护后处理
    cleaned, code_parts = protect_inline_code(line)

    # 2. 提取 Markdown 链接/图片 URL，保护后处理
    cleaned, url_parts = protect_urls(cleaned)

    # 3. 英文双引号 → 「」（U+300C / U+300D）
    #    匹配 "content"（content 不含换行、不含双引号）
    cleaned = re.sub(r'"([^"]+)"', '\u300c\\1\u300d', cleaned)

    # 4. 英文单引号 → 『』（U+300E / U+300F）
    #    匹配 'content'（content 不含换行、不含单引号）
    cleaned = re.sub(r"'([^']+)'", '\u300e\\1\u300f', cleaned)

    # 4b. 统一已有的中文弯双引号 → 方角引号
    cleaned = re.sub(r'\u201c([^\u201d]+)\u201d', '\u300c\\1\u300d', cleaned)

    # 4c. 统一已有的中文弯单引号 → 方角单引号
    cleaned = re.sub(r'\u2018([^\u2019]+)\u2019', '\u300e\\1\u300f', cleaned)

    # 5. 数字间连字符 → ~  （去掉两端空格）
    cleaned = re.sub(r'(\d)\s*-\s*(\d)', r'\1~\2', cleaned)

    # 6. 数字与非数字之间的空格 → 有且仅有一个
    #    数字后跟中文或字母
    cleaned = re.sub(r'(\d)\s*([\u4e00-\u9fffa-zA-Z])', r'\1 \2', cleaned)
    #    中文或字母后跟数字
    cleaned = re.sub(r'([\u4e00-\u9fffa-zA-Z])\s*(\d)', r'\1 \2', cleaned)
    #    数字+符号（%,~）后跟中文（弥补前两条正则遇到 % 等符号无法匹配的情况）
    cleaned = re.sub(r'(\d[%\d.~]*)\s*([\u4e00-\u9fff])', r'\1 \2', cleaned)
    #    中文后跟英文字词（如 "郡GDP" → "郡 GDP"）
    cleaned = re.sub(r'([\u4e00-\u9fff])\s*([a-zA-Z]+)', r'\1 \2', cleaned)
    #    英文字词后跟中文（如 "GDP的" → "GDP 的"）
    cleaned = re.sub(r'([a-zA-Z]+)\s*([\u4e00-\u9fff])', r'\1 \2', cleaned)

    # 7. 处理 ** 加粗标记周围空格（逐字符扫描，区分开闭）
    cleaned = fix_bold_spacing(cleaned)

    # 8. 还原 URL
    cleaned = restore_urls(cleaned, url_parts)

    # 9. 还原行内代码
    cleaned = restore_inline_code(cleaned, code_parts)

    # 10. 删除标题行序号
    cleaned = remove_heading_number(cleaned)

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

    prev_blank = False

    for line in lines:
        stripped = line.strip()
        # 检测代码块边界（``` 开头，可能跟语言标识）
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            new_lines.append(line)
            prev_blank = False
            continue

        if in_code_block:
            # 代码块内部不处理
            new_lines.append(line)
            prev_blank = False
            continue

        processed = process_line(line)
        if processed != line:
            modified = True
        # 过滤独立成行的 Markdown 分隔线 `---`
        if processed.strip() == '---':
            modified = True
            continue

        # 空行压缩：连续多个空行压缩为有且仅有一个空行
        if not processed.strip():
            if prev_blank:
                modified = True
                continue
            prev_blank = True
        else:
            prev_blank = False

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