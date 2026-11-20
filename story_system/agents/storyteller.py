"""Storyteller: turns the approved story into a read-aloud narration without changing the plot."""
from autogen_agentchat.agents import AssistantAgent
from autogen_core.models import ChatCompletionClient

from ..schemas import Narration
from ._prompts import load_prompt


def create_storyteller(model_client: ChatCompletionClient, name: str = "Storyteller") -> AssistantAgent:
    return AssistantAgent(
        name=name,
        model_client=model_client,
        description="Rewrites the approved story for reading aloud, keeping every event.",
        system_message=load_prompt("storyteller"),
        output_content_type=Narration,
    )
