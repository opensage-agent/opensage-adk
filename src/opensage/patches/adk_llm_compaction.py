from __future__ import annotations


def apply() -> None:
    # Replace google.adk.flows.llm_flows.contents._process_compaction_events
    from google.adk.events.event import Event
    from google.adk.flows.llm_flows import contents as _c

    # idempotent
    if getattr(_c, "_opensage_compaction_patched", False):
        return

    # backup original (optional; useful if later we want incremental behavior)
    if not hasattr(_c, "_process_compaction_events_orig"):
        _c._process_compaction_events_orig = _c._process_compaction_events

    def _replacement(events):
        """Processes events by applying compaction (OpenSage patched).

        Replace each valid compaction window [start, end] with a single summary
        event. Original compaction marker events are not copied to output.
        """
        # 0) Collect valid compaction markers (already branch-filtered upstream)
        markers = []
        for ev in events:
            comp = getattr(getattr(ev, "actions", None), "compaction", None)
            if not comp:
                continue
            try:
                start_ts = getattr(comp, "start_timestamp", None)
                end_ts = getattr(comp, "end_timestamp", None)
                content = getattr(comp, "compacted_content", None)
                # Basic validation
                if start_ts is None or end_ts is None or start_ts >= end_ts:
                    continue
                if not content:
                    continue
                parts = getattr(content, "parts", None)
                if parts is None or (isinstance(parts, list) and len(parts) == 0):
                    continue
                markers.append(
                    {
                        "start": float(start_ts),
                        "end": float(end_ts),
                        "content": content,
                        "branch": getattr(ev, "branch", None),
                        "invocation_id": getattr(ev, "invocation_id", None),
                        "actions": getattr(ev, "actions", None),
                    }
                )
            except Exception:
                # On any malformed compaction payload, skip it
                continue

        # 1) Sort markers by end time (stable)
        markers.sort(key=lambda m: m["end"])

        # 2) Single forward pass to build output with exact (start, end] replacement
        results = []
        i = 0
        n = len(events)

        def _is_compaction_event(e) -> bool:
            return bool(getattr(getattr(e, "actions", None), "compaction", None))

        for marker in markers:
            S = marker["start"]
            E = marker["end"]
            # Append everything with timestamp < S (exclude compaction events)
            while i < n:
                ev = events[i]
                ts = getattr(ev, "timestamp", None)
                if ts is None:
                    # No timestamp - keep it as long as it is logically before S:
                    # to be safe, retain untimestamped events until we cross S
                    if not _is_compaction_event(ev):
                        results.append(ev)
                    i += 1
                    continue
                if ts < S:
                    if not _is_compaction_event(ev):
                        results.append(ev)
                    i += 1
                else:
                    break

            # Create the replacement summary event at time E
            try:
                # Copy actions but clear compaction to avoid re-folding downstream
                actions = marker["actions"]
                try:
                    actions = actions.model_copy()
                    setattr(actions, "compaction", None)
                except Exception:
                    # Fallback: use original actions; better to keep than fail hard
                    pass

                new_event = Event(
                    timestamp=E,
                    author="model",
                    content=marker["content"],
                    branch=marker["branch"],
                    invocation_id=marker["invocation_id"],
                    actions=actions,
                )
                results.append(new_event)
            except Exception:
                # If building summary event fails, do not consume window; fall back to no-op:
                # treat as if this marker did not exist (i.e., do nothing special)
                pass

            # Skip original events within [S, E] (exclude marker events too)
            while i < n:
                ev = events[i]
                ts = getattr(ev, "timestamp", None)
                if ts is None:
                    # Untimestamped events are considered outside window; stop skipping
                    break
                if S <= ts <= E:
                    i += 1
                else:
                    break

        # 3) Append any remaining tail events (excluding compaction marker events)
        while i < n:
            ev = events[i]
            if not _is_compaction_event(ev):
                results.append(ev)
            i += 1

        return results

    _c._process_compaction_events = _replacement
    _c._opensage_compaction_patched = True
