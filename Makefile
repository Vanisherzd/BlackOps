.PHONY: bolt-build bolt-up bolt-down bolt-logs bolt-health bolt-wait bolt-shell

BOLT_CONTAINER ?= vllm-bolt

bolt-build:
	deploy/vllm/build-image.sh

bolt-up:
	deploy/vllm/start-bolt.sh

bolt-down:
	deploy/vllm/stop-bolt.sh

bolt-logs:
	deploy/vllm/logs.sh

bolt-health:
	deploy/vllm/healthcheck.sh

bolt-wait:
	deploy/vllm/healthcheck.sh --wait 900

bolt-shell:
	docker exec -it $(BOLT_CONTAINER) bash
