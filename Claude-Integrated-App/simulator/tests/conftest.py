import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mph_sim import datasets                      # noqa: E402
from mph_sim.optimizer import solve_plan, SolveOptions   # noqa: E402


@pytest.fixture(scope="session")
def s1():
    return datasets.sulphur_minimal()


@pytest.fixture(scope="session")
def s2():
    return datasets.mph_phosphate()


@pytest.fixture(scope="session")
def s3():
    return datasets.end_to_end()


@pytest.fixture(scope="session")
def s1_plan(s1):
    return solve_plan(s1, SolveOptions(max_time_s=5))


@pytest.fixture(scope="session")
def s2_plan(s2):
    return solve_plan(s2, SolveOptions(max_time_s=8))


def nav_to(at, page_label: str):
    """Navigate a canonical route through the operator sidebar.

    Historical Monitor routes remain useful test inputs.  The KPI, stockyard
    and schedule routes resolve to the combined first Monitor page; machine
    coverage resolves to the second.
    """
    from ui import common

    at.sidebar.radio[0].set_value(common.PAGE_TO_GROUP[page_label])
    at.run()
    at.sidebar.radio[1].set_value(common.visible_page_label(page_label))
    at.run()
