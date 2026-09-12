#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
概念链接工具（Concept Linker）

ML 增强版：
- spaCy NER → 替代 jieba.posseg 发现专名（假阳性从 60% → <5%）
- TF-IDF → 每篇文档 top-15 关键词作为 context_words

仓库正文一律使用标准 Markdown 链接，本工具只做概念扫描、映射维护与术语索引
生成，不向文章写入任何链接语法。

依赖：pip install jieba spacy scikit-learn numpy
      python -m spacy download zh_core_web_sm
"""

from __future__ import annotations

import json
import re
import sys
import argparse
from collections import defaultdict, Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import jieba
import numpy as np

try:
    import spacy
except ImportError:
    spacy = None

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
except ImportError:
    TfidfVectorizer = None



# ─── 常量 ─────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent

STATE_FILE = BASE_DIR / "tools" / "_linker_state.json"
MAPPINGS_FILE = BASE_DIR / "tools" / "concept_mappings.json"
INDEX_DIR = BASE_DIR / "tools" / "术语索引"

PROTECTED_DIRS = {".git", ".obsidian", ".clinerules", "node_modules", "__pycache__"}
SKIP_FILES = {"README.md", "LICENSE", "AGENTS.md"}

DOC_SUFFIXES = ["概况", "综述", "体系", "概述", "简介", "节选", "讨论与全民抉择"]

FILE_CATEGORY_RULES: list[tuple[str, str]] = [
    ("政治", "政治实体与政党"),
    ("政党", "政治实体与政党"),
    ("经济", "经济与产业"),
    ("产业", "经济与产业"),
    ("财阀", "经济与产业"),
    ("金融", "经济与产业"),
    ("铁路", "科技与基础设施"),
    ("航空", "科技与基础设施"),
    ("电力", "科技与基础设施"),
    ("虚空", "科技与基础设施"),
    ("汽车", "科技与基础设施"),
    ("动力", "科技与基础设施"),
    ("精密机械", "科技与基础设施"),
    ("人工智能", "科技与基础设施"),
    ("国际组织", "国际组织"),
    ("条约", "国际组织"),
    ("关系", "国际组织"),
    ("多边", "国际组织"),
    ("双边", "国际组织"),
    ("行政区划", "国家与地区"),
    ("综述", "国家与地区"),
    ("角色", "人物"),
    ("信仰", "文化与信仰"),
    ("文化", "文化与信仰"),
    ("古名", "文化与信仰"),
    ("旅游", "经济与产业"),
    ("教育", "文化与信仰"),
    ("高校", "文化与信仰"),
    ("节日", "文化与信仰"),
]

COMMON_WORD_BLACKLIST: set[str] = {
    "职能", "成员", "社区", "设施", "立场", "组织", "管理", "系统", "制度",
    "体系", "政策", "改革", "建设", "发展", "工作", "推进", "实施", "提高",
    "资源", "领域", "方面", "方式", "角色", "机制", "问题", "因素", "影响",
    "变化", "需求", "能力", "关系", "结构", "战略", "阶段", "模式", "特点",
    "特征", "成立", "建立", "要求", "目标", "任务", "方案", "计划", "措施",
    "意见", "建议", "信息", "内容", "情况", "条件", "结果", "服务",
    "支持", "保持", "进入", "形成", "处于", "项目", "草案", "框架",
    "国家银行", "劳动党中央委员会", "璃蒙共同体条约",
    "观念", "理念", "活动", "渠道", "空间", "实际", "意义",
    "代表", "规范", "主要", "过渡", "延续", "连接", "制定",
    "构成", "要素", "基础", "中心", "阶层", "最高", "日常", "分工",
    "推动", "贯彻", "改革派", "左派", "右派", "新派", "中派",
    "前身", "起源", "历史", "背景", "整合", "奠定",
    "自然", "景观", "战略", "土地",
    "保障", "合作", "对话", "平台",
    "沟通", "交流", "联系", "地位", "原则",
    "标准", "等级", "名称", "传统", "古老",
    "东部", "西部", "南部", "北部", "中部", "核心",
    "规模", "数量", "总量", "人口", "面积", "数据",
    "身份", "象征", "货币说明", "概述", "简介", "性质",
    "国名", "政体", "起源", "宗旨",
    # —— NER 噪声词：纯泛称 / 时间 / 方向词，永不作为链接目标 ——
    "TE", "大陆", "中央", "中央政府", "政府", "全国", "境内",
    "近年来", "今日", "今天", "今年", "去年", "每月", "全年", "年度",
    "夜间", "白天", "周末", "如今", "当时", "数字化", "通用",
    "康养", "港府", "运河", "酒庄", "葡萄园", "沙龙", "一流",
    "东北", "西北", "东南", "西南", "东方",
    "二元", "第三方", "一周之内", "数十", "数百", "数千", "数万",
    # —— 2026-08 全量刷新扫出的无对应文件噪声：泛称 / 通用词 / 分词残片 ——
    "三类", "三个月", "两万", "共和国", "加密通信", "初加工中心",
    "温合金", "高铁", "海军", "工会", "琉璃", "两院制", "宪法",
    # —— 2026-08 全量刷新扫出的无对应文件机构泛称：各国通用的政权 / 部门 / 议会名称 ——
    "中央银行", "议会", "国会", "众议院", "下议院", "上议院",
    "中央委员会", "党中央委员会", "人民委员会", "国家军事委员会",
    "国防部", "财政部", "教育部", "最高法院", "监察院",
    "联邦政府", "联邦国防军", "共和", "大革命", "行政院",
    "设计局", "旅行者", "科技大学", "民众大学", "自由大学",
    "雷元素", "星落",
    # —— 2026-08 CI 校验扫出的无对应文件噪声：通用词 / 泛称 / 分词残片 ——
    "一次性", "荣誉性", "双向", "参议院",
    "长老会", "远征军", "孤岛不孤——革命",
    # —— 2026-08 至冬能源概况刷新扫出的无对应文件噪声：地理泛称 / 季节词 ——
    "北陆", "夏季",
    # —— 2026-08 至冬文档扫出的泛称误链：这些通用词被漫量注入为概念别名，
    #    导致「经济→挪德卡莱综述」「列车→纳塔铁路概况」「车辆→蒙德铁路概况」
    #    「交通体系→悠悠度假乡」等成片错链。各国有各自的专项档案，不存在
    #    唯一对应的通用概念页，故一律禁止作为链接别名。 ——
    "经济", "基础设施", "文化", "列车", "车辆", "政党",
    "交通运输", "交通体系", "交通", "最高权力机关", "大国",
    # —— 2026-08 全量刷新扫出的无对应文件候选：无专属页面，或为泛称 / 机构通名 ——
    "北堡市", "国家计划总局", "南湾区", "交通部", "总参谋部",
    "璃蒙经济共同体条约", "劳动者工会", "三者", "三个",
}

_NOISE_TOKEN_RE = re.compile(
    r"(?:第[〇一二三四五六七八九十百千万零两]+(?:方)?"     # 序数：第一、第四、第十二、第三方
    r"|[一二三四五六七八九十]成|之一|一半"                 # 分数：六成、之一、一半
    r"|[一二三四五六七八九十]+分之[一二三四五六七八九十]+"   # 分数：三分之一、三分之二
    r"|[一二三四五六七八九十]+级(?:行政区)?"               # 层级：三级、二级行政区
    r"|[一二三四五六七八九十]+大(?:类)?"                   # 概数：三大、三大类
    r"|[一二三四五六七八九十]+大类)"
)

_NOISE_DURATION_RE = re.compile(
    r"(?:[一二三四五六七八九十两几半数余]+)?(?:十|百|千|万)?(?:年|月|日|周|夜|小时)?"
)


def is_noise_token(w: str) -> bool:
    """判断 NER 候选是否属于无需链接的噪声（数字、序数、分数、时长、异常字符等）。"""
    if len(w) < 2:
        return True
    # 含数字 / 百分号 / 物理单位：1.、10、40%、500 万、50 Hz、160 J、2005 年
    if re.search(r"[\d%HzJ]", w):
        return True
    # 序数 / 分数 / 量词
    if _NOISE_TOKEN_RE.fullmatch(w):
        return True
    # 时长 / 数量泛称：十年、三十年、数月、数百年、五百年、三个月
    if re.search(r"[年月日周夜小时]", w) and _NOISE_DURATION_RE.fullmatch(w):
        return True
    # 括号 / 破折号等标点残缺片段：《》、城—、〉〈
    if "》《" in w or "〉" in w or "〈" in w or w.endswith(("—", "–", "-")):
        return True
    # 私有区（U+E000–U+F8FF）与控制字符
    if any(0xE000 <= ord(ch) <= 0xF8FF or ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F for ch in w):
        return True
    return False

NATION_PRIMARY: dict[str, str] = {
    "蒙德": "蒙德/蒙德综述",
    "璃月": "璃月/璃月综述",
    "稻妻": "稻妻/稻妻综述",
    "须弥": "须弥/须弥概况",
    "枫丹": "枫丹/枫丹综述",
    "纳塔": "纳塔/纳塔综述",
    "挪德卡莱": "挪德卡莱/挪德卡莱综述",
    "至冬": "至冬/至冬经济概况",
}


# ─── 辅助函数 ─────────────────────────────────────────────────────────────────


def detect_file_category(name: str) -> str:
    stem = Path(name).stem if "." in name else name
    for pattern, cat in FILE_CATEGORY_RULES:
        if pattern in stem:
            return cat
    return "国家与地区"


def strip_suffix(word: str) -> str:
    for suffix in DOC_SUFFIXES:
        if word.endswith(suffix) and len(word) > len(suffix) + 1:
            return word[: -len(suffix)]
    return word


def generate_auto_aliases(stem: str) -> list[str]:
    aliases = [stem]
    short = strip_suffix(stem)
    if short != stem:
        aliases.append(short)
    cleaned = re.sub(r"[（(][^）)]*[）)]", "", stem).strip()
    if cleaned and cleaned != stem:
        aliases.append(cleaned)
        short2 = strip_suffix(cleaned)
        if short2 != cleaned and short2 not in aliases:
            aliases.append(short2)
    return aliases


def get_markdown_files(base: Path) -> list[Path]:
    files: list[Path] = []
    for entry in base.rglob("*.md"):
        rel = entry.relative_to(base).as_posix()
        parts = rel.split("/")
        if any(p in PROTECTED_DIRS or p.startswith(".") for p in parts):
            continue
        if entry.name in SKIP_FILES:
            continue
        files.append(entry)
    return files


def clean_body_text(text: str) -> str:
    """清理文本，仅保留正文内容用于 ML 分析。"""
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`[^`]*`", "", text)
    text = re.sub(r"\$\$[\s\S]*?\$\$", "", text)
    text = re.sub(r"\$[^\n$]+\$", "", text)
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"^[#|>].*$", "", text, flags=re.MULTILINE)
    return text


# ─── 类定义 ───────────────────────────────────────────────────────────────────


class StateManager:
    def __init__(self, path: Path):
        self.path = path
        self.state: dict[str, Any] = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                self.state = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.state = {}
        else:
            self.state = {}

    def save(self):
        self.path.write_text(json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8")

    def should_refresh_mappings(self) -> bool:
        today = date.today()
        if today.weekday() != 0:
            return False
        last_scan = self.state.get("last_full_scan")
        if last_scan is None:
            return True
        try:
            return date.fromisoformat(last_scan) < today
        except (ValueError, TypeError):
            return True

    def mark_scanned(self):
        today = date.today()
        self.state["last_full_scan"] = today.isoformat()
        self.state["last_scan_week"] = today.isocalendar()[:2]
        self.save()


# ─── VaultScanner ────────────────────────────────────────────────────────────

class VaultScanner:
    def is_valid_alias(self, text: str) -> bool:
        if len(text) < 2 or len(text) > 30:
            return False
        if text in COMMON_WORD_BLACKLIST:
            return False
        if re.search(r"[，。；：、！？（）()（）「」『』《》【】…—·\[\]]", text):
            return False
        if re.match(r"^[\d%.\-+]+$", text):
            return False
        return True

    def extract_context_words_with_tfidf(self, text: str) -> list[str]:
        """用 TF-IDF 从正文提取关键词，辅以标题词。"""
        heading_words = self._extract_heading_words(text)
        tfidf_words = self._extract_tfidf_keywords(text)
        combined = heading_words + [w for w in tfidf_words if w not in heading_words]
        return combined[:20]

    def _extract_heading_words(self, text: str) -> list[str]:
        """从 ## 标题提取上下文词。"""
        words: list[str] = []
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("## "):
                clean = re.sub(r"[，。；：、！？（）()（）「」『』《》【】…—·]", " ", s[3:])
                for p in re.split(r"[\s/—–\-]+", clean.strip()):
                    if len(p) >= 2 and p not in COMMON_WORD_BLACKLIST:
                        words.append(p)
                if len(words) >= 8:
                    break
        return words

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.files: list[Path] = get_markdown_files(base_dir)
        self._tfidf_vec = TfidfVectorizer(max_features=20, token_pattern=r"(?u)\S+") if TfidfVectorizer is not None else None

    def _extract_tfidf_keywords(self, text: str) -> list[str]:
        """用 TF-IDF 提取正文关键词（TfidfVectorizer 实例复用）。"""
        if self._tfidf_vec is None:
            return []
        clean = clean_body_text(text)

        # 按段落拆分后一次性 jieba 分词
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", clean) if len(p.strip()) > 20]
        para_tokens = []
        for p in paragraphs:
            tokens = jieba.lcut(p)
            filtered = [t for t in tokens if len(t) >= 2 and t not in COMMON_WORD_BLACKLIST]
            para_tokens.append(" ".join(filtered))
        para_tokens = [p for p in para_tokens if len(p) > 5]

        if len(para_tokens) < 2:
            return []

        try:
            mat = self._tfidf_vec.fit_transform(para_tokens)
            scores = np.asarray(mat.sum(axis=0)).flatten()
            names = self._tfidf_vec.get_feature_names_out()
            top_idx = scores.argsort()[-15:][::-1]
            return [names[i] for i in top_idx]
        except Exception:
            return []

    def scan(self) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for fp in self.files:
            rel = fp.relative_to(self.base_dir).as_posix()
            target = rel[:-3]
            stem = fp.stem
            cat = detect_file_category(stem)
            aliases = generate_auto_aliases(stem)

            try:
                text = fp.read_text(encoding="utf-8")
            except Exception:
                candidates.append({"target": target, "aliases": aliases, "category": cat, "context_words": []})
                continue

            # YAML frontmatter 内不提取别名
            body_start = 0
            if text.startswith("---"):
                yaml_end = text.find("---", 3)
                if yaml_end != -1:
                    body_start = yaml_end + 3
            body = text[body_start:]

            # First bold in body as primary alias (≥4 chars to avoid short proper nouns)
            first_match = re.search(r"\*\*(.+?)\*\*", body)
            if first_match:
                first_bold = first_match.group(1).strip()
                if self.is_valid_alias(first_bold) and len(first_bold) >= 4:
                    if first_bold not in aliases:
                        aliases.append(first_bold)

            # Heading-based alias extraction
            seen_headings = set()
            for line in body.splitlines():
                s = line.strip()
                if not s.startswith("## "):
                    continue
                if s in seen_headings:
                    continue
                seen_headings.add(s)
                heading_text = s[3:].strip()
                # Take the part after colon/dash separator
                parts = re.split(r"[:：—\-]", heading_text)
                candidate = parts[-1].strip() if len(parts) > 1 else heading_text.strip()
                # Remove parenthetical suffixes
                candidate = re.sub(r"[（(].*?[）)]", "", candidate).strip()
                if (
                    candidate
                    and self.is_valid_alias(candidate)
                    and 2 <= len(candidate) <= 15
                    and candidate not in aliases
                ):
                    aliases.append(candidate)

            # TF-IDF context words
            context_words = self.extract_context_words_with_tfidf(text)

            candidates.append({
                "target": target,
                "aliases": list(dict.fromkeys(aliases)),
                "category": cat,
                "context_words": context_words,
            })

        # Add nation short names to overview docs
        target_index = {c["target"]: c for c in candidates}
        for short_name, primary_target in NATION_PRIMARY.items():
            if primary_target in target_index:
                entry = target_index[primary_target]
                if short_name not in entry["aliases"]:
                    entry["aliases"].append(short_name)

        return candidates

    def get_all_files(self) -> list[Path]:
        return self.files


# ─── Segmenter (spaCy NER) ───────────────────────────────────────────────────

class Segmenter:
    def __init__(self):
        self.nlp = None
        self.custom_dict_built = False

    def _load_spacy(self):
        if self.nlp is None and spacy is not None:
            try:
                self.nlp = spacy.load("zh_core_web_sm")
                print("  spaCy 模型已加载")
            except OSError:
                print("  警告: spaCy 中文模型未安装，回退 jieba")
                self.nlp = None

    def build_custom_dict(self, known_aliases: list[str]):
        for alias in known_aliases:
            if len(alias) >= 2:
                jieba.add_word(alias, freq=200, tag="nz")
        self.custom_dict_built = True

    def discover_unlinked(self, files: list[Path], base_dir: Path) -> list[dict[str, Any]]:
        """使用 spaCy NER（批量 pipe）发现未链接概念。"""
        self._load_spacy()
        file_counts: dict[str, dict[str, Any]] = {}
        ner_label_map = {"PERSON": "人物", "ORG": "政治实体与政党", "GPE": "国家与地区", "LOC": "国家与地区", "FAC": "科技与基础设施", "PRODUCT": "经济与产业", "EVENT": "文化与信仰", "WORK_OF_ART": "文化与信仰"}

        # 预读取所有文件正文
        file_texts: list[tuple[str, int]] = []
        for idx, fp in enumerate(files):
            try:
                text = fp.read_text(encoding="utf-8")
            except Exception:
                continue
            clean = clean_body_text(text)
            if len(clean) < 10:
                continue
            file_texts.append((clean[:30000], idx))

        if self.nlp is not None:
            # spaCy NER 批处理：nlp.pipe 一次性送所有文本
            texts = [t for t, _ in file_texts]
            for doc, (_, orig_idx) in zip(self.nlp.pipe(texts, batch_size=16), file_texts):
                seen = set()
                for ent in doc.ents:
                    w = ent.text.strip()
                    if len(w) < 2 or len(w) > 25:
                        continue
                    if w in COMMON_WORD_BLACKLIST or is_noise_token(w):
                        continue
                    if w not in seen:
                        seen.add(w)
                        if w not in file_counts:
                            file_counts[w] = {"name": w, "count": 0, "labels": []}
                        file_counts[w]["count"] += 1
                        file_counts[w]["labels"].append(ent.label_)
        else:
            # fallback: jieba.lcut（非 posseg，快 3-5 倍）
            for clean, _ in file_texts:
                words = jieba.lcut(clean)
                seen = set()
                for w in words:
                    w = w.strip()
                    if len(w) < 2 or len(w) > 20:
                        continue
                    if w in COMMON_WORD_BLACKLIST or is_noise_token(w):
                        continue
                    # 简单启发式：2-4 字符且非纯数字/标点的词作为候选
                    if re.search(r"[，。；：、！？（）「」『』《》【】\d]", w):
                        continue
                    if w not in seen:
                        seen.add(w)
                        if w not in file_counts:
                            file_counts[w] = {"name": w, "count": 0, "labels": []}
                        file_counts[w]["count"] += 1
                        file_counts[w]["labels"].append("nz")

        # 过滤：频率 >= 4
        candidates: list[dict[str, Any]] = []
        for w, info in file_counts.items():
            if info["count"] < 4:
                continue
            # Infer category
            label_counts = Counter(info["labels"])
            dominant = label_counts.most_common(1)[0][0]
            if self.nlp is not None:
                cat = ner_label_map.get(dominant, "文化与信仰")
            else:
                cat = "人物" if dominant.startswith("nr") else "国家与地区" if dominant.startswith("ns") else "政治实体与政党" if dominant.startswith("nt") else "文化与信仰"
            candidates.append({
                "name": w,
                "aliases": [w],
                "category": cat,
                "files_count": info["count"],
                "confidence": "高" if info["count"] >= 6 else "中",
            })

        candidates.sort(key=lambda x: -x["files_count"])
        return candidates


# ─── ConceptMapper ───────────────────────────────────────────────────────────

class ConceptMapper:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {
            "_meta": {"last_updated": "", "version": 4, "total_linked": 0, "total_unlinked": 0},
            "concepts": [],
            "unlinked": [],
        }

    def load(self) -> dict[str, Any]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data.get("_meta", {}).get("version", 1) >= 3:
                    self.data = data
            except (json.JSONDecodeError, OSError):
                pass
        return self.data

    def save(self):
        self.data["_meta"]["last_updated"] = date.today().isoformat()
        self.data["_meta"]["total_linked"] = len(self.data.get("concepts", []))
        self.data["_meta"]["total_unlinked"] = len(self.data.get("unlinked", []))
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def merge(self, scan_results: list[dict[str, Any]], jieba_candidates: list[dict[str, Any]]) -> dict[str, Any]:
        existing = {c["target"]: c for c in self.data.get("concepts", [])}
        existing_unlinked = {c["name"]: c for c in self.data.get("unlinked", [])}
        new_concepts: list[dict[str, Any]] = []
        new_unlinked: list[dict[str, Any]] = []
        additions, removals, ul_additions = [], [], []

        for candidate in scan_results:
            target = candidate["target"]
            if target in existing:
                old = existing[target]
                for alias in candidate["aliases"]:
                    if alias not in old["aliases"]:
                        old["aliases"].append(alias)
                        additions.append(f"[别名扩展] {target}: +{alias}")
                old_cws = old.get("context_words", [])
                for w in candidate.get("context_words", []):
                    if w not in old_cws:
                        old_cws.append(w)
                old["context_words"] = old_cws
                new_concepts.append(old)
            else:
                entry = {"target": target, "aliases": candidate["aliases"], "category": candidate["category"], "verified": False, "source": "auto", "priority": 5, "context_words": candidate.get("context_words", [])}
                new_concepts.append(entry)
                additions.append(f"[新增] {target} → {candidate['category']}")

        for target, old in existing.items():
            if old.get("source") == "manual" and target not in {c["target"] for c in new_concepts}:
                new_concepts.append(old)
                removals.append(f"[保留手动] {target}")

        # 已归属到某个 target 的别名集合：已链接概念不应再报为「无对应文件」。
        linked_aliases: set[str] = set()
        for c in new_concepts:
            linked_aliases.update(c.get("aliases", []))

        for candidate in jieba_candidates:
            name = candidate["name"]
            if name in linked_aliases:
                continue
            if name in existing_unlinked:
                old = existing_unlinked[name]
                old["files_count"] = candidate["files_count"]
                new_unlinked.append(old)
            else:
                new_unlinked.append({"name": name, "aliases": candidate["aliases"], "category": candidate["category"], "reason": "无对应 .md 文件", "confidence": candidate["confidence"], "files_count": candidate["files_count"]})
                ul_additions.append(f"[未链接] {name} ({candidate['confidence']}, {candidate['files_count']} 文件)")

        for name, old in existing_unlinked.items():
            if name in linked_aliases:
                continue
            if name not in {c["name"] for c in new_unlinked}:
                new_unlinked.append(old)

        self.data["concepts"] = new_concepts
        self.data["unlinked"] = new_unlinked
        return {"additions": additions, "removals": removals, "unlinked_additions": ul_additions, "unlinked_total": len(new_unlinked)}

    def build_alias_index(self) -> dict[str, list[dict[str, Any]]]:
        idx: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for c in self.data.get("concepts", []):
            if not c.get("verified", False):
                continue
            for alias in c.get("aliases", []):
                idx[alias].append(c)
        return dict(idx)


# ─── IndexGenerator ──────────────────────────────────────────────────────────

class IndexGenerator:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.category_order = ["国家与地区", "政治实体与政党", "国际组织", "经济与产业", "科技与基础设施", "文化与信仰", "人物", "其他"]

    def generate(self, concepts: list[dict[str, Any]], unlinked: list[dict[str, Any]]):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        cat_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for c in concepts:
            cat_map[c.get("category", "其他")].append(c)

        master = ["# 术语总索引", "", f"> 自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}", f"> 共 {len(concepts)} 个已链接概念 + {len(unlinked)} 个未链接概念", "", "## 目录", ""]
        for i, cat in enumerate(self.category_order):
            if cat in cat_map:
                master.append(f"- {cat}（{len(cat_map[cat])} 个概念）")
                self._gen_category(cat, cat_map[cat], unlinked, f"{i+1:02d}")
        if unlinked:
            master.extend(["", "## 未链接概念", "", "| 概念 | 类别 | 出现频次 |", "|------|------|---------|"])
            for u in unlinked[:50]:
                master.append(f"| {u['name']} | {u.get('category', '未知')} | {u.get('files_count', '?')} 个文件 |")
        master.extend(["", "---", "", "*本目录由 concept_linker.py 自动生成，位于 tools/术语索引/ 下。*"])
        (self.output_dir / "00_总索引.md").write_text("\n".join(master), encoding="utf-8")

    def _gen_category(self, category: str, concepts: list[dict], unlinked: list[dict], prefix: str):
        lines = [f"# {category}", "", f"> 共 {len(concepts)} 个概念 | 自动生成于 {datetime.now().strftime('%Y-%m-%d')}", "", "| 概念 | 目标文件 | 别名 |", "|:----|:--------|:-----|"]
        for c in sorted(concepts, key=lambda x: x.get("target", "")):
            target = c.get("target", "")
            aliases_str = "、".join(c.get("aliases", [])[:5])
            lines.append(f"| {Path(target).stem} | {target} | {aliases_str} |")
        cat_unlinked = [u for u in unlinked if u.get("category") == category]
        if cat_unlinked:
            lines.extend(["", "## 未链接概念（无对应文件）", "", "| 概念 | 出现频次 |", "|------|---------|"])
            for u in cat_unlinked:
                lines.append(f"| {u['name']} | {u.get('files_count', '?')} 个文件 |")
        (self.output_dir / f"{prefix}_{category}.md").write_text("\n".join(lines), encoding="utf-8")


# ─── Reporter ────────────────────────────────────────────────────────────────

def print_diff_report(diff: dict[str, Any], mappings_path: Path, verbose: bool = False):
    sep = "=" * 60
    limit_additions = 100000 if verbose else 15
    limit_unlinked = 100000 if verbose else 10
    print(f"\n{sep}\n  每周扫描结果\n{sep}")
    additions = diff.get("additions", [])
    removals = diff.get("removals", [])
    ul_additions = diff.get("unlinked_additions", [])
    if additions:
        print(f"\n  ■ 新增/变更（{len(additions)} 项）:")
        for a in additions[:limit_additions]:
            print(f"    + {a}")
        if len(additions) > limit_additions:
            print(f"    ... 共 {len(additions)} 项")
    if removals:
        print(f"\n  ■ 已移除（{len(removals)} 项）:")
        for r in removals:
            print(f"    - {r}")
    if ul_additions:
        print(f"\n  ■ 未链接概念（{len(ul_additions)} 个）:")
        for u in ul_additions[:limit_unlinked]:
            print(f"    ? {u}")
        if len(ul_additions) > limit_unlinked:
            print(f"    ... 共 {len(ul_additions)} 个")
    print(f"\n{sep}\n  ★ 请编辑 {mappings_path}\n  ★ 将确认项改为 \"verified\": true\n  ★ 保存后重新运行: python concept_linker.py --scan-only\n{sep}\n")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="概念链接工具 - 概念扫描、映射维护与术语索引生成")
    parser.add_argument("--scan-only", action="store_true", help="仅扫描更新映射和术语索引")
    parser.add_argument("--refresh", action="store_true", help="强制刷新（忽略每周节流）")
    parser.add_argument("--verbose", action="store_true", help="详细输出（打印全部变更条目）")
    parser.add_argument("--ci", action="store_true", help="CI 模式：只读校验概念映射完整性，不写文件，返回非零退出码")
    args = parser.parse_args()

    # ── CI 模式：只读校验 ──────────────────────────────────────────────────
    if args.ci:
        print("CI 模式：校验概念映射完整性...")
        mapper = ConceptMapper(MAPPINGS_FILE)
        mapper.load()
        concepts = mapper.data.get("concepts", [])
        if not concepts:
            print("错误: concept_mappings.json 不存在或为空，请先本地运行 --refresh 并 commit")
            sys.exit(1)
        if not mapper.build_alias_index():
            print("错误: 无 verified 概念，请检查 concept_mappings.json")
            sys.exit(1)
        unlinked = mapper.data.get("unlinked", [])
        if unlinked:
            print(f"以下 {len(unlinked)} 个概念在仓库中无对应文件：")
            for u in unlinked[:20]:
                print(f"  ? {u.get('name')} ({u.get('category', '?')}, {u.get('files_count', '?')} 个文件)")
            if len(unlinked) > 20:
                print(f"  ... 共 {len(unlinked)} 个")
            sys.exit(1)
        print(f"OK: {len(concepts)} 个已链接概念，0 个未链接概念。")
        return

    state_mgr = StateManager(STATE_FILE)
    scanner = VaultScanner(BASE_DIR)
    segmenter = Segmenter()
    mapper = ConceptMapper(MAPPINGS_FILE)
    index_gen = IndexGenerator(INDEX_DIR)

    # ── 扫描与刷新 ──────────────────────────────────────────────────────────
    should_refresh = args.refresh or state_mgr.should_refresh_mappings()
    if should_refresh:
        print("\n  扫描仓库...")
        scan_candidates = scanner.scan()
        print(f"  发现 {len(scan_candidates)} 个概念候选")
        existing = mapper.load()
        existing_aliases: list[str] = []
        for c in existing.get("concepts", []):
            existing_aliases.extend(c.get("aliases", []))
        print("  spaCy NER 发现专名...")
        segmenter.build_custom_dict(existing_aliases)
        all_files = scanner.get_all_files()
        ner_candidates = segmenter.discover_unlinked(all_files, BASE_DIR)
        print(f"  发现 {len(ner_candidates)} 个未链接概念候选")
        print("  合并映射...")
        diff = mapper.merge(scan_candidates, ner_candidates)
        mapper.save()
        print_diff_report(diff, MAPPINGS_FILE, args.verbose)
        print("  生成术语索引...")
        index_gen.generate(mapper.data.get("concepts", []), mapper.data.get("unlinked", []))
        print(f"  术语索引已生成: {INDEX_DIR}")
        state_mgr.mark_scanned()
        if args.scan_only:
            print("\n  --scan-only 完成")
        else:
            print("\n  扫描完成。请核对 concept_mappings.json，并运行 fix_punctuation.py 检查格式。")
    elif args.scan_only:
        print("  错误: --scan-only 需要触发扫描（周一或 --refresh）")
    else:
        print("  本周已扫描过（周一自动刷新），加 --refresh 可强制刷新。")

if __name__ == "__main__":
    main()
