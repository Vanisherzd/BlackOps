# Bolt tool evaluation

Measurement: `LOCAL_MEASUREMENT`

| Metric | Value |
|---|---:|
| tool_selection_accuracy | 1.0 |
| valid_tool_call_rate | 0.9873 |
| invalid_argument_rate | 0.0127 |
| unnecessary_tool_rate | 0.0 |
| completion_rate | 0.963 |
| median_turns | 2 |
| median_latency_ms | 3093.0 |
| salvaged_tool_calls | 0 |

| Category | Passed | Total | Pass rate |
|---|---:|---:|---:|
| tool_selection | 6 | 6 | 1.0 |
| correct_arguments | 3 | 3 | 1.0 |
| unknown_tool_avoidance | 3 | 3 | 1.0 |
| malformed_argument_resistance | 3 | 3 | 1.0 |
| tool_error_handling | 3 | 3 | 1.0 |
| tool_denial_handling | 3 | 3 | 1.0 |
| multi_turn_continuation | 2 | 3 | 0.6667 |
| verification_after_action | 3 | 3 | 1.0 |

## Failed cases

- continue_api_recovery: case did not end with a final answer: max_turns
