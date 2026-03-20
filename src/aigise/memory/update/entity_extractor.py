"""Entity extraction for the memory system."""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import litellm

if TYPE_CHECKING:
    from opensage.memory.config.domain_config import DomainConfig

from opensage.memory.config.memory_settings import get_memory_settings

logger = logging.getLogger(__name__)


@dataclass
class ExtractedEntity:
    """An entity extracted from content."""

    label: str
    """Neo4j node label (e.g., 'Question', 'Topic', 'Function')."""

    properties: Dict[str, Any]
    """Entity properties to store."""

    embedding: Optional[List[float]] = None
    """Vector embedding if applicable."""

    source_content: Optional[str] = None
    """Original content this entity was extracted from."""

    extraction_confidence: float = 1.0
    """Confidence score for this extraction (0.0 to 1.0)."""


@dataclass
class ExtractionResult:
    """Result of entity extraction."""

    entities: List[ExtractedEntity] = field(default_factory=list)
    """Extracted entities."""

    success: bool = True
    """Whether extraction succeeded."""

    error: Optional[str] = None
    """Error message if extraction failed."""

    metadata: Dict[str, Any] = field(default_factory=dict)
    """Additional extraction metadata."""


async def _generate_embedding(text: str) -> List[float]:
    """Generate embedding for text using configured embedding model."""

    settings = get_memory_settings()
    # Use LiteLLM for embedding generation
    response = await litellm.aembedding(
        model=settings.embedding_model,
        input=text,
    )
    return response.data[0]["embedding"]


def _hash_text(text: str) -> str:
    """Generate SHA256 hash of text for fast lookup."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


class EntityExtractor:
    """Extracts semantic entities from content.

    This class handles:
    - Question/Answer extraction from Q&A pairs
    - Topic extraction using LLM
    - Code entity extraction (Function, Class, File references)
    - Embedding generation for similarity-searchable entities
    """

    def __init__(
        self,
        domain_config: Optional["DomainConfig"] = None,
        use_llm_extraction: bool = True,
        generate_embeddings: bool = True,
    ):
        """Initialize entity extractor.

        Args:
            domain_config (Optional['DomainConfig']): Domain configuration for entity types.
            use_llm_extraction (bool): Whether to use LLM for semantic extraction.
            generate_embeddings (bool): Whether to generate embeddings."""
        self.domain_config = domain_config
        self.use_llm_extraction = use_llm_extraction
        self.generate_embeddings = generate_embeddings

    async def extract(
        self,
        content: str,
        content_type: str = "text",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ExtractionResult:
        """Extract entities from content.

        Args:
            content (str): Content to extract entities from.
            content_type (str): Type of content ('question', 'answer', 'code', 'text').
            metadata (Optional[Dict[str, Any]]): Additional context for extraction.
        Returns:
            ExtractionResult: ExtractionResult with extracted entities.
        """
        # TODO: can we merge some of these extractions to reduce LLM calls? Let them extract together in one call; or constrain and only extract from long context
        try:
            if content_type == "question":
                return await self._extract_from_question(content, metadata)
            elif content_type == "answer":
                return await self._extract_from_answer(content, metadata)
            elif content_type == "qa_pair":
                return await self._extract_from_qa_pair(content, metadata)
            elif content_type == "code":
                return await self._extract_from_code(content, metadata)
            else:
                return await self._extract_from_text(content, metadata)
        except Exception as e:
            logger.error(f"Entity extraction failed: {e}")
            return ExtractionResult(
                success=False,
                error=str(e),
            )

    async def _extract_from_question(
        self, question_text: str, metadata: Optional[Dict[str, Any]] = None
    ) -> ExtractionResult:
        """Extract entities from a question."""
        entities = []
        metadata = metadata or {}

        # Create Question entity
        question_hash = _hash_text(question_text)
        timestamp = datetime.now().isoformat()

        question_props = {
            "text": question_text.strip(),
            "question_hash": question_hash,
            "created_at": timestamp,
            "access_count": 0,
        }

        # Generate embedding if enabled
        embedding = None
        if self.generate_embeddings:
            try:
                embedding = await _generate_embedding(question_text)
                question_props["embedding"] = embedding
            except Exception as e:
                logger.warning(f"Failed to generate embedding: {e}")

        entities.append(
            ExtractedEntity(
                label="Question",
                properties=question_props,
                embedding=embedding,
                source_content=question_text,
            )
        )

        # TODO: _extract_topics and _generate_embedding can be run asyncly
        # Extract topics from question using patterns or LLM
        topics = await self._extract_topics(question_text)
        entities.extend(topics)

        # Extract code references (function names, class names, file paths)
        code_refs = self._extract_code_references(question_text)
        entities.extend(code_refs)

        return ExtractionResult(entities=entities)

    async def _extract_from_answer(
        self, answer_text: str, metadata: Optional[Dict[str, Any]] = None
    ) -> ExtractionResult:
        """Extract entities from an answer."""
        entities = []
        metadata = metadata or {}

        # Create Answer entity
        answer_id = str(uuid.uuid4())
        timestamp = datetime.now().isoformat()

        # Log warnings for missing metadata
        if "answering_agent" not in metadata:
            logger.warning(
                "[EntityExtractor] 'answering_agent' not provided in metadata, "
                f"using 'unknown'. Answer preview: {answer_text[:100]}..."
            )
        if "answering_model" not in metadata:
            logger.warning(
                "[EntityExtractor] 'answering_model' not provided in metadata, "
                f"using 'unknown'. Answer preview: {answer_text[:100]}..."
            )

        answer_props = {
            "text": answer_text.strip(),
            "answer_id": answer_id,
            "answering_agent": metadata.get("answering_agent", "unknown"),
            "answering_model": metadata.get("answering_model", "unknown"),
            "created_at": timestamp,
        }

        entities.append(
            ExtractedEntity(
                label="Answer",
                properties=answer_props,
                source_content=answer_text,
            )
        )

        # Extract topics from answer
        topics = await self._extract_topics(answer_text)
        entities.extend(topics)

        # Extract code references
        code_refs = self._extract_code_references(answer_text)
        entities.extend(code_refs)

        return ExtractionResult(
            entities=entities,
            metadata={"answer_id": answer_id},
        )

    async def _extract_from_qa_pair(
        self, content: str, metadata: Optional[Dict[str, Any]] = None
    ) -> ExtractionResult:
        """Extract entities from a Q&A pair."""
        metadata = metadata or {}

        question = metadata.get("question", "")
        answer = metadata.get("answer", content)

        # Extract from question
        q_result = await self._extract_from_question(question, metadata)

        # Extract from answer
        a_result = await self._extract_from_answer(answer, metadata)

        # Combine entities
        all_entities = q_result.entities + a_result.entities

        return ExtractionResult(
            entities=all_entities,
            metadata={
                "question_hash": _hash_text(question),
                "answer_id": a_result.metadata.get("answer_id"),
            },
        )

    async def _extract_from_code(
        self, code_text: str, metadata: Optional[Dict[str, Any]] = None
    ) -> ExtractionResult:
        """Extract entities from code content."""
        entities = []
        metadata = metadata or {}

        # Extract function definitions
        functions = self._extract_function_definitions(code_text, metadata)
        entities.extend(functions)

        # Extract class definitions
        classes = self._extract_class_definitions(code_text, metadata)
        entities.extend(classes)

        return ExtractionResult(entities=entities)

    async def _extract_from_text(
        self, text: str, metadata: Optional[Dict[str, Any]] = None
    ) -> ExtractionResult:
        """Extract entities from generic text."""
        entities = []
        metadata = metadata or {}

        # Create main Text entity with full content and embedding
        text_hash = _hash_text(text)
        timestamp = datetime.now().isoformat()

        text_props = {
            "text": text.strip(),
            "text_hash": text_hash,
            "created_at": timestamp,
            "source": metadata.get("source", "unknown"),
        }

        # Generate embedding for full text content
        embedding = None
        if self.generate_embeddings:
            try:
                embedding = await _generate_embedding(text)
                text_props["embedding"] = embedding
            except Exception as e:
                logger.warning(f"Failed to generate embedding for text: {e}")

        entities.append(
            ExtractedEntity(
                label="Text",
                properties=text_props,
                embedding=embedding,
                source_content=text,
            )
        )

        # Extract topics for categorization (no embeddings)
        topics = await self._extract_topics(text)
        entities.extend(topics)

        # Extract code references
        code_refs = self._extract_code_references(text)
        entities.extend(code_refs)

        return ExtractionResult(entities=entities)

    async def _extract_topics(self, text: str) -> List[ExtractedEntity]:
        """Extract semantic topics from text.

        Topics are used for categorization only, not for semantic search.
        No embeddings are generated for topics.
        """
        entities = []

        if self.use_llm_extraction:
            try:
                topics = await self._llm_extract_topics(text)
                for topic_name in topics:
                    entities.append(
                        ExtractedEntity(
                            label="Topic",
                            properties={"name": topic_name},
                            embedding=None,  # Topics don't need embeddings
                            extraction_confidence=0.8,
                        )
                    )
            except Exception as e:
                logger.warning(f"LLM topic extraction failed: {e}")

        # Fall back to pattern-based extraction
        if not entities:
            topics = self._pattern_extract_topics(text)
            for topic_name in topics:
                entities.append(
                    ExtractedEntity(
                        label="Topic",
                        properties={"name": topic_name},
                        extraction_confidence=0.6,
                    )
                )

        return entities

    async def _llm_extract_topics(self, text: str) -> List[str]:
        """Use LLM to extract topics from text."""

        prompt = f"""Extract 2-5 key semantic topics from the following text.
Return only the topic names, one per line, no numbering or explanations.
Focus on technical concepts, domains, and subject areas.

Text:
{text[:2000]}

Topics:"""

        try:
            settings = get_memory_settings()
            response = await litellm.acompletion(
                model=settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=1,
                max_tokens=300,
            )
            topics_text = response.choices[0].message.content.strip()
            topics = [t.strip() for t in topics_text.split("\n") if t.strip()]
            return topics[:5]  # Limit to 5 topics
        except Exception as e:
            logger.warning(f"LLM topic extraction failed: {e}")
            return []

    def _pattern_extract_topics(self, text: str) -> List[str]:
        """Extract topics using pattern matching."""
        topics = []

        # Look for common technical patterns
        patterns = [
            r"\b(API|SDK|CLI)\b",
            r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b",  # CamelCase
            r"\b((?:memory|cache|search|graph|node|edge|query|index)\s+\w+)\b",
        ]

        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            topics.extend([m.lower() for m in matches[:3]])

        return list(set(topics))[:5]

    def _extract_code_references(self, text: str) -> List[ExtractedEntity]:
        """Extract code entity references from text."""
        entities = []

        # Function name patterns (snake_case, camelCase)
        func_patterns = [
            r"\b([a-z_][a-z0-9_]*)\(\)",  # function_name()
            r"`([a-z_][a-z0-9_]*)`",  # `function_name`
            r"\b([a-z][a-zA-Z0-9]*)\(",  # camelCase(
        ]

        seen_funcs = set()
        for pattern in func_patterns:
            matches = re.findall(pattern, text)
            for name in matches:
                if name not in seen_funcs and len(name) > 2:
                    seen_funcs.add(name)
                    entities.append(
                        ExtractedEntity(
                            label="Function",
                            properties={"name": name},
                            extraction_confidence=0.7,
                        )
                    )

        # Class name patterns (PascalCase)
        class_pattern = r"\b([A-Z][a-zA-Z0-9]+)(?:\s+class|\(|\.)"
        class_matches = re.findall(class_pattern, text)
        seen_classes = set()
        for name in class_matches:
            if name not in seen_classes and len(name) > 2:
                seen_classes.add(name)
                entities.append(
                    ExtractedEntity(
                        label="Class",
                        properties={"name": name},
                        extraction_confidence=0.7,
                    )
                )

        # File path patterns
        file_pattern = r"[\"'`]?([\w./\\-]+\.(py|js|ts|java|c|cpp|h|go|rs))[\"'`]?"
        file_matches = re.findall(file_pattern, text)
        for match in file_matches[:5]:
            path = match[0] if isinstance(match, tuple) else match
            entities.append(
                ExtractedEntity(
                    label="File",
                    properties={"path": path},
                    extraction_confidence=0.6,
                )
            )

        return entities[:10]  # Limit code references

    def _extract_function_definitions(
        self, code: str, metadata: Dict[str, Any]
    ) -> List[ExtractedEntity]:
        """Extract function definitions from code."""
        entities = []
        file_path = metadata.get("file_path", "")

        # Python function pattern
        py_func_pattern = r"^\s*(?:async\s+)?def\s+(\w+)\s*\((.*?)\):"
        for match in re.finditer(py_func_pattern, code, re.MULTILINE):
            entities.append(
                ExtractedEntity(
                    label="Function",
                    properties={
                        "name": match.group(1),
                        "file_path": file_path,
                        "signature": f"def {match.group(1)}({match.group(2)})",
                        "start_line": code[: match.start()].count("\n") + 1,
                    },
                )
            )

        return entities

    def _extract_class_definitions(
        self, code: str, metadata: Dict[str, Any]
    ) -> List[ExtractedEntity]:
        """Extract class definitions from code."""
        entities = []
        file_path = metadata.get("file_path", "")

        # Python class pattern
        py_class_pattern = r"^\s*class\s+(\w+)\s*(?:\((.*?)\))?:"
        for match in re.finditer(py_class_pattern, code, re.MULTILINE):
            entities.append(
                ExtractedEntity(
                    label="Class",
                    properties={
                        "name": match.group(1),
                        "file_path": file_path,
                        "start_line": code[: match.start()].count("\n") + 1,
                    },
                )
            )

        return entities
