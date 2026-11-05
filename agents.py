import json
import re
import ollama


MODEL = "llama3.2:latest"


# COMMON LLM FUNCTION

def call_llm(prompt, json_mode=False):
    """Call the local Ollama model, optionally enforcing JSON output."""

    arguments = {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a precise university database assistant. "
                    "Never invent database tables, columns or records."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "options": {
            "temperature": 0
        }
    }

    if json_mode:
        arguments["format"] = "json"

    response = ollama.chat(**arguments)

    return response["message"]["content"].strip()


# JSON EXTRACTION

def extract_json(text):
    """Safely extract JSON returned by Ollama."""

    text = text.strip()

    # Remove Markdown code fences if present.
    text = re.sub(r"^```json\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    # First attempt: parse the complete response.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Second attempt: extract the JSON object.
    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end > start:
        candidate = text[start:end + 1]

        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    raise ValueError(
        "Ollama returned invalid JSON even after JSON mode. "
        "Raw response:\n" + text
    )


# AGENT 1: QUERY UNDERSTANDING AGENT

def query_understanding_agent(question):

    prompt = f"""
Analyze the following university database question.

USER QUESTION:
{question}

Identify:
1. The user's intent.
2. Important entities.
3. Conditions and filters.
4. Expected output.

Return a JSON object with exactly these keys:

{{
    "intent": "description of the request",
    "entities": ["entity1", "entity2"],
    "conditions": ["condition1"],
    "expected_output": "description"
}}

Return valid JSON only.
"""

    return extract_json(call_llm(prompt, json_mode=True))


# AGENT 2: SCHEMA ANALYSIS AGENT

def schema_analysis_agent(question, intent, schema):

    prompt = f"""
You are a database schema analysis agent.

USER QUESTION:
{question}

QUERY UNDERSTANDING:
{json.dumps(intent, ensure_ascii=False)}

ACTUAL DATABASE SCHEMA:
{json.dumps(schema, ensure_ascii=False)}

Analyze the database schema and identify:
1. Relevant tables.
2. Relevant columns.
3. Relationships between tables.
4. Required JOIN conditions.
5. Reasoning behind table selection.

IMPORTANT:
- Use only tables that exist in the provided schema.
- Use only columns that exist in the provided schema.
- Do not invent relationships.

Return a JSON object with exactly these keys:

{{
    "tables": ["table1"],
    "columns": ["column1"],
    "joins": ["join condition"],
    "reasoning": "explanation"
}}

Return valid JSON only.
"""

    return extract_json(call_llm(prompt, json_mode=True))


# AGENT 3: SQL GENERATION AGENT

def sql_generation_agent(question, intent, schema_analysis, schema):

    prompt = f"""
You are an expert SQLite SQL generation agent.

Convert the user's natural-language question into an executable SQLite query.

USER QUESTION:
{question}

QUERY UNDERSTANDING:
{json.dumps(intent, ensure_ascii=False)}

SCHEMA ANALYSIS:
{json.dumps(schema_analysis, ensure_ascii=False)}

ACTUAL DATABASE SCHEMA:
{json.dumps(schema, ensure_ascii=False)}

RULES:
1. Generate only SELECT or WITH queries.
2. Use only existing database tables and columns.
3. Use correct JOIN conditions.
4. Use ? placeholders for values supplied by the user.
5. Put the corresponding values in the parameters array.
6. Never generate INSERT, UPDATE, DELETE, DROP or ALTER.
7. Do not invent database records.
8. Generate SQLite-compatible SQL.
9. Do not put Markdown code fences around SQL.
10. If no parameters are needed, return an empty array.

Return a JSON object with exactly these keys:

{{
    "sql": "SELECT ... WHERE column = ?",
    "parameters": ["value"],
    "explanation": "Explain why this SQL answers the question."
}}

IMPORTANT:
- The SQL must be a JSON string.
- Escape quotation marks correctly.
- Return valid JSON only.
"""

    result = extract_json(
        call_llm(prompt, json_mode=True)
    )

    # Check the response structure.
    if not isinstance(result, dict):
        raise ValueError("SQL generation did not return a JSON object.")

    if "sql" not in result:
        raise ValueError("SQL generation response is missing the SQL field.")

    if "parameters" not in result:
        result["parameters"] = []

    if "explanation" not in result:
        result["explanation"] = "SQL generated from the user's question."

    return result


# AGENT 4: SQL VALIDATION AGENT

def sql_validation_agent(generated_query, schema):

    sql = generated_query.get("sql", "").strip()
    parameters = generated_query.get("parameters", [])

    errors = []

    if not sql:
        errors.append("SQL query is empty.")

    if not isinstance(parameters, list):
        errors.append("SQL parameters must be a list.")
        parameters = []

    normalized = sql.upper()

    # Allow only SELECT and WITH statements.
    if not (
        normalized.startswith("SELECT ")
        or normalized.startswith("WITH ")
    ):
        errors.append("Only SELECT and WITH queries are allowed.")

    # Reject multiple SQL statements.
    if ";" in sql.rstrip(";"):
        errors.append("Multiple SQL statements are not allowed.")

    # Check parameter placeholders.
    if sql.count("?") != len(parameters):
        errors.append(
            "SQL placeholder count does not match parameter count."
        )

    # Reject destructive or administrative operations.
    forbidden_keywords = [
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "CREATE",
        "REPLACE",
        "ATTACH",
        "DETACH",
        "PRAGMA",
        "VACUUM",
        "REINDEX"
    ]

    for keyword in forbidden_keywords:
        pattern = r"\b" + keyword + r"\b"

        if re.search(pattern, normalized):
            errors.append(
                f"Forbidden SQL operation detected: {keyword}"
            )

    if errors:
        return {
            "valid": False,
            "errors": errors
        }

    return {
        "valid": True,
        "errors": [],
        "message": "Basic SQL safety checks passed."
    }


# AGENT 5: DATABASE EXECUTION AGENT

async def database_execution_agent(session, sql, parameters):

    result = await session.call_tool(
        "execute_readonly_query",
        {
            "sql": sql,
            "parameters_json": json.dumps(parameters)
        }
    )

    response_text = "\n".join(
        item.text
        for item in result.content
        if hasattr(item, "text")
    )

    try:
        return json.loads(response_text)

    except json.JSONDecodeError:
        return {
            "status": "error",
            "message": "Could not parse the MCP database response.",
            "raw_response": response_text
        }


# AGENT 6: RESULT EXPLANATION AGENT

def result_explanation_agent(question, result):

    prompt = f"""
You are a university database result explanation agent.

ORIGINAL USER QUESTION:
{question}

ACTUAL DATABASE EXECUTION RESULT:
{json.dumps(result, ensure_ascii=False, default=str)}

Explain:
1. Whether the database query succeeded.
2. The number of records retrieved.
3. Important findings from the returned records.
4. A simple summary for a faculty member.

RULES:
- Use only the records provided.
- Never invent student names or marks.
- Do not claim success if execution failed.
- If zero records were found, clearly state that.
- Keep the explanation concise and understandable.

Provide the explanation in normal text.
"""

    return call_llm(prompt, json_mode=False)