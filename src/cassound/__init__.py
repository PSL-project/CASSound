"""Public headless API. Importing this module starts no GUI, devices, or workers."""

from .api import Session
from .domain.types import Region, ExportSpec, AnalysisSpec, QuerySpec, ResultRef

__version__ = "1.0.0rc1"
__all__ = ["Session", "Region", "ExportSpec", "__version__", "AnalysisSpec", "QuerySpec", "ResultRef"]
