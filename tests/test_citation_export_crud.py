"""Unit tests for CRUD operations, citation formatting, and LaTeX/PDF document export."""

from pathlib import Path

from artikel_mcp.cache import PaperCache
from artikel_mcp.citation import (
    format_bibliography,
    format_in_text,
    format_reference,
    parse_author_name,
)
from artikel_mcp.export import compile_latex_to_pdf, render_latex
from artikel_mcp.models import PaperRecord
from artikel_mcp.service import (
    add_paper,
    delete_paper,
    export_bibliography_file,
    export_paper_document,
    format_paper_citation,
    update_paper,
)


def _sample_record() -> PaperRecord:
    return PaperRecord(
        source="manual",
        source_id="test-101",
        title="Deepfake Detection in Electoral Integrity Context",
        authors=["Chiquita Thefirstly Noerman", "Aji Lukman Ibrahim"],
        doi="10.26623/julr.v7i2.8995",
        url="https://doi.org/10.26623/julr.v7i2.8995",
        publication="Jurnal USM Law Review",
        year=2024,
        abstract="This study investigates deepfake regulation and electoral integrity.",
        research_results="The results explain that Indonesia needs explicit regulation.",
        markdown="# Deepfake Detection\n\n## Introduction\nRapid technological growth poses risks.",
        extra={"volume": "7", "issue": "2", "pages": "100-115"},
    )


# --------------------------------------------------------------------------
# 1. Author Name Parsing & Citation Styles
# --------------------------------------------------------------------------


def test_parse_author_name():
    a1 = parse_author_name("Noerman, Chiquita Thefirstly")
    assert a1.last == "Noerman"
    assert a1.first == "Chiquita"
    assert a1.middle == "Thefirstly"
    assert a1.initials == "C. T."

    a2 = parse_author_name("Aji Lukman Ibrahim")
    assert a2.last == "Ibrahim"
    assert a2.first == "Aji"
    assert a2.middle == "Lukman"

    a3 = parse_author_name("Gunawan")
    assert a3.last == "Gunawan"


def test_citation_styles_apa7():
    rec = _sample_record()
    ref = format_reference(rec, style="apa7")
    assert "Noerman, C. T., & Ibrahim, A. L." in ref
    assert "(2024)" in ref
    assert "*Jurnal USM Law Review*" in ref
    assert "https://doi.org/10.26623/julr.v7i2.8995" in ref

    in_text = format_in_text(rec, style="apa7", narrative=False)
    assert in_text == "(Noerman & Ibrahim, 2024)"

    narrative = format_in_text(rec, style="apa7", narrative=True)
    assert narrative == "Noerman and Ibrahim (2024)"


def test_citation_styles_chicago_and_ieee_and_mla():
    rec = _sample_record()

    # Chicago Author-Date
    chicago_ad = format_reference(rec, style="chicago")
    assert "2024." in chicago_ad
    assert '"Deepfake Detection in Electoral Integrity Context"' in chicago_ad

    # IEEE
    ieee = format_reference(rec, style="ieee")
    assert "C. T. Noerman and A. L. Ibrahim" in ieee
    assert "doi: 10.26623/julr.v7i2.8995" in ieee

    # MLA 9
    mla = format_reference(rec, style="mla")
    assert "Noerman, Chiquita" in mla
    assert "vol. 7" in mla
    assert "no. 2" in mla

    # Harvard
    harvard = format_reference(rec, style="harvard")
    assert "(2024)" in harvard
    assert "'Deepfake detection in electoral integrity context'," in harvard

    # BibTeX
    bib = format_reference(rec, style="bibtex")
    assert "@article{noerman2024," in bib
    assert "author = {Chiquita Thefirstly Noerman and Aji Lukman Ibrahim}," in bib


def test_format_bibliography_multi():
    r1 = _sample_record()
    r2 = PaperRecord(
        source="manual",
        source_id="test-102",
        title="AI Liability Framework",
        authors=["Gunawan Gunawan"],
        year=2026,
        publication="Jurnal Ilmu Hukum",
    )
    bib_text = format_bibliography([r1, r2], style="apa7")
    # Gunawan should appear before Noerman alphabetically
    gunawan_pos = bib_text.find("Gunawan")
    noerman_pos = bib_text.find("Noerman")
    assert gunawan_pos < noerman_pos


# --------------------------------------------------------------------------
# 2. LaTeX Rendering & Document Export
# --------------------------------------------------------------------------


def test_render_latex_templates():
    rec = _sample_record()

    # Academic template
    latex_acad = render_latex(rec, template_name="academic", citation_style="apa7")
    assert r"\documentclass[11pt,a4paper]{article}" in latex_acad
    assert "Deepfake Detection in Electoral Integrity Context" in latex_acad
    assert r"\subsection*{Introduction}" in latex_acad
    assert r"\section*{References}" in latex_acad

    # Review template
    latex_rev = render_latex(rec, template_name="review")
    assert "Research Review Dossier" in latex_rev

    # Brief template
    latex_brief = render_latex(rec, template_name="brief")
    assert r"\paragraph*{Key Findings}" in latex_brief


def test_custom_latex_template():
    rec = _sample_record()
    custom_tmpl = (
        r"\documentclass{article}\begin{document}TITLE: {{title}} REF: {{reference}}\end{document}"
    )
    rendered = render_latex(rec, custom_template=custom_tmpl)
    assert "TITLE: Deepfake Detection in Electoral Integrity Context" in rendered
    assert "REF:" in rendered


def test_compile_latex_to_pdf(tmp_path):
    simple_tex = r"""
    \documentclass{article}
    \title{Test Compilation}
    \author{Test Author}
    \begin{document}
    \maketitle
    Testing LaTeX PDF export integration.
    \end{document}
    """
    out_pdf = tmp_path / "output.pdf"
    success, msg = compile_latex_to_pdf(simple_tex, out_pdf)
    assert success is True
    assert out_pdf.exists()
    assert out_pdf.stat().st_size > 0


def test_export_paper_service(tmp_path):
    cache = PaperCache(tmp_path / "test_export.db")
    rec = _sample_record()
    cache.upsert(rec)

    res = export_paper_document(
        cache,
        rec.doi,
        template="academic",
        style="apa7",
        compile_pdf=True,
        output_dir=str(tmp_path / "exports"),
    )
    assert res["success"] is True
    assert Path(res["tex_path"]).exists()
    assert res["pdf_compiled"] is True
    assert Path(res["pdf_path"]).exists()


# --------------------------------------------------------------------------
# 3. Full CRUD Service Operations
# --------------------------------------------------------------------------


def test_crud_lifecycle(tmp_path):
    cache = PaperCache(tmp_path / "test_crud.db")

    # 1. CREATE (add_paper)
    created = add_paper(
        cache,
        title="Privacy and AI Ethics in Digital Forensics",
        authors=["Alice Smith", "Bob Jones"],
        doi="10.1234/ethics.v1i1.1",
        url="https://doi.org/10.1234/ethics.v1i1.1",
        publication="Ethics in Tech Journal",
        year=2025,
        abstract="Examining privacy dilemmas in forensic AI algorithms.",
        research_results="Highlights crucial boundaries for evidence handling.",
        markdown="# Ethics in Forensic AI\n\nDetailed analysis here.",
    )
    assert created["success"] is True
    assert created["key"] == "10.1234/ethics.v1i1.1"

    # 2. READ (get_cached_paper and search)
    cached = cache.get_by_key("10.1234/ethics.v1i1.1")
    assert cached is not None
    assert cached.title == "Privacy and AI Ethics in Digital Forensics"
    assert cached.authors == ["Alice Smith", "Bob Jones"]

    fts_hits = cache.search("forensic algorithms")
    assert len(fts_hits) >= 1
    assert fts_hits[0].doi == "10.1234/ethics.v1i1.1"

    # 3. UPDATE (update_paper)
    updated = update_paper(
        cache,
        "10.1234/ethics.v1i1.1",
        title="Privacy and AI Ethics in Digital Forensics (Updated)",
        year=2026,
        markdown="# Updated Notes\nAdded section on GDPR compliance.",
    )
    assert updated["success"] is True
    assert updated["record"]["title"] == "Privacy and AI Ethics in Digital Forensics (Updated)"
    assert updated["record"]["year"] == 2026

    # Verify update in cache
    re_cached = cache.get_by_key("10.1234/ethics.v1i1.1")
    assert re_cached.title == "Privacy and AI Ethics in Digital Forensics (Updated)"
    assert re_cached.year == 2026
    assert "GDPR compliance" in (re_cached.markdown or "")

    # 4. CITATION (format_paper_citation)
    cite = format_paper_citation(cache, "10.1234/ethics.v1i1.1", style="apa7")
    assert "Smith, A., & Jones, B." in cite["reference"]
    assert "(2026)" in cite["reference"]
    assert cite["in_text"] == "(Smith & Jones, 2026)"

    # 5. EXPORT BIBLIOGRAPHY (export_bibliography_file)
    bib_out = tmp_path / "exported.bib"
    exported = export_bibliography_file(
        cache,
        keys=["10.1234/ethics.v1i1.1"],
        format_type="bibtex",
        output_path=str(bib_out),
    )
    assert exported["count"] == 1
    assert bib_out.exists()
    assert "@article{smith2026," in bib_out.read_text(encoding="utf-8")

    # 6. DELETE (delete_paper)
    del_res = delete_paper(cache, "10.1234/ethics.v1i1.1")
    assert del_res["success"] is True

    # Verify paper is gone from cache and FTS5
    assert cache.get_by_key("10.1234/ethics.v1i1.1") is None
    assert len(cache.search("forensic algorithms")) == 0
