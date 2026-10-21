"""Workbook schema tests: Freehire dropdown, Queue approve column, category col."""

from openpyxl import load_workbook

from outreach import workbook as wb_schema


def _validations(ws):
    return [(dv.type, dv.formula1, str(dv.sqref)) for dv in ws.data_validations.dataValidation]


def test_build_workbook_dropdowns():
    wb = wb_schema.build_workbook()
    freehire_dvs = _validations(wb["Freehire"])
    assert ("list", '"y"', "H2:H1048576") in freehire_dvs

    queue_dvs = dict(
        (formula, sqref) for _, formula, sqref in _validations(wb["Queue"])
    )
    assert queue_dvs['"y,skip"'] == "S2:S1048576"   # approve = column S
    assert queue_dvs[f'"{",".join(wb_schema.STATUSES)}"'] == "R2:R1048576"  # status = R


def test_freehire_category_column_exists():
    assert "category" in wb_schema.FREEHIRE_COLUMNS
    wb = wb_schema.build_workbook()
    headers = [c.value for c in wb["Freehire"][1]]
    assert "category" in headers


def test_apply_validations_idempotent():
    wb = wb_schema.build_workbook()
    wb_schema.apply_validations(wb)
    wb_schema.apply_validations(wb)
    assert len(_validations(wb["Freehire"])) == 1
