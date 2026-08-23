"""CCF 推荐目录(AI/CV/图像处理相关子集)与 venue 分级。

- CCF_LEVELS: 规范化后的期刊/会议名(小写、去空格、去标点) -> "A"/"B"/"C"
- KEYWORDS:    简称/全称关键词, venue 归一化后包含任一关键词即命中对应级别
- classify_ccf(venue): 发表状态 + CCF 级别判定(venue 空 => 预印本)
"""

import re

# ---------------------------------------------------------------------------
# CCF_LEVELS: 规范化名 -> 级别
# 收录 AI/CV/图像处理相关期刊与会议(CFF 推荐目录子集, 以需求规格为准)。
# 一个期刊/会议可有多个别名 key(简称 / IEEE 前缀 / 全称)。
# ---------------------------------------------------------------------------
CCF_LEVELS: dict[str, str] = {
    # ===== A 类会议 =====
    "cvpr": "A",
    "iccv": "A",
    "neurips": "A",
    "nips": "A",  # NeurIPS 旧称
    "icml": "A",
    "aaai": "A",
    "ijcai": "A",
    "acl": "A",
    "iclr": "A",
    # ===== A 类期刊 =====
    "tpami": "A",
    "ieeetpami": "A",
    "ieeetransactionsonpatternanalysisandmachineintelligence": "A",
    "ieeetransonpatternanalysisandmachineintelligence": "A",  # "Trans." 缩写
    "ijcv": "A",
    "internationaljournalofcomputervision": "A",
    "aij": "A",
    "artificialintelligence": "A",
    # ===== B 类会议 =====
    "eccv": "B",
    "acmmm": "B",
    "acminternationalconferenceonmultimedia": "B",
    "icassp": "B",
    "icip": "B",
    "bmvc": "B",
    "accv": "B",
    "wacv": "B",
    "emnlp": "B",
    "naacl": "B",
    "conll": "B",
    "coling": "B",
    "sigir": "B",
    "www": "B",
    "kdd": "B",
    "icdm": "B",
    "sdm": "B",
    "ecml": "B",
    # ===== B 类期刊 =====
    "tip": "B",
    "ieeetip": "B",
    "ieeetransactionsonimageprocessing": "B",
    "ieeetransonimageprocessing": "B",  # "Trans." 缩写
    "tnnls": "B",
    "ieeetransactionsonneuralnetworksandlearningsystems": "B",
    "ieeetransonneuralnetworksandlearningsystems": "B",  # "Trans." 缩写
    "tog": "B",
    "acmtog": "B",
    "acmtransactionsongraphics": "B",
    "tcsvt": "B",
    "ieeetransactionsoncircuitsandsystemsforvideotechnology": "B",
    "ieeetransoncircuitsandsystemsforvideotechnology": "B",  # "Trans." 缩写
    "cviu": "B",
    "computervisionandimageunderstanding": "B",
    "pr": "B",
    "patternrecognition": "B",
    # ===== C 类会议 =====
    "icpr": "C",
    "icme": "C",
    "icann": "C",
    "icig": "C",
    "iconip": "C",
    # ===== C 类期刊 =====
    "signalprocessing": "C",
    "ieeetransactionsonsignalprocessing": "C",
    "neurocomputing": "C",
    "ieeeaccess": "C",
    "machinevisionandapplications": "C",
    "mva": "C",  # Machine Vision and Applications 常用简称(仅精确匹配)
}

# ---------------------------------------------------------------------------
# 简称关键词: 辨识度高, 优先匹配(如 "cvpr"、"iccv")
# 长关键词(如 naacl)在排序后先于其子串(如 acl)检查, 避免误匹配
# ---------------------------------------------------------------------------
_ACRONYM_KEYWORDS: dict[str, str] = {
    "cvpr": "A",
    "iccv": "A",
    "neurips": "A",
    "nips": "A",
    "icml": "A",
    "aaai": "A",
    "ijcai": "A",
    "acl": "A",
    "iclr": "A",
    "tpami": "A",
    "ijcv": "A",
    "eccv": "B",
    "acmmm": "B",
    "icassp": "B",
    "icip": "B",
    "bmvc": "B",
    "accv": "B",
    "wacv": "B",
    "emnlp": "B",
    "naacl": "B",
    "conll": "B",
    "coling": "B",
    "sigir": "B",
    "www": "B",
    "kdd": "B",
    "icdm": "B",
    "sdm": "B",
    "ecml": "B",
    "tip": "B",
    "tnnls": "B",
    "tog": "B",
    "tcsvt": "B",
    "cviu": "B",
    "icpr": "C",
    "icme": "C",
    "icann": "C",
    "icig": "C",
    "iconip": "C",
}

# ---------------------------------------------------------------------------
# 全称关键词: 兜底匹配(如 "patternrecognition")
# 放在简称之后: 避免 "Proceedings of ... Pattern Recognition (CVPR)" 这种
# 同时含简称与全称的 venue 被全称关键词错误压过简称
# 全称短语按长度降序优先于短短语/单词, 如 ICASSP 全称先于 "signalprocessing"
# ---------------------------------------------------------------------------
_FULLNAME_KEYWORDS: dict[str, str] = {
    # A 类会议全称(简称不出现在全称中, 需短语兜底)
    "conferenceoncomputervisionandpatternrecognition": "A",  # CVPR
    "internationalconferenceoncomputervision": "A",  # ICCV
    "internationalconferenceonmachinelearning": "A",  # ICML
    "internationalconferenceonlearningrepresentations": "A",  # ICLR
    "neuralinformationprocessingsystems": "A",  # NeurIPS
    "annualmeetingoftheassociationforcomputationallinguistics": "A",  # ACL
    # B 类会议全称
    "conferenceonacousticsspeechandsignalprocessing": "B",  # ICASSP
    "internationalconferenceonimageprocessing": "B",  # ICIP
    "europeanconferenceoncomputervision": "B",  # ECCV
    "conferenceonempiricalmethodsinnaturallanguageprocessing": "B",  # EMNLP
    "acminternationalconferenceonmultimedia": "B",  # ACM MM 全称
    "acmmultimedia": "B",  # ACM MM 长名(归一化双连 m, acmmm 三连 m 匹配不到)
    "thewebconference": "B",  # WWW 自 2018 年官方名
    # 期刊全称
    "artificialintelligence": "A",  # AIJ
    "patternrecognition": "B",  # PR
    "signalprocessing": "C",  # Signal Processing
    "neurocomputing": "C",
    "ieeeaccess": "C",
    "machinevisionandapplications": "C",
}

# 对外全量关键词列表(简称 + 全称)
KEYWORDS: dict[str, str] = {**_ACRONYM_KEYWORDS, **_FULLNAME_KEYWORDS}

# 匹配顺序: 同级内按关键词长度降序(更具体者优先, 如 naacl 先于 acl)
_ACRONYM_ORDER: list[str] = sorted(_ACRONYM_KEYWORDS, key=len, reverse=True)
_FULLNAME_ORDER: list[str] = sorted(_FULLNAME_KEYWORDS, key=len, reverse=True)


def _normalize(name: str) -> str:
    """规范化: 小写、去空格、去标点(仅保留字母与数字)。"""
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _match_keywords(norm: str) -> str | None:
    """归一化后的 venue 按关键词包含关系匹配级别(简称优先, 全称兜底)。"""
    for kw in _ACRONYM_ORDER:
        if kw in norm:
            return _ACRONYM_KEYWORDS[kw]
    for kw in _FULLNAME_ORDER:
        if kw in norm:
            return _FULLNAME_KEYWORDS[kw]
    return None


def classify_ccf(venue: str) -> dict:
    """按 venue 判定发表状态与 CCF 级别。

    返回 {"published": bool, "venue": str, "ccf_level": "A"|"B"|"C"|None}
    - venue 空 -> 预印本(published=False, venue="", ccf_level=None)
    - venue 非空 -> 已发表; 规范化后命中 CCF_LEVELS 或关键词则标注级别, 否则 None
    """
    if not venue or not venue.strip():
        return {"published": False, "venue": "", "ccf_level": None}
    norm = _normalize(venue)
    level = CCF_LEVELS.get(norm)
    if level is None:
        level = _match_keywords(norm)
    return {"published": True, "venue": venue, "ccf_level": level}
