import importlib.util
import pathlib
import types
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "decode-bench.py"
SPEC = importlib.util.spec_from_file_location("decode_bench", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
decode_bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(decode_bench)


class DecodeBenchOutputTest(unittest.TestCase):
    def test_classifies_ok_thinking_only_and_broken_outputs(self):
        # Given representative content, exhausted reasoning, and repeated-token responses.
        responses = [
            (types.SimpleNamespace(
                choices=[types.SimpleNamespace(
                    finish_reason="stop",
                    message=types.SimpleNamespace(content="The service recovered normally.", reasoning_content=None,
                                                  tool_calls=None),
                )],
                usage=types.SimpleNamespace(completion_tokens=6),
            ), "ok"),
            (types.SimpleNamespace(
                choices=[types.SimpleNamespace(
                    finish_reason="length",
                    message=types.SimpleNamespace(content="", reasoning_content="Investigating the outage carefully. " * 50,
                                                  tool_calls=None),
                )],
                usage=types.SimpleNamespace(completion_tokens=300),
            ), "thinking-only"),
            (types.SimpleNamespace(
                choices=[types.SimpleNamespace(
                    finish_reason="stop",
                    message=types.SimpleNamespace(content="loop " * 100, reasoning_content=None, tool_calls=None),
                )],
                usage=types.SimpleNamespace(completion_tokens=100),
            ), "broken"),
        ]

        # When each response is classified, then its semantic output class is preserved.
        for response, expected in responses:
            with self.subTest(expected=expected):
                self.assertEqual(decode_bench.classify_output(response)["output_class"], expected)


if __name__ == "__main__":
    unittest.main()
