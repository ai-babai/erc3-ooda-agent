# dispatcher.py — shared request schemas for the lightweight agent
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from erc3 import erc3 as dev


class ReqDeleteWikiPage(BaseModel):
    tool: Literal["/wiki/delete"] = "/wiki/delete"
    file: str
    changed_by: Optional[dev.EmployeeID] = None


class ReqListAllProjectsForUser(BaseModel):
    tool: Literal["/all-projects-for-user"] = "/all-projects-for-user"
    user: dev.EmployeeID


class ReqListAllCustomersForUser(BaseModel):
    tool: Literal["/all-customers-for-user"] = "/all-customers-for-user"
    user: dev.EmployeeID


__all__ = [
    "ReqDeleteWikiPage",
    "ReqListAllProjectsForUser",
    "ReqListAllCustomersForUser",
]

