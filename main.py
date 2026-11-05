import asyncio
import json
import sys
from pathlib import Path

import ollama

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agents import (
    query_understanding_agent,
    schema_analysis_agent,
    sql_generation_agent,
    sql_validation_agent,
    database_execution_agent,
    result_explanation_agent
)


BASE_DIR = Path(__file__).resolve().parent
SERVER_FILE = BASE_DIR / "university_mcp_server.py"


def extract_tool_text(result):
    """Extract text returned by an MCP tool."""

    return "\n".join(
        item.text
        for item in result.content
        if hasattr(item, "text")
    )


def print_records(records):
    """Display database records in a readable table."""

    if not records:
        print("\nNo records found.")
        return

    columns = list(records[0].keys())

    widths = {
        column: max(
            len(str(column)),
            max(len(str(row.get(column, ""))) for row in records)
        )
        for column in columns
    }

    header = " | ".join(
        str(column).ljust(widths[column])
        for column in columns
    )

    separator = "-+-".join(
        "-" * widths[column]
        for column in columns
    )

    print("\n" + header)
    print(separator)

    for record in records:
        print(
            " | ".join(
                str(record.get(column, "")).ljust(widths[column])
                for column in columns
            )
        )


async def main():

    print("\n==========================================")
    print("   UNIVERSITY MULTI-AGENT DATABASE SYSTEM")
    print("==========================================")

    # Check Ollama availability.
    try:
        ollama.list()
        print("\nOllama connection: Successful")
    except Exception as error:
        print("\nOllama is not running.")
        print("Start Ollama and try again.")
        print("Error:", error)
        return

    if not SERVER_FILE.exists():
        print("MCP server file not found:", SERVER_FILE)
        return

    server_parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_FILE)],
        cwd=str(BASE_DIR)
    )

    print("\nStarting University MCP Server...")

    async with stdio_client(server_parameters) as (read, write):

        async with ClientSession(read, write) as session:

            await session.initialize()

            print("MCP connection: Successful")

            # Show available MCP tools.
            tools = await session.list_tools()

            print("\nAvailable MCP Tools:")

            for tool in tools.tools:
                print("-", tool.name)

            # Ask the user for a question.
            question = input(
                "\nEnter your university database question: "
            ).strip()

            if not question:
                print("Please enter a valid question.")
                return

            # STEP 1: Query Understanding Agent
            print("\n------------------------------------------")
            print("AGENT 1: QUERY UNDERSTANDING")
            print("------------------------------------------")

            intent = query_understanding_agent(question)

            print(json.dumps(intent, indent=2))

            # Retrieve database schema using MCP.
            print("\n------------------------------------------")
            print("MCP: SCHEMA INSPECTION")
            print("------------------------------------------")

            schema_response = await session.call_tool(
                "inspect_schema",
                {}
            )

            schema_data = json.loads(
                extract_tool_text(schema_response)
            )

            if schema_data.get("status") != "success":
                print("Schema inspection failed:", schema_data)
                return

            schema = schema_data["schema"]

            print(json.dumps(schema, indent=2))

            # STEP 2: Schema Analysis Agent
            print("\n------------------------------------------")
            print("AGENT 2: SCHEMA ANALYSIS")
            print("------------------------------------------")

            schema_analysis = schema_analysis_agent(
                question,
                intent,
                schema
            )

            print(json.dumps(schema_analysis, indent=2))

            # STEP 3: SQL Generation Agent
            print("\n------------------------------------------")
            print("AGENT 3: SQL GENERATION")
            print("------------------------------------------")

            generated_query = sql_generation_agent(
                question,
                intent,
                schema_analysis,
                schema
            )

            sql = generated_query.get("sql", "")
            parameters = generated_query.get("parameters", [])

            print("\nGenerated SQL:")
            print(sql)

            print("\nSQL Parameters:")
            print(json.dumps(parameters, indent=2))

            print("\nSQL Explanation:")
            print(generated_query.get("explanation", ""))

            # STEP 4: SQL Validation Agent
            print("\n------------------------------------------")
            print("AGENT 4: SQL VALIDATION")
            print("------------------------------------------")

            validation = sql_validation_agent(
                generated_query,
                schema
            )

            print(json.dumps(validation, indent=2))

            if not validation["valid"]:
                print("\nQuery rejected. Database execution cancelled.")
                return

            # STEP 5: Database Execution Agent
            print("\n------------------------------------------")
            print("AGENT 5: DATABASE EXECUTION")
            print("------------------------------------------")

            result = await database_execution_agent(
                session,
                sql,
                parameters
            )

            print("\nExecution Status:", result.get("status"))

            if result.get("status") != "success":
                print("Execution Error:", result.get("message"))
                return

            print("Retrieved Records:", result.get("record_count"))

            print_records(result.get("records", []))

            # STEP 6: Result Explanation Agent
            print("\n------------------------------------------")
            print("AGENT 6: RESULT EXPLANATION")
            print("------------------------------------------")

            explanation = result_explanation_agent(
                question,
                result
            )

            print("\n", explanation)

            # MCP REPORT GENERATION
            print("\n------------------------------------------")
            print("MCP: REPORT GENERATION")
            print("------------------------------------------")

            report_response = await session.call_tool(
                "generate_report",
                {
                    "title": "University Database Query Report",
                    "sql": sql,
                    "records_json": json.dumps(
                        result.get("records", []),
                        default=str
                    )
                }
            )

            report_text = extract_tool_text(report_response)

            try:
                report_result = json.loads(report_text)
                print(json.dumps(report_result, indent=2))

                if report_result.get("status") == "success":
                    print("\nHTML report generated successfully.")
                    print("Report location:")
                    print(report_result.get("report_path"))

            except json.JSONDecodeError:
                print(report_text)

            print("\n==========================================")
            print("       MAS EXECUTION COMPLETED")
            print("==========================================")


if __name__ == "__main__":
    asyncio.run(main())