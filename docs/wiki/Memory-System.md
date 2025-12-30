# Memory System

> **Status**: Graph-structured memory and Memory Agent are planned features and under active development.

## Overview

In SAGE-X, Memory is treated as a manageable system resource rather than passive context. The planned memory system includes layered memory models, graph-structured storage, and an active memory agent.

## Planned Features

### Layered Memory Model

- Persistent long-term memory (cross-session)
- Sub-agent specific short-term memory
- Memory isolation per session

### Graph-Structured Memory

- Nodes represent facts, events, summaries
- Edges represent dependencies, causal relationships, semantic relationships
- Graph queries and retrieval capabilities

### Memory Agent

- Decides when to store information
- Determines when to compress or discard old information
- Manages when to retrieve historical experience
- Active memory management and scheduling

## Current Implementation

SAGE-X currently uses ADK's memory services:
- In-memory memory service (for testing)
- Vertex AI Memory Bank (for production)

## Documentation Status

Detailed documentation will be added as these features are implemented.

## Related Links

- [Introduction](Introduction.md) - Overview of SAGE-X design philosophy
- [Core Concepts](Core-Concepts.md) - Core concepts of the framework
