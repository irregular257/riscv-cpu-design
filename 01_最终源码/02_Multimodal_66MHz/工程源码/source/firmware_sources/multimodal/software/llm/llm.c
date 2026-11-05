/* RVQW401 Qwen2.5-0.5B runtime. Authored for this bare-metal project.
 * FP32 activations with symmetric group32 signed Q4 weights. Correctness-first
 * software path: it does NOT claim the existing INT8 DMA accelerates Q4.
 * Model image and all large work arrays reside in external DDR.
 */
#include "llm.h"
#include <math.h>

#define DIM 896u
#define HIDDEN 4864u
#define LAYERS 24u
#define HEADS 14u
#define KVHEADS 2u
#define HEAD_DIM 64u
#define KV_DIM 128u
#define VOCAB 151936u
#define MAX_TOKENS 4096u
#define MAX_NORMAL_BYTES 8192u
#define MAX_CODEPOINTS 4096u
#define IM_START 151644u
#define IM_END 151645u
#define END_TEXT 151643u

typedef struct {
    uint8_t *base, *tok;
    size_t capacity, cursor;
    uint32_t image_size, tok_size, ctx, pos, ready, started, limit;
    uint32_t layers_offset, last_prompt, last_generated;
    float *x, *xn, *q, *k, *v, *a, *tmp, *gate, *up, *scores, *logits;
    float *key_cache, *value_cache, *rope_cos, *rope_sin;
    uint32_t *tokens, *merge_ids, *codepoints;
    uint8_t *normalized;
    uint32_t records, byte_ids, merges, merge_count, ranges, range_count;
    uint32_t specials, special_count, decompositions, decomposition_count;
    uint32_t compositions, composition_count;
} llm_state;
static llm_state s;

static uint32_t read32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static float readf(const uint8_t *p) {
    union { uint32_t u; float f; } x;
    x.u = read32(p);
    return x.f;
}
static int finite_f(float value) {
    union { uint32_t u; float f; } x;
    x.f = value;
    return (x.u & 0x7f800000u) != 0x7f800000u;
}
static void clear_bytes(void *p, size_t n) {
    uint8_t *b = (uint8_t *)p;
    while (n--) *b++ = 0;
}
static int bytes_equal(const uint8_t *a, const uint8_t *b, size_t n) {
    while (n--) if (*a++ != *b++) return 0;
    return 1;
}
static int span(uint32_t offset, uint32_t bytes, uint32_t total) {
    return offset <= total && bytes <= total - offset;
}
static void *arena(size_t bytes) {
    size_t at = (s.cursor + 63u) & ~(size_t)63u;
    if (at > s.capacity || bytes > s.capacity - at) return 0;
    s.cursor = at + bytes;
    return s.base + at;
}
static uint32_t tok32(uint32_t offset) { return read32(s.tok + offset); }
static uint32_t unicode_flags(uint32_t cp) {
    uint32_t lo = 0, hi = s.range_count;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        const uint8_t *p = s.tok + s.ranges + mid * 12;
        uint32_t first = read32(p), last = read32(p + 4);
        if (cp < first) hi = mid;
        else if (cp > last) lo = mid + 1;
        else return read32(p + 8);
    }
    return 0;
}
static int decode_utf8(const uint8_t *p, size_t n, uint32_t *cp, size_t *used) {
    if (!n) return 0;
    uint32_t c = p[0], value, count, minimum;
    if (c < 0x80) { *cp = c; *used = 1; return 1; }
    if (c >= 0xc2 && c <= 0xdf) { value = c & 31; count = 2; minimum = 0x80; }
    else if (c >= 0xe0 && c <= 0xef) { value = c & 15; count = 3; minimum = 0x800; }
    else if (c >= 0xf0 && c <= 0xf4) { value = c & 7; count = 4; minimum = 0x10000; }
    else return 0;
    if (n < count) return 0;
    for (uint32_t i = 1; i < count; ++i) {
        if ((p[i] & 0xc0) != 0x80) return 0;
        value = (value << 6) | (p[i] & 63);
    }
    if (value < minimum || value > 0x10ffff || (value >= 0xd800 && value <= 0xdfff)) return 0;
    *cp = value; *used = count; return 1;
}
static unsigned encode_utf8(uint32_t cp, uint8_t *out) {
    if (cp < 0x80) { out[0] = (uint8_t)cp; return 1; }
    if (cp < 0x800) { out[0] = 0xc0 | (cp >> 6); out[1] = 0x80 | (cp & 63); return 2; }
    if (cp < 0x10000) {
        out[0] = 0xe0 | (cp >> 12); out[1] = 0x80 | ((cp >> 6) & 63); out[2] = 0x80 | (cp & 63); return 3;
    }
    out[0] = 0xf0 | (cp >> 18); out[1] = 0x80 | ((cp >> 12) & 63);
    out[2] = 0x80 | ((cp >> 6) & 63); out[3] = 0x80 | (cp & 63); return 4;
}
static int append_decomposed(uint32_t cp, uint32_t *count, unsigned depth) {
    if (depth > 8 || (unicode_flags(cp) & 8u)) return 0;
    if (cp >= 0xac00 && cp < 0xac00 + 11172) {
        uint32_t index = cp - 0xac00;
        if (!append_decomposed(0x1100 + index / 588, count, depth + 1) ||
            !append_decomposed(0x1161 + (index % 588) / 28, count, depth + 1)) return 0;
        return !(index % 28) || append_decomposed(0x11a7 + index % 28, count, depth + 1);
    }
    uint32_t lo = 0, hi = s.decomposition_count;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        const uint8_t *p = s.tok + s.decompositions + mid * 16;
        uint32_t key = read32(p);
        if (cp < key) hi = mid;
        else if (cp > key) lo = mid + 1;
        else {
            if (!append_decomposed(read32(p + 4), count, depth + 1)) return 0;
            return read32(p + 12) == 1 || append_decomposed(read32(p + 8), count, depth + 1);
        }
    }
    if (*count >= MAX_CODEPOINTS) return 0;
    uint32_t cc = unicode_flags(cp) >> 8, at = (*count)++;
    while (cc && at && (unicode_flags(s.codepoints[at - 1]) >> 8) > cc) {
        s.codepoints[at] = s.codepoints[at - 1]; --at;
    }
    s.codepoints[at] = cp;
    return 1;
}
static uint32_t compose(uint32_t a, uint32_t b) {
    if (a >= 0x1100 && a < 0x1113 && b >= 0x1161 && b < 0x1176)
        return 0xac00 + ((a - 0x1100) * 21 + b - 0x1161) * 28;
    if (a >= 0xac00 && a < 0xac00 + 11172 && (a - 0xac00) % 28 == 0 && b > 0x11a7 && b < 0x11c3)
        return a + b - 0x11a7;
    uint32_t lo = 0, hi = s.composition_count;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        const uint8_t *p = s.tok + s.compositions + mid * 12;
        uint32_t x = read32(p), y = read32(p + 4);
        if (a < x || (a == x && b < y)) hi = mid;
        else if (a > x || (a == x && b > y)) lo = mid + 1;
        else return read32(p + 8);
    }
    return 0;
}
static int normalize(const uint8_t *p, size_t length) {
    uint32_t count = 0;
    for (size_t at = 0; at < length;) {
        uint32_t cp; size_t used;
        if (!decode_utf8(p + at, length - at, &cp, &used) || !append_decomposed(cp, &count, 0)) return LLM_ERR_INPUT;
        at += used;
    }
    if (!count) return 0;
    uint32_t written = 1, starter_at = 0, starter = s.codepoints[0];
    uint32_t last_cc = unicode_flags(starter) >> 8;
    if (last_cc) starter = 0xffffffffu;
    for (uint32_t i = 1; i < count; ++i) {
        uint32_t cp = s.codepoints[i], cc = unicode_flags(cp) >> 8;
        uint32_t result = compose(starter, cp);
        if (result && (last_cc == 0 || last_cc < cc)) {
            s.codepoints[starter_at] = starter = result;
        } else {
            if (cc == 0) { starter_at = written; starter = cp; }
            s.codepoints[written++] = cp; last_cc = cc;
        }
    }
    uint32_t bytes = 0;
    for (uint32_t i = 0; i < written; ++i) {
        if (bytes + 4 > MAX_NORMAL_BYTES) return LLM_ERR_INPUT;
        bytes += encode_utf8(s.codepoints[i], s.normalized + bytes);
    }
    return (int)bytes;
}
static uint32_t cp_at(const uint8_t *p, size_t n, size_t *used) {
    uint32_t cp = 0; (void)decode_utf8(p, n, &cp, used); return cp;
}
static uint32_t ascii_fold(uint32_t cp) {
    if (cp >= 'A' && cp <= 'Z') return cp + ('a' - 'A');
    return cp == 0x17f ? 's' : cp;
}
/* Exact seven-alternative Qwen2 regex scanner, on validated NFC UTF-8.
 * Unicode L/N/whitespace and canonical normalization tables are image data.
 */
static size_t pretoken_length(const uint8_t *p, size_t n) {
    size_t used, next_used, at = 0;
    uint32_t cp = cp_at(p, n, &used), flags = unicode_flags(cp);
    if (cp == '\'' && used < n) {
        uint32_t a = ascii_fold(cp_at(p + used, n - used, &next_used));
        if (a == 's' || a == 't' || a == 'm' || a == 'd') return used + next_used;
        size_t second = used + next_used;
        if (second < n) {
            size_t third; uint32_t b = ascii_fold(cp_at(p + second, n - second, &third));
            if ((a == 'r' && b == 'e') || (a == 'v' && b == 'e') || (a == 'l' && b == 'l')) return second + third;
        }
    }
    if (flags & 1) at = used;
    else if (cp != '\r' && cp != '\n' && !(flags & 3) && used < n) {
        uint32_t next = cp_at(p + used, n - used, &next_used);
        if (unicode_flags(next) & 1) at = used + next_used;
    }
    if (at) {
        while (at < n) {
            uint32_t c = cp_at(p + at, n - at, &used);
            if (!(unicode_flags(c) & 1)) break;
            at += used;
        }
        return at;
    }
    if (flags & 2) return used;
    at = cp == ' ' ? 1 : 0;
    size_t start = at;
    while (at < n) {
        uint32_t c = cp_at(p + at, n - at, &next_used);
        if (unicode_flags(c) & 7) break;
        at += next_used;
    }
    if (at > start) {
        while (at < n && (p[at] == '\r' || p[at] == '\n')) ++at;
        return at;
    }
    at = 0;
    size_t last_newline = 0, before_last = 0;
    while (at < n) {
        uint32_t c = cp_at(p + at, n - at, &next_used);
        if (!(unicode_flags(c) & 4)) break;
        before_last = at; at += next_used;
        if (c == '\r' || c == '\n') last_newline = at;
    }
    if (last_newline) return last_newline;
    if (at == n) return at;
    if (before_last) return before_last;
    return at; /* final \s+; every assigned scalar matches some alternative */
}
static uint32_t merge_lookup(uint32_t a, uint32_t b, uint32_t *result) {
    uint32_t lo = 0, hi = s.merge_count;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        const uint8_t *p = s.tok + s.merges + mid * 16;
        uint32_t x = read32(p), y = read32(p + 4);
        if (a < x || (a == x && b < y)) hi = mid;
        else if (a > x || (a == x && b > y)) lo = mid + 1;
        else { *result = read32(p + 8); return read32(p + 12); }
    }
    return 0xffffffffu;
}
static int encode_piece(const uint8_t *p, size_t n, uint32_t *out, size_t capacity) {
    if (n > MAX_TOKENS) return LLM_ERR_INPUT;
    size_t count = n;
    for (size_t i = 0; i < n; ++i) s.merge_ids[i] = tok32(s.byte_ids + p[i] * 4u);
    while (count > 1) {
        uint32_t best_rank = 0xffffffffu, best_result = 0; size_t best_at = 0;
        for (size_t i = 0; i + 1 < count; ++i) {
            uint32_t result, rank = merge_lookup(s.merge_ids[i], s.merge_ids[i + 1], &result);
            if (rank < best_rank) { best_rank = rank; best_result = result; best_at = i; }
        }
        if (best_rank == 0xffffffffu) break;
        s.merge_ids[best_at] = best_result;
        for (size_t i = best_at + 1; i + 1 < count; ++i) s.merge_ids[i] = s.merge_ids[i + 1];
        --count;
    }
    if (count > capacity) return LLM_ERR_CONTEXT;
    for (size_t i = 0; i < count; ++i) out[i] = s.merge_ids[i];
    return (int)count;
}
int llm_tokenize(const uint8_t *utf8, size_t length, uint32_t *out, size_t capacity) {
    if (!s.ready) return LLM_ERR_NOT_READY;
    if ((!utf8 && length) || !out || length > LLM_MAX_PROMPT_BYTES) return LLM_ERR_INPUT;
    int n = normalize(utf8, length);
    if (n < 0) return n;
    size_t at = 0, written = 0;
    while (at < (size_t)n) {
        size_t boundary = (size_t)n, special_length = 0; uint32_t special_id = 0;
        for (size_t i = at; i < (size_t)n; ++i) {
            for (uint32_t j = 0; j < s.special_count; ++j) {
                const uint8_t *record = s.tok + s.specials + j * 12;
                uint32_t length2 = read32(record + 8), off = read32(record + 4);
                if (length2 <= (size_t)n - i && bytes_equal(s.normalized + i, s.tok + off, length2)) {
                    boundary = i;
                    if (length2 > special_length) { special_length = length2; special_id = read32(record); }
                }
            }
            if (special_length) break;
        }
        while (at < boundary) {
            size_t size = pretoken_length(s.normalized + at, boundary - at);
            if (!size) return LLM_ERR_INPUT;
            int added = encode_piece(s.normalized + at, size, out + written, capacity - written);
            if (added < 0) return added;
            written += (size_t)added; at += size;
        }
        if (special_length) {
            if (written == capacity) return LLM_ERR_CONTEXT;
            out[written++] = special_id; at += special_length;
        }
    }
    return (int)written;
}

static const uint8_t *descriptor(unsigned layer, unsigned kind) {
    return s.base + s.layers_offset + (layer * 12u + kind) * 16u;
}
static int valid_tensor(const uint8_t *desc, unsigned rows, unsigned cols, unsigned type) {
    uint32_t offset = read32(desc), r = read32(desc + 4), c = read32(desc + 8), t = read32(desc + 12);
    uint32_t bytes = type == 1 ? rows * (cols / 32u) * 20u : rows * cols * 4u;
    return r == rows && c == cols && t == type && !(offset & 3u) && span(offset, bytes, s.image_size);
}
static int signed4(unsigned x) { return (x & 8u) ? (int)(x & 15u) - 16 : (int)(x & 15u); }
static void embedding(uint32_t token) {
    uint32_t offset = read32(s.base + 128);
    const uint8_t *p = s.base + offset + token * (DIM / 32) * 20;
    for (unsigned group = 0; group < DIM / 32; ++group, p += 20) {
        float scale = readf(p);
        for (unsigned i = 0; i < 16; ++i) {
            unsigned packed = p[4 + i];
            s.x[group * 32 + i * 2] = (float)signed4(packed & 15) * scale;
            s.x[group * 32 + i * 2 + 1] = (float)signed4(packed >> 4) * scale;
        }
    }
}
static void matvec(float *out, const float *x, const uint8_t *desc) {
    const uint8_t *p = s.base + read32(desc);
    unsigned rows = read32(desc + 4), cols = read32(desc + 8);
    for (unsigned row = 0; row < rows; ++row) {
        float total = 0;
        for (unsigned base = 0; base < cols; base += 32, p += 20) {
            float sum = 0, scale = readf(p);
            for (unsigned j = 0; j < 16; ++j) {
                unsigned packed = p[4 + j];
                sum += (float)signed4(packed & 15) * x[base + j * 2];
                sum += (float)signed4(packed >> 4) * x[base + j * 2 + 1];
            }
            total += sum * scale;
        }
        out[row] = total;
    }
}
static void add_bias(float *out, const uint8_t *desc) {
    const uint8_t *p = s.base + read32(desc);
    unsigned size = read32(desc + 8);
    for (unsigned i = 0; i < size; ++i) out[i] += readf(p + i * 4);
}
static void rmsnorm(float *out, const float *x, const uint8_t *desc) {
    const uint8_t *p = s.base + read32(desc);
    float sum = 0;
    for (unsigned i = 0; i < DIM; ++i) sum += x[i] * x[i];
    float scale = 1.0f / sqrtf(sum / (float)DIM + 1.0e-6f);
    for (unsigned i = 0; i < DIM; ++i) out[i] = x[i] * scale * readf(p + i * 4);
}
static void apply_rope(float *x, unsigned heads, unsigned pos) {
    for (unsigned h = 0; h < heads; ++h) for (unsigned j = 0; j < HEAD_DIM / 2; ++j) {
        float a = x[h * HEAD_DIM + j], b = x[h * HEAD_DIM + j + HEAD_DIM / 2];
        float c = s.rope_cos[pos * (HEAD_DIM / 2) + j], sn = s.rope_sin[pos * (HEAD_DIM / 2) + j];
        x[h * HEAD_DIM + j] = a * c - b * sn;
        x[h * HEAD_DIM + j + HEAD_DIM / 2] = b * c + a * sn;
    }
}
static int forward(uint32_t token, unsigned pos, unsigned want_logits) {
    if (token >= VOCAB || pos >= s.ctx) return LLM_ERR_CONTEXT;
    embedding(token);
    for (unsigned layer = 0; layer < LAYERS; ++layer) {
        rmsnorm(s.xn, s.x, descriptor(layer, 0));
        matvec(s.q, s.xn, descriptor(layer, 2)); add_bias(s.q, descriptor(layer, 9));
        matvec(s.k, s.xn, descriptor(layer, 3)); add_bias(s.k, descriptor(layer, 10));
        matvec(s.v, s.xn, descriptor(layer, 4)); add_bias(s.v, descriptor(layer, 11));
        apply_rope(s.q, HEADS, pos); apply_rope(s.k, KVHEADS, pos);
        unsigned layer_offset = layer * s.ctx * KV_DIM, cache_offset = layer_offset + pos * KV_DIM;
        for (unsigned i = 0; i < KV_DIM; ++i) {
            s.key_cache[cache_offset + i] = s.k[i]; s.value_cache[cache_offset + i] = s.v[i];
        }
        for (unsigned head = 0; head < HEADS; ++head) {
            unsigned kv_head = head / (HEADS / KVHEADS), q_offset = head * HEAD_DIM;
            float maximum = -3.402823466e38f;
            for (unsigned t = 0; t <= pos; ++t) {
                unsigned at = layer_offset + t * KV_DIM + kv_head * HEAD_DIM;
                float score = 0;
                for (unsigned j = 0; j < HEAD_DIM; ++j) score += s.q[q_offset + j] * s.key_cache[at + j];
                score *= 0.125f; s.scores[t] = score;
                if (score > maximum) maximum = score;
            }
            float denominator = 0;
            for (unsigned t = 0; t <= pos; ++t) { s.scores[t] = expf(s.scores[t] - maximum); denominator += s.scores[t]; }
            if (!(denominator > 0) || !finite_f(denominator)) return LLM_ERR_NUMERIC;
            for (unsigned j = 0; j < HEAD_DIM; ++j) s.a[q_offset + j] = 0;
            for (unsigned t = 0; t <= pos; ++t) {
                unsigned at = layer_offset + t * KV_DIM + kv_head * HEAD_DIM;
                float probability = s.scores[t] / denominator;
                for (unsigned j = 0; j < HEAD_DIM; ++j) s.a[q_offset + j] += probability * s.value_cache[at + j];
            }
        }
        matvec(s.tmp, s.a, descriptor(layer, 5));
        for (unsigned i = 0; i < DIM; ++i) s.x[i] += s.tmp[i];
        rmsnorm(s.xn, s.x, descriptor(layer, 1));
        matvec(s.gate, s.xn, descriptor(layer, 6));
        matvec(s.up, s.xn, descriptor(layer, 8));
        for (unsigned i = 0; i < HIDDEN; ++i) {
            float x = s.gate[i];
            /* Stable SiLU avoids expf overflow for strongly negative inputs. */
            float sigmoid = x >= 0 ? 1.0f / (1.0f + expf(-x)) : expf(x) / (1.0f + expf(x));
            s.gate[i] = x * sigmoid * s.up[i];
        }
        matvec(s.tmp, s.gate, descriptor(layer, 7));
        for (unsigned i = 0; i < DIM; ++i) s.x[i] += s.tmp[i];
    }
    if (want_logits) {
        rmsnorm(s.xn, s.x, s.base + 144);
        matvec(s.logits, s.xn, s.base + 128);
        for (unsigned i = 0; i < VOCAB; ++i) if (!finite_f(s.logits[i])) return LLM_ERR_NUMERIC;
    }
    return LLM_OK;
}
static int tokenizer_init(void) {
    const uint8_t magic[8] = {'R','V','T','K','4','0','1',0};
    if (s.tok_size < 128 || !bytes_equal(s.tok, magic, 8) || tok32(8) != 1 || tok32(12) != s.tok_size || tok32(16) != VOCAB) return 0;
    s.records = tok32(20); s.byte_ids = tok32(28); s.merges = tok32(32); s.merge_count = tok32(36);
    s.ranges = tok32(40); s.range_count = tok32(44); s.specials = tok32(48); s.special_count = tok32(52);
    s.decompositions = tok32(64); s.decomposition_count = tok32(68);
    s.compositions = tok32(72); s.composition_count = tok32(76);
    if (s.merge_count > 200000 || s.range_count > 10000 || s.special_count > 64 || s.decomposition_count > 10000 || s.composition_count > 10000) return 0;
    if (!span(s.records, VOCAB * 8, s.tok_size) || !span(s.byte_ids, 256 * 4, s.tok_size) ||
        !span(s.merges, s.merge_count * 16, s.tok_size) || !span(s.ranges, s.range_count * 12, s.tok_size) ||
        !span(s.specials, s.special_count * 12, s.tok_size) || !span(s.decompositions, s.decomposition_count * 16, s.tok_size) ||
        !span(s.compositions, s.composition_count * 12, s.tok_size)) return 0;
    for (unsigned i = 0; i < VOCAB; ++i) {
        uint32_t at = tok32(s.records + i * 8), n = tok32(s.records + i * 8 + 4);
        if (!span(at, n, s.tok_size) || n > 4096) return 0;
    }
    for (unsigned i = 0; i < 256; ++i) if (tok32(s.byte_ids + i * 4) >= VOCAB) return 0;
    for (unsigned i = 0; i < s.merge_count; ++i) {
        const uint8_t *p = s.tok + s.merges + i * 16;
        if (read32(p) >= VOCAB || read32(p + 4) >= VOCAB || read32(p + 8) >= VOCAB || read32(p + 12) >= s.merge_count) return 0;
        if (i) {
            uint32_t a = read32(p - 16), b = read32(p - 12);
            if (read32(p) < a || (read32(p) == a && read32(p + 4) <= b)) return 0;
        }
    }
    for (unsigned i = 0; i < s.special_count; ++i) {
        const uint8_t *p = s.tok + s.specials + i * 12;
        if (read32(p) >= VOCAB || !read32(p + 8) || !span(read32(p + 4), read32(p + 8), s.tok_size)) return 0;
    }
    return 1;
}
int llm_init(void *ddr_base, size_t ddr_bytes) {
    const uint8_t magic[8] = {'R','V','Q','W','4','0','1',0};
    clear_bytes(&s, sizeof(s));
    if (!ddr_base || ((uintptr_t)ddr_base & 3u) || ddr_bytes < 512) return LLM_ERR_IMAGE;
    s.base = (uint8_t *)ddr_base; s.capacity = ddr_bytes;
    if (!bytes_equal(s.base, magic, 8) || read32(s.base + 8) != 1) return LLM_ERR_IMAGE;
    s.image_size = read32(s.base + 12); s.ctx = read32(s.base + 40); s.layers_offset = read32(s.base + 52);
    if (s.image_size > ddr_bytes || s.image_size < 512 || s.ctx < 64 || s.ctx > 2048 ||
        read32(s.base + 16) != DIM || read32(s.base + 20) != HIDDEN || read32(s.base + 24) != LAYERS ||
        read32(s.base + 28) != HEADS || read32(s.base + 32) != KVHEADS || read32(s.base + 36) != VOCAB ||
        readf(s.base + 44) != 1000000.0f || readf(s.base + 48) != 1.0e-6f ||
        !span(s.layers_offset, LAYERS * 12 * 16, s.image_size)) return LLM_ERR_IMAGE;
    if (!valid_tensor(s.base + 128, VOCAB, DIM, 1) || !valid_tensor(s.base + 144, 1, DIM, 2)) return LLM_ERR_IMAGE;
    const unsigned rows[12] = {1,1,DIM,KV_DIM,KV_DIM,DIM,HIDDEN,DIM,HIDDEN,1,1,1};
    const unsigned cols[12] = {DIM,DIM,DIM,DIM,DIM,DIM,DIM,HIDDEN,DIM,DIM,KV_DIM,KV_DIM};
    for (unsigned layer = 0; layer < LAYERS; ++layer) for (unsigned j = 0; j < 12; ++j)
        if (!valid_tensor(descriptor(layer,j),rows[j],cols[j],j>=2 && j<=8 ? 1 : 2)) return LLM_ERR_IMAGE;
    uint32_t tok_offset = read32(s.base + 56); s.tok_size = read32(s.base + 60);
    if (!span(tok_offset, s.tok_size, s.image_size)) return LLM_ERR_IMAGE;
    s.tok = s.base + tok_offset;
    if (!tokenizer_init()) return LLM_ERR_IMAGE;
    s.cursor = s.image_size;
#define FLOATS(field, count) do { s.field = (float *)arena((size_t)(count) * 4); if (!s.field) return LLM_ERR_MEMORY; } while (0)
    FLOATS(x,DIM); FLOATS(xn,DIM); FLOATS(q,DIM); FLOATS(k,KV_DIM); FLOATS(v,KV_DIM);
    FLOATS(a,DIM); FLOATS(tmp,DIM); FLOATS(gate,HIDDEN); FLOATS(up,HIDDEN); FLOATS(scores,s.ctx);
    FLOATS(logits,VOCAB); FLOATS(key_cache,LAYERS*s.ctx*KV_DIM); FLOATS(value_cache,LAYERS*s.ctx*KV_DIM);
    FLOATS(rope_cos,s.ctx*(HEAD_DIM/2)); FLOATS(rope_sin,s.ctx*(HEAD_DIM/2));
#undef FLOATS
    s.tokens = (uint32_t *)arena(MAX_TOKENS * 4);
    s.merge_ids = (uint32_t *)arena(MAX_TOKENS * 4);
    s.codepoints = (uint32_t *)arena(MAX_CODEPOINTS * 4);
    s.normalized = (uint8_t *)arena(MAX_NORMAL_BYTES);
    if (!s.tokens || !s.merge_ids || !s.codepoints || !s.normalized) return LLM_ERR_MEMORY;
    for (unsigned j = 0; j < HEAD_DIM / 2; ++j) {
        float frequency = powf(1000000.0f, -(2.0f * (float)j) / (float)HEAD_DIM);
        for (unsigned p = 0; p < s.ctx; ++p) {
            float angle = (float)p * frequency;
            s.rope_cos[p * (HEAD_DIM / 2) + j] = cosf(angle);
            s.rope_sin[p * (HEAD_DIM / 2) + j] = sinf(angle);
        }
    }
    s.limit = LLM_DEFAULT_GENERATION_TOKENS; s.ready = 1;
    llm_reset();
    return LLM_OK;
}
void llm_reset(void) {
    /* KV is position-bounded; after reset every accessed position is rewritten. */
    s.pos = 0; s.started = 0; s.last_prompt = 0; s.last_generated = 0;
}
int llm_init_loaded(void *ddr_base, size_t uploaded_bytes, size_t ddr_bytes) {
    if (!ddr_base || uploaded_bytes < 512 || uploaded_bytes > ddr_bytes ||
        read32((const uint8_t *)ddr_base + 12) != uploaded_bytes) {
        clear_bytes(&s, sizeof(s));
        return LLM_ERR_IMAGE;
    }
    return llm_init(ddr_base, ddr_bytes);
}
void llm_get_info(llm_info_t *info) {
    if (!info) return;
    info->ready = s.ready; info->image_bytes = s.image_size;
    info->arena_bytes = s.cursor >= s.image_size ? (uint32_t)(s.cursor - s.image_size) : 0;
    info->context_tokens = s.ctx; info->used_tokens = s.pos;
    info->last_prompt_tokens = s.last_prompt; info->last_generated_tokens = s.last_generated;
}
int llm_set_generation_limit(unsigned tokens) {
    if (tokens < 1 || tokens > 256) return LLM_ERR_INPUT;
    s.limit = tokens; return LLM_OK;
}
static int append_encoded(const uint8_t *p, size_t n, unsigned *count) {
    int added = llm_tokenize(p, n, s.tokens + *count, MAX_TOKENS - *count);
    if (added < 0) return added;
    *count += (unsigned)added; return LLM_OK;
}
static int append_token(uint32_t token, unsigned *count) {
    if (*count >= MAX_TOKENS) return LLM_ERR_CONTEXT;
    s.tokens[(*count)++] = token; return LLM_OK;
}
int llm_chat(const uint8_t *prompt, size_t length, llm_emit_fn emit, void *user) {
    static const uint8_t system[] = "system\nYou are a helpful assistant. Reply concisely in the user's language.";
    static const uint8_t user_role[] = "user\n", assistant_role[] = "assistant\n", newline[] = "\n";
    if (!s.ready) return LLM_ERR_NOT_READY;
    if (!prompt || !length || length > LLM_MAX_PROMPT_BYTES || !emit) return LLM_ERR_INPUT;
    unsigned count = 0; int status;
#define APPEND(expr) do { status = (expr); if (status < 0) return status; } while (0)
    if (!s.started) {
        APPEND(append_token(IM_START,&count)); APPEND(append_encoded(system,sizeof(system)-1,&count));
        APPEND(append_token(IM_END,&count)); APPEND(append_encoded(newline,1,&count));
    }
    APPEND(append_token(IM_START,&count)); APPEND(append_encoded(user_role,sizeof(user_role)-1,&count));
    APPEND(append_encoded(prompt,length,&count)); APPEND(append_token(IM_END,&count));
    APPEND(append_encoded(newline,1,&count)); APPEND(append_token(IM_START,&count));
    APPEND(append_encoded(assistant_role,sizeof(assistant_role)-1,&count));
#undef APPEND
    if (count + s.limit + 2 > s.ctx - s.pos) return LLM_ERR_CONTEXT;
    s.last_prompt = count; s.last_generated = 0;
    for (unsigned i = 0; i < count; ++i) {
        status = forward(s.tokens[i], s.pos, i + 1 == count);
        if (status < 0) { llm_reset(); return status; }
        ++s.pos;
    }
    s.started = 1;
    for (unsigned step = 0; step < s.limit; ++step) {
        uint32_t next = 0;
        for (unsigned i = 1; i < VOCAB; ++i) if (s.logits[i] > s.logits[next]) next = i;
        if (next == IM_END || next == END_TEXT) break;
        uint32_t at = tok32(s.records + next * 8), n = tok32(s.records + next * 8 + 4);
        if (!n || next == IM_START) { llm_reset(); return LLM_ERR_TOKEN; }
        if (!emit(s.tok + at, n, user)) { llm_reset(); return LLM_ERR_CANCELLED; }
        ++s.last_generated;
        status = forward(next, s.pos, step + 1 < s.limit);
        if (status < 0) { llm_reset(); return status; }
        ++s.pos;
    }
    /* Complete assistant turn even at the explicit generation cap, so a later
     * user message cannot be interpreted as unfinished assistant content. */
    status = forward(IM_END, s.pos, 0);
    if (status < 0) { llm_reset(); return status; }
    ++s.pos;
    uint32_t newline_token; int n = llm_tokenize(newline, 1, &newline_token, 1);
    if (n != 1) { llm_reset(); return LLM_ERR_TOKEN; }
    status = forward(newline_token, s.pos, 0);
    if (status < 0) { llm_reset(); return status; }
    ++s.pos; return LLM_OK;
}
#ifdef LLM_TEST_API
const float *llm_test_forward(uint32_t token, uint32_t position) {
    if (!s.ready || forward(token, position, 1) != LLM_OK) return 0;
    return s.logits;
}
uint32_t llm_test_vocab_size(void) { return VOCAB; }
#endif
