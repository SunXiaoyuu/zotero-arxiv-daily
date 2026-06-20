from zotero_arxiv_daily.retriever.openalex_retriever import OpenAlexRetriever
from zotero_arxiv_daily.venues import Venue


def test_openalex_convert_to_paper_sets_venue_and_abstract():
    retriever = OpenAlexRetriever.__new__(OpenAlexRetriever)
    raw = {
        "venue": Venue(
            kind="conference",
            name="International Conference on Software Engineering (ICSE)",
            ccf="CCF A",
            rank="Top",
        ),
        "source": {"display_name": "International Conference on Software Engineering"},
        "work": {
            "id": "https://openalex.org/W1",
            "title": "LLMs for Smart Contract Generation",
            "abstract_inverted_index": {"Large": [0], "language": [1], "models": [2]},
            "authorships": [
                {"author": {"display_name": "Alice"}},
                {"author": {"display_name": "Bob"}},
            ],
            "primary_location": {
                "landing_page_url": "https://example.com/paper",
                "pdf_url": "https://example.com/paper.pdf",
            },
        },
    }

    paper = retriever.convert_to_paper(raw)

    assert paper.source == "openalex"
    assert paper.title == "LLMs for Smart Contract Generation"
    assert paper.abstract == "Large language models"
    assert paper.authors == ["Alice", "Bob"]
    assert paper.venue == "International Conference on Software Engineering (Conference; CCF A; Top)"
    assert paper.pdf_url == "https://example.com/paper.pdf"
