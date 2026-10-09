import json
from unittest.mock import patch

from ddgs.exceptions import DDGSException

from uloo.runtime.tools import ToolRegistry
from uloo.runtime.web_search import web_search


def test_web_search_returns_bounded_sources_and_timestamp():
    with patch("uloo.runtime.web_search.DDGS") as search:
        search.return_value.text.return_value = [
            {"title": "Quote", "href": "https://example.com/quote", "body": "Price dated yesterday"},
            {"href": "javascript:alert(1)"},
        ]
        result = json.loads(web_search("plywood price", 2))
        assert result["status"] == "ok"
        assert result["retrieved_at"]
        assert result["results"] == [
            {"title": "Quote", "url": "https://example.com/quote", "snippet": "Price dated yesterday"}
        ]
        search.return_value.text.assert_called_once_with("plywood price", region="cn-zh", max_results=2)
    assert ToolRegistry().resolve_many(["web-search"]) == [web_search]


def test_search_failure_does_not_invent_results():
    with patch("uloo.runtime.web_search.DDGS") as search:
        search.return_value.text.side_effect = DDGSException("offline")
        assert json.loads(web_search("price"))["status"] == "unavailable"
        search.return_value.text.side_effect = DDGSException("No results found.")
        assert json.loads(web_search("price"))["status"] == "empty"
    assert json.loads(web_search(""))["status"] == "invalid_query"
    assert json.loads(web_search("price", 100))["status"] == "invalid_query"
