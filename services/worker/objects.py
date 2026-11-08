from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator

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
    origin: Literal["discussed", "inferred", "proposed"] = Field(
        default="discussed", description="Distinguish meeting requests from derived needs and new AI ideas",
    )
    assumptions: list[str] = Field(default_factory=list, description="Unconfirmed scope, roles, or behavior introduced by inference/proposal")
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
    related_story_ids: list[str] = Field(
        default_factory=list,
        description="Existing board references with overlapping scope, in source:id format; not automatic duplicates",
    )
    priority: Literal["high", "med", "low"]
    estimate: Optional[int] = None
    needs_clarification: bool
    source: SourceRef

    @model_validator(mode="after")
    def mark_unconfirmed_scope(self):
        if self.origin != "discussed":
            self.needs_clarification = True
        return self

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

class StoryRelationship(BaseModel):
    other_story_index: Optional[int] = Field(default=None, ge=0)
    existing_story_id: Optional[str] = None
    relationship: Literal["independent", "overlap", "contains", "contained_by", "depends_on"]
    reason: str = Field(min_length=1, description="Explain actual behavior/criteria overlap or why the scopes are distinct")


class StoryReview(BaseModel):
    index: int
    invest_score: int = Field(ge=0, le=6)
    confidence: float = Field(ge=0.0, le=1.0)
    flags: list[InvestFlag]
    relationships: list[StoryRelationship] = Field(default_factory=list)
    suggested_title: Optional[str] = None
    suggested_acceptance_criteria: Optional[list[str]] = None
    recommend_split: bool
    split_suggestion: Optional[str] = None
    recommend_reject: bool

class CritiqueResult(BaseModel):
    reviews: list[StoryReview]
