<p align="center">
  <img src="docs/wiki/assets/logos/opensage-logo.svg" width="20%" alt="OpenSage ADK">
</p>

<h1 align="center">OpenSage-ADK</h1>

<p align="center">
Next generation agent development kit that enables AI to self-create agent topology, synthesize toolsets, and manage structured memory.
</p>

<p align="center">
  <a href="https://github.com/opensage-agent/opensage-adk/stargazers"><img src="https://img.shields.io/github/stars/opensage-agent/opensage-adk?style=flat-square&color=yellow" alt="Stars"/></a>
  <img src="https://img.shields.io/badge/python-3.12%2B-007ACC?style=flat-square&color=bluebrightgreen" alt="Python 3.12+"/>
  <img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg?style=flat-square" alt="License: Apache 2.0">
  <a href="https://discord.gg/zbKe5ue8xc"><img src="https://img.shields.io/badge/💬%20Discord-Join%20Us-5865F2?style=flat-square" alt="Discord"/></a>
  <!-- <a href="https://www.xiaohongshu.com/user/profile/69bf26c7000000003402ea57"><img src="https://img.shields.io/badge/📕%20RedNote-Follow%20Us-FF2442?style=flat-square" alt="RedNote"/></a> -->
</p>

<p align="center">
  English | <a href="README_zh.md">中文</a> •
  <a href="https://opensage-agent.ai/adk.html"><b>Web</b></a> •
  <a href="#rocket-quick-start"><b>Quick Start</b></a> •
  <a href="https://docs.adk.opensage-agent.ai/get-started/welcome/"><b>Docs</b></a> •
  <a href="https://arxiv.org/abs/2602.16891"><b>Paper</b></a>
</p>

---

## :bulb: Highlights

Instead of requiring developers to hand-craft agent topology, tool lists, and memory, OpenSage-ADK provides a minimal scaffold that lets the model **create** and **orchestrate** these components at runtime.

<table>
  <thead>
    <tr>
      <th>Category</th>
      <th>Feature</th>
      <th>Description</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td rowspan="3"><b>🌐 Topology</b></td>
      <td>AI-created topology</td>
      <td>Dynamically create, execute, and terminate sub-agents during task execution</td>
    </tr>
    <tr>
      <td>Agent management</td>
      <td>Support vertical topology by decomposing complex tasks into sequential sub-tasks</td>
    </tr>
    <tr>
      <td>Agent ensemble</td>
      <td>Support horizontal topology where multiple parallel agents execute and merge results</td>
    </tr>
    <tr>
      <td rowspan="2"><b>🛠️ Tool</b></td>
      <td>AI-written tools</td>
      <td>Dynamically create custom tools and skills during execution</td>
    </tr>
    <tr>
      <td>Tool management</td>
      <td>Sandboxing system enabling tool-isolated execution and state management</td>
    </tr>
    <tr>
      <td rowspan="3"><b>🧠 Memory</b></td>
      <td>AI-created memory</td>
      <td>File-based memory for both long-term (cross-task) and short-term (per-task) storage</td>
    </tr>
    <tr>
      <td>Graph-based structure</td>
      <td>Hierarchical memory structure for organizing agent context</td>
    </tr>
    <tr>
      <td>AI-driven management</td>
      <td>Built-in dedicated memory agent that can be enabled with a single line of code</td>
    </tr>
  </tbody>
</table>

## :rocket: Quick Start

### Prerequisites

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/getting-started/installation/#installing-uv) (dependency manager)
- Docker (for sandbox execution)

### Setup

```bash
git clone https://github.com/opensage-agent/opensage-adk.git
cd opensage-adk
uv venv --python 3.12
uv sync
```

> [!NOTE]
> Use `uv run <command>` to run commands with project dependencies. Use `uv add` / `uv remove` instead of `pip` for dependency management. See [uv docs](https://docs.astral.sh/uv/concepts/projects/run) for details.


### Create an Agent

Create a directory for your agent and add an `agent.py` file with a `mk_agent()` factory function.

```
my_agent/
├── agent.py        # Required: defines mk_agent()
├── config.toml     # Optional: sandbox, model, and plugin configuration
└── __init__.py
```

Here's a minimal agent example:

```python
import os
from typing import Optional

from google.adk.models.lite_llm import LiteLlm

import opensage
from opensage.agents import OpenSageAgent


def mk_agent(session_id: str, model=None):
    session = opensage.get_opensage_session(session_id)

    if model is None:
        model = LiteLlm(
            model="YOUR_MODEL_NAME",
            api_key=os.environ.get("YOUR_API_KEY"),
        )

    return OpenSageAgent(
        name="my_agent",
        description="My custom OpenSage agent.",
        model=model,
        instruction="You are a helpful assistant.",
        enabled_skills="all",
        tools=[],
        subagents=[],
    )
```

> [!IMPORTANT]
> You should ensure that an API key for the model you want to use is set in your environment variables or passed directly when initializing the model. For more details, see the [Docs](https://docs.adk.opensage-agent.ai/get-started/quick-start/).

### Run the Agent

```bash
# launch the Web UI
uv run opensage web --agent /path/to/my_agent --port 8000
```
Open the web UI at [http://localhost:8000](http://localhost:8000), chat with the agent, and inspect tool calls and session state from there.

## :mag: Example Agents

The repo includes [agent library examples](agent_library/) from basic agents to dynamic sub-agents, MCP integrations, tool combos, web search, and agent ensembles.

| Directory | Description |
| --- | --- |
| [`agent_library/agents_101/`](agent_library/agents_101/) | Minimal agent patterns and starter examples |
| [`agent_library/agents_with_features/`](agent_library/agents_with_features/) | Feature-focused demos such as dynamic sub-agents, tool combos, and web search |
| [`agent_library/agents/`](agent_library/agents/) | More complete agent examples with various tools and skills |


## :page_facing_up: Citation

If you use OpenSage in your research, please cite our [paper](https://arxiv.org/abs/2602.16891).

```bibtex
@article{li2026opensage,
      title={OpenSage: Self-programming Agent Generation Engine},
      author={Hongwei Li and Zhun Wang and Qinrun Dai and Yuzhou Nie and Jinjun Peng and Ruitong Liu and Jingyang Zhang and Kaijie Zhu and Jingxuan He and Lun Wang and Yangruibo Ding and Yueqi Chen and Wenbo Guo and Dawn Song},
      year={2026},
      eprint={2602.16891},
      archivePrefix={arXiv},
      primaryClass={cs.AI},
      url={https://arxiv.org/abs/2602.16891},
}
```


## :memo: License

Apache 2.0 - See [LICENSE](./LICENSE)
