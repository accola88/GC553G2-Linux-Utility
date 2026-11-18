// SPDX-License-Identifier: GPL-2.0-or-later
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <linux/videodev2.h>
#include <obs-module.h>
#include "v4l2-decoder.h"
#include "v4l2-helpers.h"

int main(int argc, char **argv)
{
    if (argc != 2) {
        fprintf(stderr, "Usage: %s input.jpg\n", argv[0]);
        return 2;
    }
    FILE *file = fopen(argv[1], "rb");
    assert(file);
    fseek(file, 0, SEEK_END);
    long size = ftell(file);
    assert(size > 0);
    rewind(file);
    unsigned char *jpeg = malloc(size);
    assert(jpeg && fread(jpeg, 1, size, file) == (size_t)size);
    fclose(file);
    struct v4l2_decoder decoder = {0};
    decoder.mode = getenv("TEST_BACKEND") ? atoi(getenv("TEST_BACKEND")) : V4L2_MJPEG_SOFTWARE;
    struct obs_source_frame frame = {0};
    assert(v4l2_init_decoder(&decoder, V4L2_PIX_FMT_MJPEG) == 0);
    assert(v4l2_decode_frame(&frame, jpeg, size, &decoder) == 0);
    assert(frame.format == VIDEO_FORMAT_I420 && frame.data[0]);
    unsigned char broken[] = {1, 2, 3, 4, 5};
    assert(v4l2_decode_frame(&frame, broken, sizeof(broken), &decoder) < 0);
    assert(v4l2_decode_frame(&frame, jpeg, size, &decoder) == 0);
    assert(v4l2_decode_frame(&frame, jpeg, 0, &decoder) > 0);
    /* Exercise fallback state transitions without pretending a GPU is available. */
    decoder.using_hardware = true;
    for (int i = 0; i < 7; ++i)
        assert(v4l2_decode_frame(&frame, broken, sizeof(broken), &decoder) < 0);
    assert(v4l2_decode_frame(&frame, broken, sizeof(broken), &decoder) > 0);
    assert(!decoder.using_hardware);
    assert(v4l2_decode_frame(&frame, jpeg, size, &decoder) == 0);
    v4l2_destroy_decoder(&decoder);
    free(jpeg);
    int numerator, denominator;
    int64_t packed = v4l2_pack_tuple(1, 120);
    assert(packed > INT32_MAX);
    v4l2_unpack_tuple(&numerator, &denominator, packed);
    assert(numerator == 1 && denominator == 120);
    puts("PASS: JPEG decode, damaged-frame recovery, empty-packet skip, 64-bit 120fps tuple");
    return 0;
}
