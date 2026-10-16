# Model card recipe: https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB (2026-10-16)
ARG BOLT_IMAGE_TAG=nightly-77154ca6116ae34cbfe314beef1016bde9b63989
FROM vllm/vllm-openai:${BOLT_IMAGE_TAG}

# The model card says `pip install b12x && vllm download-kernels`. `vllm download-kernels` is
# `pip install flashinfer-jit-cache==0.7.0.post1+cu130` from flashinfer's index (measured on the Spark,
# 2026-10-16; this nightly already ships it, sm121a included), but it probes for a GPU first and no GPU exists
# inside `docker build`, so the two installs are written out explicitly and pinned.
RUN pip install --no-cache-dir --root-user-action=ignore b12x==1.5.1 \
 && pip install --no-cache-dir --root-user-action=ignore \
      --extra-index-url https://flashinfer.ai/whl/cu130 "flashinfer-jit-cache==0.7.0.post1+cu130"

COPY ultra_v3_reasoning_parser.py /opt/bolt/ultra_v3_reasoning_parser.py

ENV VLLM_USE_V2_MODEL_RUNNER=1
