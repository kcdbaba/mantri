"""
Shared pagination helper for publish scripts.

Generates paginated table rows with data-page attributes and
navigation controls above the table.
"""

import re

PAGE_SIZE = 10
UNIT_PAGE_SIZE = 20


def paginate_rows(rows: list[str], table_id: str, page_size: int = PAGE_SIZE) -> tuple[str, str]:
    """
    Add pagination to table rows.

    Returns (controls_html, rows_html).
    - controls_html goes ABOVE the table
    - rows_html replaces the original rows inside <tbody>

    If <= page_size rows, controls_html is empty and rows are unchanged.
    """
    if len(rows) <= page_size:
        return "", "".join(rows)

    total_pages = (len(rows) + page_size - 1) // page_size
    paginated = []

    for i, row in enumerate(rows):
        page = i // page_size
        hide = " style='display:none'" if page > 0 else ""
        # Inject data-page and class into the <tr> tag
        row = re.sub(
            r'<tr(\s|>)',
            f"<tr data-page='{page}' class='prow-{table_id}'{hide}\\1",
            row, count=1,
        )
        paginated.append(row)

    controls = (
        f"<div class='pagination'>"
        f"<button onclick=\"paginate('{table_id}',-1)\">‹ Prev</button>"
        f"<span id='page-ind-{table_id}'>Page 1 of {total_pages}</span>"
        f"<button onclick=\"paginate('{table_id}',1)\">Next ›</button>"
        f"</div>"
    )

    return controls, "".join(paginated)
