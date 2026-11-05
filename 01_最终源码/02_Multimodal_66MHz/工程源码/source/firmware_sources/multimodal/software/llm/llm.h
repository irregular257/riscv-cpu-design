#ifndef MULTIMODAL_LLM_H
#define MULTIMODAL_LLM_H
#include <stddef.h>
#include <stdint.h>

/* Qwen2.5-0.5B, all tokenization and inference on the calling CPU.
 * DDR contains the exported RVQW401 image followed by a writable arena.
 * The API is single-threaded; image and chat execution must be serialized.
 * No malloc, operating system, file access, host inference or host tokenizer.
 */
#define LLM_MAX_PROMPT_BYTES 2048u
#define LLM_DEFAULT_GENERATION_TOKENS 64u
enum {
    LLM_OK = 0,
    LLM_ERR_IMAGE = -1,
    LLM_ERR_MEMORY = -2,
    LLM_ERR_NOT_READY = -3,
    LLM_ERR_INPUT = -4,
    LLM_ERR_CONTEXT = -5,
    LLM_ERR_TOKEN = -6,
    LLM_ERR_CANCELLED = -7,
    LLM_ERR_NUMERIC = -8
};
/* Return nonzero to continue. Bytes may split a UTF-8 character across calls. */
typedef int (*llm_emit_fn)(const uint8_t *bytes, size_t length, void *user);
typedef struct {
    uint32_t ready, image_bytes, arena_bytes, context_tokens, used_tokens;
    uint32_t last_prompt_tokens, last_generated_tokens;
} llm_info_t;

int llm_init(void *ddr_base, size_t ddr_bytes);
/* Loader entry: also require the exact received image length to match header.
 * Call after verifying transport CRC; ddr_bytes includes writable arena space. */
int llm_init_loaded(void *ddr_base, size_t uploaded_bytes, size_t ddr_bytes);
int llm_chat(const uint8_t *prompt_utf8, size_t length, llm_emit_fn emit, void *user);
void llm_reset(void);
void llm_get_info(llm_info_t *info);
/* Optional bounded output length; valid values 1..256, default 64. */
int llm_set_generation_limit(unsigned tokens);
/* Exposed for exact tokenizer tests and diagnostics; does not advance KV state. */
int llm_tokenize(const uint8_t *utf8, size_t length, uint32_t *tokens, size_t capacity);

#ifdef LLM_TEST_API
const float *llm_test_forward(uint32_t token, uint32_t position);
uint32_t llm_test_vocab_size(void);
#endif
#endif
