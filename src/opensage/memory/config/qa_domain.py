"""Q&A domain configuration for the memory system.

This defines node types and relationships for question-answer pairs:
- Question, Answer, Topic nodes
- Relationships like ABOUT, HAS_ANSWER, HAS_TOPIC, RELATED_TO
"""

from opensage.memory.config.domain_config import DomainConfig, register_domain
from opensage.memory.schema.node_types import (
    ANSWER_NODE,
    QUESTION_NODE,
    TEXT_NODE,
    TOPIC_NODE,
)
from opensage.memory.schema.relationship_types import (
    ABOUT_RELATIONSHIP,
    HAS_ANSWER_RELATIONSHIP,
    HAS_TOPIC_RELATIONSHIP,
    RELATED_TO_RELATIONSHIP,
)

QA_DOMAIN_CONFIG = DomainConfig(
    name="qa",
    description="Question-answer domain with semantic topics and relationships",
    node_types={
        "Question": QUESTION_NODE,
        "Answer": ANSWER_NODE,
        "Topic": TOPIC_NODE,
        "Text": TEXT_NODE,
    },
    relationships={
        "ABOUT": ABOUT_RELATIONSHIP,
        "HAS_ANSWER": HAS_ANSWER_RELATIONSHIP,
        "HAS_TOPIC": HAS_TOPIC_RELATIONSHIP,
        "RELATED_TO": RELATED_TO_RELATIONSHIP,
    },
    search_strategies=[
        "embedding_search",
        "keyword_search",
        "title_browse",
    ],
    default_strategy="embedding_search",
)

# Register on import
register_domain(QA_DOMAIN_CONFIG)
