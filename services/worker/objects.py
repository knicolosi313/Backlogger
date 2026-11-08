from typing import Literal, Optional
from pydantic import BaseModel, Field

class TranscriptSegment(BaseModel):
    speaker: str = Field(description='Name if stated in the audio, otherwise "Speaker N"')
    start_s: float
    end_s: float
    text: str

class Transcript(BaseModel):
    language: str = Field(description='Main language spoken, e.g. "en"')
    segments: list[TranscriptSegment]

class SourceRef(BaseModel):
    quote: str
    start_s: float
    end_s: float
    speaker: str

class DraftStory(BaseModel):
    title: str
    as_a: str
    i_want: str
    so_that: str
    acceptance_criteria: list[str] = Field(
        description="Distinct testable checklist items; aim for 2–6 when supported by the transcript"
    )
    solution_tasks: list[str] = Field(
        default_factory=list,
        description="Proposed actionable implementation steps, separate from acceptance criteria",
    )
    epic: Optional[str] = None
    priority: Literal["high", "med", "low"]
    estimate: Optional[int] = None
    needs_clarification: bool
    source: SourceRef

class OpenQuestion(BaseModel):
    question: str
    related_story_title: Optional[str] = None
    start_s: Optional[float] = None

class ExtractionResult(BaseModel):
    stories: list[DraftStory]
    open_questions: list[OpenQuestion]

class InvestFlag(BaseModel):
    criterion: Literal[
        "Independent", "Negotiable", "Valuable",
        "Estimable", "Small", "Testable", "Grounded",
    ]
    issue: str
    quote: Optional[str] = None

class StoryReview(BaseModel):
    index: int
    invest_score: int = Field(ge=0, le=6)
    confidence: float = Field(ge=0.0, le=1.0)
    flags: list[InvestFlag]
    suggested_title: Optional[str] = None
    suggested_acceptance_criteria: Optional[list[str]] = None
    recommend_split: bool
    split_suggestion: Optional[str] = None
    recommend_reject: bool

class CritiqueResult(BaseModel):
    reviews: list[StoryReview]
