"""
文本规范化工具：遍历所有 *.md 文件，执行以下操作：
0. 制表符统一替换为 4 空格；缩进严格约束为 4 空格一级——非 4 倍数贴齐、
   不越级（每行缩进 = 父行缩进 + 4），2n 缩进方案整体 ×2 保持原有层级
0e. 括号格式统一：含中文 → `（……）`；不含中文 → `(...)`，并处理两侧间距
1. 将英文单双引号替换为中文引号
2. 数字间连字符 `-` 统一为 `~`
3. 数字与非数字（含中文、英文）之间的空格调整为有且仅有一个
4. 加粗符号 `**` 周围空格规范化：
   内部（** 与内容之间）不留空格，紧贴任意非空字符；
   外部（** 与相邻字符之间）左侧基于前一个字符 或 加粗内容首字符：
      任一非 CJK → 补 1 空格；右侧基于后一个字符 或 加粗内容末字符：
      任一非 CJK → 补 1 空格
      （数字、% 等视为非 CJK，故 `**30%~40%**` 两侧均补空格）
      （全角标点一侧除外：`**ZF-36**（`、`：**05:30**` 等紧贴不留空格）
      （相邻字符为 wikilink/MD 链接时按其显示文本判定首末字符，与
      左侧保持一致：`**移交** [[…|纳塔]]` → `**移交**[[…|纳塔]]`）
5. 中文与英文之间补 1 空格（"郡GDP" → "郡 GDP"，"GDP的" → "GDP 的"）
6. 数字+百分号/连字符（45%~50%、30%）后跟中文 → 补 1 空格
7. 代码块（```...```）和行内代码（`...`）内部不做变动
8. 删除 Markdown 标题行序号（"## 一、背景" → "## 背景"，"## 1.2 经济" → "## 经济"）
9. 删除独立成行的 Markdown 分隔线 `---`
10. 连续多个空行压缩为有且仅有一个空行
11. 删除内容文章（地区目录与 国际/ 下）独立成行的一级标题 `# ...`，整行
    删除，删除位置按空行参与压缩（与相邻空行去重、紧贴内容时保留 1 个
    分隔空行）；非文章文档（README.md、用户手册.md、AGENTS.md、
    .clinerules/ 等）豁免
12. 列表块规范化：列表块（含单项列表）与前后文之间补 1 空行，项与项
    之间的空行删除；每项以 `；` 或 `。` 结尾（非末项：原以 `；`/`。`
    结尾者保留，无标点者补 `；`；末项 `。`），以 `：` 结尾的标签项及
    `？`/`！` 结尾的项豁免；块引用（>）与表格（|）行、代码块内部不
    处理。局限：嵌套子列表会中断父块的连续扫描，父项各自成为单块末项，
    故嵌套结构中的父项均以 `。` 结尾
13. 数学区（$$...$$ / $...$）内剥离链接语法：[[target|label]] → label、
    [[target]] → target、[label](url) → label、![alt](url) → alt。
    链接会被 Markdown 渲染为 <a> 标签，KaTeX 无法解析，导致公式内
    出现链接或渲染失败
"""

import argparse
import re
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

ARTICLE_TOP_DIRS = {"蒙德", "璃月", "稻妻", "须弥", "枫丹", "纳塔", "挪德卡莱", "国际"}


def is_article_file(path: Path) -> bool:
    """是否为内容文章文件（地区目录或 国际/ 下），其一级标题应被删除。"""
    return path.relative_to(BASE_DIR).parts[0] in ARTICLE_TOP_DIRS


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
    # (?!\[) 负向前瞻防止将 [[wikilink]](paren) 误认为 Markdown 链接
    result = re.sub(r'!?\[(?!\[)(?:[^\[\]]|\[[^\[\]]*\])*\]\([^)]+\)', repl, line)
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


def is_fullwidth_punct(c: str) -> bool:
    """判断字符是否为 CJK 标点（全角标点、CJK 符号、通用标点；不含汉字与全角字母数字）。"""
    if '\u3000' <= c <= '\u303f' or '\u2000' <= c <= '\u206f':
        return True
    if '\uff00' <= c <= '\uffef':
        return not ('\uff10' <= c <= '\uff19' or    # 全角数字
                    '\uff21' <= c <= '\uff3a' or    # 全角大写字母
                    '\uff41' <= c <= '\uff5a')      # 全角小写字母
    return False


def _extract_bold_first_last(content_raw: str, url_parts: list[str] | None = None) -> tuple[str | None, str | None]:
    """从加粗内容的原始文本提取首末有效字符（处理 wikilink / MD 链接嵌套）。"""
    stripped = content_raw.strip()
    if not stripped:
        return None, None

    # 还原 Markdown 链接占位符为显示文本，使间距行为与 wikilink 一致
    if url_parts:
        for i, part in enumerate(url_parts):
            placeholder = f"{chr(0)}{chr(1)}{i}{chr(0)}{chr(1)}"
            if placeholder in stripped:
                m = re.match(r'^\[([^\[\]]+)\]\([^)]+\)', part)
                if m:
                    stripped = stripped.replace(placeholder, m.group(1))
                else:
                    m = re.match(r'^\[\[(?:[^\[\]]+\|)?([^\[\]]+)\]\]\([^)]+\)', part)
                    if m:
                        stripped = stripped.replace(placeholder, m.group(1))

    first: str | None = stripped[0]
    last: str | None = stripped[-1]
    if stripped.startswith('[['):
        close = stripped.find(']]')
        if close != -1:
            inner = stripped[2:close]
            pipe = inner.find('|')
            display = inner[pipe + 1:] if pipe >= 0 else inner
            if display:
                first = display[0]
    if stripped.endswith(']]'):
        open_pos = stripped.rfind('[[')
        if open_pos != -1:
            inner = stripped[open_pos + 2:-2]
            pipe = inner.find('|')
            display = inner[pipe + 1:] if pipe >= 0 else inner
            if display:
                last = display[-1]
    return first, last


VERSION_NUM_RE = re.compile(r'^\d+(?:\.\d+)+\s+')


def _effective_prev_char(chars: list[str], url_parts: list[str] | None = None) -> str:
    """取 ** 开启标记前一元素的有效末字符：解开尾部 wikilink 或 MD 链接占位符。"""
    s = ''.join(chars)
    if s.endswith(']]'):
        open_pos = s.rfind('[[')
        if open_pos != -1:
            inner = s[open_pos + 2:-2]
            pipe = inner.find('|')
            display = inner[pipe + 1:] if pipe >= 0 else inner.rsplit('/', 1)[-1]
            if display:
                return display[-1]
        return ']'
    if url_parts:
        m = re.search(r'\x00\x01(\d+)\x00\x01$', s)
        if m and int(m.group(1)) < len(url_parts):
            part = url_parts[int(m.group(1))]
            lm = re.match(r'^\[([^\[\]]+)\]\([^)]+\)', part)
            if lm and lm.group(1):
                return lm.group(1)[-1]
    return s[-1]


def _effective_next_char(text: str, pos: int, url_parts: list[str] | None = None) -> str:
    """取 ** 关闭标记后一元素的有效首字符：解开开头 wikilink 或 MD 链接占位符。"""
    if pos + 2 <= len(text) and text[pos:pos+2] == '[[':
        close = text.find(']]', pos)
        if close != -1:
            inner = text[pos+2:close]
            pipe = inner.find('|')
            display = inner[pipe + 1:] if pipe >= 0 else inner
            if display:
                return display[0]
        return '['
    if url_parts:
        m = re.match(r'\x00\x01(\d+)\x00\x01', text[pos:])
        if m and int(m.group(1)) < len(url_parts):
            part = url_parts[int(m.group(1))]
            lm = re.match(r'^\[([^\[\]]+)\]\([^)]+\)', part)
            if lm and lm.group(1):
                return lm.group(1)[0]
    return text[pos]


def fix_bold_spacing(text: str, url_parts: list[str] | None = None) -> str:
    """处理 ** 加粗标记周围的空格（单次扫描，无重复遍历）。"""
    result = []
    i = 0

    while i < len(text):
        if i + 3 <= len(text) and text[i:i+3] == '***':
            result.append(text[i])
            i += 1
            continue

        if i + 2 <= len(text) and text[i:i+2] == '**':
            open_pos = i
            # 扫描找到闭 **，同时跳过 *** 干扰
            j = open_pos + 2
            while j < len(text) and text[j] == ' ':
                j += 1
            k = j
            close_pos = -1
            while k + 2 <= len(text):
                if k + 3 <= len(text) and text[k:k+3] == '***':
                    k += 1
                    continue
                if text[k:k+2] == '**':
                    close_pos = k
                    break
                k += 1

            if close_pos == -1:
                result.append(text[i])
                i += 1
                continue

            content_raw = text[j:close_pos]
            first_char, last_char = _extract_bold_first_last(content_raw, url_parts)

            # --- 开启 ** ---
            while result and result[-1] == ' ':
                result.pop()
            prev_eff = _effective_prev_char(result, url_parts) if result else None
            if (result and first_char is not None
                    and not (prev_eff is not None and is_fullwidth_punct(prev_eff))
                    and (not is_cjk(first_char) or (prev_eff is not None and not is_cjk(prev_eff)))):
                result.append(' ')
            result.append('**')
            result.append(content_raw)
            # --- 关闭 ** ---
            while result and result[-1] == ' ':
                result.pop()
            result.append('**')
            i = close_pos + 2
            while i < len(text) and text[i] == ' ':
                i += 1
            next_eff = _effective_next_char(text, i, url_parts) if i < len(text) else None
            if (next_eff is not None and last_char is not None
                    and not is_fullwidth_punct(next_eff)
                    and (not is_cjk(last_char) or not is_cjk(next_eff))):
                if result and result[-1] != ' ':
                    result.append(' ')
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


def _snap_to_4(width: int) -> int:
    """将缩进宽度贴齐到最近的 4 倍数（半进位：1→0、2→4、3→4、6→8）。"""
    return (width + 2) // 4 * 4


def fix_indentation(lines: list[str]) -> tuple[list[str], bool]:
    """严格约束行首缩进（代码块内部与空行不做处理）：

    1. 所有非代码块行首缩进统一为 4 的倍数；
    2. 不越级缩进：每行缩进 = 父行（上方最近、缩进更小的行所代表的层级）缩进 + 4，
       同一逻辑层（宽度相等的兄弟行）缩进一致，缩进归零处重置层级栈；
    3. 2n 缩进方案文件（存在 ≡ 2 mod 4 的宽度）整体 ×2 保持原有层级，
       其余先贴齐最近的 4 倍数再按层级映射。

    返回 (修复后的行列表, 是否有修改)。"""
    # 第一步：统计非代码块、非空行的缩进宽度（制表符按 4 展开）
    in_code = False
    widths: set[int] = set()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not stripped:
            continue
        m = re.match(r'^(\s+)\S', line)
        if m:
            widths.add(len(m.group(1).replace('\t', '    ')))
    if not widths:
        return lines, False

    # 第二步：宽度贴齐到 4 倍数
    if any(w % 4 == 2 for w in widths):
        # 2n 方案：偶数宽度 ×2 保持层级；奇数宽度贴齐 4 倍数
        snapped = {w: (w * 2 if w % 2 == 0 else _snap_to_4(w)) for w in widths}
    else:
        snapped = {w: _snap_to_4(w) for w in widths}

    # 第三步：栈式结构重排，每行目标缩进 = 父级目标 + 4
    targets: dict[int, int] = {0: 0}
    stack: list[list[int]] = [[0, 0]]  # [贴齐后宽度, 目标缩进]
    in_code = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code or not stripped:
            continue
        m = re.match(r'^(\s+)\S', line)
        w = len(m.group(1).replace('\t', '    ')) if m else 0
        sw = snapped.get(w, w)
        if sw == 0:
            stack = [[0, 0]]
            targets[w] = 0
            continue
        while stack and stack[-1][0] >= sw:
            stack.pop()
        if not stack:
            stack = [[0, 0]]
        target = stack[-1][1] + 4
        targets[w] = target
        if sw > stack[-1][0]:
            stack.append([sw, target])

    # 第四步：重写行首空白
    new_lines: list[str] = []
    changed = False
    in_code = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            new_lines.append(line)
            continue
        if in_code or not stripped:
            new_lines.append(line)
            continue
        m = re.match(r'^(\s+)(\S.*)$', line)
        if not m:
            new_lines.append(line)
            continue
        old = m.group(1)
        w = len(old.replace('\t', '    '))
        target = targets.get(w, w)
        if ' ' * target == old:
            new_lines.append(line)
        else:
            new_lines.append(' ' * target + m.group(2))
            changed = True
    return new_lines, changed


LIST_MARKER_RE = re.compile(r'^(\s*)([-*+]|\d+[.、)])\s+\S')


def _fix_item_end(item: str, is_last: bool) -> str:
    """补全列表项结尾标点：非末项保留原有 `；`/`。`，无标点者补 `；`；末项 → `。`。

    以 `：` 结尾的项视为标签项（内容在后续行）、以 `？`/`！` 结尾的
    项保留原义，均不做修改。
    """
    if item.endswith(('：', '？', '！')):
        return item
    if is_last:
        if item.endswith('；'):
            return item[:-1] + '。'
        if item.endswith('。'):
            return item
        return item.rstrip('，、') + '。'
    if item.endswith(('；', '。')):
        return item
    return item.rstrip('，、') + '；'


def fix_list_blocks(lines: list[str]) -> tuple[list[str], bool]:
    """列表块规范化（规则 12）：

    1. 列表块（含单项列表）与前后文之间补 1 个空行（紧邻行已是空行、
       前后为列表项/引用/表格/缩进续行时跳过）；项与项之间的空行删除；
    2. 每项以 `；` 或 `。` 结尾：非末项保留原有 `；`/`。`、无标点者补
       `；`，末项改为/补为 `。`；以 `：`/`？`/`！` 结尾的项豁免；
    3. 块引用（>）、表格（|）行与代码块内部不处理。

    返回 (修复后的行列表, 是否有修改)。
    """
    new_lines: list[str] = []
    changed = False
    n = len(lines)
    i = 0
    in_code = False
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith('```'):
            in_code = not in_code
            new_lines.append(line)
            i += 1
            continue
        if in_code or not stripped or stripped.startswith(('>', '|')):
            new_lines.append(line)
            i += 1
            continue
        m = LIST_MARKER_RE.match(line)
        if not m:
            new_lines.append(line)
            i += 1
            continue
        indent = len(m.group(1))
        # 扫描同缩进的连续列表项（其间空行将被跳过删除）
        items: list[int] = []
        pending_blank = False
        j = i
        while j < n:
            s = lines[j].strip()
            if not s:
                pending_blank = True
                j += 1
                continue
            if s.startswith(('```', '>', '|')):
                break
            mm = LIST_MARKER_RE.match(lines[j])
            if mm and len(mm.group(1)) == indent:
                items.append(j)
                if pending_blank:
                    changed = True
                    pending_blank = False
                j += 1
                continue
            break
        if not items:
            new_lines.append(line)
            i += 1
            continue
        # 空行规则：列表块（含单项列表）与前后文之间补 1 个空行
        k = i - 1
        if k >= 0:
            pv = lines[k]
            ps = pv.strip()
            if (ps and not ps.startswith(('>', '|'))
                    and not LIST_MARKER_RE.match(pv)
                    and not re.match(r'^\s+\S', pv)):
                new_lines.append('')
                changed = True
        # 标点规则：逐项补全结尾标点
        for idx, it in enumerate(items):
            new_item = _fix_item_end(lines[it].rstrip(), idx == len(items) - 1)
            if new_item != lines[it]:
                changed = True
            new_lines.append(new_item)
        k = items[-1] + 1
        if k < n:
            nv = lines[k]
            ns = nv.strip()
            if (ns and not ns.startswith(('>', '|'))
                    and not LIST_MARKER_RE.match(nv)
                    and not re.match(r'^\s+\S', nv)):
                new_lines.append('')
                changed = True
        i = items[-1] + 1
    return new_lines, changed


def _has_cjk_or_url_cjk(content: str, url_parts: list[str] | None = None) -> bool:
    """检查 content 是否包含中文，若有 URL 占位符则检查原文 URL 含中文的情况。"""
    if re.search(r'[\u4e00-\u9fff]', content):
        return True
    if url_parts:
        for i, part in enumerate(url_parts):
            placeholder = f"{chr(0)}{chr(1)}{i}{chr(0)}{chr(1)}"
            if placeholder in content and re.search(r'[\u4e00-\u9fff]', part):
                return True
    return False


def fix_parentheses(text: str, url_parts: list[str] | None = None) -> str:
    """统一括号格式：
    - 括号内容不含中文 → 英文括号 `(...)`；
    - 括号内容含中文 → 中文括号 `（...）`。
    """
    # 1. 中文括号 → 英文括号（仅当内容及 URL 均不含 CJK 时转换）
    text = re.sub(
        r'（([^）]*)）',
        lambda m: f'({m.group(1)})' if not _has_cjk_or_url_cjk(m.group(1), url_parts) else m.group(0),
        text
    )
    # 2. 英文括号 → 中文括号（仅检查直接内容，不查 URL 占位符以免过度转换）
    text = re.sub(
        r'\(([^)]*)\)',
        lambda m: f'（{m.group(1)}）' if re.search(r'[\u4e00-\u9fff]', m.group(1)) else m.group(0),
        text
    )
    return text


def _strip_links_in_math_span(span: str) -> str:
    """在单个数学区片段内剥离所有链接语法，仅保留显示文本。

    [[target|label]] → label、[[target]] → target、
    [label](url) → label、![alt](url) → alt。
    """
    # wikilink（含显示文本与纯目标，图片式 ![[...]] 一并处理）
    span = re.sub(r"!?\[\[(?:[^\[\]]+\|)?([^\[\]]+)\]\]", r"\1", span)
    # 图片 ![alt](url)
    span = re.sub(r"!\[([^\[\]]*)\]\([^)]*\)", r"\1", span)
    # Markdown 链接 [label](url)（负向前瞻排除图片；KaTeX 的 [..] 后跟 ( 的
    # 场景极罕见，如 \begin{bmatrix} 的 ]} 不受影响）
    span = re.sub(r"(?<!!)\[([^\[\]]*)\]\([^)]*\)", r"\1", span)
    return span


def fix_links_in_math(text: str) -> tuple[str, bool]:
    """规则 13：数学区（$$...$$ / $...$）内禁止任何链接语法。

    MkDocs 的 Markdown 处理器会先把 [label](url) / [[wikilink]] 渲染为
    <a> 标签，KaTeX auto-render 随后再解析数学，导致公式内出现可点击
    链接或渲染失败。本规则将数学区内的链接语法剥离为纯显示文本。
    返回 (处理后的文本, 是否发生修改)。
    """
    math_spans: list[tuple[int, int]] = []
    # 块级数学 $$...$$（可跨行），优先匹配
    for m in re.finditer(r"\$\$[\s\S]*?\$\$", text):
        math_spans.append((m.start(), m.end()))
    # 行内数学 $...$（单行），跳过与块级数学重叠的匹配
    for m in re.finditer(r"\$[^\n$]+\$", text):
        if not any(s <= m.start() < e for s, e in math_spans):
            math_spans.append((m.start(), m.end()))

    modified = False
    chunks: list[str] = []
    pos = 0
    for s, e in sorted(math_spans):
        chunks.append(text[pos:s])
        span = text[s:e]
        fixed = _strip_links_in_math_span(span)
        if fixed != span:
            modified = True
        chunks.append(fixed)
        pos = e
    chunks.append(text[pos:])
    return "".join(chunks), modified


def process_line(line: str) -> str:
    """对单行文本执行所有标点规范化处理。"""
    # 0. 制表符统一替换为 4 空格（代码块内部由 fix_file 跳过）
    line = line.replace('\t', '    ')
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

    # 4d. 统一括号格式（根据内容语言选择英文/中文括号）
    cleaned = fix_parentheses(cleaned, url_parts)

    # 5. 数字间连字符 → ~  （去掉两端空格）
    cleaned = re.sub(r'(\d)\s*-\s*(\d)', r'\1~\2', cleaned)

    # 6. 数字与非数字之间的空格 → 有且仅有一个
    #    数字后跟中文或字母
    cleaned = re.sub(r'(\d)\s*([\u4e00-\u9fffa-zA-Z])', r'\1 \2', cleaned)
    #    中文或字母后跟数字
    cleaned = re.sub(r'([\u4e00-\u9fffa-zA-Z])\s*(\d)', r'\1 \2', cleaned)
    #    数字+百分比等符号后跟中文（弥补前两条遇到 % 等符号无法匹配的情况）
    cleaned = re.sub(r'(\d[%\d.]*)\s*([\u4e00-\u9fff])', r'\1 \2', cleaned)
    #    ~ 分隔的数字范围后跟中文
    cleaned = re.sub(r'(\d[%\d]*~[%\d]*)\s*([\u4e00-\u9fff])', r'\1 \2', cleaned)
    #    中文后跟英文字词（如 "郡GDP" → "郡 GDP"）
    cleaned = re.sub(r'([\u4e00-\u9fff])\s*([a-zA-Z]+)', r'\1 \2', cleaned)
    #    英文字词后跟中文（如 "GDP的" → "GDP 的"）
    cleaned = re.sub(r'([a-zA-Z]+)\s*([\u4e00-\u9fff])', r'\1 \2', cleaned)
    #    英文括号与中文之间的空格
    cleaned = re.sub(r'([\u4e00-\u9fff])\s*\(', r'\1 (', cleaned)
    cleaned = re.sub(r'\)\s*([\u4e00-\u9fff])', r') \1', cleaned)
    #    wikilink 与英文括号之间：`]]` 后跟 `(` → 补 1 空格
    cleaned = re.sub(r'\]\]\s*\(', r']] (', cleaned)

    # 7. 处理 ** 加粗标记周围空格（逐字符扫描，区分开闭）
    cleaned = fix_bold_spacing(cleaned, url_parts)

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
    # 规则 13：数学区（$$...$$ / $...$）内剥离链接语法。须在其他行级规则
    # 之前执行，避免 protect_urls 等将数学区内的链接提前保护而不受处理
    joined = "\n".join(lines)
    joined, math_fixed = fix_links_in_math(joined)
    if math_fixed:
        # 用 split 而非 splitlines 还原行，避免丢失末尾空行元素
        lines = joined.split("\n")
    # 缩进严格约束（4 空格一级、不越级），必须在具体处理之前
    lines, indent_fixed = fix_indentation(lines)
    # 列表块规范化（空行 + 结尾标点）
    lines, list_fixed = fix_list_blocks(lines)
    new_lines: list[str] = []
    in_code_block = False
    modified = indent_fixed or list_fixed or math_fixed
    removed_h1 = False

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

        # 11. 删除内容文章的一级标题 `# ...`（非文章文档豁免；兼容 BOM 头）
        #     整行删除，该位置按空行参与压缩：与相邻空行去重，紧贴内容时保留 1 个分隔空行
        if processed.strip().lstrip("\ufeff").startswith("# ") and is_article_file(path):
            modified = True
            removed_h1 = True
            if not prev_blank:
                new_lines.append("")
                prev_blank = True
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
        # 删除一级标题后清理文件首部残留空行
        if removed_h1:
            new_content = new_content.lstrip("\n")
        # 确保末尾有换行（如果原文件有）
        if path.read_text(encoding="utf-8").endswith("\n"):
            new_content += "\n"
        path.write_text(new_content, encoding="utf-8")
        print(f"  [FIXED] {path.relative_to(BASE_DIR)}")

    return modified


def main() -> None:
    parser = argparse.ArgumentParser(description="文本规范化工具")
    parser.add_argument("--check", action="store_true",
                        help="检查模式：仅列出需要修复的文件，不修改，返回非零退出码")
    parser.add_argument("--files", nargs="*",
                        help="仅处理指定文件（路径相对于仓库根目录）")
    args = parser.parse_args()

    if args.files:
        md_files = sorted(
            p for fn in args.files
            if fn.endswith(".md")
            for p in [BASE_DIR / fn]
            if p.exists() and p.is_file() and not should_skip_file(p)
        )
    else:
        md_files = sorted(
            p for p in BASE_DIR.rglob("*.md")
            if not should_skip_file(p) and p.is_file()
        )

    if not md_files:
        print("未找到任何 .md 文件，退出。")
        return

    print(f"\n找到 {len(md_files)} 个 .md 文件")
    total_bytes = sum(p.stat().st_size for p in md_files)

    if args.check:
        # --check 模式：只报告不修改
        needs_fix: list[str] = []
        for path in md_files:
            original = path.read_text(encoding="utf-8")
            lines = original.splitlines(keepends=False)
            # 规则 13：数学区内剥离链接语法（与修复模式保持一致）
            joined = "\n".join(lines)
            joined, _ = fix_links_in_math(joined)
            # 用 split 而非 splitlines 还原行，避免丢失末尾空行元素
            lines = joined.split("\n")
            # 与修复模式共用同一缩进约束
            lines, _ = fix_indentation(lines)
            lines, _ = fix_list_blocks(lines)
            in_code = False
            processed_lines: list[str] = []
            prev_blank = False
            removed_h1 = False
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("```"):
                    in_code = not in_code
                    processed_lines.append(line)
                    prev_blank = False
                    continue
                if in_code:
                    processed_lines.append(line)
                    prev_blank = False
                    continue
                processed = process_line(line)
                if processed.strip() == '---':
                    continue
                # 11. 删除内容文章的一级标题 `# ...`（与修复模式保持一致）
                if processed.strip().lstrip("\ufeff").startswith("# ") and is_article_file(path):
                    removed_h1 = True
                    if not prev_blank:
                        processed_lines.append("")
                        prev_blank = True
                    continue
                if not processed.strip():
                    if prev_blank:
                        continue
                    prev_blank = True
                else:
                    prev_blank = False
                processed_lines.append(processed)
            result = "\n".join(processed_lines)
            if removed_h1:
                result = result.lstrip("\n")
            if original.endswith("\n"):
                result += "\n"
            if result != original:
                needs_fix.append(str(path.relative_to(BASE_DIR)))
        if needs_fix:
            print(f"\n以下 {len(needs_fix)} 个文件需要修复：")
            for f in needs_fix:
                print(f"  {f}")
            sys.exit(1)
        else:
            print("所有文件格式正确。")
        return

    # 正常模式：修复文件
    print("\n--- 开始规范化 ---")
    start_time = time.perf_counter()
    fixed_count = 0
    for path in md_files:
        if fix_file(path):
            fixed_count += 1
    end_time = time.perf_counter()

    total_mb = total_bytes / (1024 * 1024)
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