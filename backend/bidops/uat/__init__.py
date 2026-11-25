"""Offline UAT preparation; never authenticates people or authorizes a release."""

from bidops.uat.environment import check_environment
from bidops.uat.evidence import verify_bundle
from bidops.uat.schemas import UatBundle

__all__ = ["UatBundle", "check_environment", "verify_bundle"]
