"""Pydantic schemas for counterfactual anchor collection."""

from pydantic import BaseModel, Field


class CounterfactualSubmitRequest(BaseModel):
    """One life-decision counterfactual anchor submitted by a user."""

    decision_context: str = Field(..., min_length=1, max_length=2000)
    actual_choice: str | None = Field(default=None, max_length=2000)
    actual_result: str | None = Field(default=None, max_length=4000)
    counterfactual_action: str = Field(..., min_length=1, max_length=2000)
    counterfactual_result: str = Field(..., min_length=1, max_length=4000)


class CounterfactualSubmitResponse(BaseModel):
    """Summary returned after storing a counterfactual anchor."""

    saved: bool
    core_memory_updated: bool


class CounterfactualSuggestion(BaseModel):
    """LLM-discovered candidate decision point."""

    context: str = Field(..., min_length=1, max_length=2000)
    actual_choice: str = Field(..., min_length=1, max_length=2000)
    actual_result: str = Field(..., min_length=1, max_length=4000)
