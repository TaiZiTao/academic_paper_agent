from app.paper.library_state import LibraryQAState


def test_state_allows_partial_updates():
    s: LibraryQAState = {"input_text": "超分方法", "retry_count": 0}
    s["intent"] = "qa"
    assert s["intent"] == "qa" and s["retry_count"] == 0


def test_state_default_fields():
    s: LibraryQAState = {}
    assert s.get("retry_count", 0) == 0
