<!-- category: architecture -->
<!-- last-verified: 2025-07-17 -->

# Cross-Cutting Paths

End-to-end flows that traverse multiple modules. Each entry:

### benchmark-run
- **Trigger:** `Evaluation.run()` → `_generate_one` → `_run_agent`
- **Sequence:** evaluation/base.py:_run_agent → agent_manager.run_turn_stream → orchestration/manager._invoke_instance → _run_invocation_collect → runner.run_async → _release_and_post → _post_invocation
- **Invariants:** `run_until_explicit_finish` loop re-invokes with `continuation_prompt` until `task_finished=True` or LLM budget exhausted. Agent must be spawned before run_turn_stream. Session must exist in session_service.
- **Past breakage:** none yet

### cli-web-chat
- **Trigger:** User sends message via web UI `/run_sse` or `/run` endpoint
- **Sequence:** opensage_web_app.run_agent_sse → runner.run_async (direct, not via agent_manager.run_turn for root) → events streamed back
- **Invariants:** root agent session pre-created via agent_manager.spawn in cli_web. AgentManager started in uvicorn lifespan.
- **Past breakage:** none yet

### agent-invocation-lifecycle
- **Trigger:** `run_turn` / `run_turn_stream` called on agent_manager
- **Sequence:** ensure_loaded → _invoke_instance (check SLEEPING → set RUNNING) → drain inbox → _build_user_content → runner.run_async → _release_and_post → _post_invocation (re-wake if inbox pending, else unload)
- **Invariants:** Instance must be SLEEPING to accept invocation. _done_event cleared on start, set on release. Only one invocation at a time per instance.
- **Past breakage:** none yet

### session-resume
- **Trigger:** `opensage web --resume` / `--resume-from`
- **Sequence:** cli_web → _resolve_saved_session_dir → _resume_environment_async → _attach_sandboxes_from_snapshot_async → _load_adk_session_into_service_async → agent_manager.spawn (adopts existing session)
- **Invariants:** Resume re-uses existing session dirs. Snapshot must have metadata.json. ADK session loaded before spawn.
- **Past breakage:** none yet
