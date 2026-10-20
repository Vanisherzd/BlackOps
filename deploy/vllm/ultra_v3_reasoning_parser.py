from vllm.reasoning.abs_reasoning_parsers import ReasoningParserManager
from vllm.reasoning.deepseek_r1_reasoning_parser import DeepSeekR1ReasoningParser


@ReasoningParserManager.register_module("ultra_v3")
class UltraV3ReasoningParser(DeepSeekR1ReasoningParser):
    def extract_reasoning(self, model_output, request):
        chat_template_kwargs = getattr(request, "chat_template_kwargs", None) or {}

        if chat_template_kwargs.get("enable_thinking") is False:
            """
            The chat template ends the prompt with `<think></think>`, so the model
            never emits `</think>` and there is no reasoning channel at all. The
            `deepseek_r1` parser this inherits from assumes reasoning always comes
            first, so it would return the whole answer as reasoning content with an
            empty content field. Everything generated is content here, including any
            stray `</think>` the model echoes.
            """
            return None, model_output or None

        reasoning_content, final_content = super().extract_reasoning(
            model_output, request
        )

        if (
            reasoning_content
            and reasoning_content.startswith("\n")
            and self.start_token in model_output
        ):
            """
            The chat template owns the newline in `<think>\n` and re-adds it when it
            replays the turn, so `reasoning_content` must not carry it or the next
            prompt drifts by a token. Live vLLM output never contains the start token
            (the template prefilled it), so this only fires when the parser is handed
            a full assistant span: offline replay, the chat-template verifier, or
            re-parsing training data.
            """
            reasoning_content = reasoning_content[1:]

        if (
            final_content is None
            and chat_template_kwargs.get("force_nonempty_content") is True
        ):
            """
            There are rare cases where the model will output only reasoning without an
            end-think token `</think>` (e.g. reasoning exceeds max length), which
            results in empty content returned. End users may want to unilaterally
            avoid such cases and always have a content response even if the model does
            not finish its reasoning.
            """
            reasoning_content, final_content = None, reasoning_content

        return reasoning_content, final_content
