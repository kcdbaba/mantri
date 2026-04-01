"""
Shared pagination helper for publish scripts.

Generates paginated table rows with data-page attributes and
navigation controls (prev/next + page indicator).
"""

PAGE_SIZE = 10


def paginate_rows(rows: list[str], table_id: str, page_size: int = PAGE_SIZE) -> str:
    """
    Wrap table rows with pagination attributes.

    Each row gets class='prow-{table_id}' and data-page='{n}'.
    Rows beyond the first page are hidden.
    Returns the wrapped rows + pagination controls.
    """
    if len(rows) <= page_size:
        # No pagination needed
        return "".join(rows)

    total_pages = (len(rows) + page_size - 1) // page_size
    paginated = []

    for i, row in enumerate(rows):
        page = i // page_size
        # Add pagination class and page attribute
        # Insert class and data-page into the <tr> tag
        if f"class='" in row:
            row = row.replace("<tr class='", f"<tr class='prow-{table_id} ", 1)
        elif '<tr class="' in row:
            row = row.replace('<tr class="', f'<tr class="prow-{table_id} ', 1)
        elif "<tr>" in row:
            row = row.replace("<tr>", f"<tr class='prow-{table_id}'", 1)
        else:
            row = row.replace("<tr ", f"<tr class='prow-{table_id}' ", 1)

        # Add data-page
        row = row.replace(f"class='prow-{table_id}", f"data-page='{page}' class='prow-{table_id}", 1)
        if f'class="prow-{table_id}' in row:
            row = row.replace(f'class="prow-{table_id}', f'data-page="{page}" class="prow-{table_id}', 1)

        # Hide pages beyond first
        if page > 0:
            row = row.replace(f"data-page='{page}'", f"data-page='{page}' style='display:none'", 1)
            if f'data-page="{page}"' in row:
                row = row.replace(f'data-page="{page}"', f'data-page="{page}" style="display:none"', 1)

        paginated.append(row)

    controls = (
        f"<div class='pagination'>"
        f"<button onclick=\"paginate('{table_id}',-1)\">‹ Prev</button>"
        f"<span id='page-ind-{table_id}'>Page 1 of {total_pages}</span>"
        f"<button onclick=\"paginate('{table_id}',1)\">Next ›</button>"
        f"</div>"
    )

    return "".join(paginated) + controls


def paginate_group_rows(rows: list[str], group_key: str,
                         page_size: int = PAGE_SIZE) -> list[str]:
    """
    Paginate child rows within a collapsible group.
    Uses group_key as the pagination table_id.
    Preserves page state across collapse/expand via pageState JS object.
    """
    if len(rows) <= page_size:
        return rows

    total_pages = (len(rows) + page_size - 1) // page_size
    paginated = []

    for i, row in enumerate(rows):
        page = i // page_size
        # Add data-gpage attribute for group pagination
        if page > 0:
            row = row.replace("style='display:none'", "", 1)  # remove group-child hide
            row = row.replace(f"data-group='{group_key}'",
                              f"data-group='{group_key}' data-gpage='{page}'", 1)
        else:
            row = row.replace(f"data-group='{group_key}'",
                              f"data-group='{group_key}' data-gpage='0'", 1)
        paginated.append(row)

    # Add pagination controls as the last child row
    controls_row = (
        f"<tr class='group-child pagination-row' data-group='{group_key}'>"
        f"<td colspan='20'><div class='pagination'>"
        f"<button onclick=\"paginateGroup('{group_key}',-1)\">‹ Prev</button>"
        f"<span id='gpage-ind-{group_key}'>Page 1 of {total_pages}</span>"
        f"<button onclick=\"paginateGroup('{group_key}',1)\">Next ›</button>"
        f"</div></td></tr>"
    )
    paginated.append(controls_row)

    return paginated


PAGINATION_CSS = """
.pagination { display: flex; align-items: center; gap: 0.5rem;
              justify-content: center; padding: 0.4rem 0; font-size: 0.78rem; }
.pagination button { background: #2d3748; color: #e2e8f0; border: 1px solid #4a5568;
                     border-radius: 3px; padding: 0.2rem 0.6rem; cursor: pointer;
                     font-size: 0.75rem; }
.pagination button:hover { background: #4a5568; }
.pagination span { color: #718096; }
"""

PAGINATION_JS = """
var pageState = pageState || {};
function paginate(tid, dir) {
    if (!pageState[tid]) pageState[tid] = 0;
    var rows = document.querySelectorAll('tr[data-page].prow-' + tid);
    var maxPage = 0;
    for (var i = 0; i < rows.length; i++) {
        var p = parseInt(rows[i].getAttribute('data-page'));
        if (p > maxPage) maxPage = p;
    }
    var newPage = pageState[tid] + dir;
    if (newPage < 0 || newPage > maxPage) return;
    pageState[tid] = newPage;
    for (var i = 0; i < rows.length; i++) {
        var p = parseInt(rows[i].getAttribute('data-page'));
        rows[i].style.display = (p === newPage) ? '' : 'none';
    }
    var ind = document.getElementById('page-ind-' + tid);
    if (ind) ind.textContent = 'Page ' + (newPage + 1) + ' of ' + (maxPage + 1);
}

var gpageState = gpageState || {};
function paginateGroup(gkey, dir) {
    if (!gpageState[gkey]) gpageState[gkey] = 0;
    var rows = document.querySelectorAll('tr.group-child[data-group="' + gkey + '"][data-gpage]');
    var maxPage = 0;
    for (var i = 0; i < rows.length; i++) {
        var p = parseInt(rows[i].getAttribute('data-gpage'));
        if (p > maxPage) maxPage = p;
    }
    var newPage = gpageState[gkey] + dir;
    if (newPage < 0 || newPage > maxPage) return;
    gpageState[gkey] = newPage;
    for (var i = 0; i < rows.length; i++) {
        var cls = rows[i].className;
        if (cls.indexOf('pagination-row') >= 0) continue;
        var p = parseInt(rows[i].getAttribute('data-gpage'));
        rows[i].style.display = (p === newPage) ? '' : 'none';
    }
    var ind = document.getElementById('gpage-ind-' + gkey);
    if (ind) ind.textContent = 'Page ' + (newPage + 1) + ' of ' + (maxPage + 1);
}
"""
