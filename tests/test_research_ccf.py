"""CCF 期刊/会议分级 classify_ccf 测试。"""

from app.research.ccf import CCF_LEVELS, classify_ccf


def test_ccf_directory_covers_required_venues():
    """目录须覆盖规格要求的 AI/CV/图像处理相关期刊会议。"""
    required = [
        # A 类会议
        "cvpr", "iccv", "neurips", "icml", "aaai", "ijcai", "acl", "iclr",
        # A 类期刊
        "tpami", "ijcv", "aij",
        # B 类会议
        "eccv", "acmmm", "icassp", "icip", "bmvc", "accv", "wacv",
        "emnlp", "naacl", "conll", "coling", "sigir", "www", "kdd",
        "icdm", "sdm", "ecml",
        # B 类期刊
        "tip", "tnnls", "tog", "tcsvt", "cviu", "pr",
        # C 类会议
        "icpr", "icme", "icann", "icig", "iconip",
        # C 类期刊
        "signalprocessing", "neurocomputing", "ieeeaccess", "machinevisionandapplications",
    ]
    for name in required:
        assert name in CCF_LEVELS, f"CCF 目录缺少 {name}"


def test_ccf_directory_size_reasonable():
    """AI/CV 子集规模 40-60 个期刊/会议(含全称别名不超过 80 个 key)。"""
    assert len(CCF_LEVELS) >= 40
    assert len(CCF_LEVELS) <= 80


def test_classify_ccf_empty_venue_is_preprint():
    assert classify_ccf("") == {"published": False, "venue": "", "ccf_level": None}


def test_classify_ccf_whitespace_venue_is_preprint():
    result = classify_ccf("   ")
    assert result["published"] is False
    assert result["ccf_level"] is None


def test_classify_ccf_matches_ccf_a_conference():
    """CVPR 命中 A; venue 保留原文。"""
    result = classify_ccf("CVPR 2024")
    assert result["published"] is True
    assert result["venue"] == "CVPR 2024"
    assert result["ccf_level"] == "A"


def test_classify_ccf_matches_ccf_a_journal():
    assert classify_ccf("IEEE TPAMI")["ccf_level"] == "A"
    assert classify_ccf("IEEE Transactions on Pattern Analysis and Machine Intelligence")["ccf_level"] == "A"
    assert classify_ccf("International Journal of Computer Vision")["ccf_level"] == "A"


def test_classify_ccf_matches_ccf_b():
    assert classify_ccf("ECCV 2024")["ccf_level"] == "B"
    assert classify_ccf("Pattern Recognition")["ccf_level"] == "B"
    assert classify_ccf("ACM Transactions on Graphics")["ccf_level"] == "B"


def test_classify_ccf_matches_ccf_c():
    assert classify_ccf("ICPR 2024")["ccf_level"] == "C"
    assert classify_ccf("Neurocomputing")["ccf_level"] == "C"
    assert classify_ccf("IEEE Access")["ccf_level"] == "C"


def test_classify_ccf_published_but_not_in_directory():
    """venue 非空但不在 CCF 目录 → 已发表、无级别。"""
    result = classify_ccf("Journal of Weird Studies")
    assert result == {"published": True, "venue": "Journal of Weird Studies", "ccf_level": None}


def test_classify_ccf_fuzzy_long_name_contains_acronym():
    """长名含简称 → 命中对应级别(CVPR 全称含 pattern recognition, 须优先命中 cvpr)。"""
    result = classify_ccf("Proceedings of the IEEE Conference on Computer Vision and Pattern Recognition (CVPR)")
    assert result["published"] is True
    assert result["ccf_level"] == "A"


def test_classify_ccf_normalization_strips_punctuation():
    """连字符/点号/& 等标点归一化后不影响匹配。"""
    assert classify_ccf("CVPR-2024")["ccf_level"] == "A"
    assert classify_ccf("IEEE TPAMI (Pattern Analysis & Machine Intelligence)")["ccf_level"] == "A"
    assert classify_ccf("IEEE Trans. on Image Processing")["ccf_level"] == "B"


def test_classify_ccf_naacl_precedes_acl():
    """naacl 含 acl 子串, 必须匹配更具体的 naacl(B) 而非 acl(A)。"""
    assert classify_ccf("NAACL 2024")["ccf_level"] == "B"


def test_classify_ccf_icml_not_confused_with_icme():
    """icml(A) 与 icme(C) 不得相互误匹配。"""
    assert classify_ccf("ICML 2024")["ccf_level"] == "A"
    assert classify_ccf("ICME 2024")["ccf_level"] == "C"


def test_acm_multimedia_long_name():
    """ACM Multimedia 长名/简写均命中 B(acmmm 匹配不到 acmmultimedia, 需全称关键词)。"""
    assert classify_ccf("ACM Multimedia")["ccf_level"] == "B"
    assert classify_ccf("ACM Multimedia 2024")["ccf_level"] == "B"
    assert classify_ccf("Proceedings of the 31st ACM International Conference on Multimedia")["ccf_level"] == "B"


def test_web_conference_official_name():
    """WWW 自 2018 年官方名 The Web Conference 命中 B; 旧简称 WWW 保持 B。"""
    assert classify_ccf("The Web Conference 2024")["ccf_level"] == "B"
    assert classify_ccf("WWW 2024")["ccf_level"] == "B"

