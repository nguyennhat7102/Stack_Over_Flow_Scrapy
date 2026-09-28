# Define here the models for your scraped items
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/items.html

from dataclasses import dataclass, field


@dataclass
class StackOverFlowItem:
    """
    Data transfer object for a single Stack Overflow question-tag transaction.

    Fields
    ------
    question_id : int
        Unique Stack Overflow question identifier. The primary / dedup key.
    tags : list[str]
        Raw tag list from the API. Validated (>=2 distinct) and normalised
        (lowercased, stripped, deduped) by the pipeline chain before storage.
    title : str
        Human-readable question title. Stored for context; not part of the
        market-basket transaction definition.
    creation_date : int
        UNIX timestamp from the API. Converted to ISO-8601 UTC string by
        NormalizationPipeline before storage.
    score : int
        Question score. Optional context field; defaults to 0.
    answer_count : int
        Number of answers. Optional context field; defaults to 0.
    """

    question_id: int
    tags: list = field(default_factory=list)
    title: str = ""
    creation_date: int = 0
    score: int = 0
    answer_count: int = 0
