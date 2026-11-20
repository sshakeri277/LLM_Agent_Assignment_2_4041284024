"""Author: plans and writes the personalised draft, then revises it from the Editor's feedback."""
from autogen_agentchat.agents import AssistantAgent
from autogen_core.models import ChatCompletionClient

from ..schemas import StoryDraft
from ._prompts import load_prompt

# How an Author draft reads when it is passed to another agent (the outline stays private to the Author).
DRAFT_FORMAT = ("Title: {title}\nMoral (a separate output field, not part of the story): {moral}\n\n"
                "Story:\n{story}")


def create_author(model_client: ChatCompletionClient, name: str = "Author") -> AssistantAgent:
    return AssistantAgent(
        name=name,
        model_client=model_client,
        description="Writes and revises a personalised Persian children's story.",
        system_message=load_prompt("author"),
        output_content_type=StoryDraft,
        output_content_type_format=DRAFT_FORMAT,
    )
