"""Project-neutral result-analysis contracts and read-only query tools.

Project adapters emit a normalized run bundle.  The generic toolbox can then
inspect optimization runs, simulations, forecasts or APC histories without
knowing the project's domain classes.
"""

from .toolkit import AnalysisToolError, RunBundleRepository, ResultAnalysisTools

__all__ = ["AnalysisToolError", "RunBundleRepository", "ResultAnalysisTools"]
