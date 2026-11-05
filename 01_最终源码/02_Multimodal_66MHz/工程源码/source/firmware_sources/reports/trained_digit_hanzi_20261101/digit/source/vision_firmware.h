#ifndef VISION_FIRMWARE_H
#define VISION_FIRMWARE_H
#include <stdint.h>
typedef struct { uint8_t x,y,w,h,label,confidence; } vision_detection_t;
/* Up to four detections. Raw head output is 4x4x15 signed INT16 Q4 values. */
unsigned vision_infer(const uint8_t pixels[1024],vision_detection_t detections[4]);
const int16_t *vision_last_head(void);
void vision_loop(void);
#endif
