import base64


def test_keyword_scan_bytes_roundtrip():
    """Verify base64 encode/decode preserves XLSX bytes exactly."""
    original = b"PK\x03\x04fake_xlsx_content_bytes_here"
    encoded = base64.b64encode(original).decode()
    assert isinstance(encoded, str)
    decoded = base64.b64decode(encoded)
    assert decoded == original
