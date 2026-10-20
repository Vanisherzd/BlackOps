"""Custom action for the tool-input rail (nemo/guardrails-actions/rails.co).

The checked message is JSON: {"action": tool name, "args": {...}, "injected": [lines the output rails removed]}.
A call is refused when an injected line names the same tool (or its plain-language form), or names an argument value
of the call (a pid, a container, a file). The agent may still reach that action on its own evidence, so the refusal
text tells it to justify the action from evidence gathered outside the injected text and escalate otherwise.
"""
import json
import re

from nemoguardrails.actions import action

PLAIN = {"kill_process": r"kill", "cleanup_disk": r"clean ?up (?:the )?disk|free (?:up )?(?:disk )?space|delete (?:the )?logs",
         "clear_scratch_volume": r"scratch", "recreate_stack": r"recreate|rebuild|redeploy", "write_config": r"config",
         "restore_config": r"restore", "network_repair": r"network", "docker_restart": r"restart", "docker_start": r"start"}


@action(name="action_independent_of_injection")
async def action_independent_of_injection(context: dict = None):
    try:
        req = json.loads((context or {}).get("user_message") or "{}")
    except ValueError:
        return True
    injected = " ".join(req.get("injected") or []).lower()
    if not injected:
        return True
    tool = str(req.get("action", ""))
    if tool and (tool.lower() in injected or re.search(PLAIN.get(tool, r"$^"), injected)):
        return False
    for v in (req.get("args") or {}).values():
        v = str(v).lower()
        if len(v) >= 2 and re.search(rf"\b{re.escape(v)}\b", injected):
            return False
    return True
