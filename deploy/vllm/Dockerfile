# Model card recipe: https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB (2026-10-16)
ARG BOLT_IMAGE_TAG=nightly-77154ca6116ae34cbfe314beef1016bde9b63989
FROM vllm/vllm-openai:${BOLT_IMAGE_TAG}

RUN pip install b12x && vllm download-kernels

COPY ultra_v3_reasoning_parser.py /opt/bolt/ultra_v3_reasoning_parser.py

ENV VLLM_USE_V2_MODEL_RUNNER=1
