# Board-side Qwen2.5 runtime

This module runs the real Qwen2.5-0.5B-Instruct model on the calling CPU. The
product path performs UTF-8 normalization, BPE tokenization, the complete
Transformer forward pass, greedy sampling and conversation state management on
the FPGA's RISC-V CPU. A PC sends input bytes and receives output bytes. The
Python download, export and validation scripts are build tools only.

## Model and implementation

- Official model: `Qwen/Qwen2.5-0.5B-Instruct`.
- Pinned revision: `7ae557604adf67be50417f59c2c2f167def9a775`.
- Architecture: 24 layers, dimension 896, feed-forward 4864, 14 query heads,
  two key/value heads, head dimension 64, vocabulary 151936.
- Q/K/V biases, split-half RoPE with theta 1e6, grouped-query attention,
  RMSNorm epsilon 1e-6, SiLU-gated MLP, residuals, KV cache and shared embedding /
  output matrix are implemented.
- Weight quantization: symmetric signed Q4, groups of 32 consecutive row
  elements, nearest-even rounding to [-7,7], one FP32 scale per group.
  Activations, KV cache and normalization are FP32.
- The implementation is authored for this project. Mathematical structure and
  tokenizer semantics follow the official Qwen2 implementation. This is not a
  llama.cpp/GGUF loader, a rules engine or a template reply selector.

The current `llm.c` is a **correctness-first software implementation**. It does
not use the existing INT8 DMA accelerator and does not claim fast or interactive
board performance. RV32I has no hardware floating-point instructions; its
soft-float arithmetic and external memory accesses can be extremely slow for a
model of this size. No FPGA token/s measurement has been obtained. A subsequent
performance implementation needs a compatible Q4 matrix engine, memory bursts
and block buffering, validated against this reference.

## API and memory

Use `llm_init_loaded(base, uploaded_bytes, ddr_bytes)` after the loader has
validated the entire transfer. It requires the declared image length to equal
the actual received length before walking any tensor or tokenizer records.
`llm_init(base, ddr_bytes)` is retained for callers that have independently
validated their image. All large work arrays are placed after the image in the
same DDR window, aligned to 64 bytes. There is no heap, file I/O or OS dependency.
The existing low-address BRAM, image models and image buffers are not used as
LLM weight storage.

The produced 512-context image has:

| Allocation | Bytes |
|---|---:|
| Model image including tokenizer | 313700836 |
| Arena, including alignment | 13439004 |
| Total DDR span | 327139840 |
| KV cache alone | 12582912 |

The embedding matrix is never fully expanded to floats. Only the current
token's 896-element row is decoded. A 1 GiB DDR region starting at `0x80000000`
can hold this particular image and arena, subject to the actual hardware being
present and successfully initialized.

`llm_chat(prompt, length, emit, user)` accepts up to 2048 input bytes, constructs
ChatML on the board, and appends to its existing KV history. The default maximum
response is 64 tokens; `llm_set_generation_limit(1..256)` changes it. The caller
may set a lower serial-protocol input limit. Exhausted context returns
`LLM_ERR_CONTEXT` before changing history; call `llm_reset()` for a new chat.
Generation uses deterministic greedy sampling. A generation cap explicitly
finishes the assistant turn. The emit callback returns nonzero to continue;
zero cancels and resets the conversation. Output chunks may end inside a UTF-8
character, so a serial viewer should use incremental decoding. A token cap may
truncate a sentence.

Link dependencies are the correct `rv32i/ilp32` libgcc and libm implementations
of `sqrtf`, `expf`, `powf`, `sinf` and `cosf`. The project supplies any compiler
generated `memcpy` / libm errno glue. No `malloc` or libc stdio is required.
Verify the final disassembly contains only the CPU's implemented ISA.

## Exact tokenizer support

The exporter refuses unsupported BPE model options, AddedToken matching
options, pretokenizer patterns or normalizers. The supported pinned tokenizer
uses NFC plus Qwen2's seven-alternative Unicode pretokenizer and GPT-2 reversible
byte mapping. The board implements canonical decomposition, stable canonical
ordering and composition, including algorithmic Hangul composition. The
normalization tables explicitly use the Python Unicode version recorded in the
export report (15.0.0 in this build). Unassigned codepoints in that version are
rejected instead of silently approximated. Invalid UTF-8 is rejected.

BPE merges use a sorted pair/rank table exported from the official tokenizer.
Added token strings, including ChatML delimiters, are recognized. An input
containing such delimiters therefore has the same tokenization semantics as the
official tokenizer; the serial interface is a local experimental interface.

## Reproduction

Run the following from `advanced/` with a Python environment containing NumPy,
PyTorch, safetensors, tokenizers and regex. The current scripts also look for
isolated dependencies in `multimodal/reports/llm_deps`; they do not require
installing those libraries on the FPGA.

```powershell
.\.venv-training\Scripts\python.exe multimodal/scripts/llm_download.py
.\.venv-training\Scripts\python.exe multimodal/scripts/llm_export.py
.\.venv-training\Scripts\python.exe multimodal/scripts/llm_validate.py
```

The downloader checks the official LFS SHA256 before completing the checkpoint
download. `reports/llm_source/download_manifest.json` records source URLs,
revision, file hashes and upstream LFS hashes. The original checkpoint is
988097824 bytes. Exported images and reports are under `reports/llm_model/`.
The current image SHA256 is
`cca9485dc43c87df25e6d945857c4767ce62b8def32fae5c55bb944108612698`.

The native validation tool compiles the same `llm.c` with strict warnings and
compares it to independent NumPy equations using the exported real weights. It
also compares token IDs to the official Rust tokenizer loaded from the pinned
`tokenizer.json`. Its result is **host validation**, not FPGA execution or a
measurement of board speed.

Actual results in `reports/llm_validation/summary.json`:

- 2022 tokenizer inputs matched exactly, including Chinese, English, emoji,
  canonical combining sequences, Hangul, ChatML and seeded mixed-text cases.
- Six malformed UTF-8, unsupported Unicode and oversized-input cases rejected;
  insufficient token output capacity rejected.
- Two sequential positions of the complete 24-layer model: all 151936 logits
  per position compared; maximum absolute difference below 4.7e-5, with the
  same greedy next-token choice. This verifies quantized C versus quantized
  reference arithmetic, not a quality benchmark against the unquantized model.
- Actual two-turn host C generation: the first prompt produced a Chinese
  introduction (limited to 16 tokens); the second, `一加一等于几？`, produced
  `一加一等于2。`. No response was provided by a lookup table or host service.

## RVQW401 file format

All integers and IEEE binary32 values are little endian; offsets are byte
offsets. Top header size is 512 bytes. Tensor descriptors consist of four
uint32 fields: `offset, rows, columns, type`. Type 1 is Q4, type 2 is FP32.

| Header offset | Meaning |
|---:|---|
| 0 | Eight bytes `RVQW401\0` |
| 8 | Version 1 |
| 12 | Total image byte length |
| 16,20,24,28,32,36 | Dimension, FFN, layers, Q heads, KV heads, vocabulary |
| 40 | Configured context capacity |
| 44,48 | FP32 RoPE theta and RMSNorm epsilon |
| 52 | Layer descriptor table offset |
| 56,60 | Tokenizer blob offset and byte length |
| 128 | Shared embedding tensor descriptor |
| 144 | Final norm tensor descriptor |

Each layer has 12 descriptors: attention norm, FFN norm, Q, K, V, O, gate,
down, up, Q bias, K bias, V bias. Every Q4 block is 20 bytes: a FP32 scale and
16 packed bytes, with even-indexed signed nibbles in the low half and odd-indexed
nibbles in the high half. Norms and biases are FP32; the shared embedding is Q4.

The tokenizer blob uses magic `RVTK401\0`, version 1, a 128-byte header,
vocabulary offset/length records, raw decoded token bytes, 256 byte token IDs,
sorted BPE pair records, Unicode category/combining-class ranges, AddedToken
records, canonical decompositions and canonical compositions. See the exporter
for the exact byte offsets; runtime and exporter explicitly validate the format
version before accepting it. It is not compatible with a renamed GGUF file.

## Remaining physical requirements

Board acceptance requires the actual Pango DDR IP to calibrate, a reliable
model loading path, complete-image integrity verification, and UART tests using
the generated bitstream. At 115200/8N1 this 313700836-byte image alone takes at
least approximately 7.56 hours to transfer, before framing and verification.
DDR loses contents on power loss, so a practical installation should use a
faster initialization channel or persistent model storage. UART remains suitable
for short chat messages. No source or generic RAM simulation proves physical
DDR operation or timing closure.

## Sources and license

- [Official model](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct)
- [Pinned model revision](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct/tree/7ae557604adf67be50417f59c2c2f167def9a775)
- [Official Qwen2 operators](https://github.com/huggingface/transformers/blob/main/src/transformers/models/qwen2/modeling_qwen2.py)
- [Official tokenizer semantics](https://github.com/huggingface/transformers/blob/v4.45.2/src/transformers/models/qwen2/tokenization_qwen2.py)

The model is distributed under Apache-2.0. The downloaded upstream license is
retained as `reports/llm_source/LICENSE` and alongside this implementation as
`LICENSE.Qwen`.
