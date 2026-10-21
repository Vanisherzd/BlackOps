"""Decode speed of the serving endpoint on agent-like prompts, with an output check.

  .venv/bin/python scripts/decode-bench.py [--url http://127.0.0.1:8000] [--out logs/decode-bench.json]

Three prompts (a tool-calling turn, an incident report, ~9k tokens of logs), temperature 0, thinking on, 700 tokens
max, 3 runs each; reports median wall-clock tokens/s. Speed alone misleads: with NGram speculative decoding the
server reported 4x the baseline rate while every answer had decayed to one word and empty tokens (2026-10-19). So each
answer is also checked: characters of text per generated token, and whether a tool call or content came out at all.
"""
import argparse
import json
import statistics as st
import time

LOGS = "\n".join(
    [f"2026/10/19 08:{i // 60:02d}:{i % 60:02d} [notice] 1#1: signal 17 (SIGCHLD) received from {30 + i}" for i in range(40)]
    + ['2026/10/19 08:41:00 [emerg] 1#1: invalid parameter "listen" in /etc/nginx/conf.d/default.conf:3'])
TOOLS = [{"type": "function", "function": {"name": n, "description": d, "parameters": {"type": "object", "properties": {"name": {"type": "string"}}}}}
         for n, d in [("docker_ps", "State of the containers"), ("docker_logs", "Last lines of a container's logs"),
                      ("docker_restart", "Restart a container"), ("recreate_stack", "Recreate a container from its definition"),
                      ("check_health", "HTTP status of the health URLs")]]
PROMPTS = {
    "tool-turn": [{"role": "system", "content": "You are an on-call SRE agent for a two-container nginx stack (edge-victim front end, edge-victim-app upstream). Diagnose with read-only tools first, then fix with the least invasive action, then verify."},
                  {"role": "user", "content": "Alert: /api/ returns 504, / returns 200."},
                  {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "type": "function", "function": {"name": "docker_logs", "arguments": json.dumps({"name": "edge-victim-app"})}}]},
                  {"role": "tool", "tool_call_id": "1", "content": "[logs of edge-victim-app]\n" + LOGS}],
    "report": [{"role": "user", "content": "Write the incident report for this nginx outage: root cause, the actions taken (docker_start edge-victim-app, then recreate_stack edge-victim-app replace=true), and final health. Logs:\n" + LOGS}],
    "long-context": [{"role": "user", "content": "Here are the logs. Find the one line that explains the outage and say which container it came from and what to do.\n" + (LOGS + "\n") * 5}],
}


def classify_output(response):
    choice = response.choices[0]
    message = choice.message
    content = (message.content or "").strip()
    reasoning = (getattr(message, "reasoning_content", None) or getattr(message, "reasoning", None) or "").strip()
    text = content or reasoning
    chars_per_token = len(text) / max(1, response.usage.completion_tokens)
    words = text.split()
    repeated = len(words) > 1 and len(set(words)) == 1 and len(words[0]) <= 16
    answered = bool(content or getattr(message, "tool_calls", None))
    if answered and chars_per_token >= 1.5 and not repeated:
        output_class = "ok"
    elif choice.finish_reason == "length" and reasoning and chars_per_token >= 2.0 and not repeated:
        output_class = "thinking-only"
    else:
        output_class = "broken"
    return dict(output_class=output_class, chars=len(text))


def main():
    from openai import OpenAI

    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--out")
    a = ap.parse_args()
    c = OpenAI(base_url=f"{a.url}/v1", api_key="none", timeout=600)
    out = {}
    for name, msgs in PROMPTS.items():
        runs = []
        for _ in range(3):
            t0 = time.time()
            r = c.chat.completions.create(model="edge-agent", messages=msgs, tools=TOOLS if name == "tool-turn" else None,
                                          max_tokens=700, temperature=0, extra_body={"chat_template_kwargs": {"enable_thinking": True}})
            check = classify_output(r)
            runs.append(dict(gen=r.usage.completion_tokens, s=time.time() - t0, **check))
        gen, s = st.median(x["gen"] for x in runs), st.median(x["s"] for x in runs)
        cpt = st.median(x["chars"] / max(1, x["gen"]) for x in runs)
        output_classes = [x["output_class"] for x in runs]
        broken = "broken" in output_classes
        out[name] = dict(prompt=r.usage.prompt_tokens, gen=gen, seconds=round(s, 2), tok_s=round(gen / s, 1),
                         chars_per_token=round(cpt, 2), output_broken=broken, output_classes=output_classes)
        print(f"{name:13} prompt {r.usage.prompt_tokens:6}  gen {gen:5}  {s:6.2f}s  {gen / s:6.1f} tok/s  "
              f"{cpt:4.2f} chars/token  runs {'/'.join(output_classes)}"
              f"{'  OUTPUT BROKEN: this speed means nothing' if broken else ''}")
    if a.out:
        json.dump(out, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
