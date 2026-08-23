"""发表年份提取测试。"""

from app.paper.parser import extract_publication_year


def test_meta_year():
    assert extract_publication_year("", "2024") == 2024


def test_regex_copyright():
    assert extract_publication_year("(c) 2024 IEEE. All rights reserved.", "") == 2024


def test_regex_arxiv():
    assert extract_publication_year("arXiv:2401.12345v2", "") == 2024


def test_no_year():
    assert extract_publication_year("Some text without year", "") is None


def test_fund_number_not_matched():
    assert extract_publication_year("U23B2052", "") is None


def test_pdf_creation_date_metadata():
    assert extract_publication_year("", "D:20231215093027") == 2023


def test_meta_takes_priority():
    assert extract_publication_year("published in 2019", "2023") == 2023
