# Bolt tool evaluation

Measurement: `LOCAL_MEASUREMENT`

| Metric | Value |
|---|---:|
| tool_selection_accuracy | 0.963 |
| valid_tool_call_rate | 0.9851 |
| invalid_argument_rate | 0.0149 |
| unnecessary_tool_rate | 0.0 |
| completion_rate | 0.8519 |
| median_turns | 2 |
| median_latency_ms | 3049.0 |
| salvaged_tool_calls | 0 |

| Category | Passed | Total | Pass rate |
|---|---:|---:|---:|
| tool_selection | 6 | 6 | 1.0 |
| correct_arguments | 3 | 3 | 1.0 |
| unknown_tool_avoidance | 2 | 3 | 0.6667 |
| malformed_argument_resistance | 3 | 3 | 1.0 |
| tool_error_handling | 3 | 3 | 1.0 |
| tool_denial_handling | 3 | 3 | 1.0 |
| multi_turn_continuation | 1 | 3 | 0.3333 |
| verification_after_action | 2 | 3 | 0.6667 |

## Failed cases

- unsupported_rotate_cert: case did not end with a final answer: max_turns
- continue_api_down: case did not end with a final answer: max_turns
- continue_api_recovery: case did not end with a final answer: max_turns
- verify_fix_api: restart_container was not followed by a read tool; case did not end with a final answer: max_turns
