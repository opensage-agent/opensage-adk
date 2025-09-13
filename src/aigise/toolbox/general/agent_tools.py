import os

from aigise.extended_features.neo4j_history_manager import get_neo4j_history_manager
from aigise.llm.lite_llm import LiteLlm
from aigise.llm.llm_request import LlmRequest
from aigise.llm.types import Content, Part
from aigise.toolbox.general.tool_context import ToolContext

FLAG_UNJUSTIFIED_CLAIMS_MODEL = (
    os.getenv("FLAG_UNJUSTIFIED_CLAIMS_MODEL") or "anthropic/claude-sonnet-4-20250514"
)


async def flag_unjustified_claims(tool_context: ToolContext):
    """
    Flag the unjustified claims in the history, this is done by another model

    Returns:
        A natural language analysis of unjustified claims found in the conversation
    """
    # Get model name from environment variable
    model_name = FLAG_UNJUSTIFIED_CLAIMS_MODEL
    if not model_name:
        print("FLAG_UNJUSTIFIED_CLAIMS_MODEL not configured")
        return []

    # Get events from tool context
    events = tool_context._invocation_context.session.events
    if not events:
        print("No events to analyze")
        return []

    # Create LiteLLM model instance
    model = LiteLlm(model=model_name)

    # Prepare events text for analysis
    events_text = []
    for i, event in enumerate(events):
        event_info = f"Event {i} (Author: {event.author}, Time: {event.timestamp}):"
        if event.content and event.content.parts:
            content_parts = []
            for part in event.content.parts:
                if part.text:
                    # Include full text for claim analysis
                    content_parts.append(f"Text: {part.text}")
                elif hasattr(part, "function_call") and part.function_call:
                    content_parts.append(f"Function Call: {part.function_call.name}")
                elif hasattr(part, "function_response") and part.function_response:
                    # Include function responses as they might contain claims
                    response_text = str(part.function_response.response)
                    if len(response_text) > 500:
                        response_text = response_text[:500] + "..."
                    content_parts.append(
                        f"Function Response: {part.function_response.name} -> {response_text}"
                    )

            if content_parts:
                event_info += f"\n  Content: {chr(10).join(content_parts)}"

        events_text.append(event_info)

    # Create the prompt for claim analysis
    prompt = f"""You are analyzing a conversation history to identify unjustified claims. An unjustified claim is a statement that:

1. Makes factual assertions without providing evidence or sources
2. States opinions as if they were facts
3. Makes definitive statements about uncertain or complex topics
4. Claims expertise or authority without backing
5. Makes predictions or guarantees without basis
6. States absolute generalizations without qualification

Here is the conversation history with {len(events)} events:

{chr(10).join(events_text)}

Please analyze each event and identify any unjustified claims. For each claim you identify, please include:
- Which event (by index) contains the claim
- The exact text of the unjustified claim
- Why this claim is unjustified (lack of evidence, stated as fact vs opinion, etc.)
- Who made the claim

Guidelines:
- Focus on factual claims that lack support, not opinions clearly stated as opinions
- Consider the context - some claims might be justified by earlier evidence in the conversation
- Look for hedge words like "might", "could", "seems" - their absence in uncertain topics is a red flag
- Technical claims without citations or evidence are particularly suspect
- Personal experiences and preferences are generally not unjustified claims

If no unjustified claims are found, simply state that no problematic claims were identified."""

    # Create LLM request
    llm_request = LlmRequest()
    llm_request.contents = [
        types.Content(role="user", parts=[types.Part.from_text(prompt)])
    ]

    try:
        # Call the model
        response = None
        async for llm_response in model.generate_content_async(llm_request):
            response = llm_response
            break

        if not response or not response.content:
            print("No response from model")
            return "No response from model"

        # Extract text response
        response_text = ""
        for part in response.content.parts:
            if part.text:
                response_text += part.text

        if not response_text.strip():
            print("Empty response from model")
            return "Empty response from model"

        # Return the model's natural language response directly
        print("Model analysis of unjustified claims:")
        print(response_text)

        return response_text.strip()

    except Exception as e:
        error_msg = f"Error in flag_unjustified_claims: {str(e)}"
        print(error_msg)
        import traceback

        traceback.print_exc()
        return error_msg
