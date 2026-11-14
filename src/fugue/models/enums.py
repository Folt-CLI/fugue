"""Exact vocabulary used by Fugue tickets and configuration."""

from enum import StrEnum


class Size(StrEnum):
    XS = "XS"
    S = "S"
    M = "M"
    L = "L"
    XL = "XL"


class Status(StrEnum):
    TO_DO = "S0: To Do"
    IN_ANALYSIS = "S1: In Analysis"
    READY_FOR_IMPLEMENTATION = "S3: Ready For Implementation"
    IN_IMPLEMENTATION = "S4: In Implementation"
    DONE = "S5: Done"


class IssueType(StrEnum):
    TASK = "Task"
    BUG = "Bug"
    FEATURE = "Feature"
    QUESTION = "Question"
    EPIC = "Epic"
    MAINTENANCE = "Maintenance"
    DOCUMENTATION = "Documentation"
    INCIDENT = "Incident"
    TEST = "Test"


class Priority(StrEnum):
    URGENT = "Urgent"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class Component(StrEnum):
    BUILD = "Build"
    DEV_DOCS = "Dev Docs"
    DEV_TOOLS = "Dev Tools"
    SOURCE_CODE = "Source Code"
    TESTS = "Tests"
    USER_DOCS = "User Docs"


class Disposition(StrEnum):
    ABANDONED = "Abandoned"
    ANSWERED = "Answered"
    DUPLICATE = "Duplicate"
    INVALID = "Invalid"
    MERGED = "Merged"
    OBSOLETE = "Obsolete"
    OUT_OF_SCOPE = "Out of Scope"
    RELEASED = "Released"
    SUPERSEDED = "Superseded"
    WITHDRAWN = "Withdrawn"
    WONT_DO = "Won't Do"


class Role(StrEnum):
    ANALYST = "analyst"
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"
    STAND_IN = "stand_in"


class ModelTier(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class AuthenticationMode(StrEnum):
    SUBSCRIPTION_LOGIN = "subscription_login"
    API_KEY = "api_key"
