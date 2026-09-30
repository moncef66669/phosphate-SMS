"""Cross-site asset labels remain unambiguous without changing engine IDs."""

from mph_sim import datasets
from mph_sim.domain import Job
from ui.theme import gantt_figure, jobs_to_gantt_df, machine_display_label


def _cross_site_jobs():
    return [
        Job("J-MPH", "ST01-A", "stack", "HP-01", grade="TBT"),
        Job("J-LOU", "140LG-RC-01", "reclaim", "LT-P1", grade="TBT"),
        Job("J-BEN", "RC2", "dispatch", "BG-P1", grade="TBT"),
    ]


def test_machine_display_labels_use_requested_site_prefixes():
    sc = datasets.end_to_end()
    assert machine_display_label(sc, "ST01-A") == "MPH · ST01-A"
    assert machine_display_label(sc, "140LG-RC-01") == "LOU · 140LG-RC-01"
    assert machine_display_label(sc, "RC2") == "BEN · RC2"


def test_job_dataframe_keeps_ids_and_adds_operator_labels():
    sc = datasets.end_to_end()
    df = jobs_to_gantt_df(sc, _cross_site_jobs())
    assert set(df.machine) == {"ST01-A", "140LG-RC-01", "RC2"}
    assert set(df.machine_label) == {
        "MPH · ST01-A", "LOU · 140LG-RC-01", "BEN · RC2"
    }


def test_machine_gantt_uses_site_qualified_axis_labels():
    sc = datasets.end_to_end()
    df = jobs_to_gantt_df(sc, _cross_site_jobs())
    fig = gantt_figure(df, color_by="kind", group_by="machine")
    axis_labels = {str(v) for trace in fig.data for v in trace.y}
    assert axis_labels == {
        "MPH · ST01-A", "LOU · 140LG-RC-01", "BEN · RC2"
    }

