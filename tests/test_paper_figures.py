"""图表检测图注匹配回归测试。

覆盖 2026-08-21 修复的三类图注风格(冒号/句号/空格)与两类防护:
- 正文引用行误判为图注的过滤器(_CAPTION_SENTENCE)
- 空格风格表格标题的宽松匹配(_TABLE_CAPTION_LOOSE)

说明: 图注文本在 _line_captions 中按词无空格拼接(如 "Fig. 1." → "Fig.1."),
因此测试用例使用拼接后的形态, 与真实检测路径一致。
"""

import re

from app.paper.figures import (
    _CAPTION,
    _CAPTION_SENTENCE,
    _TABLE_CAPTION_LOOSE,
)


# ---------- _CAPTION: 主图注匹配(要求标签+编号+标点) ----------

def test_caption_colon_style():
    assert _CAPTION.match("Figure 1: Comparison of methods")
    assert _CAPTION.match("TABLE I: Results on benchmarks")
    assert _CAPTION.match("图 1：超分辨率对比")


def test_caption_period_style_joined():
    """句号风格是 2026-08-21 主修复: 旧正则只认冒号导致此类论文 0 图表。"""
    assert _CAPTION.match("Fig.1.ComparisonbetweenourproposedmethodandSOTA")
    assert _CAPTION.match("Figure1.Manga109datasetforupscalingfactor×4.")
    assert _CAPTION.match("Figure2.NetworkarchitectureoftheproposedAMCANet.")
    assert _CAPTION.match("Table1.ComparisonwithCNN-basedlightweightSRmethods")
    assert _CAPTION.match("Fig.5.Illustrationofthegridpartitioning")


def test_caption_rejects_running_text():
    """正文引用行不是图注: 不位于行首/编号后无标点都不匹配。"""
    assert not _CAPTION.match("As shown in Fig. 1, the method")
    assert not _CAPTION.match("shownbyFig.4.")  # 标签不在行首
    assert not _CAPTION.match("Figure 1 shows the results")  # 编号后无标点
    assert not _CAPTION.match("Table 1 Computational complexity")  # 空格风格归宽松匹配


# ---------- _TABLE_CAPTION_LOOSE: 空格风格表格标题 ----------

def test_loose_table_caption_accepts():
    assert _TABLE_CAPTION_LOOSE.match("Table1Computationalcomplexityofeachmodule")
    assert _TABLE_CAPTION_LOOSE.match("Table4ComparisonofparametersandFLOPs")
    assert _TABLE_CAPTION_LOOSE.match("Table2Ablationstudyonhierarchicalwindow")
    assert _TABLE_CAPTION_LOOSE.match("Table1")  # 标签单独成行(标题在下一行)


def test_loose_table_caption_rejects_running_text():
    """正文引用("TABLE II presents...")以小写动词开头, 宽松匹配拒绝。"""
    assert not _TABLE_CAPTION_LOOSE.match("TABLEIIpresentsodsatscalefactors")
    assert not _TABLE_CAPTION_LOOSE.match("Table1andTable2show")
    assert not _TABLE_CAPTION_LOOSE.match("TableV.Wefindthatthemodel")


# ---------- _CAPTION_SENTENCE: 句子型误报过滤器 ----------

def test_sentence_filter_rejects_running_text():
    """这些行是正文句子被误判为图注的典型案例, 必须被过滤。"""
    assert _CAPTION_SENTENCE.match("Fig.5.Then,wecombinethreepoolingmethods")
    assert _CAPTION_SENTENCE.match("TableV.Wefindthatthemodelachievesthebest")
    assert _CAPTION_SENTENCE.match("TableIII.WeevaluateallmodelsontheUrban100")
    assert _CAPTION_SENTENCE.match("Fig.2.Incontrast,LAM[12]suggests")


def test_sentence_filter_keeps_real_captions():
    """真实图注是名词短语; 即使包含 "We train"(实验描述)也不应被过滤。"""
    assert not _CAPTION_SENTENCE.match("Fig.1.Comparisonbetweenourproposedmethod")
    assert not _CAPTION_SENTENCE.match("Figure2.NetworkarchitectureoftheproposedAMCANet")
    assert not _CAPTION_SENTENCE.match("Table1Computationalcomplexityofeachmodule")
    assert not _CAPTION_SENTENCE.match("Table4.Ablationstudy.WetrainallmodelsonDF2K,andtest")
    assert not _CAPTION_SENTENCE.match("Fig.6.ComparisonofourmethodLFCA")
