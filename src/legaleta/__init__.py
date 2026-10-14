"""legaleta: EU driving-time scheduler. Planning aid, not legal advice."""

__version__ = "0.1.0"
DISCLAIMER = "Planning aid, not legal advice."

from legaleta.checker import Violation, validate  # noqa: E402
from legaleta.clock import Clock  # noqa: E402
from legaleta.policy import PRESETS as POLICY_PRESETS  # noqa: E402
from legaleta.policy import Policy, policy_from  # noqa: E402
from legaleta.rules import PRESETS as RULE_PRESETS  # noqa: E402
from legaleta.rules import RuleSet, rules_from  # noqa: E402
from legaleta.scheduler import PlanResult, SchedulingError, plan  # noqa: E402
from legaleta.state import DriverState, Envelope, envelope, project  # noqa: E402
from legaleta.timeline import Block, Drive, Ferry, Stop  # noqa: E402

__all__ = [
    "DISCLAIMER",
    "POLICY_PRESETS",
    "RULE_PRESETS",
    "Block",
    "Clock",
    "Drive",
    "DriverState",
    "Envelope",
    "Ferry",
    "PlanResult",
    "Policy",
    "RuleSet",
    "SchedulingError",
    "Stop",
    "Violation",
    "__version__",
    "envelope",
    "plan",
    "policy_from",
    "project",
    "rules_from",
    "validate",
]
