# Local model for ChatGPT/Codex smoke validation

## Decision

Use [`mlx-community/Qwen3.5-2B-OptiQ-4bit`](https://huggingface.co/mlx-community/Qwen3.5-2B-OptiQ-4bit) at immutable revision `adc8669eb431e3168aeb4e320bd7b757914350e2`, served by `mlx-optiq` on loopback through the OpenAI Responses API. Select the model's `:no-think` variant for deterministic smoke checks.

This is the smallest current OptiQ candidate with a credible tool-calling margin. The 0.8B artifact is smaller, but its published BFCL-V3 simple score is only 55.5%; the 2B artifact reaches 77.0% while remaining a 1.4 GB language-model artifact. The 4B artifact reaches 90.0%, but doubles the language-model disk footprint to about 3.0 GB and is unnecessary for the deterministic smoke contract proven here.

The exact 2B snapshot completed a text response, emitted a required function call with the correct arguments, consumed the function output, and returned the final answer. Ticket **Select a local smoke-test model** can close with this selection. Do not create durable machine configuration yet; the commands below are an isolated development recipe and input to the eventual implementation plan.

## Candidate comparison

The comparison uses the immutable Hugging Face model cards and configs current during this investigation. All three are dense Qwen3.5 conditional-generation models, predominantly 4-bit with sensitivity-selected 8-bit layers, group size 64, Apache-2.0 licensed, Apple-silicon MLX artifacts, and compatible with `optiq serve`. Their cards advertise an OpenAI-compatible server and native tool-call templates.

| Candidate | Immutable revision | Card disk size | BFCL-V3 simple | Configured context | Decision |
| --- | --- | ---: | ---: | ---: | --- |
| [`Qwen3.5-0.8B-OptiQ-4bit`](https://huggingface.co/mlx-community/Qwen3.5-0.8B-OptiQ-4bit/commit/ef60586933bd2cc02b763f77eb8839a5114bbec1) | `ef60586933bd2cc02b763f77eb8839a5114bbec1` | 0.6 GB | 55.5% | 262,144 tokens | Too little tool-call margin for the primary smoke model |
| [`Qwen3.5-2B-OptiQ-4bit`](https://huggingface.co/mlx-community/Qwen3.5-2B-OptiQ-4bit/commit/adc8669eb431e3168aeb4e320bd7b757914350e2) | `adc8669eb431e3168aeb4e320bd7b757914350e2` | 1.4 GB | 77.0% | 262,144 tokens | **Selected: smallest credible tool-calling candidate** |
| [`Qwen3.5-4B-OptiQ-4bit`](https://huggingface.co/mlx-community/Qwen3.5-4B-OptiQ-4bit/commit/6cb5bdfd0bf15f484881fb9f1ab6d7c840fddde9) | `6cb5bdfd0bf15f484881fb9f1ab6d7c840fddde9` | 3.0 GB | 90.0% | 262,144 tokens | Better fallback if broader agent behavior later exceeds the smoke contract |

The selected snapshot occupies about 2.1 GB when every published sidecar is materialized: about 1.4 GB for `model.safetensors`, 632 MB for the optional vision sidecar, 44 MB for the optional MTP head, and 19 MB for the tokenizer. The text-only server did not enable vision or MTP. The card's 1.4 GB figure describes the language-model artifact rather than the complete repository snapshot.

These are publisher benchmark claims, not independent benchmark reruns. The ticket-specific deterministic tool-call probe is the acceptance evidence for this project. The 262,144-token value is the architecture maximum in each exact config, not a claim that a scant-RAM machine can use that context economically. Use short smoke prompts and the 4-bit KV cache.

## Immutable artifact verification

The selected download resolved to the requested revision and contained 13 files. Hugging Face cache verification checked all 13 files with neither missing nor extra files:

```text
repo_id=mlx-community/Qwen3.5-2B-OptiQ-4bit
repo_type=model
checked=13
revision=adc8669eb431e3168aeb4e320bd7b757914350e2
```

Reproduce the download and verification without a Hugging Face credential:

```sh
MODEL_REPO=mlx-community/Qwen3.5-2B-OptiQ-4bit
MODEL_REV=adc8669eb431e3168aeb4e320bd7b757914350e2

hf download "$MODEL_REPO" --revision "$MODEL_REV"
hf cache verify "$MODEL_REPO" \
  --revision "$MODEL_REV" \
  --fail-on-missing-files \
  --fail-on-extra-files
```

For an air-gapped target, stage this already-verified snapshot through the project's future artifact-transfer procedure. Do not resolve `main` or download from Hugging Face at runtime.

## Runner environment

The successful isolated environment used:

| Component | Exact version or revision |
| --- | --- |
| Python | `3.12.13` |
| `uv` | `0.11.26` |
| `mlx-optiq` | `0.2.15` |
| `mlx-lm` package version | `0.31.3` |
| `mlx-lm` source | [`ab1806e8f5d6aa035973af194a1b9198ab4754dc`](https://github.com/ml-explore/mlx-lm/commit/ab1806e8f5d6aa035973af194a1b9198ab4754dc) |
| `mlx` / `mlx-metal` | `0.32.0` |
| `transformers` | `5.13.1` |
| `huggingface-hub` | `1.23.0` |
| `tokenizers` | `0.22.2` |
| `safetensors` | `0.8.0` |

Released `mlx-lm==0.31.3` crashes at import with `transformers>=5.13.0` because it passes a string where the Transformers registration API now expects a config class. The failure and temporary `<5.13.0` pin are recorded in upstream [issue 1458](https://github.com/ml-explore/mlx-lm/issues/1458) and [issue 1461](https://github.com/ml-explore/mlx-lm/issues/1461). The tested environment instead pins the upstream fix commit above, which removes the incorrect quoting and works with `transformers==5.13.1`.

Create the environment under any disposable local directory:

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python 'mlx-optiq==0.2.15'
uv pip install --python .venv/bin/python \
  'mlx-lm @ git+https://github.com/ml-explore/mlx-lm.git@ab1806e8f5d6aa035973af194a1b9198ab4754dc'
```

`mlx-optiq` had newer releases when this report was written. Keep `0.2.15` for reproduction because that is the version exercised end to end; upgrading the runner is a new compatibility check, not an incidental environment refresh.

## Launch

Resolve the verified local snapshot once, then start a single-concurrency loopback server. The explicit local snapshot path binds inference to the verified revision even if the repository's remote `main` later moves.

```sh
MODEL_REPO=mlx-community/Qwen3.5-2B-OptiQ-4bit
MODEL_REV=adc8669eb431e3168aeb4e320bd7b757914350e2
MODEL_DIR="$(hf download "$MODEL_REPO" --revision "$MODEL_REV")"

.venv/bin/optiq serve \
  --model "$MODEL_DIR" \
  --host 127.0.0.1 \
  --port 18997 \
  --kv-bits 4 \
  --max-concurrent 1 \
  --no-anthropic \
  --responses
```

The server was observed listening only on `127.0.0.1:18997`. Anthropic compatibility was disabled because bundled Codex requires the Responses API. MTP was deliberately left off: speculative decoding is unnecessary for short smoke requests and adds another memory-bearing sidecar and runtime path.

Use the exact loaded model identifier reported by `GET /v1/models`, with the `:no-think` suffix. When the server was started from an immutable local snapshot, that identifier was the snapshot path plus `:no-think`. Using the exact loaded identifier avoids remote revision ambiguity and avoids treating a repository alias as a separately loadable model.

## Live Responses API proof

All requests went directly to the loopback server and used no real credential. The canonical run observed:

| Step | Result | HTTP | Wall time |
| --- | --- | ---: | ---: |
| Deterministic text | Exactly `LOCAL_OK` | 200 | 0.564 s |
| Required tool selection | `function_call` named `add_numbers` with `{"a": 2, "b": 3}` | 200 | 3.267 s, including first model load |
| Tool output continuation | Final answer `5` | 200 | 0.706 s |

An independent repeat also returned `LOCAL_OK`, the same correct function call, and the final text `The result of adding 2 and 3 is 5.` The warm function-call and continuation requests took 0.933 s and 0.617 s. Exact latency is load- and cache-sensitive; the acceptance claim is the response contract, not a throughput target.

A minimal text request is:

```json
{
  "model": "<exact-loaded-model-id>:no-think",
  "input": "Reply with exactly LOCAL_OK and nothing else.",
  "max_output_tokens": 24,
  "temperature": 0
}
```

The required-tool request is:

```json
{
  "model": "<exact-loaded-model-id>:no-think",
  "input": "Use the add_numbers tool to add 2 and 3. You must call the tool.",
  "tools": [
    {
      "type": "function",
      "name": "add_numbers",
      "description": "Add two integers.",
      "parameters": {
        "type": "object",
        "properties": {
          "a": {"type": "integer"},
          "b": {"type": "integer"}
        },
        "required": ["a", "b"],
        "additionalProperties": false
      },
      "strict": true
    }
  ],
  "tool_choice": "required",
  "max_output_tokens": 80,
  "temperature": 0
}
```

Pass the returned `call_id` back in a `function_call_output` item with output `5`, using `previous_response_id` from the function-call response. `optiq` returned the final assistant answer. This proves the two-stage Responses shape needed by a Codex tool loop, not merely that the model can print tool-looking text.

## Memory observations

Immediately after the canonical load and smoke sequence, macOS `footprint` reported:

- current physical footprint: 1,881 MB;
- peak physical footprint: 3,054 MB;
- current `IOAccelerator` allocation: 1,630 MB.

After additional independent repeat probes, the same process reported a 1,963 MB current footprint, 4,730 MB process-lifetime peak, and 1,754 MB current `IOAccelerator` allocation. The later peak included extra model-identifier and cache-path probes, so treat 4.7 GB as the conservative transient allowance for this exact exploratory session rather than a clean-start steady-state requirement.

For a scant-RAM MacBook Pro:

- keep one server process and `--max-concurrent 1`;
- use `--kv-bits 4` and short prompts;
- omit MTP and vision for smoke tests;
- use the exact already-loaded local model identifier rather than asking the router to resolve another alias;
- stop the server when validation is complete; and
- retain the 0.8B artifact only as an emergency footprint fallback, not the default, because its tool-call benchmark margin is materially lower.

## Configure bundled Codex against the server

The exact bundled Codex host already supports a file-configured provider with `wire_api = "responses"` and `requires_openai_auth = false`. In the isolated copy's `CODEX_HOME/config.toml`, write the exact loaded model identifier as the `model` value:

```toml
model = "<exact-local-snapshot-path>:no-think"
model_provider = "local-optiq"
request_max_retries = 1
stream_max_retries = 1

[model_providers.local-optiq]
name = "Local OptiQ smoke model"
base_url = "http://127.0.0.1:18997/v1"
env_key = "LOCAL_OPTIQ_API_KEY"
wire_api = "responses"
requires_openai_auth = false
```

Set `LOCAL_OPTIQ_API_KEY` to a disposable local-only value in the environment that launches the separately named ChatGPT copy. The loopback OptiQ smoke server accepted unauthenticated requests, but the bundled host's custom-provider contract expects its configured `env_key` to exist. This value is not an OpenAI, Hugging Face, or GLM credential.

Provision the config before launching the isolated app and cold-start the app/host after any provider or auth-policy change. The model will use bundled fallback metadata because it is absent from the app's catalog; the smoke test must not interpret that fallback as authoritative context, reasoning, or modality metadata.

Do not put a machine-local absolute model path into committed configuration. The eventual initializer should discover the verified staged snapshot and render that host-local path into the isolated target's config.

## Stop, restart, and cleanup

Stop the foreground server with `Ctrl-C`. If it was deliberately backgrounded, record its PID at launch and send that exact PID `TERM`; do not use a broad process-name kill.

Restart by repeating artifact verification and the launch command against the same immutable snapshot. Confirm `GET http://127.0.0.1:18997/v1/models` before launching the isolated ChatGPT copy.

Cleanup is limited to disposable development state:

1. stop the exact server process;
2. remove the disposable virtual environment and isolated ChatGPT/Codex profile when no longer needed;
3. optionally remove the exact Hugging Face snapshot with `hf cache rm` after checking that no other local work references it; and
4. leave the production `ChatGPT.app`, normal profile, Keychain, app-group state, updater, and machine-wide configuration untouched.

The verified model snapshot should remain available until the implementation plan defines its reproducible air-gap staging and integrity manifest.

## Risks carried forward

- **Runner pin:** the tested `mlx-lm` source is an unreleased commit layered under a released `mlx-optiq` version. Build a lockfile or wheelhouse before air-gap transfer and retest any upgrade.
- **Memory transient:** the process-lifetime peak reached 4.7 GB during exploratory alias/cache probes. A clean isolated-app validation must measure whole-system pressure, not only steady-state server memory.
- **Model quality:** a deterministic arithmetic tool call is enough for plumbing smoke validation, not proof that a 2B model can complete realistic multi-tool coding tasks reliably.
- **Protocol completeness:** direct Responses calls passed, but bundled Codex streaming, cancellation, approvals, parallel tools, compaction, and error recovery still require end-to-end validation.
- **Metadata fallback:** the app does not know this model's real capabilities. Keep prompts short and do not trust fallback limits.
- **Loopback trust:** the tested server does not require authentication on loopback. Keep it bound to `127.0.0.1`, use it only for isolated local development, and stop it after tests.
- **Optional sidecars:** the downloaded snapshot includes vision and MTP files that the smoke path does not need. A future minimal transfer may omit them only after a fresh manifest and load test prove the reduced artifact is sufficient.

## Sources

- [Selected immutable model revision](https://huggingface.co/mlx-community/Qwen3.5-2B-OptiQ-4bit/commit/adc8669eb431e3168aeb4e320bd7b757914350e2)
- [OptiQ model family and candidate sizes](https://mlx-optiq.com/models)
- [`mlx-optiq` package and server overview](https://pypi.org/project/mlx-optiq/)
- [`mlx-lm` Transformers 5.13 import regression](https://github.com/ml-explore/mlx-lm/issues/1458)
- [`mlx-lm` registration-contract report](https://github.com/ml-explore/mlx-lm/issues/1461)
- [Pinned `mlx-lm` fix commit](https://github.com/ml-explore/mlx-lm/commit/ab1806e8f5d6aa035973af194a1b9198ab4754dc)
