from __future__ import annotations

import re

from google.adk import Agent
from google.adk.models import BaseLlm
from google.adk.models.lite_llm import LiteLlm


def analyze_code_snippet(code_snippet: str) -> dict:
    """Analyze code and return likely vulnerability signals.

    Args:
        code_snippet: Source code text to inspect.

    Returns:
        A dictionary containing suspected vulnerability types, matched evidence,
        and a coarse risk level.
    """
    patterns = {
        "buffer_overflow": [r"\bstrcpy\s*\(", r"\bgets\s*\(", r"\bsprintf\s*\("],
        "sql_injection": [
            r"SELECT\s+\*\s+FROM\s+.*\+",
            r"execute\s*\(\s*query\s*\+",
            r"format\s*\(.*SELECT",
        ],
        "command_injection": [
            r"\bos\.system\s*\(",
            r"\bsubprocess\.Popen\s*\(",
            r"\beval\s*\(",
        ],
        "xss": [r"innerHTML\s*=", r"document\.write\s*\(", r"dangerouslySetInnerHTML"],
        "path_traversal": [
            r"\.\./",
            r"open\s*\(\s*user_input",
            r"send_file\s*\(\s*request",
        ],
    }

    lowered = code_snippet.lower()
    suspects: list[str] = []
    evidence: list[str] = []

    for vuln_type, vuln_patterns in patterns.items():
        for pattern in vuln_patterns:
            if re.search(pattern, code_snippet, flags=re.IGNORECASE):
                suspects.append(vuln_type)
                evidence.append(pattern)
                break

    if not suspects:
        risk_level = "low"
    elif len(suspects) == 1:
        risk_level = "medium"
    else:
        risk_level = "high"

    return {
        "suspected_vuln_types": suspects,
        "evidence": evidence,
        "risk_level": risk_level,
        "snippet_length": len(lowered),
    }


def check_vulnerability_type(code_snippet: str, expected_vuln_type: str) -> dict:
    """Check whether the expected vulnerability type appears in code.

    Args:
        code_snippet: Source code text to inspect.
        expected_vuln_type: Vulnerability type to validate, such as
            "buffer_overflow" or "sql_injection".

    Returns:
        A dictionary describing whether the expected type matches analysis,
        including detected types and reasoning.
    """
    analysis = analyze_code_snippet(code_snippet)
    detected = analysis.get("suspected_vuln_types", [])
    normalized_expected = expected_vuln_type.strip().lower().replace(" ", "_")
    matched = normalized_expected in detected

    if matched:
        reason = f"Detected expected vulnerability type: {normalized_expected}."
    elif detected:
        reason = f"Expected {normalized_expected}, but detected {', '.join(detected)} instead."
    else:
        reason = f"No obvious vulnerability pattern detected for {normalized_expected}."

    return {
        "matched": matched,
        "expected_vuln_type": normalized_expected,
        "detected_vuln_types": detected,
        "reason": reason,
    }


def summarize_findings(
    findings: str,
    suspected_vuln_type: str = "unknown",
    confidence: float = 0.5,
) -> str:
    """Summarize vulnerability findings in a compact sentence.

    Args:
        findings: Raw analysis findings or observations.
        suspected_vuln_type: Main vulnerability type to highlight.
        confidence: Confidence score between 0.0 and 1.0.

    Returns:
        A concise human-readable summary string.
    """
    bounded_confidence = max(0.0, min(confidence, 1.0))
    return (
        "Potential vulnerability analysis: "
        f"type={suspected_vuln_type.strip().lower().replace(' ', '_')}, "
        f"confidence={bounded_confidence:.2f}. "
        f"Key findings: {findings.strip()}"
    )


def mk_agent(
    opensage_session_id: str | None = None,
    model: BaseLlm | None = None,
):
    """Build a lightweight mock vulnerability analysis agent.

    Args:
        opensage_session_id: Optional session identifier, unused by this mock agent.
        model: Optional ADK-compatible model for RL integration. If not provided,
            a default LiteLlm model is used.

    Returns:
        Configured ADK agent instance.
    """
    _ = opensage_session_id
    selected_model = model if model is not None else LiteLlm(model="openai/o4-mini")

    return Agent(
        name="mock_rl_vulnerability_agent",
        model=selected_model,
        instruction=(
            "You are a security analysis assistant focused on vulnerability triage. "
            "Analyze code snippets, identify likely vulnerability classes, "
            "explain your reasoning, and keep output concise and actionable."
        ),
        description="Mock RL agent for lightweight vulnerability analysis.",
        tools=[
            analyze_code_snippet,
            check_vulnerability_type,
            summarize_findings,
        ],
    )
