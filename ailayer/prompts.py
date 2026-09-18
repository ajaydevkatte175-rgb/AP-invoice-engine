import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from ailayer.errors import PromptNotFoundError


class PromptTemplate(BaseModel):
    """Encapsulates a loaded versioned prompt with frontmatter metadata."""

    name: str
    version: int
    description: str = ""
    model: str | None = None
    temperature: float = 0.0
    max_tokens: int = 4096
    system_prompt: str = ""
    user_prompt_template: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    def render_system(self, **kwargs: Any) -> str:
        """Render system prompt replacing template variables."""
        if not self.system_prompt:
            return ""
        return self._render(self.system_prompt, kwargs)

    def render_user(self, **kwargs: Any) -> str:
        """Render user prompt replacing template variables."""
        if not self.user_prompt_template:
            return ""
        return self._render(self.user_prompt_template, kwargs)

    @staticmethod
    def _render(template_text: str, kwargs: dict[str, Any]) -> str:
        rendered = template_text
        for key, value in kwargs.items():
            # Support both {{ key }} and {key}
            rendered = rendered.replace(f"{{{{ {key} }}}}", str(value))
            rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
            rendered = re.sub(rf"(?<!\{{)\{{{key}\}}(?!\}})", str(value), rendered)
        return rendered


class PromptLoader:
    """Loads and caches versioned markdown prompts containing YAML frontmatter."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir else self._find_prompts_dir()
        self._cache: dict[tuple[str, int], PromptTemplate] = {}

    def _find_prompts_dir(self) -> Path:
        """Find the root prompts directory."""
        if "PROMPTS_DIR" in os.environ:
            p = Path(os.environ["PROMPTS_DIR"])
            if p.exists():
                return p

        current = Path(__file__).resolve().parent
        for _ in range(4):
            candidate = current / "prompts"
            if candidate.exists() and candidate.is_dir():
                return candidate
            current = current.parent

        return Path("prompts")

    def load_prompt(self, name: str, version: int = 1) -> PromptTemplate:
        """Load prompts/{name}/v{version}.md."""
        key = (name, version)
        if key in self._cache:
            return self._cache[key]

        file_path = self.base_dir / name / f"v{version}.md"
        if not file_path.exists():
            raise PromptNotFoundError(
                f"Prompt template '{name}' version {version} not found at {file_path}"
            )

        content = file_path.read_text(encoding="utf-8")
        template = self.parse_markdown_prompt(content, default_name=name, default_version=version)
        self._cache[key] = template
        return template

    @staticmethod
    def parse_markdown_prompt(
        content: str,
        default_name: str = "prompt",
        default_version: int = 1,
    ) -> PromptTemplate:
        """Parse frontmatter and markdown body sections."""
        frontmatter: dict[str, Any] = {}
        body = content

        # Check for YAML frontmatter enclosed in ---
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                raw_yaml = parts[1]
                body = parts[2].strip()
                frontmatter = yaml.safe_load(raw_yaml) or {}

        name = frontmatter.get("name", default_name)
        version = int(frontmatter.get("version", default_version))
        description = frontmatter.get("description", "")
        model = frontmatter.get("model")
        temperature = float(frontmatter.get("temperature", 0.0))
        max_tokens = int(frontmatter.get("max_tokens", 4096))
        system_prompt = frontmatter.get("system", "")

        # Check if body splits into System and User sections
        user_prompt_template = body
        system_match = re.search(
            r"##\s*System(?:\s*Prompt)?\s*\n(.*?)(?=##\s*User(?:\s*Prompt)?|\Z)",
            body,
            re.DOTALL | re.IGNORECASE,
        )
        user_match = re.search(
            r"##\s*User(?:\s*Prompt)?\s*\n(.*)\Z",
            body,
            re.DOTALL | re.IGNORECASE,
        )

        if system_match:
            system_prompt = system_match.group(1).strip()
        if user_match:
            user_prompt_template = user_match.group(1).strip()
        elif system_match:
            # If system match exists but no user header, remainder is empty user
            user_prompt_template = ""

        return PromptTemplate(
            name=name,
            version=version,
            description=description,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
            user_prompt_template=user_prompt_template,
            metadata=frontmatter,
        )
