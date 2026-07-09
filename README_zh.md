<p align="center">
  <img src="docs/wiki/assets/logos/opensage-logo.svg" width="20%" alt="OpenSage ADK">
</p>

<h1 align="center">OpenSage-ADK</h1>

<p align="center">
新一代 Agent Development Kit，让 AI 能够自主创建 Agent 拓扑、合成工具集，并管理结构化记忆。
</p>

<p align="center">
  <a href="https://github.com/opensage-agent/opensage-adk/stargazers"><img src="https://img.shields.io/github/stars/opensage-agent/opensage-adk?style=flat-square&color=yellow" alt="Stars"/></a>
  <img src="https://img.shields.io/badge/python-3.12%2B-007ACC?style=flat-square&color=bluebrightgreen" alt="Python 3.12+"/>
  <img src="https://img.shields.io/badge/许可证-Apache_2.0-blue.svg?style=flat-square" alt="License: Apache 2.0">
  <a href="https://discord.gg/zbKe5ue8xc"><img src="https://img.shields.io/badge/💬%20Discord-加入我们-5865F2?style=flat-square" alt="Discord"/></a>
  <!-- <a href="https://www.xiaohongshu.com/user/profile/69bf26c7000000003402ea57"><img src="https://img.shields.io/badge/📕%20RedNote-Follow%20Us-FF2442?style=flat-square" alt="RedNote"/></a> -->
</p>

<p align="center">
  <a href="README.md">English</a> | 中文 •
  <a href="https://opensage-agent.ai/adk.html"><b>官网</b></a> •
  <a href="#rocket-快速开始"><b>快速开始</b></a> •
  <a href="https://docs.adk.opensage-agent.ai/get-started/welcome/"><b>文档</b></a> •
  <a href="https://arxiv.org/abs/2602.16891"><b>论文</b></a>
</p>

---

## :bulb: 特性

OpenSage-ADK 不再要求开发者手工设计 Agent 拓扑、工具列表和记忆结构，而是提供一个最小脚手架，让模型在运行时**创建**并**编排**这些组件。

<table>
  <thead>
    <tr>
      <th>类别</th>
      <th>特性</th>
      <th>说明</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td rowspan="3"><b>🌐 拓扑</b></td>
      <td>AI 创建拓扑</td>
      <td>在任务执行过程中动态创建、执行和终止子 Agent</td>
    </tr>
    <tr>
      <td>Agent 管理</td>
      <td>支持纵向拓扑，将复杂任务拆解为顺序执行的子任务</td>
    </tr>
    <tr>
      <td>Agent ensemble</td>
      <td>支持横向拓扑，让多个并行 Agent 执行并合并结果</td>
    </tr>
    <tr>
      <td rowspan="2"><b>🛠️ 工具</b></td>
      <td>AI 编写工具</td>
      <td>在执行期间动态创建自定义工具和技能</td>
    </tr>
    <tr>
      <td>工具管理</td>
      <td>提供沙箱系统，实现工具隔离执行与状态管理</td>
    </tr>
    <tr>
      <td rowspan="3"><b>🧠 记忆</b></td>
      <td>AI 创建记忆</td>
      <td>基于文件的记忆系统，同时支持长期记忆（跨任务）和短期记忆（单任务）</td>
    </tr>
    <tr>
      <td>图结构组织</td>
      <td>使用层级化记忆结构来组织 Agent 上下文</td>
    </tr>
    <tr>
      <td>AI 驱动管理</td>
      <td>内置专用 memory agent，只需一行代码即可启用</td>
    </tr>
  </tbody>
</table>

## :rocket: 快速开始

### 前置要求

- Python >= 3.12
- [uv](https://docs.astral.sh/uv/getting-started/installation/#installing-uv)（依赖管理工具）
- Docker（用于沙箱执行）

### 安装

```bash
git clone https://github.com/opensage-agent/opensage-adk.git
cd opensage-adk
uv venv --python 3.12
uv sync
```

> [!NOTE]
> 使用 `uv run <command>` 在项目依赖环境中运行命令。使用 `uv add` / `uv remove` 而不是 `pip` 来管理依赖。详见 [uv 文档](https://docs.astral.sh/uv/concepts/projects/run)。

### 创建 Agent

为你的 Agent 创建一个目录，并添加包含 `mk_agent()` 工厂函数的 `agent.py` 文件。

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
> 你需要确保所使用模型的 API key 已经通过环境变量设置，或在初始化模型时直接传入。更多说明见[文档](https://docs.adk.opensage-agent.ai/get-started/quick-start/)。

### 运行 Agent

```bash
# 启动 Web UI
uv run opensage web --agent /path/to/my_agent --port 8000
```

在 [http://localhost:8000](http://localhost:8000) 打开 Web UI，与 Agent 交互，并查看工具调用和会话状态。

## :mag: Agents 示例

仓库提供了 [Agent Library 示例](agent_library/)，涵盖基础 Agent、动态子 Agent、MCP 集成、Tool Combo、Web Search 和 Agent Ensemble。

| 目录 | 说明 |
| --- | --- |
| [`agent_library/agents_101/`](agent_library/agents_101/) | 最小 Agent 模式与入门示例 |
| [`agent_library/agents_with_features/`](agent_library/agents_with_features/) | 按功能分类的示例，如动态子 Agent、Tool Combo 和 Web Search |
| [`agent_library/agents/`](agent_library/agents/) | 更完整的 Agent 示例，覆盖更多工具与技能 |


## :page_facing_up: 引用

如果你在研究中使用了 OpenSage，请考虑引用我们的[论文](https://arxiv.org/abs/2602.16891)。

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

## :memo: 许可证

Apache 2.0 - 参见[许可证](./LICENSE)
