"""LaTeX and PDF document generation with standardized academic templates."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from artikel_mcp.citation import format_reference
from artikel_mcp.models import PaperRecord

logger = logging.getLogger("artikel_mcp.export")

_LATEX_SPECIAL = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def escape_latex(text: str) -> str:
    """Escape LaTeX special characters in plain text."""
    if not text:
        return ""
    pattern = re.compile(r"([\\&%$#_{}~^])")
    return pattern.sub(lambda m: _LATEX_SPECIAL[m.group(1)], text)


def markdown_to_latex(md: str) -> str:
    """Convert academic markdown into clean LaTeX markup."""
    if not md:
        return ""

    lines = md.splitlines()
    out: list[str] = []
    in_code_block = False
    in_itemize = False
    in_enumerate = False

    for line in lines:
        stripped = line.strip()

        # Fenced code blocks
        if stripped.startswith("```"):
            if in_code_block:
                out.append(r"\end{verbatim}")
                in_code_block = False
            else:
                if in_itemize:
                    out.append(r"\end{itemize}")
                    in_itemize = False
                if in_enumerate:
                    out.append(r"\end{enumerate}")
                    in_enumerate = False
                out.append(r"\begin{verbatim}")
                in_code_block = True
            continue

        if in_code_block:
            out.append(line)
            continue

        # Horizontal rules
        if re.match(r"^(\*{3,}|-{3,}|_{3,})$", stripped):
            if in_itemize:
                out.append(r"\end{itemize}")
                in_itemize = False
            if in_enumerate:
                out.append(r"\end{enumerate}")
                in_enumerate = False
            out.append(r"\bigskip\hrule\bigskip")
            continue

        # Section Headings
        m_head = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if m_head:
            if in_itemize:
                out.append(r"\end{itemize}")
                in_itemize = False
            if in_enumerate:
                out.append(r"\end{enumerate}")
                in_enumerate = False

            level = len(m_head.group(1))
            htitle = escape_latex(m_head.group(2))
            if level == 1:
                out.append(f"\n\\section*{{{htitle}}}\n")
            elif level == 2:
                out.append(f"\n\\subsection*{{{htitle}}}\n")
            elif level == 3:
                out.append(f"\n\\subsubsection*{{{htitle}}}\n")
            else:
                out.append(f"\n\\paragraph*{{{htitle}}}\n")
            continue

        # Unordered list items
        m_item = re.match(r"^[-*+]\s+(.+)$", stripped)
        if m_item:
            if in_enumerate:
                out.append(r"\end{enumerate}")
                in_enumerate = False
            if not in_itemize:
                out.append(r"\begin{itemize}")
                in_itemize = True
            item_text = _inline_markdown_to_latex(m_item.group(1))
            out.append(f"  \\item {item_text}")
            continue

        # Ordered list items
        m_num = re.match(r"^\d+\.\s+(.+)$", stripped)
        if m_num:
            if in_itemize:
                out.append(r"\end{itemize}")
                in_itemize = False
            if not in_enumerate:
                out.append(r"\begin{enumerate}")
                in_enumerate = True
            item_text = _inline_markdown_to_latex(m_num.group(1))
            out.append(f"  \\item {item_text}")
            continue

        # If not a list item, close any open list
        if in_itemize and not stripped:
            out.append(r"\end{itemize}")
            in_itemize = False
        elif in_enumerate and not stripped:
            out.append(r"\end{enumerate}")
            in_enumerate = False

        if not stripped:
            out.append("")
            continue

        # Blockquote
        if stripped.startswith(">"):
            quote_text = _inline_markdown_to_latex(stripped.lstrip("> ").strip())
            out.append(f"\\begin{{quote}}\n{quote_text}\n\\end{{quote}}")
            continue

        # Regular paragraph text
        out.append(_inline_markdown_to_latex(line))

    if in_code_block:
        out.append(r"\end{verbatim}")
    if in_itemize:
        out.append(r"\end{itemize}")
    if in_enumerate:
        out.append(r"\end{enumerate}")

    return "\n".join(out)


def _inline_markdown_to_latex(text: str) -> str:
    """Format bold, italic, code, and links within a text line."""
    # First protect inline math if any
    math_placeholders: list[str] = []

    def _math_sub(m):
        math_placeholders.append(m.group(0))
        return f"__MATH_{len(math_placeholders)-1}__"

    text = re.sub(r"\$[^$]+\$", _math_sub, text)

    # Protect code spans
    code_placeholders: list[str] = []

    def _code_sub(m):
        code_placeholders.append(m.group(1))
        return f"__CODE_{len(code_placeholders)-1}__"

    text = re.sub(r"`([^`]+)`", _code_sub, text)

    # Escape plain characters
    text = escape_latex(text)

    # Restore code
    for i, c in enumerate(code_placeholders):
        text = text.replace(f"__CODE_{i}__", f"\\texttt{{{escape_latex(c)}}}")

    # Restore math
    for i, m in enumerate(math_placeholders):
        text = text.replace(f"__MATH_{i}__", m)

    # Bold: **text** or __text__
    text = re.sub(r"\*\*([^*]+)\*\*", r"\\textbf{\1}", text)
    # Italic: *text* or _text_
    text = re.sub(r"(?<!\\)\*([^*]+)\*", r"\\textit{\1}", text)

    # Markdown links: [text](url)
    def _link_sub(m):
        lbl = m.group(1)
        url = m.group(2)
        return f"\\href{{{url}}}{{{lbl}}}"

    text = re.sub(r"\\\[([^\]]+)\\\]\(([^)]+)\)", _link_sub, text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _link_sub, text)

    return text


TEMPLATES = {
    "academic": r"""\documentclass[11pt,a4paper]{article}
\usepackage[margin=1in]{geometry}
\usepackage{amsmath,amssymb}
\usepackage{booktabs}
\usepackage{fancyhdr}
\usepackage{xcolor}
\usepackage{hyperref}

\hypersetup{
    colorlinks=true,
    linkcolor=blue,
    filecolor=magenta,
    urlcolor=teal,
    citecolor=blue,
    pdftitle={{title_plain}}
}

\pagestyle{fancy}
\fancyhf{}
\rhead{\small \textit{{{running_title}}}}
\lhead{\small \textsc{{{publication_plain}}}}
\cfoot{\thepage}

\begin{document}

\begin{center}
    {\LARGE \textbf{{{title}}}}\\[1.5ex]
    {\large {{authors}}}\\[1ex]
    \textit{{{publication}}}\\[0.5ex]
    \small Year: {{year}} \quad \textbar \quad DOI: \href{https://doi.org/{{doi}}}{{doi}}
\end{center}

\vspace{1.5ex}
\hrule
\vspace{2ex}

\begin{abstract}
{{abstract}}
\end{abstract}

{{findings_box}}

\vspace{1ex}

{{body}}

\vspace{3ex}
\section*{References}
\noindent
{{reference}}

\end{document}
""",
    "review": r"""\documentclass[11pt,a4paper]{article}
\usepackage[margin=1in]{geometry}
\usepackage{xcolor}
\usepackage{booktabs}
\usepackage{fancyhdr}
\usepackage{hyperref}

\hypersetup{
    colorlinks=true,
    linkcolor=purple,
    urlcolor=blue,
    pdftitle={{title_plain}}
}

\pagestyle{fancy}
\fancyhf{}
\lhead{\textbf{Research Review Dossier}}
\rhead{\textit{{{year}}}}
\cfoot{\thepage}

\begin{document}

{\Huge \textbf{{{title}}}}\\[2ex]
\textbf{Authors:} {{authors}}\\[0.5ex]
\textbf{Publication:} {{publication}} ({{year}})\\[0.5ex]
\textbf{Identifier:} \href{https://doi.org/{{doi}}}{{{doi}}}

\vspace{2ex}
\hrule
\vspace{2ex}

\subsection*{Executive Summary \& Abstract}
{{abstract}}

\vspace{1.5ex}
{{findings_box}}

\vspace{2ex}
\subsection*{Full Text \& Analysis}
{{body}}

\vspace{3ex}
\subsection*{Bibliographic Citation}
\noindent
{{reference}}

\end{document}
""",
    "brief": r"""\documentclass[10pt,a4paper]{article}
\usepackage[margin=0.75in]{geometry}
\usepackage{xcolor}
\usepackage{fancyhdr}
\usepackage{hyperref}

\hypersetup{colorlinks=true, urlcolor=teal, pdftitle={{title_plain}}}
\pagestyle{plain}

\begin{document}

{\Large \textbf{{{title}}}}\\[1ex]
\textbf{{{authors}}} \textbar\ \textit{{{publication}}} ({{year}}) \textbar\ \href{https://doi.org/{{doi}}}{{{doi}}}

\vspace{1ex}
\hrule
\vspace{1.5ex}

\paragraph*{Key Findings}
{{findings}}

\paragraph*{Abstract}
{{abstract}}

\vspace{1.5ex}
\paragraph*{Full Analysis}
{{body}}

\vspace{2ex}
\hrule
\vspace{1ex}
\footnotesize
\textbf{Citation:} {{reference}}

\end{document}
""",
}


def render_latex(
    record: PaperRecord,
    template_name: str = "academic",
    citation_style: str = "apa7",
    custom_template: str | None = None,
) -> str:
    """Render a PaperRecord into a complete LaTeX document string."""
    template_str = custom_template or TEMPLATES.get(template_name, TEMPLATES["academic"])

    # Prepare escaped values
    title_esc = escape_latex(record.title or "Untitled Document")
    title_plain = re.sub(r'["\\]', "", record.title or "Untitled")[:50]
    raw_title = record.title or ""
    running_title = escape_latex(
        raw_title[:40] + "..." if len(raw_title) > 40 else raw_title
    )
    authors_esc = escape_latex(", ".join(record.authors) if record.authors else "Anonymous")
    pub_esc = escape_latex(record.publication or "Academic Index")
    pub_plain = escape_latex((record.publication or "Academic Index")[:30])
    year_esc = str(record.year or "n.d.")
    doi_esc = escape_latex(record.doi or "")
    abstract_esc = escape_latex(record.abstract or "No abstract provided.")
    results_esc = escape_latex(record.research_results or "No specific findings recorded.")

    body_latex = markdown_to_latex(record.markdown or record.abstract or "")
    ref_formatted = format_reference(record, style=citation_style)
    ref_latex = _inline_markdown_to_latex(ref_formatted)

    findings_box = (
        r"\begin{quote}\small\textbf{Key Research Findings:}\newline "
        f"{results_esc}\\end{{quote}}"
    )

    rendered = template_str
    rendered = rendered.replace("{{title}}", title_esc)
    rendered = rendered.replace("{{title_plain}}", title_plain)
    rendered = rendered.replace("{{running_title}}", running_title)
    rendered = rendered.replace("{{authors}}", authors_esc)
    rendered = rendered.replace("{{publication}}", pub_esc)
    rendered = rendered.replace("{{publication_plain}}", pub_plain)
    rendered = rendered.replace("{{year}}", year_esc)
    rendered = rendered.replace("{{doi}}", doi_esc)
    rendered = rendered.replace("{{abstract}}", abstract_esc)
    rendered = rendered.replace("{{findings}}", results_esc)
    rendered = rendered.replace("{{findings_box}}", findings_box)
    rendered = rendered.replace("{{body}}", body_latex)
    rendered = rendered.replace("{{reference}}", ref_latex)

    return rendered


def compile_latex_to_pdf(
    latex_code: str,
    output_pdf_path: str | Path,
    compiler: str = "auto",
) -> tuple[bool, str]:
    """Compile LaTeX source to PDF using pdflatex or xelatex.

    Returns (success, message_or_log).
    """
    compilers = ["pdflatex", "xelatex"] if compiler == "auto" else [compiler]
    available_compiler = next((c for c in compilers if shutil.which(c)), None)

    if not available_compiler:
        return False, f"LaTeX compiler ({compilers}) not found on system."

    out_path = Path(output_pdf_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_tex = Path(tmp_dir) / "document.tex"
        tmp_tex.write_text(latex_code, encoding="utf-8")

        cmd = [
            available_compiler,
            "-interaction=nonstopmode",
            "-halt-on-error",
            "document.tex",
        ]

        logger.info("compiling latex using %s in %s", available_compiler, tmp_dir)
        try:
            # Run twice for cross-references and header layouts
            res = subprocess.run(cmd, cwd=tmp_dir, capture_output=True, text=True, timeout=60)
            if res.returncode == 0:
                subprocess.run(cmd, cwd=tmp_dir, capture_output=True, text=True, timeout=60)
            else:
                log_snippet = res.stdout[-1000:] if res.stdout else "Compilation failed"
                logger.warning("latex compilation error: %s", log_snippet)
                return False, f"Compilation error: {log_snippet}"

            tmp_pdf = Path(tmp_dir) / "document.pdf"
            if tmp_pdf.exists() and tmp_pdf.stat().st_size > 0:
                shutil.copyfile(tmp_pdf, out_path)
                logger.info(
                    "successfully generated PDF at %s (%d bytes)",
                    out_path,
                    out_path.stat().st_size,
                )
                return True, str(out_path)
            else:
                return False, "PDF was not generated."
        except subprocess.TimeoutExpired:
            return False, "LaTeX compilation timed out after 60s."
        except Exception as e:
            return False, f"LaTeX compilation error: {e}"


def export_paper(
    record: PaperRecord,
    *,
    template: str = "academic",
    citation_style: str = "apa7",
    compile_pdf: bool = True,
    output_dir: str | Path | None = None,
    custom_template: str | None = None,
) -> dict:
    """Export a paper record to LaTeX source (.tex) and optionally compile to PDF."""
    target_dir = (
        Path(output_dir)
        if output_dir
        else Path.home() / ".local" / "share" / "artikel-mcp" / "exports"
    )
    target_dir.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r"\W+", "_", record.title or "paper")[:40].strip("_").lower()
    tex_file = target_dir / f"{safe_name}.tex"
    pdf_file = target_dir / f"{safe_name}.pdf"

    latex_code = render_latex(
        record,
        template_name=template,
        citation_style=citation_style,
        custom_template=custom_template,
    )
    tex_file.write_text(latex_code, encoding="utf-8")

    pdf_success = False
    pdf_msg = "PDF compilation skipped (compile_pdf=False)"
    if compile_pdf:
        pdf_success, pdf_msg = compile_latex_to_pdf(latex_code, pdf_file)

    return {
        "success": True,
        "title": record.title,
        "template": template,
        "citation_style": citation_style,
        "tex_path": str(tex_file),
        "pdf_path": str(pdf_file) if pdf_success else None,
        "pdf_compiled": pdf_success,
        "message": pdf_msg if not pdf_success else f"PDF successfully created at {pdf_file}",
    }
