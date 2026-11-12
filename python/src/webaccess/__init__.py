"""Web Access Framework: a fast, polite, parallel engine that goals build on. See spec/ for the design."""
from .config import Config
from .engine import Engine
from .goals import CrawlGoal, FetchGoal, Goal
from .model import Response, Task

__all__ = ["Config", "Engine", "Goal", "FetchGoal", "CrawlGoal", "Task", "Response"]
__version__ = "0.1.0"
