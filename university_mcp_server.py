import sqlite3
import json
import html
from pathlib import Path
from mcp.server.fastmcp import FastMCP

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "university.db"
REPORT_DIR = BASE_DIR / "reports"

REPORT_DIR.mkdir(exist_ok=True)

mcp = FastMCP("University Database MCP Server")


def get_connection():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database not found: {DB_PATH}")

    connection = sqlite3.connect(
        f"file:{DB_PATH}?mode=ro",
        uri=True
    )

    connection.row_factory = sqlite3.Row

    def authorize(action, arg1, arg2, database, trigger):
        allowed_actions = {
            sqlite3.SQLITE_SELECT,
            sqlite3.SQLITE_READ,
            sqlite3.SQLITE_FUNCTION,
            sqlite3.SQLITE_RECURSIVE
        }

        if action in allowed_actions:
            return sqlite3.SQLITE_OK

        # Permit only table_info PRAGMA for schema inspection.
        if action == sqlite3.SQLITE_PRAGMA:
            if arg1 and arg1.lower() == "table_info":
                return sqlite3.SQLITE_OK

        return sqlite3.SQLITE_DENY

    connection.set_authorizer(authorize)
    return connection


@mcp.tool()
def inspect_schema():
    """Inspect database tables, columns and relationships."""

    connection = get_connection()

    try:
        tables = connection.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """).fetchall()

        schema = {}

        for table_row in tables:
            table_name = table_row["name"]

            columns = connection.execute(
                f'PRAGMA table_info("{table_name}")'
            ).fetchall()

            schema[table_name] = [
                {
                    "column": column["name"],
                    "type": column["type"],
                    "primary_key": bool(column["pk"])
                }
                for column in columns
            ]

        return json.dumps({
            "status": "success",
            "schema": schema
        }, indent=2)

    finally:
        connection.close()


@mcp.tool()
def execute_readonly_query(sql: str, parameters_json: str = "[]"):
    """Execute an authorized, parameterized, read-only SQL query."""

    sql = sql.strip()

    if not sql:
        return json.dumps({
            "status": "error",
            "message": "SQL query cannot be empty."
        })

    normalized = sql.upper()

    if not (
        normalized.startswith("SELECT ")
        or normalized.startswith("WITH ")
    ):
        return json.dumps({
            "status": "rejected",
            "message": "Only SELECT and WITH queries are permitted."
        })

    # Reject multiple SQL statements.
    if ";" in sql.rstrip(";"):
        return json.dumps({
            "status": "rejected",
            "message": "Multiple SQL statements are not permitted."
        })

    try:
        parameters = json.loads(parameters_json)

        if not isinstance(parameters, list):
            raise ValueError("SQL parameters must be a JSON list.")

        connection = get_connection()

        try:
            cursor = connection.execute(sql, parameters)

            records = [
                dict(row) for row in cursor.fetchall()
            ]

            return json.dumps({
                "status": "success",
                "sql": sql,
                "parameters": parameters,
                "columns": [
                    description[0]
                    for description in cursor.description
                ] if cursor.description else [],
                "record_count": len(records),
                "records": records
            }, indent=2, default=str)

        finally:
            connection.close()

    except Exception as error:
        return json.dumps({
            "status": "error",
            "message": str(error)
        })


@mcp.tool()
def generate_report(
    title: str,
    sql: str,
    records_json: str
):
    """Generate an HTML report containing retrieved records and a chart."""

    try:
        records = json.loads(records_json)

        if not isinstance(records, list):
            raise ValueError("Records must be a JSON list.")

        safe_title = html.escape(title)

        if records:
            columns = list(records[0].keys())

            table_header = "".join(
                f"<th>{html.escape(str(column))}</th>"
                for column in columns
            )

            table_rows = ""

            for record in records:
                cells = "".join(
                    f"<td>{html.escape(str(record.get(column, '')))}</td>"
                    for column in columns
                )

                table_rows += f"<tr>{cells}</tr>"

            table_html = f"""
                <table>
                    <thead><tr>{table_header}</tr></thead>
                    <tbody>{table_rows}</tbody>
                </table>
            """

            # Use the first numeric column for a simple bar chart.
            numeric_column = None

            for column in columns:
                if all(
                    isinstance(record.get(column), (int, float))
                    and not isinstance(record.get(column), bool)
                    for record in records
                ):
                    numeric_column = column
                    break

            chart_html = "<p>No numeric column available for chart generation.</p>"

            if numeric_column:
                values = [
                    float(record[numeric_column])
                    for record in records
                ]

                maximum = max(values) if values else 1
                maximum = maximum if maximum > 0 else 1

                bars = ""

                for index, record in enumerate(records):
                    label = str(
                        record.get(columns[0], f"Record {index + 1}")
                    )

                    value = float(record[numeric_column])
                    width = max(2, (value / maximum) * 100)

                    bars += f"""
                        <div class="bar-row">
                            <div class="bar-label">
                                {html.escape(label)}
                            </div>
                            <div class="bar-container">
                                <div class="bar" style="width:{width:.2f}%"></div>
                            </div>
                            <div class="bar-value">{value:g}</div>
                        </div>
                    """

                chart_html = f"""
                    <h2>Chart: {html.escape(numeric_column)}</h2>
                    {bars}
                """

        else:
            table_html = "<p>No records found.</p>"
            chart_html = "<p>No data available for chart generation.</p>"

        report = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>{safe_title}</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    margin: 35px;
                    color: #222;
                }}
                h1, h2 {{
                    color: #263b65;
                }}
                table {{
                    border-collapse: collapse;
                    width: 100%;
                    margin-top: 20px;
                }}
                th, td {{
                    border: 1px solid #ddd;
                    padding: 10px;
                    text-align: left;
                }}
                th {{
                    background: #e9eef7;
                }}
                tr:nth-child(even) {{
                    background: #f7f7f7;
                }}
                .bar-row {{
                    display: flex;
                    align-items: center;
                    gap: 10px;
                    margin: 12px 0;
                }}
                .bar-label {{
                    width: 150px;
                    overflow-wrap: anywhere;
                }}
                .bar-container {{
                    background: #eee;
                    flex: 1;
                    height: 20px;
                }}
                .bar {{
                    background: #5479b8;
                    height: 20px;
                }}
                .bar-value {{
                    width: 45px;
                }}
            </style>
        </head>
        <body>
            <h1>{safe_title}</h1>
            <h2>SQL Query</h2>
            <pre>{html.escape(sql)}</pre>
            <h2>Retrieved Records</h2>
            <p>Total records: {len(records)}</p>
            {table_html}
            {chart_html}
        </body>
        </html>
        """

        report_path = REPORT_DIR / "university_mas_report.html"
        report_path.write_text(report, encoding="utf-8")

        return json.dumps({
            "status": "success",
            "report_path": str(report_path),
            "record_count": len(records)
        }, indent=2)

    except Exception as error:
        return json.dumps({
            "status": "error",
            "message": str(error)
        })


if __name__ == "__main__":
    mcp.run()