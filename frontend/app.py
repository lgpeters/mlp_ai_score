import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, jsonify, request, send_from_directory

from pipelines.indexing.finlang_embedding import get_model
from shared.db import get_connection
from shared.storage import get_chunk_content

APP_DIR = Path(__file__).resolve().parent
app = Flask(__name__)

SEARCH_SQL = """
    with best_chunk_per_doc as (
        select distinct on (ci.content_id)
            c.content_id, co.name as company_name, c.type, c.sub_type,
            c.publication_date, c.storage_path, ci.chunk_id, ci.start_offset, ci.end_offset,
            (ci.finlang_embedding <=> %(qvec)s::halfvec) as distance,
            cc.company_relevant, cc.ai_associated
        from content_index ci
        join content c using (content_id)
        join companies co using (company_id)
        left join chunk_classifications cc on cc.chunk_id = ci.chunk_id
        where ci.finlang_embedding is not null
          and (%(company)s::text is null or co.name = %(company)s)
          and (%(type)s::text is null or c.type = %(type)s)
          and (%(sub_type)s::text is null or c.sub_type = %(sub_type)s)
          and (%(date_from)s::timestamptz is null or c.publication_date >= %(date_from)s)
          and (%(date_to)s::timestamptz is null or c.publication_date <= %(date_to)s)
        order by ci.content_id, distance asc
    )
    select * from best_chunk_per_doc order by distance asc
"""

# Total documents implied by the filters alone -- independent of whether
# they've been embedded yet, so the UI can show "searched N of M".
FILTER_COUNT_SQL = """
    select count(*)
    from content c
    join companies co using (company_id)
    where (%(company)s::text is null or co.name = %(company)s)
      and (%(type)s::text is null or c.type = %(type)s)
      and (%(sub_type)s::text is null or c.sub_type = %(sub_type)s)
      and (%(date_from)s::timestamptz is null or c.publication_date >= %(date_from)s)
      and (%(date_to)s::timestamptz is null or c.publication_date <= %(date_to)s)
"""


@app.route("/")
def index():
    resp = send_from_directory(APP_DIR, "index.html")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/api/filters")
def filters():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("select name from companies order by name")
            companies = [r[0] for r in cur.fetchall()]
            cur.execute("select distinct type from content order by type")
            types = [r[0] for r in cur.fetchall()]
            cur.execute("select distinct sub_type from content where sub_type is not null order by sub_type")
            sub_types = [r[0] for r in cur.fetchall()]
    return jsonify({"companies": companies, "types": types, "sub_types": sub_types})


@app.route("/api/search", methods=["POST"])
def search():
    body = request.get_json()
    query_vector = get_model().encode(body["query"]).tolist()

    params = {
        "qvec": "[" + ",".join(f"{x:.6f}" for x in query_vector) + "]",
        "company": body.get("company") or None,
        "type": body.get("type") or None,
        "sub_type": body.get("sub_type") or None,
        "date_from": body.get("date_from") or None,
        "date_to": body.get("date_to") or None,
    }

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(FILTER_COUNT_SQL, params)
            total_filtered = cur.fetchone()[0]

            cur.execute(SEARCH_SQL, params)
            columns = [d.name for d in cur.description]
            rows = [dict(zip(columns, row)) for row in cur.fetchall()]

    def serialize(row):
        return {
            "company_name": row["company_name"],
            "type": row["type"],
            "sub_type": row["sub_type"],
            "publication_date": row["publication_date"].isoformat() if row["publication_date"] else None,
            "distance": float(row["distance"]),
            "company_relevant": row["company_relevant"],
            "ai_relevant": (row["ai_associated"] or 0) > 0 if row["ai_associated"] is not None else None,
            "markdown": get_chunk_content(row["content_id"], row["type"], row["storage_path"], row["start_offset"], row["end_offset"]),
        }

    top = [serialize(r) for r in rows[:5]]
    bottom = [serialize(r) for r in reversed(rows[-5:])]

    return jsonify({
        "top": top,
        "bottom": bottom,
        "total_matched": len(rows),
        "total_filtered": total_filtered,
    })


AI_CATEGORY_LABELS = {
    1: "AI Partnership", 2: "Operational Efficiencies", 3: "Restructuring",
    4: "Clear Positive", 5: "Clear Negative", 6: "Product Roadmap",
}
AI_ASSOCIATED_LABELS = {0: "No", 1: "Core AI", 2: "Infra/Compute", 3: "Platform"}

CLASSIFICATIONS_SQL = """
    select cc.chunk_id, ci.content_id, ci.start_offset, ci.end_offset,
           co.name as company_name, c.type, c.sub_type, c.publication_date, c.storage_path,
           cc.company_relevant, cc.ai_associated, cc.ai_category,
           cc.ai_materiality_score, cc.ai_saliency_score, cc.vader_compound, cc.round,
           (cc.ai_category is not null) as llm_read
    from chunk_classifications cc
    join content_index ci using (chunk_id)
    join content c using (content_id)
    join companies co using (company_id)
    where cc.ai_category is not null
      and (%(company)s::text is null or co.name = %(company)s)
      and (%(type)s::text is null or c.type = %(type)s)
      and (%(sub_type)s::text is null or c.sub_type = %(sub_type)s)
      and (%(ai_category)s::int is null or cc.ai_category = %(ai_category)s)
      and (%(min_materiality)s::int is null or cc.ai_materiality_score >= %(min_materiality)s)
      and (%(date_from)s::timestamptz is null or c.publication_date >= %(date_from)s)
      and (%(date_to)s::timestamptz is null or c.publication_date <= %(date_to)s)
    order by {sort_col} {sort_dir}
"""

CLASSIFICATIONS_COUNT_SQL = """
    select count(*), count(*) filter (where cc.ai_category is not null)
    from chunk_classifications cc
    join content_index ci using (chunk_id)
    join content c using (content_id)
    join companies co using (company_id)
    where (%(company)s::text is null or co.name = %(company)s)
      and (%(type)s::text is null or c.type = %(type)s)
      and (%(sub_type)s::text is null or c.sub_type = %(sub_type)s)
      and (%(ai_category)s::int is null or cc.ai_category = %(ai_category)s)
      and (%(min_materiality)s::int is null or cc.ai_materiality_score >= %(min_materiality)s)
      and (%(date_from)s::timestamptz is null or c.publication_date >= %(date_from)s)
      and (%(date_to)s::timestamptz is null or c.publication_date <= %(date_to)s)
"""


@app.route("/api/classifications", methods=["POST"])
def classifications():
    body = request.get_json()
    sort_by = body.get("sort_by") or "ai_saliency_score"
    if sort_by not in ("ai_saliency_score", "ai_materiality_score"):
        sort_by = "ai_saliency_score"

    params = {
        "company": body.get("company") or None,
        "type": body.get("type") or None,
        "sub_type": body.get("sub_type") or None,
        "ai_category": body.get("ai_category") or None,
        "min_materiality": body.get("min_materiality") or None,
        "date_from": body.get("date_from") or None,
        "date_to": body.get("date_to") or None,
    }

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(CLASSIFICATIONS_COUNT_SQL, params)
            total_filtered, total_llm_read = cur.fetchone()

            cur.execute(CLASSIFICATIONS_SQL.format(sort_col=sort_by, sort_dir="desc"), params)
            columns = [d.name for d in cur.description]
            top_rows = [dict(zip(columns, row)) for row in cur.fetchall()][:20]

            cur.execute(CLASSIFICATIONS_SQL.format(sort_col=sort_by, sort_dir="asc"), params)
            bottom_rows = [dict(zip(columns, row)) for row in cur.fetchall()][:20]

    def serialize(row):
        return {
            "company_name": row["company_name"],
            "type": row["type"],
            "sub_type": row["sub_type"],
            "publication_date": row["publication_date"].isoformat() if row["publication_date"] else None,
            "llm_read": row["llm_read"],
            "company_relevant": row["company_relevant"],
            "ai_associated": row["ai_associated"],
            "ai_associated_label": AI_ASSOCIATED_LABELS.get(row["ai_associated"]),
            "ai_category": row["ai_category"],
            "ai_category_label": AI_CATEGORY_LABELS.get(row["ai_category"]),
            "ai_materiality_score": row["ai_materiality_score"],
            "ai_saliency_score": row["ai_saliency_score"],
            "vader_compound": float(row["vader_compound"]) if row["vader_compound"] is not None else None,
            "round": row["round"],
            "content": get_chunk_content(row["content_id"], row["type"], row["storage_path"], row["start_offset"], row["end_offset"]),
        }

    return jsonify({
        "top": [serialize(r) for r in top_rows],
        "bottom": [serialize(r) for r in bottom_rows],
        "total_filtered": total_filtered,
        "total_llm_read": total_llm_read,
        "sort_by": sort_by,
    })


if __name__ == "__main__":
    print("Loading FinLang model...")
    get_model()
    app.run(debug=True, port=5050)
