import os
from abc import ABC, abstractmethod
from typing import Optional
from core.llm_manager import llm_manager, LLMProviderManager, LLMProviderException


class LLMClient(ABC):
    @abstractmethod
    async def complete(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        """Generate text completion from prompt and optional system prompt."""
        pass


class OpenAILLMClient(LLMClient):
    """Production OpenAI LLM client delegating to centralized LLMProviderManager."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 20.0,
        request_type: str = "general",
    ):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.base_url = (base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")
        self.timeout = timeout
        self.request_type = request_type

        # Use shared connection pool manager if standard config, or dedicated instance if overridden
        if (
            (api_key and api_key != llm_manager.api_key)
            or (base_url and base_url != llm_manager.base_url)
            or (model and model != llm_manager.model)
        ):
            self.manager = LLMProviderManager(
                api_key=self.api_key,
                base_url=self.base_url,
                model=self.model,
                default_request_timeout_seconds=self.timeout,
            )
        else:
            self.manager = llm_manager

    async def complete(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        return await self.manager.complete(
            prompt=prompt,
            system_prompt=system_prompt,
            request_type=self.request_type,
            deadline_seconds=self.timeout,
        )
