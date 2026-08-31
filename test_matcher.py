from app.services.matcher import match_profile
from app.collectors.base import RawJob
from app.schemas import SearchProfile


def test_profile_only_role_and_type():
    p = SearchProfile(location="Berlin", employment_types=["Werkstudent"], target_roles=["Risikomanagement"], skills=["Excel"])
    j = RawJob(title="Werkstudent Risikomanagement", location="Berlin", description="Excel und Risikomanagement", employment_type="Werkstudent")
    ok, reasons = match_profile(j, p)
    assert ok
    assert any("Risikomanagement" in x for x in reasons)


def test_unconfigured_role_is_not_invented():
    p = SearchProfile(location="Berlin", employment_types=["Werkstudent"], target_roles=["Risikomanagement"], skills=[])
    j = RawJob(title="Werkstudent Customer Service", location="Berlin", description="Kundenservice", employment_type="Werkstudent")
    ok, _ = match_profile(j, p)
    assert not ok


def test_wrong_employment_type_rejected():
    p = SearchProfile(location="Berlin", employment_types=["Werkstudent"], target_roles=["Finance"])
    j = RawJob(title="Finance Analyst", location="Berlin", description="Vollzeit Finance", employment_type="Vollzeit")
    ok, _ = match_profile(j, p)
    assert not ok


def test_wrong_location_rejected():
    p = SearchProfile(location="Berlin", employment_types=["Werkstudent"], target_roles=["Finance"])
    j = RawJob(title="Werkstudent Finance", location="Hamburg", description="Finance", employment_type="Werkstudent")
    ok, _ = match_profile(j, p)
    assert not ok
