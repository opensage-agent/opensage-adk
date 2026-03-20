import json
from typing import Generator

from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai.types import Content, Part


class MockModelCLI(BaseLlm):
    model: str = "mock-cli"

    @staticmethod
    def supported_models() -> list[str]:
        return ["mock-cli"]

    @staticmethod
    def show_llm_request(llm_request: LlmRequest) -> None:
        print("=== LLM Request ===")
        print(f"Model: {llm_request.model}")
        print("Tools:")
        for tool_name, tool in llm_request.tools_dict.items():
            print(f"- {tool_name}: {tool._get_declaration()}")
        print("Content:")
        for content in llm_request.contents:
            print(f"[{content.role}] {content.parts}")
        print("===================")

    def generate_content(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> Generator[LlmResponse, None, None]:
        self.show_llm_request(llm_request)
        msg_or_tool = input("Send msg or tool (m/t): ").strip().lower()
        if msg_or_tool == "m":
            content = input("Enter your message: ")
            response = LlmResponse(
                content=Content(role="assistant", parts=[Part(text=content)])
            )
            yield response
        elif msg_or_tool == "t":
            tool_name = input("Enter tool name: ")
            args_str = input("Enter tool args (as dict): ")
            args = json.loads(args_str)
            response = LlmResponse(
                content=Content(
                    role="assistant",
                    parts=[
                        Part(text=f"Invoking tool '{tool_name}' with args {args}"),
                        Part.from_function_call(name=tool_name, args=args),
                    ],
                )
            )
            yield response

    async def generate_content_async(self, llm_request, stream=False):
        for response in self.generate_content(llm_request, stream):
            yield response
