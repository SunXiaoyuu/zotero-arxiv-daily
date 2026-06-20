from zotero_arxiv_daily.sent_history import SentHistory, paper_key
from tests.canned_responses import make_sample_paper


def test_paper_key_normalizes_arxiv_versions():
    paper_v1 = make_sample_paper(url="https://arxiv.org/abs/2606.00001v1")
    paper_v2 = make_sample_paper(url="https://arxiv.org/abs/2606.00001v2")

    assert paper_key(paper_v1) == "arxiv:2606.00001"
    assert paper_key(paper_v2) == "arxiv:2606.00001"


def test_sent_history_adds_and_detects_duplicates(tmp_path):
    history = SentHistory(tmp_path / "sent.json")
    paper = make_sample_paper(url="https://arxiv.org/abs/2606.00001v1")
    same_paper_new_version = make_sample_paper(url="https://arxiv.org/abs/2606.00001v2")

    assert not history.contains(paper)

    history.add_many([paper])

    reloaded = SentHistory(tmp_path / "sent.json")
    assert reloaded.contains(same_paper_new_version)
