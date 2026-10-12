# coding: utf-8
"""
语气词过滤（清洗规则，纯规则实现，只依赖标准库）

在分句之后执行，需要句子级上下文。

删除：
  - 句首的"嗯/呃"（后跟停顿，如"嗯，我觉得可以"→"我觉得可以"）
  - 句首"那个/那"堆叠（后跟停顿，如"那那个那个，我觉得"→"我觉得"；
    注意"那个方案"中的"那个"是只指示代词，后跟名词，不删除）
  - 句中独立出现的"呃"（不管在哪里，前后是标点/空白即删，
    如"去掉了，呃，不过"→"去掉了，不过"；不删除"呃逆"这类词中的呃）
  - 单独成句的"呃"（一律删除，不管前一句是什么）
保留：
  - 句尾表语气的"吧/呢/啊"（"我们走吧""是这样吧"）
  - 表感叹的"啊"（"啊，太好了"）
  - 问答中单独成句的应答"嗯"（前一句是问句才保留）
"""

from __future__ import annotations

import re

# 分句：按终结标点切分，标点保留在句尾
_SENT_END = re.compile(r"(?<=[。！？!?；…])")

# 句首填充词"嗯/呃"（可重复），后跟停顿或句末
_LEAD_UM = re.compile(r"^(?:嗯|呃)+(?:[，,、\s…]|$)")
# 句首"那/那个"堆叠，后跟停顿或句末
_LEAD_NA = re.compile(r"^(?:那|那个)+(?:[，,、\s…]|$)")
# 句中独立出现的"呃"（前后是标点/空白，即独立的语气词，如"，呃，"）
# 注意：不删除"呃逆"这类词中的呃（前后不是标点则不动）
_MID_E = re.compile(r"(?<=[，。？！、；：…—\s])呃+")
# 清理后可能留下的重复标点（"，呃，"→"，，"→"，"）
_DUP_PUNCT = re.compile(r"([，。？！、；：…—])\1+")
# 单独成句的"嗯"（问答应答保留）
_STANDALONE_EN = re.compile(r"^嗯+(?:[。！？!?\s…]*)$")
# 单独成句的"呃"（一律删除）
_STANDALONE_E = re.compile(r"^呃+(?:[。！？!?\s…]*)$")
# 问句结尾
_Q_END = re.compile(r"[？?]\s*$")


def split_sentences(text: str) -> list[str]:
    """按标点分句，标点保留在句尾，返回非空句子列表。"""
    return [p for p in (_s.strip() for _s in _SENT_END.split(text)) if p]


def clean_sentence(sent: str, prev_sent: str = "") -> str:
    """
    清洗单个句子。

    Args:
        sent: 单个句子（含标点）
        prev_sent: 前一个句子（用于判断单独成句的"嗯"是否为问答应答）

    Returns:
        清洗后的句子；返回 "" 表示整句删除。
    """
    s = sent.strip()

    # 1. 反复剥离句首填充词
    while True:
        m = _LEAD_UM.match(s) or _LEAD_NA.match(s)
        if not m:
            break
        s = s[m.end():].lstrip()
    s = s.strip()
    if not s:
        return ""

    # 2. 删除句中独立出现的"呃"（不管在哪里，如"，呃，"→"，"）
    s = _MID_E.sub("", s)
    s = _DUP_PUNCT.sub(r"\1", s)
    s = s.strip()
    if not s:
        return ""

    # 3. 单独成句：
    #    - "呃"：一律删除（不管前一句是什么）
    #    - "嗯"：仅当前一句是问句时保留（问答应答），否则删除
    if _STANDALONE_E.match(s):
        return ""
    if _STANDALONE_EN.match(s):
        if prev_sent and _Q_END.search(prev_sent):
            return s
        return ""

    return s


def clean_text(text: str, prev_last_sent: str = "") -> str:
    """
    清洗整段文本（先分句，再逐句清洗）。

    Args:
        text: 原始转写文本
        prev_last_sent: 上一段文本的最后一个句子（跨段上下文）

    Returns:
        清洗后的文本
    """
    out: list[str] = []
    prev = prev_last_sent
    for s in split_sentences(text):
        c = clean_sentence(s, prev)
        if c:
            out.append(c)
            prev = c
    return "".join(out)
