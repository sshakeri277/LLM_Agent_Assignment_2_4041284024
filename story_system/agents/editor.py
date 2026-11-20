"""Editor: reviews Author drafts with scores and actionable issues, and checks the Storyteller's version."""
from autogen_agentchat.agents import AssistantAgent
from autogen_core.models import ChatCompletionClient

from ..schemas import EditorReview
from ._prompts import load_prompt


def create_editor(model_client: ChatCompletionClient, name: str = "Editor") -> AssistantAgent:
    return AssistantAgent(
        name=name,
        model_client=model_client,
        description="Strict editor of Persian children's stories: quality control and safety.",
        system_message=load_prompt("editor"),
        output_content_type=EditorReview,
    )
