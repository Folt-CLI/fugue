"""What GitHub knows about the repository itself."""

from pydantic import BaseModel, ConfigDict


class RepoInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    slug: str
    description: str = ""
    default_branch: str
    private: bool
    open_issues: int
    stars: int
    url: str
