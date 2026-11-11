"""Every link in the URI format doc must parse."""

from __future__ import annotations

import re
from pathlib import Path

from omarchy_proxy.uri import ParseError, parse_uri

_DOC = Path(__file__).resolve().parents[1] / "docs" / "uri-formats.md"


def test_each_documented_uri_parses() -> None:
    text = _DOC.read_text(encoding="utf-8")
    blocks = re.findall(r"```uri\n(.*?)```", text, flags=re.DOTALL)
    links = [line.strip() for block in blocks for line in block.splitlines() if line.strip()]
    assert links
    for link in links:
        parsed = parse_uri(link)
        assert not isinstance(parsed, ParseError), link
