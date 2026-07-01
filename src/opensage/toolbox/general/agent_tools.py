import asyncio
import logging

from google.adk.models.llm_request import LlmRequest
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from opensage.session import get_opensage_session
from opensage.utils.agent_utils import get_opensage_session_id_from_context

logger = logging.getLogger(__name__)


def _budgeted_model_responses(model, llm_request, opensage_session, tool_context):
    from opensage.llm.budget import generate_content_with_budget

    session_id = getattr(
        getattr(tool_context._invocation_context, "session", None), "id", None
    )
    return generate_content_with_budget(
        model,
        llm_request,
        budget_manager=opensage_session.budget,
        session_id=session_id,
    )


async def complain(complaint: str, tool_context: ToolContext):
    """
    If you have a complaint, you should call this tool to complain about it. E.g., if a tool is hard to use, if a file or folder is supposed to be there but is not, etc. We will take your complaint into consideration and improve the tooling.
    If there is a description that contradicts with the reality, you should call this tool to complain about it.
    Note that the task description is always correct, and there is definitely a way to complete it,you should not complain about it.

    Returns:
        "Complained, we will take your complaint into consideration and improve the tooling."
    """
    return "Complained, we will take your complaint into consideration and improve the tooling."


async def note_suspicious_things(suspicious_things: str, tool_context: ToolContext):
    """
    If you have multiple intereting points or suspicious things to explore, you can call this tool to note them down so that you don't forget them.

    Returns:
        "Noted"
    """
    return "Noted"


async def think(thinking: str, tool_context: ToolContext):
    """
    If you have want to do some reasoning, do not output the reasoning in plain text, call this tool to do the reasoning.

    Returns:
        "Thinking done"
    """
    return "Thinking done"


async def plan(plan: str, tool_context: ToolContext):
    """
    If you have want to do some planning, do not output the plan in plain text, call this tool to do the planning.

    Returns:
        "Planning done"
    """
    return "Planning done"


async def critique(model_name: str, tool_context: ToolContext):
    """Consult another model for critical feedback on the current task progress.

    Sends the conversation history and agent instruction to the specified model,
    which returns an independent analysis of progress, missed steps, unjustified
    claims, and suggested next actions. Use ``get_available_models`` to discover
    valid model names.

    Args:
        model_name: Name of a registered model to use (e.g. "claude-opus-4-8").

    Returns:
        dict with 'idea' containing the consulting model's analysis, or an error.
    """
    try:
        opensage_session_id = get_opensage_session_id_from_context(tool_context)
        opensage_session = get_opensage_session(opensage_session_id)
        model = opensage_session.llms.get(model_name)

        invocation_context = tool_context._invocation_context
        session = invocation_context.session
        current_branch = getattr(invocation_context, "branch", None)

        agent = invocation_context.agent
        agent_instruction = getattr(agent, "instruction", "")

        def _format_event_to_text(event) -> str:
            compaction = getattr(getattr(event, "actions", None), "compaction", None)
            if compaction:
                compacted_content = getattr(compaction, "compacted_content", None)
                if compacted_content and getattr(compacted_content, "parts", None):
                    summary_parts = [
                        part.text
                        for part in compacted_content.parts
                        if getattr(part, "text", None)
                    ]
                    if summary_parts:
                        author = getattr(event, "author", "model")
                        return f"[{author}][Summary]: {' | '.join(summary_parts)}"

            parts_text = []

            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text:
                        parts_text.append(part.text)
                    elif part.function_call:
                        parts_text.append(
                            f"[TOOL_CALL] {part.function_call.name}({part.function_call.args})"
                        )
                    elif part.function_response:
                        parts_text.append(
                            f"[TOOL_RESULT] {part.function_response.name}: {part.function_response.response}"
                        )

            if parts_text:
                return f"[{event.author}]: {' | '.join(parts_text)}"
            return ""

        def _is_branch_match(event) -> bool:
            if not current_branch:
                return True
            event_branch = getattr(event, "branch", None)
            return event_branch is None or event_branch == current_branch

        events = session.events or []
        branch_events = [event for event in events if _is_branch_match(event)]

        processed_events = branch_events
        if branch_events:
            try:
                from google.adk.flows.llm_flows import contents as adk_contents

                processed = adk_contents._process_compaction_events(branch_events)
                if processed:
                    processed_events = processed
            except Exception as exc:
                logger.warning(
                    "Failed to apply compaction summarization for history: %s", exc
                )

        history_text = []
        for event in processed_events:
            formatted = _format_event_to_text(event)
            if formatted:
                history_text.append(formatted)

        context_summary = (
            "\n".join(history_text) if history_text else "No recent history"
        )

        prompt = f"""You are being consulted by another AI agent who is stuck on a task.

**Original Task**: {agent_instruction}

**Recent conversation history**:
{context_summary}

**The agent needs help with**: Understanding what to do next, what might be missing, or alternative approaches.

Please provide:
1. A brief analysis of what the agent has tried so far
2. Suggestions on what the agent should see next
3. Any potential issues or missing steps you notice

You need to be critical and objective, do not sugarcoat the truth, do not be afraid to tell the agent what they are doing wrong.
You should also find all unjustified claims and assumptions and flag them.
There are probably something missing or wrong in the task, you need to find it and tell the agent.
There are probably some context missing, the agent might not have all the information it needs to solve the task, indicate what needs to be added to the context.
Does the agent verify the result of the task carefully, considering all possible cases and edge cases?
Keep your response concise and actionable."""

        llm_request = LlmRequest()
        llm_request.config = types.GenerateContentConfig()
        llm_request.contents = [
            types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
        ]

        idea_parts = []
        async for llm_response in _budgeted_model_responses(
            model, llm_request, opensage_session, tool_context
        ):
            if llm_response.content and llm_response.content.parts:
                for part in llm_response.content.parts:
                    if part.text:
                        idea_parts.append(part.text)

        idea = "".join(idea_parts).strip()

        return {
            "success": True,
            "idea": idea,
            "model_used": model_name,
        }

    except Exception as e:
        logger.exception(f"Failed to get idea from other models: {e}")
        return {
            "success": False,
            "error": f"Failed to get idea from other models: {str(e)}",
        }


async def log_finding(finding: str, tool_context: ToolContext):
    """Log a finding or dead end to the shared findings.md (visible to ALL agents in this session).

    WHEN TO CALL:
    - You ruled out an attack vector or approach (state the assumption that failed)
    - You discovered an exploitation gadget or bypass technique for a step
    - You identified a key step in the attack chain (e.g. "SSRF in /api/fetch
      can reach internal services", "auth bypass via type confusion in login")

    WHEN NOT TO CALL:
    - Routine recon facts like open ports, service versions, credentials (use pinned.md)
    - Status updates or progress reports (use send_message instead)
    - Speculative ideas you haven't verified yet
    - Intermediate command output (just keep working)

    After appending, the tool runs an automatic contradiction check against all
    prior findings. If a contradiction is detected, it is also written to
    findings.md so that other agents see it on their next read.

    Args:
        finding: A concise, self-contained statement. For dead ends, include
            the key assumption, e.g.:
            "DNS rebinding ruled out — CoreDNS is not the only resolver,
            target also accepts resolution via /etc/hosts"

    Returns:
        dict with "logged" (bool) and "contradictions" (str or None).
        If contradictions are found, they describe which findings conflict and why.
    """
    import shlex

    from opensage.memory.file_based.short_term.session_files import (
        MEM_AGENT_DIR_KEY,
        _short_term_root,
    )
    from opensage.utils.agent_utils import get_sandbox_from_context

    try:
        opensage_session_id = get_opensage_session_id_from_context(tool_context)
        opensage_session = get_opensage_session(opensage_session_id)
        sandbox = get_sandbox_from_context(tool_context, "main")

        root_dir = "/shared"
        findings_path = f"{root_dir}/findings.md"

        import datetime

        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        entry = f"\n- [{timestamp}] {finding}\n"
        lock_path = f"{findings_path}.lock"

        # Ensure directory exists
        await sandbox.arun_command_in_container(f"mkdir -p {shlex.quote(root_dir)}")

        # Acquire file lock — serializes all log_finding calls so that
        # dedup and contradiction checks see the latest state.
        lock_cmd = (
            f"exec 9>{shlex.quote(lock_path)} && flock 9 && "
            # --- critical section start ---
            f"cat {shlex.quote(findings_path)} 2>/dev/null"
        )
        all_findings, exit_code = await sandbox.arun_command_in_container(
            f"bash -c {shlex.quote(lock_cmd)}"
        )

        # For the first finding, just append and return
        lines = [
            l.strip() for l in (all_findings or "").strip().split("\n") if l.strip()
        ]
        if len(lines) < 1:
            await sandbox.arun_command_in_container(
                f"bash -c {shlex.quote(f'exec 9>{lock_path} && flock 9 && printf %s {shlex.quote(entry)} >> {shlex.quote(findings_path)}')}"
            )
            return {"logged": True, "contradictions": None}

        # Use a model to check for duplicates, contradictions, completions
        critique_model_name = getattr(
            opensage_session.config.llm, "summarize_model", None
        )
        if critique_model_name:
            from opensage.utils.agent_utils import create_litellm_model

            model = create_litellm_model(critique_model_name)
        else:
            names = opensage_session.llms.list_names()
            if not names:
                await sandbox.arun_command_in_container(
                    f"bash -c {shlex.quote(f'exec 9>{lock_path} && flock 9 && printf %s {shlex.quote(entry)} >> {shlex.quote(findings_path)}')}"
                )
                return {"logged": True, "contradictions": None}
            model = opensage_session.llms.get(names[0])

        prompt = (
            "Below is a list of existing findings, followed by a NEW finding "
            "to be added. Perform three checks:\n\n"
            "1. DUPLICATE: Is the NEW finding substantively the same as any "
            "existing finding? (Same discovery, same evidence, even if worded "
            "differently.) If yes, reply: DUPLICATE_OF: [timestamp]\n\n"
            "2. CONTRADICTION: Does the NEW finding invalidate the ASSUMPTION "
            "behind an earlier DEAD END?\n\n"
            "3. COMPLETION: Does the NEW finding provide a genuinely missing "
            "piece identified in an earlier PARTIAL entry? A finding that merely "
            "confirms or adds evidence to an existing finding is NOT a completion "
            "— that is just consistent. Only flag COMPLETION when a specific gap "
            "from a PARTIAL is now filled.\n\n"
            f"Existing findings:\n{all_findings}\n\n"
            f"NEW finding:\n{finding}\n\n"
            "IMPORTANT: Prior ⚠️ CONTRADICTION/COMPLETION analyses are already "
            "recorded in existing findings. Do NOT repeat them. Only report "
            "NEW interactions involving the NEW finding. Keep each to 2-3 "
            "sentences max. Do not provide general strategy advice.\n\n"
            "Reply with one of:\n"
            "- DUPLICATE_OF: [timestamp] (if duplicate)\n"
            "- Your contradiction/completion analysis (if found)\n"
            "- NO_CONTRADICTIONS (if nothing found)"
        )

        llm_request = LlmRequest()
        llm_request.config = types.GenerateContentConfig()
        llm_request.contents = [
            types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
        ]

        result_parts = []
        async for resp in _budgeted_model_responses(
            model, llm_request, opensage_session, tool_context
        ):
            if resp.content and resp.content.parts:
                for p in resp.content.parts:
                    if p.text:
                        result_parts.append(p.text)

        result_text = "".join(result_parts).strip()

        # Handle duplicate — don't append, don't broadcast
        if "DUPLICATE_OF" in result_text:
            return {"logged": False, "duplicate": True, "reason": result_text}

        # Not a duplicate — append finding (with lock)
        append_cmd = f"exec 9>{lock_path} && flock 9 && printf %s {shlex.quote(entry)} >> {shlex.quote(findings_path)}"

        if "NO_CONTRADICTIONS" in result_text:
            await sandbox.arun_command_in_container(
                f"bash -c {shlex.quote(append_cmd)}"
            )
            contradictions = None
        else:
            # Append finding + contradiction together (atomic under lock)
            contradiction_entry = (
                f"\n- [{timestamp}] \u26a0\ufe0f CONTRADICTION: {result_text}\n"
            )
            combined = entry + contradiction_entry
            combined_cmd = f"exec 9>{lock_path} && flock 9 && printf %s {shlex.quote(combined)} >> {shlex.quote(findings_path)}"
            await sandbox.arun_command_in_container(
                f"bash -c {shlex.quote(combined_cmd)}"
            )
            contradictions = result_text

        # Broadcast the finding to all peer agents so they don't need to
        # poll findings.md.  Use fire-and-forget via the agent manager.
        try:
            manager = opensage_session.agent_manager
            from_sid = tool_context._invocation_context.session.id
            broadcast_msg = f"[log_finding] {finding}"
            if contradictions:
                broadcast_msg += f"\n[contradiction] {contradictions}"
            await manager.send_message(
                from_sid=from_sid, to_sid="*", content=broadcast_msg, kind="text"
            )
        except Exception:
            pass  # best-effort broadcast; don't fail the log

        return {"logged": True, "contradictions": contradictions}

    except Exception as e:
        logger.exception("log_finding failed: %s", e)
        return {"logged": False, "error": str(e)}


async def audit_assumptions(model_name: str, tool_context: ToolContext):
    """Audit the confidence level of all assumptions driving the current work.

    Sends the conversation history to the specified model, which classifies
    every piece of information the agent is relying on into one of three tiers:

    - **Ground truth**: directly observed via tool output (file contents, command
      results, server responses) — no inference involved.
    - **Solid inference**: logically derived from ground truth with clear
      reasoning chain.
    - **Weak assumption**: guessed, assumed by analogy, or based on incomplete
      evidence. These are the most dangerous — if wrong, they silently derail
      the entire approach.

    Use ``get_available_models`` to discover valid model names.

    Args:
        model_name: Name of a registered model to use (e.g. "claude-opus-4-8").

    Returns:
        A structured confidence audit of the agent's current working assumptions.
    """
    opensage_session_id = get_opensage_session_id_from_context(tool_context)
    opensage_session = get_opensage_session(opensage_session_id)
    model = opensage_session.llms.get(model_name)

    invocation_context = tool_context._invocation_context
    session = invocation_context.session
    current_branch = getattr(invocation_context, "branch", None)

    agent = invocation_context.agent
    agent_instruction = getattr(agent, "instruction", "")

    def _is_branch_match(event) -> bool:
        if not current_branch:
            return True
        event_branch = getattr(event, "branch", None)
        return event_branch is None or event_branch == current_branch

    events = session.events or []
    if not events:
        return "No events to analyze."

    branch_events = [e for e in events if _is_branch_match(e)]

    processed_events = branch_events
    if branch_events:
        try:
            from google.adk.flows.llm_flows import contents as adk_contents

            processed = adk_contents._process_compaction_events(branch_events)
            if processed:
                processed_events = processed
        except Exception as exc:
            logger.warning("Failed to apply compaction for audit: %s", exc)

    def _format_event(event) -> str:
        compaction = getattr(getattr(event, "actions", None), "compaction", None)
        if compaction:
            compacted_content = getattr(compaction, "compacted_content", None)
            if compacted_content and getattr(compacted_content, "parts", None):
                parts = [
                    p.text for p in compacted_content.parts if getattr(p, "text", None)
                ]
                if parts:
                    return f"[{event.author}][Summary]: {' | '.join(parts)}"

        parts_text = []
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    parts_text.append(part.text)
                elif part.function_call:
                    parts_text.append(
                        f"[TOOL_CALL] {part.function_call.name}({part.function_call.args})"
                    )
                elif part.function_response:
                    resp = str(part.function_response.response)
                    if len(resp) > 1000:
                        resp = resp[:1000] + "..."
                    parts_text.append(
                        f"[TOOL_RESULT] {part.function_response.name}: {resp}"
                    )
        if parts_text:
            return f"[{event.author}]: {' | '.join(parts_text)}"
        return ""

    history_lines = []
    for event in processed_events:
        line = _format_event(event)
        if line:
            history_lines.append(line)

    context_text = "\n".join(history_lines) if history_lines else "No recent history"

    prompt = f"""You are auditing the confidence level of every assumption and piece of information that an AI agent is currently relying on to drive its work.

**Agent's task**: {agent_instruction}

**Conversation history**:
{context_text}

Classify every piece of information the agent is relying on into exactly one of these three tiers:

### Ground Truth
Directly observed facts — tool output the agent actually saw (file contents, command stdout/stderr, server responses, error messages). No inference involved. These are reliable.

### Solid Inference
Conclusions logically derived from ground truth with a clear, traceable reasoning chain. For each, briefly state the ground truth it rests on and the reasoning step.

### Weak Assumptions
Anything the agent is treating as fact but that is actually:
- Guessed or assumed without verification
- Based on analogy to similar-looking problems
- Inferred from partial or ambiguous evidence
- Assumed from naming conventions, comments, or documentation without confirming in actual behavior
- Carried over from a prior hypothesis that was never validated
- Based on "common sense" about how something "usually works"

For each weak assumption, state:
- What the agent believes
- Why it's weak (what evidence is missing)

### Current Direction Assessment
Based on the audit above, briefly assess:
1. Is the agent's current approach built on solid ground or shaky assumptions?
2. Which weak assumptions should be verified IMMEDIATELY before proceeding?
3. Are there any ground truths the agent observed but seems to have ignored or misinterpreted?

Be ruthlessly honest. The purpose is to catch silent failure modes before they waste time."""

    llm_request = LlmRequest()
    llm_request.config = types.GenerateContentConfig()
    llm_request.contents = [
        types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
    ]

    try:
        idea_parts = []
        async for llm_response in _budgeted_model_responses(
            model, llm_request, opensage_session, tool_context
        ):
            if llm_response.content and llm_response.content.parts:
                for part in llm_response.content.parts:
                    if part.text:
                        idea_parts.append(part.text)

        result = "".join(idea_parts).strip()
        if not result:
            return "Empty response from model."

        return {"success": True, "audit": result, "model_used": model_name}

    except Exception as e:
        logger.exception("Failed to audit assumptions: %s", e)
        return {"success": False, "error": str(e)}


async def validate_claim(model_name: str, claim: str, tool_context: ToolContext):
    """Validate whether a claimed piece of progress is backed by solid evidence.

    Call this BEFORE writing any progress to ``pinned.md``. The tool sends the
    claim together with the conversation history to the specified model, which
    checks whether the claim is supported by ground truth (direct tool output)
    or solid inference, versus being a weak guess or hallucination.

    Use ``get_available_models`` to discover valid model names.

    Args:
        model_name: Name of a registered model to use (e.g. "claude-opus-4-8").
        claim: The specific progress statement you want to validate, e.g.
            "The binary has a stack buffer overflow in parse_input at offset 0x40".

    Returns:
        dict with ``valid`` (bool), ``confidence`` ("ground_truth",
        "solid_inference", or "weak_assumption"), and ``reasoning``.
    """
    opensage_session_id = get_opensage_session_id_from_context(tool_context)
    opensage_session = get_opensage_session(opensage_session_id)
    model = opensage_session.llms.get(model_name)

    invocation_context = tool_context._invocation_context
    session = invocation_context.session
    current_branch = getattr(invocation_context, "branch", None)

    def _is_branch_match(event) -> bool:
        if not current_branch:
            return True
        event_branch = getattr(event, "branch", None)
        return event_branch is None or event_branch == current_branch

    events = session.events or []
    if not events:
        return {
            "valid": False,
            "confidence": "weak_assumption",
            "reasoning": "No conversation history to verify against.",
        }

    branch_events = [e for e in events if _is_branch_match(e)]

    processed_events = branch_events
    if branch_events:
        try:
            from google.adk.flows.llm_flows import contents as adk_contents

            processed = adk_contents._process_compaction_events(branch_events)
            if processed:
                processed_events = processed
        except Exception as exc:
            logger.warning("Failed to apply compaction for validate_claim: %s", exc)

    def _format_event(event) -> str:
        compaction = getattr(getattr(event, "actions", None), "compaction", None)
        if compaction:
            compacted_content = getattr(compaction, "compacted_content", None)
            if compacted_content and getattr(compacted_content, "parts", None):
                parts = [
                    p.text for p in compacted_content.parts if getattr(p, "text", None)
                ]
                if parts:
                    return f"[{event.author}][Summary]: {' | '.join(parts)}"

        parts_text = []
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    parts_text.append(part.text)
                elif part.function_call:
                    parts_text.append(
                        f"[TOOL_CALL] {part.function_call.name}({part.function_call.args})"
                    )
                elif part.function_response:
                    resp = str(part.function_response.response)
                    if len(resp) > 1000:
                        resp = resp[:1000] + "..."
                    parts_text.append(
                        f"[TOOL_RESULT] {part.function_response.name}: {resp}"
                    )
        if parts_text:
            return f"[{event.author}]: {' | '.join(parts_text)}"
        return ""

    history_lines = []
    for event in processed_events:
        line = _format_event(event)
        if line:
            history_lines.append(line)

    context_text = "\n".join(history_lines) if history_lines else "No recent history"

    prompt = f"""You are validating whether a specific claim of progress is backed by evidence in the conversation history.

**Claim to validate:**
{claim}

**Conversation history:**
{context_text}

Classify this claim into exactly ONE of:

1. **ground_truth** — The claim directly restates something observed in tool output (command stdout, file contents, server response, error message). The evidence is verbatim in the history.

2. **solid_inference** — The claim is a logical conclusion from ground truth with a clear, short reasoning chain. State the ground truth and the reasoning step.

3. **weak_assumption** — The claim relies on guessing, analogy, partial evidence, naming conventions, "common sense", or information the agent never actually verified. This includes:
   - Reading text from images without independent verification
   - Assuming behavior without testing
   - Extrapolating from similar-looking problems
   - Treating LLM-generated analysis as observed fact

Respond in EXACTLY this format (three lines, nothing else):

CONFIDENCE: ground_truth|solid_inference|weak_assumption
VALID: true|false
REASONING: one-sentence explanation of what evidence supports or undermines this claim"""

    llm_request = LlmRequest()
    llm_request.config = types.GenerateContentConfig()
    llm_request.contents = [
        types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
    ]

    try:
        resp_parts = []
        async for llm_response in _budgeted_model_responses(
            model, llm_request, opensage_session, tool_context
        ):
            if llm_response.content and llm_response.content.parts:
                for part in llm_response.content.parts:
                    if part.text:
                        resp_parts.append(part.text)

        raw = "".join(resp_parts).strip()
        if not raw:
            return {
                "valid": False,
                "confidence": "weak_assumption",
                "reasoning": "Empty response from model.",
            }

        confidence = "weak_assumption"
        valid = False
        reasoning = raw

        for line in raw.splitlines():
            line = line.strip()
            if line.upper().startswith("CONFIDENCE:"):
                val = line.split(":", 1)[1].strip().lower()
                if val in ("ground_truth", "solid_inference", "weak_assumption"):
                    confidence = val
            elif line.upper().startswith("VALID:"):
                valid = line.split(":", 1)[1].strip().lower() == "true"
            elif line.upper().startswith("REASONING:"):
                reasoning = line.split(":", 1)[1].strip()

        return {
            "valid": valid,
            "confidence": confidence,
            "reasoning": reasoning,
            "model_used": model_name,
        }

    except Exception as e:
        logger.exception("Failed to validate claim: %s", e)
        return {
            "valid": False,
            "confidence": "weak_assumption",
            "reasoning": f"Error: {e}",
        }
