"""How results look in the terminal: one view per area."""

from fugue.cli.views.branches import BranchView
from fugue.cli.views.comments import CommentView
from fugue.cli.views.labels import LabelView
from fugue.cli.views.messages import Messages
from fugue.cli.views.preview import PreviewView
from fugue.cli.views.projects import ProjectView
from fugue.cli.views.pulls import PullRequestView
from fugue.cli.views.repo import RepoView
from fugue.cli.views.tickets import TicketView
from fugue.cli.views.view import View
from fugue.cli.views.work import WorkView

__all__ = [
    "BranchView",
    "CommentView",
    "LabelView",
    "Messages",
    "PreviewView",
    "ProjectView",
    "PullRequestView",
    "RepoView",
    "TicketView",
    "View",
    "WorkView",
]
