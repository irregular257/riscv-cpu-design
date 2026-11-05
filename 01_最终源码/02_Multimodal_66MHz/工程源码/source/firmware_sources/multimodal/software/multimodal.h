#ifndef MULTIMODAL_H
#define MULTIMODAL_H
#include <stdint.h>
typedef struct { uint8_t x,y,w,h,label,confidence; } vision_detection_t;
unsigned digit_vision_infer(const uint8_t *,vision_detection_t *);
unsigned hanzi_vision_infer(const uint8_t *,vision_detection_t *);
const int16_t *digit_vision_last_head(void);
const int16_t *hanzi_vision_last_head(void);
void multimodal_loop(void);
#endif
