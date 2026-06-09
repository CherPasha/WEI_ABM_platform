from app.services.keyword_scanner import _compute_company_hits


def test_compute_hits_counts_total_and_groups():
    groups = [
        {"name": "Group A", "keywords": ["kw1", "kw2"]},
        {"name": "Group B", "keywords": ["kw3"]},
    ]
    keyword_results = {
        "kw1": {"count": 3},
        "kw2": {"count": 0},
        "kw3": {"count": 5},
    }
    hit_count, hit_groups = _compute_company_hits(groups, keyword_results)
    assert hit_count == 8
    assert hit_groups == 2


def test_compute_hits_no_matches():
    groups = [{"name": "G", "keywords": ["kw1"]}]
    keyword_results = {"kw1": {"count": 0}}
    hit_count, hit_groups = _compute_company_hits(groups, keyword_results)
    assert hit_count == 0
    assert hit_groups == 0


def test_compute_hits_partial_group():
    groups = [{"name": "G", "keywords": ["kw1", "kw2"]}]
    keyword_results = {"kw1": {"count": 2}, "kw2": {"count": 0}}
    hit_count, hit_groups = _compute_company_hits(groups, keyword_results)
    assert hit_count == 2
    assert hit_groups == 1  # group G has at least one hit
