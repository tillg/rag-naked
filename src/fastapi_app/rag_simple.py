import logging
import pathlib
from collections.abc import AsyncGenerator
from typing import (
    Any,
)

from openai import AsyncOpenAI
from openai_messages_token_helper import build_messages, get_token_limit

from .api_models import ThoughtStep
from .postgres_searcher import PostgresSearcher


class SimpleRAGChat:
    def __init__(
        self,
        *,
        searcher: PostgresSearcher,
        openai_chat_client: AsyncOpenAI,
        chat_model: str,
        chat_deployment: str | None,  # Not needed for non-Azure OpenAI
    ):
        self.searcher = searcher
        self.openai_chat_client = openai_chat_client
        self.chat_model = chat_model
        self.chat_deployment = chat_deployment
        self.chat_token_limit = get_token_limit(
            chat_model, default_to_minimum=True)
        current_dir = pathlib.Path(__file__).parent
        self.answer_prompt_template = open(
            current_dir / "prompts/answer.txt").read()

    async def run(
        self, messages: list[dict], overrides: dict[str, Any] = {}
    ) -> dict[str, Any] | AsyncGenerator[dict[str, Any], None]:

        logger = logging.getLogger("ragapp")
        logger.info(f"Received messages: {messages}")
        text_search = overrides.get("retrieval_mode") in [
            "text", "hybrid", None]
        vector_search = overrides.get("retrieval_mode") in [
            "vectors", "hybrid", None]
        top = overrides.get("top", 3)

        original_user_query = messages[-1]["content"]
        past_messages = messages[:-1]
        thoughts = []

        # Retrieve relevant items from the database
        results = []
        thoughts.append(
            ThoughtStep(
                title="Search query for database",
                description=original_user_query if text_search else None,
                props={
                    "top": top,
                    "vector_search": vector_search,
                    "text_search": text_search,
                },
            )
        )
        try:
            results = await self.searcher.search_and_embed(
                original_user_query, top=top, enable_vector_search=vector_search, enable_text_search=text_search
            )
            thoughts.append(
                ThoughtStep(
                    title="Search results",
                    description=[result.to_dict() for result in results],
                )
            )
        except Exception as e:
            thoughts.append(
                ThoughtStep(
                    title="Search results",
                    description=f"An error occurred while searching for items: {e}",
                )
            )
            results = []
            chat_resp = {"choices": [{}]}
            chat_resp["choices"][0]["context"] = {
                "thoughts": thoughts

            }
            return chat_resp

        sources_content = [
            f"[{(item.id)}]:{item.to_str_for_rag()}\n\n" for item in results]
        content = "\n".join(sources_content)

        # Generate a contextual and content specific answer using the search results and chat history
        response_token_limit = 1024
        messages = build_messages(
            model=self.chat_model,
            system_prompt=overrides.get(
                "prompt_template") or self.answer_prompt_template,
            new_user_content=original_user_query + "\n\nSources:\n" + content,
            past_messages=past_messages,
            max_tokens=self.chat_token_limit - response_token_limit,
            fallback_to_default=True,
        )
        thoughts.append(
            ThoughtStep(
                title="Prompt to generate answer",
                description=[str(message) for message in messages],
                props=(
                    {"model": self.chat_model,
                     "deployment": self.chat_deployment}
                    if self.chat_deployment
                    else {"model": self.chat_model}
                ),
            )
        )

        try:
            chat_completion_response = await self.openai_chat_client.chat.completions.create(
                # Azure OpenAI takes the deployment name as the model name
                model=self.chat_deployment if self.chat_deployment else self.chat_model,
                messages=messages,
                temperature=overrides.get("temperature", 0.3),
                max_tokens=response_token_limit,
                n=1,
                stream=False,
            )
            chat_resp = chat_completion_response.model_dump()
            chat_resp["choices"][0]["context"] = {
                "data_points": {"text": sources_content},
                "thoughts": thoughts,
            }
            return chat_resp
        except Exception as e:
            chat_resp = {"choices": [{}]}
            chat_resp["choices"][0]["context"] = {
                "data_points": {"text": sources_content},
                "thoughts": thoughts,
            }
            return chat_resp
