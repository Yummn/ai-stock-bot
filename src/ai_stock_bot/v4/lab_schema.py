"""Research inputs are independent of the active account and plan."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .model_recipes import ModelTuning, model_specs, resolved_spec


class Study(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    mode: Literal["compare", "tune"] = "compare"
    recipes: list[str] = Field(default_factory=lambda: ["rotation", "hgb20_regularized", "ridge20"], min_length=1, max_length=8)
    recipe: str = "hgb20_regularized"
    tuning: ModelTuning = Field(default_factory=ModelTuning)
    start_year: int = Field(2021, ge=2015, le=2099)
    end_year: int = Field(2024, ge=2015, le=2099)
    check_start_year: int = Field(2025, ge=2015, le=2099)
    check_end_year: int = Field(2025, ge=2015, le=2099)
    budget: Literal[3, 5, 9] = 5

    @model_validator(mode="after")
    def valid_study(self):
        allowed = {*model_specs(), "rotation", "hold"}
        if any(key not in allowed for key in self.recipes) or self.recipe not in model_specs():
            raise ValueError("未知研究模型")
        self.recipes = list(dict.fromkeys(self.recipes))
        if self.start_year > self.end_year or self.end_year >= date.today().year:
            raise ValueError("研究区间必须是已经结束的自然年")
        if self.mode == "tune":
            if not self.end_year < self.check_start_year <= self.check_end_year < date.today().year:
                raise ValueError("检验年份必须晚于调参年份，不能重叠，且必须是完整自然年")
            resolved_spec(self.recipe, self.tuning)
        return self
