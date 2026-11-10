import os
from abc import ABC, abstractmethod
from typing import Optional
from core.llm_manager import get_llm_manager, LLMProviderManager, LLMProviderException


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
        manager: Optional[LLMProviderManager] = None,
    ):
        self.timeout = timeout
        self.request_type = request_type

        if manager is not None:
            self.manager = manager
        elif api_key or base_url or model:
            self.manager = LLMProviderManager(
                api_key=api_key,
                base_url=base_url,
                model=model,
                default_request_timeout_seconds=self.timeout,
            )
        else:
            self.manager = get_llm_manager()

        self.api_key = self.manager.api_key
        self.base_url = self.manager.base_url
        self.model = self.manager.model

    async def complete(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        return await self.manager.complete(
            prompt=prompt,
            system_prompt=system_prompt,
            request_type=self.request_type,
            deadline_seconds=self.timeout,
        )

    async def complete(self, prompt: str, system_prompt: Optional[str] = None) -> str:
        return await self.manager.complete(
            prompt=prompt,
            system_prompt=system_prompt,
            request_type=self.request_type,
            deadline_seconds=self.timeout,
        )
