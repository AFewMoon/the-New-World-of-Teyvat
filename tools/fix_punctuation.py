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
                first = stripped[0]
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
            if result and (not is_cjk(first_char) or not is_cjk(result[-1])):
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
                    if i < len(text) and (not is_cjk(last_char) or not is_cjk(text[i])):
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
    # 模式4：纯数字 + 空格 — "3 "
    rest = re.sub(r'^\d+\s+', '', rest)

    return prefix + rest


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

    # 5. 数字与非数字之间的空格 → 有且仅有一个
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

    # 6. 处理 ** 加粗标记周围空格（逐字符扫描，区分开闭）
    cleaned = fix_bold_spacing(cleaned)

    # 7. 还原行内代码
    cleaned = restore_inline_code(cleaned, code_parts)

    # 8. 删除标题行序号
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

# ============================================================
# 行文逻辑分析
# ============================================================
#
# 一、整体架构（分层设计）
#
#   本工具采用"入口→调度→处理→保护"的四层架构：
#
#   1. 入口层  (main)
#      - 递归扫描 BASE_DIR 下所有 *.md 文件
#      - 通过 should_skip_file 过滤 tools/、.git/ 等无关目录
#      - 遍历调用 fix_file，统计修改数量与耗时
#
#   2. 文件层  (fix_file)
#      - 读入文件全部行
#      - 用 in_code_block 状态跟踪 ``` 代码块边界
#      - 代码块内：原样保留，不做任何处理
#      - 代码块外：逐行调用 process_line
#      - 有修改则写回文件
#
#   3. 核心处理层  (process_line)
#      对单行文本执行 8 步规范化流水线（见下文）
#
#   4. 保护/辅助层
#      - protect_inline_code / restore_inline_code：
#        将行内 `code` 替换为控制字符占位符 \x00CODE{n}\x01，
#        避免被后续正则误改，处理完再还原
#      - is_inside_pair：检查某位置是否被成对标记包裹（当前流程未直接使用，为扩展备用）
#
#
# 二、核心流水线（process_line 的 8 个步骤）
#
#   输入：单行文本（已确认不在代码块内）
#   输出：规范化后的文本
#
#   ① 提取行内代码
#      正则：`([^`]+)`  →  \x00CODE{n}\x01
#      目的：保护 `...` 不被后续步骤篡改
#
#   ② 英文双引号 → 中文双引号
#      正则："([^"]+)"  →  \u201c\\1\u201d
#      目的：将英文风格引号替换为中文排版标准
#
#   ③ 英文单引号 → 中文单引号
#      正则：'([^']+)'  →  \u2018\\1\u2019
#      目的：同上，统一为中文标点
#
#   ④ 数字间连字符 → 波浪线
#      正则：(\d)\s*-\s*(\d)  →  \1~\2
#      同时删除连字符两侧多余空格
#      例："85-90" → "85~90"
#
#   ⑤ 数字与非数字之间的空格规范化
#      方向 1：(\d)\s*([\u4e00-\u9fffa-zA-Z])  →  \1 \2
#      方向 2：([\u4e00-\u9fffa-zA-Z])\s*(\d)  →  \1 \2
#      效果：保证数字与中文/字母之间恰好有一个空格
#           "85%" → 不匹配（% 不在字符集内）
#           "85 万" → "85 万"
#           "85万" → "85 万"
#
#   ⑥ 处理 ** 加粗标记周围空格 (fix_bold_spacing)
#      算法：逐字符扫描，用 in_bold 状态区分开闭 **。
#      内部规则：** 与内容之间不留空格，紧贴任意非空字符。
#         - 遇到 ** 时，先删除累积在结果末尾的空格（内部紧贴）
#         - 跳过 ** 后跟随的空格（内部紧贴）
#      外部规则：借助辅助函数 is_cjk(c) 判断字符类型。
#        is_cjk 返回 True 的范围：汉字 \u4e00-\u9fff、
#        CJK 符号 \u3000-\u303f、全角字符 \uff00-\uffef、
#        通用标点 \u2000-\u206f（em dash、省略号等）。
#        左侧：加粗内容首字符 或 前一个字符，任一非 CJK → 补 1 空格
#        右侧：加粗内容末字符 或 后一个字符，任一非 CJK → 补 1 空格
#        （注意：此处"或"使得 `-**力**`（前字符 `-` 非 CJK）和
#         `的**30%**`（内容首 `3` 非 CJK）均能正确补空格）
#      特殊处理：***（粗斜体）的前两个 * 不作为加粗解析，原样保留。
#      例： "中文**加粗**内容" → "中文**加粗**内容"
#          "en **bold** text" → "en **bold** text"
#          "中文**加粗**"     → "中文**加粗**"
#          "en**bold**text"   → "en **bold** text"
#          "**：**"           → "**：**"
#          "、**"             → "、**"
#          "元**（**"         → "元**（**"
#
#   ⑦ 还原行内代码
#      将 \x00CODE{n}\x01 还原为 `原始内容`
#
#   ⑧ 删除 Markdown 标题行序号 (remove_heading_number)
#      先检查行是否以 `#{1,6}\s` 开头（匹配标题前缀）。
#      若是，提取前缀后对剩余文本依次尝试 4 种序号模式：
#        模式 1：中文数字 + "、" — "一、" "二、" … "十二、"  → 删除
#        模式 2：多层小数编号 + 空格 — "1.2 " "3.4.5 "      → 删除
#        模式 3：纯数字 + "、"/"．" — "3、" "2．"           → 删除
#        模式 4：纯数字 + 空格 — "3 "                       → 删除
#      前缀部分（# 本身）不受影响。
#      非标题行（不以 # 开头）直接跳过。
#      例："## 一、背景" → "## 背景"
#         "## 1.2 经济概况" → "## 经济概况"
#         "正文含一、不删" → 跳过
#
#
# 三、保护策略
#
#   ▸ 代码块保护：fix_file 中用 in_code_block 布尔标记，
#     代码块内所有行完全跳过 process_line
#   ▸ 行内代码保护：process_line 第①步提取占位符，第⑦步还原，
#     中间 5 步正则无法匹配控制字符，从而避免了误改
#
#
# 四、跳过过滤（should_skip_file）
#
#   检查路径的每一级目录名是否包含以下关键字之一：
#   tools、.git、node_modules、__pycache__
#   若包含则跳过，确保工具自身代码和版本控制文件不受影响
#
#
# 五、执行统计（main 末尾）
#
#   处理结束后打印：
#   • 总耗时（秒）
#   • 总数据量（MB）
#   • 处理速度（秒/MB 和 MB/秒）
#
#   便于在大规模规范化前后对比性能
#
# ============================================================