from app.paper.prompts import parse_filter_response

def test_parse_empty():
    f = parse_filter_response("{}")
    assert f["field"] == "" and f["year_min"] is None
    assert f["year_max"] is None and f["authors"] == []
    assert f["keywords"] == [] and f["language"] == ""

def test_parse_full():
    f = parse_filter_response('{"field":"超分辨率","year_min":2024,"year_max":2025,"authors":["张"],"keywords":["轻量"],"language":"en"}')
    assert f["field"] == "超分辨率" and f["year_min"] == 2024
    assert f["year_max"] == 2025 and f["language"] == "en"
    assert f["authors"] == ["张"] and f["keywords"] == ["轻量"]

def test_parse_natural_text():
    f = parse_filter_response('前后文: {"field": "超分辨率"} 结尾')
    assert f["field"] == "超分辨率"

def test_parse_bad_json():
    f = parse_filter_response("not json at all")
    assert f["field"] == "" and f["year_min"] is None

def test_parse_null_values():
    f = parse_filter_response('{"field": null, "year_min": null, "authors": [], "language": ""}')
    assert f["field"] == "" and f["year_min"] is None and f["authors"] == []

def test_year_string():
    f = parse_filter_response('{"year_min": "2024"}')
    assert f["year_min"] == 2024

def test_year_out_of_range():
    f = parse_filter_response('{"year_min": 1899}')
    assert f["year_min"] is None

def test_authors_string():
    f = parse_filter_response('{"authors": "Wang"}')
    assert f["authors"] == ["Wang"]

def test_list_with_nulls():
    f = parse_filter_response('{"authors": ["A", null, "B"]}')
    assert f["authors"] == ["A", "B"]

def test_filter_prompt_includes_available_fields():
    from app.paper.prompts import build_filter_extraction_prompt
    p = build_filter_extraction_prompt("超分的方法", ["超分辨率", "图像去雾"])
    assert "超分辨率" in p and "图像去雾" in p
    assert "归一化" in p or "口语" in p

def test_filter_prompt_without_fields():
    from app.paper.prompts import build_filter_extraction_prompt
    p = build_filter_extraction_prompt("有哪些论文", None)
    assert "用户问题" in p
