from app.services.scanner import ScanManager


def test_studis_online_listing_is_never_a_final_job():
    assert ScanManager._is_listing_page("https://www.studis-online.de/jobben/werkstudent.php")
    assert ScanManager._is_listing_page("https://www.studis-online.de/jobben/studentenjobs.php")


def test_studis_online_listing_expands_internal_and_external_job_links():
    html = '''
    <html><body>
      <a href="/jobben/studentenjobs.php">Werkstudentenjobs</a>
      <a href="/jobben/werkstudent-data-analyst-123">Werkstudent Data Analyst Berlin</a>
      <a href="https://careers.example.com/job/456">Werkstudent Business Intelligence</a>
      <a href="/kontakt">Kontakt</a>
      <a href="/jobben/studentenjobs.php?seite=2">weiter</a>
    </body></html>
    '''
    rows = ScanManager._listing_links(
        html,
        "https://www.studis-online.de/jobben/studentenjobs.php",
        "studis_online",
    )
    urls = [r.url for r in rows]
    assert "https://www.studis-online.de/jobben/werkstudent-data-analyst-123" in urls
    assert "https://careers.example.com/job/456" in urls
    assert not any("studentenjobs.php" in u for u in urls)
    assert not any("/kontakt" in u for u in urls)


def test_non_job_documents_and_info_pages_are_rejected():
    from app.services.discovery import is_non_job_url, is_direct_job_url
    assert is_non_job_url("https://www.informationsportal.de/wp-content/uploads/document__2572__2016-11-23-Gem-Rundschreiben-Werkstudenten-final.pdf")
    assert not is_direct_job_url("https://www.informationsportal.de/wp-content/uploads/document__2572__2016-11-23-Gem-Rundschreiben-Werkstudenten-final.pdf", "generic")


def test_arbeitsagentur_search_page_is_listing():
    assert ScanManager._is_listing_page("https://www.arbeitsagentur.de/jobsuche/suche?was=werkstudent&suchbereich=jobs")
    assert not ScanManager._is_listing_page("https://www.arbeitsagentur.de/jobsuche/jobdetail/10000-123")
