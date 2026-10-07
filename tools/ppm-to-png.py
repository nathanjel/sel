#!/usr/bin/env python3
"""Converts a plain-text PPM (P3, 8 bits), such as examples/raytrace prints, to PNG.

    node examples/raytrace/js.mjs --ppm 640 360 2 > mark.ppm
    python3 tools/ppm-to-png.py mark.ppm mark.png [--scale N]

--scale N repeats every pixel N times along each axis (nearest neighbour), for
looking at a small frame. Standard library only: PNG is zlib and CRC-32.
"""
import argparse
import struct
import sys
import zlib


def read_p3(path):
    with open(path, encoding='ascii') as fh:
        tokens = fh.read().split()
    if not tokens or tokens[0] != 'P3':
        sys.exit(f'{path}: not a plain-text PPM (P3)')
    width, height, top = int(tokens[1]), int(tokens[2]), int(tokens[3])
    values = tokens[4:]
    if top != 255 or len(values) != width * height * 3:
        sys.exit(f'{path}: expected {width * height * 3} values up to 255, found {len(values)} up to {top}')
    return width, height, bytes(int(v) for v in values)


def png(width, height, rgb, scale):
    rows = []
    for y in range(height):
        line = rgb[y * width * 3:(y + 1) * width * 3]
        if scale > 1:
            line = b''.join(line[x * 3:x * 3 + 3] * scale for x in range(width))
        rows += [b'\x00' + line] * scale            # filter type 0 on every row

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    header = struct.pack('>IIBBBBB', width * scale, height * scale, 8, 2, 0, 0, 0)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header)
            + chunk(b'IDAT', zlib.compress(b''.join(rows), 9)) + chunk(b'IEND', b''))


def main():
    parser = argparse.ArgumentParser(description='plain-text PPM (P3) to PNG')
    parser.add_argument('ppm')
    parser.add_argument('png')
    parser.add_argument('--scale', type=int, default=1, help='repeat each pixel N times per axis')
    a = parser.parse_args()
    if a.scale < 1:
        parser.error('--scale must be at least 1')
    width, height, rgb = read_p3(a.ppm)
    with open(a.png, 'wb') as fh:
        fh.write(png(width, height, rgb, a.scale))


if __name__ == '__main__':
    main()
