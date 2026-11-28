"""What GitHub knows about the repository itself."""

from pydantic import BaseModel, ConfigDict


class RepoInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    slug: str
    description: str = ""
    default_branch: str
    private: bool
    owner_is_org: bool = True
    open_issues: int
    stars: int
    url: str
